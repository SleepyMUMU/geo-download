"""Bounded XYZ download -> independent 7z -> official Quark relay.

Run needs only Pillow, requests and 7z. Plan alone uses the existing sat GIS environment.
No GIS processing, unofficial upload, or production cache deletion occurs here.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import io
import json
import math
import os
import re
from pathlib import Path
import shutil
import subprocess
import threading
import time
import zlib
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

from PIL import Image
import requests
import artifact_utils as common

ROOT = Path(__file__).resolve().parent
BASE = ROOT / 'jobs' / 'cloud-relay'
SOURCE_URL = 'https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/58924/{z}/{y}/{x}'
MIB = 1024**2
MAX_TILE_BYTES = 4 * MIB
LOCAL = threading.local()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate_plan(plan):
    body = {k: v for k, v in plan.items() if k != 'id'}
    if plan.get('id') != common.fingerprint(body)[:20] or plan.get('schema') != 1:
        raise ValueError('Plan identity/schema is invalid')
    if plan.get('source_url') != SOURCE_URL or plan.get('zoom') != 16:
        raise ValueError('Only the verified Wayback58924 z16 source is supported')
    tiles = plan.get('tiles', [])
    if not tiles or len(tiles) > 100000:
        raise ValueError('Plan must contain 1–100000 tiles')
    if any(len(t) != 3 or any(type(n) is not int for n in t) or t[2] != 16
           or not (0 <= t[0] < 65536 and 0 <= t[1] < 65536) for t in tiles):
        raise ValueError('Invalid XYZ coordinates')
    if len({tuple(t) for t in tiles}) != len(tiles):
        raise ValueError('Duplicate tiles in plan')
    return plan


def task_root(plan):
    root = BASE / plan['id']
    if root.resolve().parent != BASE.resolve() or root.is_symlink():
        raise ValueError('Task directory must be isolated')
    return root


@contextmanager
def worker_lock(root):
    """OS lock releases after a crash; lock inode is never unlinked while in use."""
    import fcntl
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'worker.lock').open('a+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('This relay task is already running') from exc
        stream.seek(0)
        stream.truncate()
        stream.write(str(os.getpid()))
        stream.flush()
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def record_path(root, tile):
    x, y, z = tile
    return root / 'records' / f'{z}_{x}_{y}.json'


def valid_record(root, record):
    relative = Path(record['path'])
    path = root / relative
    if (relative.is_absolute() or '..' in relative.parts or relative.parts[0] != 'data'
            or path.is_symlink() or not path.resolve().is_relative_to((root / 'data').resolve())):
        raise ValueError('Unsafe tile record path')
    if not path.is_file() or path.stat().st_size != record['size'] or common.digest_file(path) != record['sha256']:
        return False
    try:
        with Image.open(path) as image:
            image.load()
            return image.size == (256, 256) and image.mode in ('RGB', 'RGBA') and (
                image.mode != 'RGBA' or image.getextrema()[3] == (255, 255))
    except (OSError, ValueError):
        return False


def download_tile(root, tile, retries=3):
    stored = record_path(root, tile)
    if stored.exists():
        record = read_json(stored)
        if record.get('tile') == tile and valid_record(root, record):
            return record, False
    if not hasattr(LOCAL, 'session'):
        LOCAL.session = requests.Session()  # Respect the cloud's HTTP(S)_PROXY.
        LOCAL.session.headers['User-Agent'] = 'Mozilla/5.0 GeoDownloadRelay/1.0'
    x, y, z = tile
    for attempt in range(retries):
        try:
            with LOCAL.session.get(SOURCE_URL.format(x=x, y=y, z=z), timeout=(15, 60), stream=True) as response:
                if response.status_code == 429:
                    delay = response.headers.get('Retry-After', '5')
                    try:
                        delay = max(0, float(delay))
                    except ValueError:
                        try:
                            delay = max(0, (parsedate_to_datetime(delay) - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError):
                            delay = 5
                    if delay > 60:
                        raise RuntimeError(f'Server requires Retry-After={delay:.0f}s; pause instead of ignoring it')
                    time.sleep(delay)
                response.raise_for_status()
                parts, total = [], 0
                for part in response.iter_content(65536):
                    total += len(part)
                    if total > MAX_TILE_BYTES:
                        raise ValueError('Tile response exceeds 4 MiB bound')
                    parts.append(part)
                data = b''.join(parts)
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                if image.size != (256, 256) or image.mode not in ('RGB', 'RGBA'):
                    raise ValueError('Unexpected image dimensions/mode')
                if image.mode == 'RGBA' and image.getextrema()[3] != (255, 255):
                    raise ValueError('Transparent/no-data tile')
                if image.format not in ('JPEG', 'PNG'):
                    raise ValueError('Unexpected image format')
                suffix = '.jpg' if image.format == 'JPEG' else '.png'
            path = root / 'data' / str(z) / str(x) / f'{y}{suffix}'
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix('.partial')
            temp.write_bytes(data)  # Preserve original wire image bytes.
            os.replace(temp, path)
            record = {'tile': tile, 'path': path.relative_to(root).as_posix(), 'size': len(data),
                      'sha256': common.digest_file(path), 'crc32': f'{zlib.crc32(data):08X}'}
            common.write_json(stored, record)
            return record, True
        except (requests.RequestException, OSError, ValueError):
            if attempt + 1 == retries:
                raise
            time.sleep(min(2**attempt, 8))


def check_space(root, raw_budget, reserve):
    free = shutil.disk_usage(root).free
    required = 2 * raw_budget + reserve
    if free < required:
        raise RuntimeError(f'Disk budget insufficient: free={free}, required={required}')
    return free


def archive_manifest(plan, packet):
    return {'schema': 1, 'plan_id': plan['id'], 'source_url': plan['source_url'],
            'package': packet['name'], 'tiles': packet['tiles']}


def check_archive(root, plan, packet, seven_zip):
    archive = root / 'archives' / packet['name']
    subprocess.run([seven_zip, 't', '-bd', str(archive)], check=True, stdout=subprocess.DEVNULL)
    embedded = subprocess.check_output([seven_zip, 'x', '-so', str(archive), packet['manifest_path']])
    if json.loads(embedded) != archive_manifest(plan, packet):
        raise RuntimeError('Archive manifest does not match the planned package')
    listing = subprocess.check_output([seven_zip, 'l', '-slt', str(archive)], text=True)
    blocks = listing.split('----------\n', 1)[-1].split('\n\n')
    files = {}
    for block in blocks:
        values = dict(line.split(' = ', 1) for line in block.splitlines() if ' = ' in line)
        if 'Path' in values and values.get('Folder') != '+':
            files[values['Path']] = values
    expected = {r['path'] for r in packet['tiles']} | {packet['manifest_path']}
    if set(files) != expected:
        raise RuntimeError('Archive member list mismatch')
    for record in packet['tiles']:
        member = files[record['path']]
        if int(member['Size']) != record['size'] or member.get('CRC', '').upper() != record['crc32']:
            raise RuntimeError('Archive tile size/CRC does not match original download')
    if packet.get('sha256') and common.digest_file(archive) != packet['sha256']:
        raise RuntimeError('Archive SHA changed since local verification')
    return archive


def make_archive(root, plan, packet, seven_zip, limit):
    manifest = root / packet['manifest_path']
    common.write_json(manifest, archive_manifest(plan, packet))
    archive = root / 'archives' / packet['name']
    archive.parent.mkdir(exist_ok=True)
    if not archive.exists():
        temp = archive.with_suffix('.partial.7z')
        temp.unlink(missing_ok=True)  # Only this job's incomplete archive.
        listing = manifest.with_suffix('.files.txt')
        listing.write_text('\n'.join([packet['manifest_path'], *[r['path'] for r in packet['tiles']]]) + '\n', encoding='utf-8')
        subprocess.run([seven_zip, 'a', '-t7z', '-m0=LZMA2', '-mx=3', '-md=16m', '-mmt=2',
                        '-bd', '-scsUTF-8', str(temp), '@' + str(listing)], cwd=root,
                       check=True, stdout=subprocess.DEVNULL)
        if temp.stat().st_size > limit:
            raise RuntimeError('Archive exceeds package size limit; retain files and reduce package size')
        os.replace(temp, archive)
    check_archive(root, plan, packet, seven_zip)
    if archive.stat().st_size > limit:
        raise RuntimeError('Existing archive exceeds configured package size limit')
    packet.update(sha256=common.digest_file(archive), archive_bytes=archive.stat().st_size,
                  source_bytes=sum(r['size'] for r in packet['tiles']), phase='local_verified')
    return archive


def cloud_match(packet, receipt, parent):
    if (not receipt.get('verified') or receipt.get('verification') != 'fresh_listing_fid_name_size'
            or not receipt.get('fid') or not receipt.get('cloud_listing_fid')
            or receipt.get('local_sha256') != packet.get('sha256')
            or receipt.get('size') != packet.get('archive_bytes') or receipt.get('parent_fid') != parent):
        raise RuntimeError('Cloud verification evidence is incomplete/mismatched')


def validate_ledger(plan, ledger):
    if ledger.get('schema') != 1 or ledger.get('plan_id') != plan['id']:
        raise RuntimeError('Ledger/plan mismatch')
    planned = {tuple(t) for t in plan['tiles']}
    found = set()
    phases = {'archiving', 'local_verified', 'uploading', 'cloud_verified', 'releasing', 'released'}
    for number, packet in enumerate(ledger['packets'], 1):
        if (packet.get('name') != f'GeoRelay_{plan["id"]}_p{number:05d}.7z'
                or packet.get('manifest_path') != f'manifests/p{number:05d}.json'
                or packet.get('phase') not in phases or not packet.get('tiles')):
            raise RuntimeError('Invalid package identity/phase')
        for record in packet['tiles']:
            tile = tuple(record['tile'])
            x, y, z = tile
            if (tile not in planned or tile in found
                    or record['path'] not in (f'data/{z}/{x}/{y}.jpg', f'data/{z}/{x}/{y}.png')
                    or not 0 < record['size'] <= MAX_TILE_BYTES
                    or not re.fullmatch('[0-9a-f]{64}', record['sha256'])
                    or not re.fullmatch('[0-9A-F]{8}', record['crc32'])):
                raise RuntimeError('Invalid/duplicate/foreign tile record')
            found.add(tile)


def release_packet(root, packet, receipt, quark):
    cloud_match(packet, receipt, quark.options['parent_fid'])
    # Require a NEW lookup right before any deletion, never trust the saved receipt alone.
    found = quark.verify(receipt['parent_fid'], receipt['fid'], packet['name'], packet['archive_bytes'])
    if not found or found != receipt['cloud_listing_fid']:
        raise RuntimeError('Fresh remote lookup failed; keep all local files')
    archive = root / 'archives' / packet['name']
    if archive.exists() and common.digest_file(archive) != packet['sha256']:
        raise RuntimeError('Archive changed; refuse cleanup')
    # Preflight all paths BEFORE removing anything, including a resumed partial cleanup.
    for record in packet['tiles']:
        path = root / record['path']
        if path.exists() and not valid_record(root, record):
            raise RuntimeError('Tile changed; refuse cleanup')
    for record in packet['tiles']:
        (root / record['path']).unlink(missing_ok=True)
    archive.unlink(missing_ok=True)
    packet['phase'] = 'released'
    packet['cloud'] = receipt


def execute(plan, args, quark=None):
    validate_plan(plan)
    root = task_root(plan)
    limit = int(args.package_mib * MIB)
    reserve = int(args.reserve_mib * MIB)
    # Leave metadata + incompressible LZMA2/container overhead within the hard package cap.
    raw_budget = limit - max(MIB, int(limit * .01))
    with worker_lock(root):
        ledger_path = root / 'ledger.json'
        ledger = read_json(ledger_path) if ledger_path.exists() else {
            'schema': 1, 'plan_id': plan['id'], 'packets': [], 'status': 'running'}
        validate_ledger(plan, ledger)
        try:
            # Finish old packages first, with hash/CRC and fresh cloud verification.
            for packet in ledger['packets']:
                if packet['phase'] == 'released':
                    cloud_match(packet, packet['cloud'], packet['cloud']['parent_fid'])
                    if quark and not quark.verify(packet['cloud']['parent_fid'], packet['cloud']['fid'],
                                                   packet['name'], packet['archive_bytes']):
                        raise RuntimeError('Previously released package is missing remotely')
                    continue
                finish_packet(root, plan, packet, args, quark, ledger, ledger_path, limit)
            covered = {tuple(r['tile']) for p in ledger['packets'] for r in p['tiles']}
            remaining = [t for t in plan['tiles'] if tuple(t) not in covered]
            new_packages = 0
            while remaining:
                check_space(root, raw_budget, reserve)
                records, total, downloaded, reused = [], 0, 0, 0
                started = time.monotonic()
                # A window bounds concurrent response memory and prefetch disk use.
                with ThreadPoolExecutor(max_workers=args.threads) as pool:
                    position = 0
                    stop = False
                    while position < len(remaining) and not stop:
                        window = remaining[position:position + args.threads]
                        check_space(root, raw_budget - total + MAX_TILE_BYTES * len(window), reserve)
                        results = list(pool.map(lambda t: download_tile(root, t), window))
                        downloaded += sum(r['size'] for r, fresh in results if fresh)
                        for record, fresh in results:
                            if total + record['size'] > raw_budget:
                                stop = True
                                break  # Prefetched records remain for the next package.
                            records.append(record)
                            total += record['size']
                            reused += int(not fresh)
                        position += len(window)
                if not records:
                    raise RuntimeError('Package budget cannot hold one tile')
                number = len(ledger['packets']) + 1
                packet = {'name': f'GeoRelay_{plan["id"]}_p{number:05d}.7z', 'tiles': records,
                          'manifest_path': f'manifests/p{number:05d}.json', 'phase': 'archiving',
                          'download_seconds': round(time.monotonic() - started, 3),
                          'network_bytes': downloaded, 'reused_tiles': reused}
                ledger['packets'].append(packet)
                common.write_json(ledger_path, ledger)
                finish_packet(root, plan, packet, args, quark, ledger, ledger_path, limit)
                new_packages += 1
                selected = {tuple(r['tile']) for r in records}
                remaining = [t for t in remaining if tuple(t) not in selected]
                if args.max_packages and new_packages >= args.max_packages:
                    break
            covered = [tuple(r['tile']) for p in ledger['packets'] for r in p['tiles']]
            if len(covered) != len(set(covered)) or not set(covered).issubset(map(tuple, plan['tiles'])):
                raise RuntimeError('Package coverage duplicate/foreign tile')
            complete = len(covered) == len(plan['tiles'])
            cloud_complete = complete and all(p['phase'] in ('cloud_verified', 'released') for p in ledger['packets'])
            ledger.update(status='cloud_complete' if cloud_complete else 'local_complete' if complete else 'paused',
                          planned_tiles=len(plan['tiles']), covered_tiles=len(covered), error=None)
            common.write_json(ledger_path, ledger)
            print(json.dumps({k: v for k, v in ledger.items() if k != 'packets'}, ensure_ascii=False), flush=True)
            return ledger
        except Exception as exc:
            ledger.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            common.write_json(ledger_path, ledger)
            raise


def finish_packet(root, plan, packet, args, quark, ledger, ledger_path, limit):
    if packet['phase'] == 'releasing':
        if quark is None:
            raise RuntimeError('Interrupted cleanup requires fresh cloud verification; rerun with --upload')
        release_packet(root, packet, packet['cloud'], quark)
        common.write_json(ledger_path, ledger)
        return
    previous_phase = packet['phase']
    if not (root / 'archives' / packet['name']).exists():
        needed = sum(r['size'] for r in packet['tiles']) * 1.02 + args.reserve_mib * MIB
        if shutil.disk_usage(root).free < needed:
            raise RuntimeError('Not enough disk for archive plus safety reserve; keep downloaded tiles')
    started = time.monotonic()
    archive = make_archive(root, plan, packet, args.seven_zip, limit)
    packet['archive_check_seconds'] = round(time.monotonic() - started, 3)
    if previous_phase == 'cloud_verified' and quark is None:
        cloud_match(packet, packet['cloud'], packet['cloud']['parent_fid'])
        packet['phase'] = 'cloud_verified'
    common.write_json(ledger_path, ledger)
    if quark:
        receipt_path = root / 'receipts' / (packet['name'] + '.json')
        # An interrupted upload may exist remotely without a receipt: stop to reconcile it.
        if not receipt_path.exists():
            matches = [item for item in quark.browse(quark.options['parent_fid'])
                       if item.get('filename') == packet['name']]
            if matches:
                raise RuntimeError('Remote name already exists without receipt; reconcile/resume official upload')
        packet['phase'] = 'uploading'
        common.write_json(ledger_path, ledger)
        started = time.monotonic()
        receipt = quark.upload(archive, receipt_path)
        cloud_match(packet, receipt, quark.options['parent_fid'])
        packet.update(phase='cloud_verified', cloud=receipt, upload_seconds=round(time.monotonic() - started, 3))
        common.write_json(ledger_path, ledger)
        if args.release_verified:
            packet['phase'] = 'releasing'
            common.write_json(ledger_path, ledger)
            release_packet(root, packet, receipt, quark)
            common.write_json(ledger_path, ledger)
    print(f'{packet["name"]}: {packet["phase"]}, {len(packet["tiles"])} tiles, {packet["archive_bytes"]} bytes', flush=True)


def make_plan(args):
    # Heavy GIS dependencies are deliberately restricted to the planning command.
    import geopandas as gpd
    import mercantile
    from shapely.geometry import box
    from xinjiang_batch import SOURCE_FILE_CODES, EXCLUDED_CODES
    allowed = {code: name for name, codes in SOURCE_FILE_CODES.items() for code in codes if code not in EXCLUDED_CODES}
    if args.code not in allowed:
        raise ValueError('Code is outside the authorized original Shapefile selection')
    source = ROOT / 'boundaries' / 'xinjiang_available.gpkg'
    frame = gpd.read_file(source).to_crs(4326)
    rows = frame[frame.dt_adcode.astype(str) == args.code]
    if len(rows) != 1:
        raise ValueError('Boundary code is not unique')
    geom = rows.iloc[0].geometry
    point = geom.representative_point()
    center = mercantile.tile(point.x, point.y, 16)
    tiles = []
    radius = 0
    while len(tiles) < args.tile_limit and radius < 2048:
        for x in range(center.x - radius, center.x + radius + 1):
            for y in range(center.y - radius, center.y + radius + 1):
                if max(abs(x - center.x), abs(y - center.y)) != radius:
                    continue
                t = mercantile.Tile(x, y, 16)
                if 0 <= x < 65536 and 0 <= y < 65536 and geom.intersects(box(*mercantile.bounds(t))):
                    tiles.append([x, y, 16])
                    if len(tiles) == args.tile_limit:
                        break
            if len(tiles) == args.tile_limit:
                break
        radius += 1
    if len(tiles) != args.tile_limit:
        raise ValueError('Requested sample cannot be filled within the bounded planning window')
    body = {'schema': 1, 'source_url': SOURCE_URL, 'zoom': 16, 'code': args.code,
            'source_file': allowed[args.code], 'boundary_catalog_sha256': common.digest_file(source),
            'boundary_sha256': common.fingerprint(geom.wkb_hex), 'tiles': tiles,
            'scope': 'bounded sample intersecting authorized county, not full county'}
    body['id'] = common.fingerprint(body)[:20]
    validate_plan(body)
    path = task_root(body) / 'plan.json'
    common.write_json(path, body)
    print(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    plan = sub.add_parser('plan')
    plan.add_argument('--code', default='652925')
    plan.add_argument('--tile-limit', type=int, default=128)
    run = sub.add_parser('run')
    run.add_argument('plan', type=Path)
    run.add_argument('--package-mib', type=int, default=1024)
    run.add_argument('--reserve-mib', type=int, default=1024)
    run.add_argument('--threads', type=int, default=8)
    run.add_argument('--max-packages', type=int, default=1, help='0 means process all planned packages')
    run.add_argument('--seven-zip', default=shutil.which('7z') or '7z')
    run.add_argument('--upload', action='store_true')
    run.add_argument('--release-verified', action='store_true')
    run.add_argument('--skill-dir', type=Path)
    run.add_argument('--parent-fid')
    run.add_argument('--session-input', type=Path)
    run.add_argument('--session-id')
    args = parser.parse_args()
    if os.name != 'posix':
        parser.error('This isolated cloud relay currently requires Linux/POSIX')
    branch = subprocess.run(['git', 'branch', '--show-current'], cwd=ROOT, capture_output=True, text=True)
    if branch.returncode == 0 and branch.stdout.strip() in ('master', 'main'):
        parser.error('Use codex-cloud-demo; production branch execution is refused')
    if args.action == 'plan':
        if not 1 <= args.tile_limit <= 100000:
            parser.error('--tile-limit must be 1–100000')
        make_plan(args)
        return
    if not 16 <= args.package_mib <= 2048 or args.reserve_mib < 64 or not 1 <= args.threads <= 32 or args.max_packages < 0:
        parser.error('Invalid package, reserve, thread, or max-package bounds')
    if args.release_verified and not args.upload:
        parser.error('--release-verified requires --upload')
    executable = shutil.which(args.seven_zip)
    if not executable:
        parser.error('7z executable is unavailable')
    args.seven_zip = str(Path(executable).resolve())
    quark = None
    if args.upload:
        if not all([args.skill_dir, args.parent_fid, args.session_input, args.session_id]):
            parser.error('Upload requires official skill-dir, real parent-fid, session-input and session-id')
        if not (args.skill_dir / 'SKILL.md').is_file() or not (args.skill_dir / 'scripts/quark-drive.cjs').is_file():
            parser.error('Official Quark Skill is unavailable; keep local packages')
        if not args.session_input.is_file():
            parser.error('Session input file is unavailable')
        from quark_backend import Quark
        quark = Quark({'jobs_dir': str(BASE), 'quark': {'skill_dir': str(args.skill_dir.resolve()),
                       'parent_fid': args.parent_fid}}, str(args.session_input.resolve()), args.session_id)
    execute(read_json(args.plan), args, quark)


if __name__ == '__main__':
    main()
