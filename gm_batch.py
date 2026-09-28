"""Unattended, resumable local-tile -> MBTiles -> Global Mapper GeoTIFF batch.

This is separate from the completed Xinjiang delivery. It never uploads files.
Run with the sat Python interpreter; one county is processed at a time.
"""

import argparse
import json
import msvcrt
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from pathlib import Path

from osgeo import gdal
from shapely.geometry import mapping

import geo_common as common
from imagery import download
from xinjiang import catalog
from xinjiang_batch import (
    EXCLUDED_CODES, FILE_RANGE_END, FILE_RANGE_START, SOURCE_FILE_CODES,
    resolve_source_files, select_source_files,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_ROOT = ROOT / 'jobs' / 'gm-batch-wayback58924-z16'
GM_EXE = Path(r'C:\Program Files\GlobalMapper26.0_64bit\global_mapper.exe')


def status(root, code, **fields):
    path = root / code / 'gm_status.json'
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'code': code}
    data.update(fields, updated=time.strftime('%Y-%m-%d %H:%M:%S'))
    common.write_json(path, data)
    print(f"{code}: {data['phase']}" + (f" — {data['error']}" if data.get('error') else ''), flush=True)


def quote_script(value):
    value = str(value)
    if '"' in value or '\n' in value or '\r' in value:
        raise ValueError('GM script path contains a quote or newline')
    return '"' + value + '"'


def make_mbtiles(path, tiles, cfg, geom):
    """Package already validated XYZ PNGs; resume an interrupted SQLite package."""
    partial = path.with_name(path.stem + '.partial.mbtiles')
    if path.exists():
        con = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)
        try:
            found = con.execute('SELECT COUNT(*) FROM tiles').fetchone()[0]
            good = con.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
            schema = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='tiles'").fetchone()
            gm_compatible = schema is not None and 'WITHOUT ROWID' not in schema[0].upper()
            if found == len(tiles) and good and gm_compatible:
                return path
        finally:
            con.close()
        if not good:
            print(f'Rebuilding invalid MBTiles: {path}', flush=True)
        elif not gm_compatible:
            print(f'Rebuilding MBTiles with GM-compatible SQLite schema: {path}', flush=True)
        else:
            print(f'Rebuilding incomplete MBTiles {found}/{len(tiles)}: {path}', flush=True)
    con = sqlite3.connect(partial)
    try:
        con.execute('PRAGMA journal_mode=DELETE')
        con.execute('PRAGMA synchronous=NORMAL')
        con.execute('CREATE TABLE IF NOT EXISTS metadata (name TEXT, value TEXT)')
        con.execute('CREATE TABLE IF NOT EXISTS tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)')
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS tile_index ON tiles (zoom_level, tile_column, tile_row)')
        west, south, east, north = geom.bounds
        metadata = {
            'name': path.stem, 'type': 'baselayer', 'version': '1.3', 'format': 'png',
            'minzoom': str(cfg['zoom']), 'maxzoom': str(cfg['zoom']),
            'bounds': f'{west},{south},{east},{north}',
        }
        con.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)', metadata.items())
        con.commit()
        for i, tile in enumerate(tiles, 1):
            key = (tile.z, tile.x, (1 << tile.z) - 1 - tile.y)
            if con.execute('SELECT 1 FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?', key).fetchone() is None:
                tile_file = common.tile_path(cfg, tile)
                con.execute('INSERT INTO tiles VALUES (?,?,?,?)', (*key, tile_file.read_bytes()))
            if i % 1000 == 0:
                con.commit()
                if i % 10000 == 0:
                    print(f'MBTiles {path.stem}: {i}/{len(tiles)}', flush=True)
        con.commit()
        found = con.execute('SELECT COUNT(*) FROM tiles').fetchone()[0]
        if found != len(tiles) or con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError(f'MBTiles count/integrity failed: {found}/{len(tiles)}')
    finally:
        con.close()
    os.replace(partial, path)
    return path


