"""Create independently verifiable 7z archives of completed XYZ tile columns.

Only columns outside the remaining Qemo/Ruoqiang longitude span may be
archived while gm_batch.py is still running. The final pass may archive all
remaining columns after the batch finishes with --keep-tiles.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

SOURCE = Path(r'D:\WorkSpace\AI\Geo Download\tiles_v2\4d430470504d4d5926b5\16')
BACKUP = Path(r'D:\WorkSpace\AI\Geo Download\jobs\gm-tile-backup')
SEVEN_ZIP = Path(r'C:\Program Files\7-Zip\7z.exe')
SUMMARY = Path(r'D:\WorkSpace\AI\Geo Download\jobs\gm-batch-wayback58924-z16\summary.json')


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    temp = path.with_suffix(path.suffix + '.partial')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--safe-until-x', type=int, help='Archive only x values below this bound while GM is active')
    parser.add_argument('--max-gib', type=float, default=10.0)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.max_gib <= 0:
        parser.error('--max-gib must be positive')
    if not SOURCE.is_dir() or not SEVEN_ZIP.is_file():
        raise RuntimeError('Tile source or 7-Zip executable is missing')
    if args.safe_until_x is None:
        summary = json.loads(SUMMARY.read_text(encoding='utf-8'))
        if summary.get('selected') != 25 or summary.get('failed_codes') != []:
            raise RuntimeError('Full GM batch has not completed; specify a proven safe x bound')

    BACKUP.mkdir(parents=True, exist_ok=True)
    volumes = BACKUP / 'volumes'
    volumes.mkdir(exist_ok=True)
    ledger_path = BACKUP / 'ledger.json'
    ledger = json.loads(ledger_path.read_text(encoding='utf-8')) if ledger_path.exists() else {'source': str(SOURCE), 'archives': []}
    recorded = {x for item in ledger['archives'] for x in item['columns']}
    candidates = sorted((p for p in SOURCE.iterdir() if p.is_dir() and p.name.isdigit()
                         and p.name not in recorded
                         and (args.safe_until_x is None or int(p.name) < args.safe_until_x)),
                        key=lambda p: int(p.name))
    if not candidates:
        print('No unarchived eligible tile columns', flush=True)
        return

    limit = int(args.max_gib * 1024**3)
    columns = []
    total = 0
    count = 0
    for folder in candidates:
        files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() == '.png']
        folder_bytes = sum(f.stat().st_size for f in files)
        if columns and total + folder_bytes > limit:
            break
        columns.append(folder.name)
        total += folder_bytes
        count += len(files)
    name = f'Wayback58924_z16_tiles_x{columns[0]}-{columns[-1]}.7z'
    destination = volumes / name
    if destination.exists():
        raise RuntimeError(f'Unrecorded archive exists; inspect before retry: {destination}')
    print(f'{name}: {len(columns)} columns, {count} PNG files, {total / 1024**3:.2f} GiB', flush=True)
    if args.dry_run:
        return

    command = [str(SEVEN_ZIP), 'a', '-t7z', '-m0=LZMA2', '-mx=9', '-md=64m', '-ms=on',
               '-mmt=8', str(destination), *columns]
    subprocess.run(command, cwd=SOURCE, check=True)
    subprocess.run([str(SEVEN_ZIP), 't', str(destination)], check=True)
    record = {'archive': str(destination), 'columns': columns, 'file_count': count,
              'source_bytes': total, 'archive_bytes': destination.stat().st_size,
              'sha256': digest(destination), 'local_verified_at': time.strftime('%Y-%m-%d %H:%M:%S'),
              'cloud': None}
    ledger['archives'].append(record)
    write_json(ledger_path, ledger)
    print(f'LOCAL_VERIFIED {name} {record["archive_bytes"]} {record["sha256"]}', flush=True)


if __name__ == '__main__':
    main()
