"""Compare VRT and GTI mosaics on already downloaded counties without running a pipeline."""
import argparse
import json
import time
from pathlib import Path

import mercantile
import numpy as np
from osgeo import gdal, osr

import geo_common as common
import imagery
from xinjiang import catalog


def warp_sample(path, tile, epsg):
    left, bottom, right, top = mercantile.xy_bounds(tile)
    src = osr.SpatialReference()
    src.ImportFromEPSG(3857)
    dst = osr.SpatialReference()
    dst.ImportFromEPSG(epsg)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    center = osr.CoordinateTransformation(src, dst).TransformPoint((left + right) / 2, (bottom + top) / 2)
    x, y = center[:2]
    start = time.perf_counter()
    ds = gdal.Warp('', str(path), options=gdal.WarpOptions(
        format='MEM', dstSRS=f'EPSG:{epsg}', outputBounds=(x-1280, y-1280, x+1280, y+1280),
        width=512, height=512, resampleAlg='average', dstAlpha=True,
        warpOptions=['NUM_THREADS=2'], multithread=True))
    if ds is None:
        raise RuntimeError(f'样区重投影失败: {path}')
    data = ds.ReadAsArray()
    ds = None
    return data, time.perf_counter()-start


def run(code, cfg, root):
    g = catalog()
    hit = g[g.dt_adcode == code]
    if len(hit) != 1:
        raise ValueError(f'行政代码不存在或不唯一: {code}')
    geom = hit.iloc[0].geometry
    tiles = common.expected_tiles(geom, cfg['zoom'])
    epsg = common.utm_for(geom)
    folder = root / code
    folder.mkdir(parents=True, exist_ok=True)
    results = {'code': code, 'city': hit.iloc[0].dt_name, 'tile_count': len(tiles)}
    for name, builder in (('vrt', imagery.build_vrt), ('gti', imagery.build_gti)):
        print(f'{code} {name}: 构建 {len(tiles)} 瓦片索引', flush=True)
        work = folder / name
        work.mkdir(exist_ok=True)
        start = time.perf_counter()
        source = builder(cfg, tiles, work)
        results[name] = {'path': str(source), 'build_seconds': time.perf_counter()-start,
                         'index_bytes': source.stat().st_size, 'sample_seconds': 0.0}
        print(f'{code} {name}: {results[name]["build_seconds"]:.1f} 秒', flush=True)
    sample_ids = sorted(set(np.linspace(0, len(tiles)-1, num=min(5, len(tiles)), dtype=int)))
    for sample_number, index in enumerate(sample_ids):
        order = ('vrt', 'gti') if sample_number % 2 == 0 else ('gti', 'vrt')
        samples = {}
        for name in order:
            samples[name], elapsed = warp_sample(results[name]['path'], tiles[index], epsg)
            results[name]['sample_seconds'] += elapsed
        reference, candidate = samples['vrt'], samples['gti']
        if not np.any(reference[3]):
            raise RuntimeError(f'样区 {index} 无有效像元')
        if reference.shape != candidate.shape:
            raise RuntimeError(f'样区 {index} 波段或尺寸不一致')
        difference = np.abs(reference.astype(np.int16)-candidate.astype(np.int16))
        if difference.max() != 0:
            raise RuntimeError(f'样区 {index} 像素不同: {np.count_nonzero(difference)} 个值, 最大差 {difference.max()}')
    results['sample_count'] = len(sample_ids)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('codes', nargs='+', help='仅限当前任务中已下载的地区行政代码')
    parser.add_argument('--work', default='jobs/mosaic-benchmark')
    args = parser.parse_args()
    cfg = common.load_config()
    root = Path(args.work).resolve()
    for code in args.codes:
        result = run(code, cfg, root)
        common.write_json(root/f'{code}.json', result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
