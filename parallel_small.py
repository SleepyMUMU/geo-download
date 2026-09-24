"""Process selected small counties beside the main batch in isolated work/output paths.

This is a processing worker, not a second xinjiang_batch pipeline. Its results
must be reviewed and reconciled before the main batch adopts them.
"""
import argparse
import json
import os
from pathlib import Path

import geo_common as common
from imagery import download, process
from xinjiang import catalog
from xinjiang_batch import (EXCLUDED_CODES, FILE_RANGE_END, FILE_RANGE_START,
                            SOURCE_FILE_CODES, resolve_source_files)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('codes', nargs='+', help='当前原始文件范围内的行政代码')
    parser.add_argument('--work', default='jobs/xinjiang-small-parallel')
    args = parser.parse_args()
    source_files = resolve_source_files(FILE_RANGE_START, FILE_RANGE_END)
    allowed = {code for name in source_files for code in SOURCE_FILE_CODES[name]}
    allowed -= EXCLUDED_CODES
    if len(args.codes) != len(set(args.codes)) or any(code not in allowed for code in args.codes):
        parser.error('行政代码重复、超出当前原始文件范围或属于排除项')

    cfg = common.load_config()
    root = Path(args.work).resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock = root / 'worker.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f'第二路已有工作进程或遗留锁: {lock}') from exc
    with os.fdopen(fd, 'w', encoding='ascii') as stream:
        stream.write(str(os.getpid()))
    try:
        cfg['jobs_dir'] = str(root)
        cfg['output_dir'] = str(root / 'output')
        cfg['processing_threads'] = 8
        cfg['warp_memory_mb'] = 512
        cfg['gdal_cache_mb'] = 512
        cfg['mosaic_driver'] = 'gti'
        data = catalog().reset_index(drop=True)
        batch = Path(common.load_config()['jobs_dir']) / 'xinjiang-selection-bf377779e0'
        for code in args.codes:
            status = json.loads((batch / 'status.json').read_text(encoding='utf-8'))
            current = next(row for row in status if row['code'] == code)
            if current['压制状态'] not in ('等待', '跳过', '失败'):
                print(f'{code} 主流水线已经处理，跳过第二路', flush=True)
                continue
            row = data[data.dt_adcode == code]
            if len(row) != 1:
                raise RuntimeError(f'原始行政代码不存在或不唯一: {code}')
            row = row.iloc[0]
            work = root / code
            work.mkdir(exist_ok=True)
            common.write_json(work/'parallel_status.json',
                              {'code': code, 'city': row.dt_name, 'phase': 'validating_tiles'})
            try:
                tiles = download(cfg, row.geometry)
                common.write_json(work/'parallel_status.json',
                                  {'code': code, 'city': row.dt_name, 'phase': 'processing',
                                   'tile_count': len(tiles)})
                common.write_json(work/'state.json', {'status': 'processing', 'code': code})
                artifact = process(cfg, row.geometry, row.dt_name, code, work, tiles)
                common.write_json(work/'state.json',
                                  {'status': 'processed', 'code': code, 'artifact': artifact})
                common.write_json(work/'parallel_status.json',
                                  {'code': code, 'city': row.dt_name,
                                   'phase': 'awaiting_visual_review_and_upload',
                                   'artifact': artifact['path']})
                print(f'{row.dt_name} {code} 本地压制和自动质检完成', flush=True)
            except Exception as exc:
                common.write_json(work/'parallel_status.json',
                                  {'code': code, 'city': row.dt_name, 'phase': 'failed',
                                   'error': f'{type(exc).__name__}: {exc}'})
                print(f'{row.dt_name} {code} 失败: {type(exc).__name__}: {exc}', flush=True)
    finally:
        lock.unlink()


if __name__ == '__main__':
    main()