def gm_script(work, mbtiles, boundary, output, epsg):
    script = work / 'export.gms'
    log = work / 'global_mapper.log'
    lines = [
        'GLOBAL_MAPPER_SCRIPT VERSION=1.00 LOG_ERRLOG_MESSAGES=YES LOG_TO_COMMAND_PROMPT=YES',
        f'SET_LOG_FILE FILENAME={quote_script(log)} APPEND_TO_FILE=NO',
        f'IMPORT FILENAME={quote_script(mbtiles)} TYPE=MBTILES LAYER_DESC="Source_tiles"',
        f'LOAD_PROJECTION PROJ="EPSG:{epsg}"',
        ('EXPORT_RASTER TYPE=GEOTIFF '
         f'FILENAME={quote_script(output)} EXPORT_LAYER="Source_tiles" '
         f'POLYGON_CROP_FILE={quote_script(boundary)} SPATIAL_RES="5,5" '
         'SAMPLING_METHOD=DEFAULT PALETTE=OPTIMIZED COMPRESSION=PACKBITS '
         'INC_VECTOR_DATA=NO BG_TRANSPARENT=NO ADD_OVERVIEW_LAYERS=NO '
         'OVERWRITE_EXISTING=NO'),
        'LOG_MESSAGE GM_BATCH_EXPORT_COMPLETE',
    ]
    script.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return script, log


