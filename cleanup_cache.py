"""Conservative cleanup for verified Xinjiang batch intermediates and tiles."""
import argparse
import json
import time
from pathlib import Path

import mercantile

import geo_common as common
from xinjiang import catalog
from xinjiang_batch import FILE_RANGE_START, FILE_RANGE_END, resolve_source_files, select_source_files


def verified_row(row, work, output_root, parent_fid):
    if row['压制状态'] != '已压制（质检通过）' or row['上传状态'] != '已上传并核验':
        return False
    try:
        state=json.loads((work/'state.json').read_text(encoding='utf-8'))
        artifact=state['artifact']
        output=Path(artifact['path']).resolve()
        quality=json.loads((work/'quality.json').read_text(encoding='utf-8'))
        review=json.loads((work/'visual_review.json').read_text(encoding='utf-8'))
        receipt=json.loads((work/'upload_receipt.json').read_text(encoding='utf-8'))
        return (state['status']=='processed'
                and output.is_relative_to(output_root.resolve())
                and output.is_file() and output.stat().st_size==artifact['size']
                and common.digest_file(output)==artifact['sha256']
                and quality.get('passed') is True
                and quality.get('full_readback_verified') is True
                and review.get('result')=='pass'
                and artifact.get('comparisons')
                and all(Path(p).is_file() for p in artifact['comparisons'])
                and receipt.get('verified') is True
                and receipt.get('verification')=='fresh_listing_fid_name_size'
                and receipt.get('cloud_listing_fid')
                and receipt.get('local_sha256')==artifact['sha256']
                and receipt.get('size')==artifact['size']
                and receipt.get('parent_fid')==parent_fid)
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        return False


def overlaps_pending(tile, pending_bounds):
    west,south,east,north=mercantile.bounds(tile)
    return any(not (east < w or west > e or north < s or south > n)
               for w,s,e,n in pending_bounds)


def cleanup_once(cfg, batch, geoms, rows, dry_run=False, intermediates_only=False):
    by_code={r['code']:r for r in rows}
    eligible={code for code in geoms if code in by_code and
              verified_row(by_code[code],batch/code,Path(cfg['output_dir']),cfg['quark']['parent_fid'])}
    pending_bounds=[geom.bounds for code,geom in geoms.items() if code not in eligible]
    reclaimed=0
    removed_tiles=0
    for code in sorted(eligible):
        work=batch/code
        for name in ('reference_rgb.tif','mosaic.vrt'):
            path=work/name
            if path.is_file():
                size=path.stat().st_size
                if not dry_run:
                    path.unlink()
                reclaimed+=size
        print(f'已核验并处理地区 {code}',flush=True)
        if intermediates_only:
            continue
        for tile in common.expected_tiles(geoms[code],cfg['zoom']):
            if overlaps_pending(tile,pending_bounds):
                continue
            path=common.tile_path(cfg,tile)
            if path.is_file():
                size=path.stat().st_size
                if not dry_run:
                    path.unlink()
                reclaimed+=size
                removed_tiles+=1
            world=path.with_suffix('.pgw')
            if world.is_file() and not dry_run:
                world.unlink()
    verb='预计可释放' if dry_run else '已释放'
    print(f'可清理地区 {len(eligible)}/{len(geoms)}，瓦片 {removed_tiles}，{verb} {reclaimed/1024**3:.2f} GiB',flush=True)
    return reclaimed


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--watch',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--intermediates-only',action='store_true')
    parser.add_argument('--interval',type=int,default=120)
    args=parser.parse_args()
    cfg=common.load_config()
    files=resolve_source_files(FILE_RANGE_START,FILE_RANGE_END)
    geoms={str(r.dt_adcode):r.geometry for _,r in select_source_files(catalog().reset_index(drop=True),files)}
    batch=Path(cfg['jobs_dir'])/f"xinjiang-selection-{common.fingerprint({'source_files':files})[:10]}"
    status=batch/'status.json'
    last_signature=None
    while True:
        rows=json.loads(status.read_text(encoding='utf-8'))
        signature=(tuple((r['code'],r['压制状态'],r['上传状态']) for r in rows),
                   tuple((code,(batch/code/'visual_review.json').stat().st_mtime_ns
                          if (batch/code/'visual_review.json').exists() else 0)
                         for code in geoms))
        if signature!=last_signature:
            cleanup_once(cfg,batch,geoms,rows,args.dry_run,args.intermediates_only)
            last_signature=signature
        if not args.watch:
            break
        time.sleep(args.interval)


if __name__=='__main__':
    main()