def check_disk_for_county(root, cfg, tiles):
    """Estimate missing PNGs plus a temporary MBTiles before network writes."""
    missing = 0
    sampled_sizes = []
    stride = max(1, len(tiles) // 128)
    for index, tile in enumerate(tiles):
        path = common.tile_path(cfg, tile)
        if not path.is_file():
            missing += 1
        elif index % stride == 0:
            sampled_sizes.append(path.stat().st_size)
    bytes_per_tile = max(150_000, sum(sampled_sizes) // len(sampled_sizes)) if sampled_sizes else 150_000
    reserve = 40 * 1024**3
    estimated = (missing + len(tiles)) * bytes_per_tile + reserve
    free = shutil.disk_usage(root).free
    if free < estimated:
        raise RuntimeError(f'Insufficient disk: {free // 1024**3} GiB free, '
                           f'{estimated // 1024**3} GiB estimated for tiles, MBTiles and reserve')
    return {'missing_tiles': missing, 'estimated_tile_bytes': bytes_per_tile,
            'free_gib': round(free / 1024**3, 1)}


def run_gm(script, log, output, timeout_hours=24):
    if not GM_EXE.is_file():
        raise FileNotFoundError(GM_EXE)
    started = time.monotonic()
    proc = subprocess.Popen([str(GM_EXE), str(script)], cwd=str(script.parent),
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    while True:
        result = proc.poll()
        if result is not None:
            content = log.read_text(encoding='utf-8', errors='replace') if log.exists() else ''
            if result != 0 or 'GM_BATCH_EXPORT_COMPLETE' not in content or 'Script processing COMPLETED' not in content:
                raise RuntimeError(f'GM exit={result}, completion marker absent; log={log}')
            if not output.exists() or output.stat().st_size < 1024:
                raise RuntimeError(f'GM reported completion but output is missing: {output}')
            return
        if time.monotonic() - started > timeout_hours * 3600:
            proc.terminate()
            raise TimeoutError(f'GM exceeded {timeout_hours} hours; log={log}')
        time.sleep(10)


def verify_output(path, epsg):
    ds = gdal.Open(str(path), gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(f'Unreadable GeoTIFF: {path}')
    band = ds.GetRasterBand(1)
    gt = ds.GetGeoTransform()
    actual = {
        'width': ds.RasterXSize, 'height': ds.RasterYSize,
        'epsg': ds.GetSpatialRef().GetAuthorityCode(None),
        'resolution': [abs(gt[1]), abs(gt[5])], 'bands': ds.RasterCount,
        'dtype': gdal.GetDataTypeName(band.DataType),
        'colors': band.GetColorTable().GetCount() if band.GetColorTable() else 0,
        'compression': ds.GetMetadataItem('COMPRESSION', 'IMAGE_STRUCTURE'),
        'overviews': band.GetOverviewCount(), 'block': band.GetBlockSize(),
    }
    if not (actual['epsg'] == str(epsg) and actual['bands'] == 1
            and actual['dtype'] == 'Byte' and actual['colors'] == 256
            and actual['compression'] == 'PACKBITS' and actual['overviews'] == 0
            and all(abs(x - 5) < 1e-6 for x in actual['resolution'])
            and actual['width'] > 0 and actual['height'] > 0):
        raise RuntimeError(f'GM GeoTIFF specification mismatch: {actual}')
    rows_per_read = max(1, min(256, 32_000_000 // actual['width']))
    for y in range(0, actual['height'], rows_per_read):
        rows = min(rows_per_read, actual['height'] - y)
        if band.ReadRaster(0, y, actual['width'], rows) is None:
            raise RuntimeError(f'GM GeoTIFF readback failed at row {y}: {path}')
    ds = None
    actual['full_readback_verified'] = True
    actual['bytes'] = path.stat().st_size
    actual['sha256'] = common.digest_file(path)
    return actual


def process_one(root, cfg, row, source_file):
    code = str(row.dt_adcode)
    name = str(row.dt_name)
    epsg = common.utm_for(row.geometry)
    work = root / code
    work.mkdir(parents=True, exist_ok=True)
    output = root / 'output' / f'{code}_{name}_Wayback58924_z16_GM_8bit_5m_UTM{epsg-32600}N_default.tif'
    output.parent.mkdir(parents=True, exist_ok=True)
    state_file = work / 'gm_status.json'
    if state_file.exists():
        previous = json.loads(state_file.read_text(encoding='utf-8'))
        if (previous.get('phase') == 'complete' and output.exists()
                and output.stat().st_size == previous.get('output', {}).get('bytes')):
            if common.digest_file(output) == previous['output']['sha256']:
                print(f'{code}: verified existing output, skipped', flush=True)
                return
    if output.exists():
        try:
            result = verify_output(output, epsg)
        except Exception as exc:
            raise RuntimeError(f'Existing output needs manual audit before retry: {output}: {exc}') from exc
        status(root, code, phase='complete', name=name, source_file=source_file,
               epsg=epsg, output_path=str(output), output=result, error=None,
               traceback=None)
        return
    status(root, code, phase='planning', name=name, source_file=source_file,
           epsg=epsg, output_path=str(output), error=None)
    tiles = common.expected_tiles(row.geometry, cfg['zoom'])
    if not tiles:
        raise RuntimeError('No intersecting tiles')
    disk_plan = check_disk_for_county(root, cfg, tiles)
    status(root, code, phase='downloading', tile_count=len(tiles), disk_plan=disk_plan)
    for attempt in range(2):
        try:
            tiles = download(cfg, row.geometry, tiles=tiles)
            break
        except Exception:
            if attempt:
                raise
            status(root, code, phase='download_retry', error='First pass had missing tiles; retrying after 60 seconds')
            time.sleep(60)
    boundary = work / 'boundary.geojson'
    boundary.write_text(json.dumps({'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': {'code': code, 'name': name},
         'geometry': mapping(row.geometry)}]}, ensure_ascii=False), encoding='utf-8')
    if shutil.disk_usage(root).free < 40 * 1024**3:
        raise RuntimeError('Less than 40 GiB free before MBTiles packaging')
    mbtiles = work / f'{code}_Wayback58924_z16.mbtiles'
    status(root, code, phase='packaging')
    make_mbtiles(mbtiles, tiles, cfg, row.geometry)
    script, log = gm_script(work, mbtiles, boundary, output, epsg)
    for attempt in range(2):
        status(root, code, phase='exporting', gm_script=str(script), gm_log=str(log),
               export_attempt=attempt + 1, error=None)
        try:
            run_gm(script, log, output)
            status(root, code, phase='verifying')
            result = verify_output(output, epsg)
            break
        except Exception:
            stamp = time.strftime('%Y%m%d-%H%M%S')
            if output.exists():
                output.replace(output.with_name(f'{output.stem}.failed-{stamp}{output.suffix}'))
            if log.exists():
                shutil.copy2(log, work / f'global_mapper.failed-{stamp}.log')
            if attempt:
                raise
            status(root, code, phase='export_retry', error='First GM export failed; retrying from validated MBTiles')
    status(root, code, phase='complete', output=result, error=None, traceback=None)
    mbtiles.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--only-code', action='append', default=[])
    parser.add_argument('--start-file', default=FILE_RANGE_START)
    parser.add_argument('--end-file', default=FILE_RANGE_END)
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--download-threads', type=int, default=16)
    parser.add_argument('--keep-tiles', action='store_true',
                        help='Retain validated XYZ tile cache after all selected counties complete')
    args = parser.parse_args()
    cfg = common.load_config()
    if not 1 <= args.download_threads <= 32:
        parser.error('--download-threads must be from 1 to 32')
    cfg['max_threads'] = args.download_threads
    source_files = resolve_source_files(args.start_file, args.end_file)
    selected = select_source_files(catalog().reset_index(drop=True), source_files)
    wanted = set(args.only_code)
    if wanted:
        selected = [(source, row) for source, row in selected if str(row.dt_adcode) in wanted]
        if {str(row.dt_adcode) for _, row in selected} != wanted:
            parser.error('Unknown or excluded --only-code in selected source-file range')
    else:
        # Xinhe was already exported and compared separately.
        selected = [(source, row) for source, row in selected if str(row.dt_adcode) != '652925']
        # Get usable results early; the largest counties are processed last.
        selected.sort(key=lambda item: item[1].geometry.area)
    root = args.root.resolve()
    if args.plan:
        for source, row in selected:
            print(f'{row.dt_adcode}\t{row.dt_name}\t{source}\tEPSG:{common.utm_for(row.geometry)}')
        return
    root.mkdir(parents=True, exist_ok=True)
    summary_file = root / 'summary.json'
    if summary_file.exists():
        summary = json.loads(summary_file.read_text(encoding='utf-8'))
        if summary.get('selected') == len(selected) and summary.get('failed_codes') == []:
            print('GM batch already completed; no work to repeat', flush=True)
            return
    lock = root / 'worker.lock'
    lock_stream = lock.open('a+b')
    try:
        lock_stream.seek(0)
        msvcrt.locking(lock_stream.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        lock_stream.close()
        raise RuntimeError(f'GM batch already running: {lock}') from exc
    lock_stream.seek(0)
    lock_stream.truncate()
    lock_stream.write(str(os.getpid()).encode('ascii'))
    lock_stream.flush()
    failures = []
    try:
        for source, row in selected:
            code = str(row.dt_adcode)
            try:
                process_one(root, cfg, row, source)
            except Exception as exc:
                failures.append(code)
                status(root, code, phase='failed', error=f'{type(exc).__name__}: {exc}',
                       traceback=traceback.format_exc()[-5000:])
        cleaned_tiles = 0
        if not failures and not args.keep_tiles:
            for _, row in selected:
                for tile in common.expected_tiles(row.geometry, cfg['zoom']):
                    path = common.tile_path(cfg, tile)
                    if path.exists():
                        path.unlink()
                        cleaned_tiles += 1
            print(f'Cleaned {cleaned_tiles} verified, no-longer-needed XYZ tiles', flush=True)
        common.write_json(root / 'summary.json',
                          {'selected': len(selected), 'failed_codes': failures,
                           'cleaned_tiles': cleaned_tiles,
                           'finished': time.strftime('%Y-%m-%d %H:%M:%S')})
    finally:
        lock_stream.seek(0)
        msvcrt.locking(lock_stream.fileno(), msvcrt.LK_UNLCK, 1)
        lock_stream.close()
        lock.unlink(missing_ok=True)
    if failures:
        sys.exit(1)


if __name__ == '__main__':
    main()
