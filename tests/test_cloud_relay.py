import io
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

from PIL import Image
import requests
import cloud_relay as relay
import artifact_utils as common


class FakeQuark:
    options = {'parent_fid': 'demo-parent'}
    exists = True
    uploads = 0

    def browse(self, parent):
        return []

    def upload(self, path, receipt_path):
        self.uploads += 1
        receipt = {'verified': True, 'verification': 'fresh_listing_fid_name_size',
                   'fid': 'upload-id', 'cloud_listing_fid': 'listing-id',
                   'parent_fid': self.options['parent_fid'],
                   'size': path.stat().st_size, 'local_sha256': common.digest_file(path)}
        common.write_json(receipt_path, receipt)
        return receipt

    def verify(self, *args):
        return 'listing-id' if self.exists else None


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / 'relay'
        self.patch = patch.object(relay, 'BASE', self.base)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.plan = {'schema': 1, 'source_url': relay.SOURCE_URL, 'zoom': 16,
                     'tiles': [[100 + i, 200, 16] for i in range(12)]}
        self.plan['id'] = common.fingerprint(self.plan)[:20]
        self.root = relay.task_root(self.plan)
        self.seven = shutil.which('7z') or str(relay.ROOT / '.venv/sat/bin/7z')
        self.args = SimpleNamespace(package_mib=2, reserve_mib=64, threads=2, max_packages=0,
                                    seven_zip=self.seven, release_verified=False)

    def seed(self, tile, random=False):
        import random as random_module
        data = random_module.Random(tile[0]).randbytes(256 * 256 * 3) if random else b'\x55' * (256 * 256 * 3)
        image = Image.frombytes('RGB', (256, 256), data)
        path = self.root / 'data' / str(tile[2]) / str(tile[0]) / f'{tile[1]}.png'
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        content = path.read_bytes()
        record = {'tile': tile, 'path': path.relative_to(self.root).as_posix(), 'size': len(content),
                  'sha256': common.digest_file(path), 'crc32': f'{zlib.crc32(content):08X}'}
        common.write_json(relay.record_path(self.root, tile), record)
        return record

    def seeded_execute(self, quark=None):
        for tile in self.plan['tiles']:
            self.seed(tile, random=True)
        return relay.execute(self.plan, self.args, quark)

    def test_plan_rejects_duplicate_tamper_and_wrong_source(self):
        for change in [{'tiles': [self.plan['tiles'][0]] * 2}, {'source_url': 'https://other/{z}/{x}/{y}'}]:
            plan = dict(self.plan, **change)
            plan.pop('id')
            plan['id'] = common.fingerprint(plan)[:20]
            with self.assertRaises(ValueError):
                relay.validate_plan(plan)
        self.plan['tiles'][0][0] += 1
        with self.assertRaises(ValueError):
            relay.validate_plan(self.plan)

    def test_corrupt_cache_refetched_and_wire_bytes_preserved(self):
        record = self.seed(self.plan['tiles'][0])
        (self.root / record['path']).write_bytes(b'broken')
        stream = io.BytesIO()
        Image.new('RGB', (256, 256), (10, 20, 30)).save(stream, 'JPEG')
        content = stream.getvalue()
        response = SimpleNamespace(status_code=200, headers={}, raise_for_status=lambda: None,
                                   iter_content=lambda size: [content])
        from contextlib import nullcontext
        session = SimpleNamespace(get=lambda *a, **kw: nullcontext(response))
        with patch.object(relay.LOCAL, 'session', session, create=True):
            fresh, fetched = relay.download_tile(self.root, self.plan['tiles'][0])
        self.assertTrue(fetched)
        self.assertEqual((self.root / fresh['path']).read_bytes(), content)
        self.assertTrue(fresh['path'].endswith('.jpg'))

    def test_missing_download_records_failure_and_creates_no_archive(self):
        with patch.object(relay, 'download_tile', side_effect=requests.ConnectionError('offline')):
            with self.assertRaises(requests.ConnectionError):
                relay.execute(self.plan, self.args)
        self.assertEqual(relay.read_json(self.root / 'ledger.json')['status'], 'failed')
        self.assertFalse((self.root / 'archives').exists())

    def test_low_disk_stops_before_download(self):
        with patch.object(relay.shutil, 'disk_usage', return_value=SimpleNamespace(free=1)), \
                patch.object(relay, 'download_tile') as fetch:
            with self.assertRaises(RuntimeError):
                relay.execute(self.plan, self.args)
            fetch.assert_not_called()

    def test_real_7z_packages_resume_and_detect_damage(self):
        ledger = self.seeded_execute()
        self.assertEqual(ledger['status'], 'local_complete')
        self.assertGreater(len(ledger['packets']), 1)
        self.assertEqual(ledger['covered_tiles'], 12)
        self.assertTrue(all(p['archive_bytes'] <= 2 * relay.MIB for p in ledger['packets']))
        with patch.object(relay, 'download_tile', side_effect=AssertionError('should not redownload')):
            resumed = relay.execute(self.plan, self.args)
        self.assertEqual(len(resumed['packets']), len(ledger['packets']))
        packet = ledger['packets'][0]
        archive = self.root / 'archives' / packet['name']
        archive.write_bytes(b'not 7z')
        with self.assertRaises(Exception):
            relay.execute(self.plan, self.args)
        self.assertTrue(archive.exists())

    def test_upload_failure_keeps_data(self):
        quark = FakeQuark()
        with patch.object(quark, 'upload', side_effect=RuntimeError('login expired')):
            with self.assertRaises(RuntimeError):
                self.seeded_execute(quark)
        ledger = relay.read_json(self.root / 'ledger.json')
        self.assertEqual(ledger['status'], 'failed')
        self.assertTrue((self.root / 'archives' / ledger['packets'][0]['name']).exists())
        self.assertTrue(list((self.root / 'data').rglob('*.png')))

    def test_cloud_mismatch_refuses_cleanup_and_success_releases(self):
        self.args.release_verified = True
        quark = FakeQuark()
        quark.exists = False
        with self.assertRaises(RuntimeError):
            self.seeded_execute(quark)
        ledger = relay.read_json(self.root / 'ledger.json')
        packet = ledger['packets'][0]
        self.assertEqual(packet['phase'], 'releasing')
        self.assertTrue((self.root / 'archives' / packet['name']).exists())
        # Simulate a crash after a single deletion; resume must re-check cloud, not rebuild.
        (self.root / packet['tiles'][0]['path']).unlink()
        quark.exists = True
        result = relay.execute(self.plan, self.args, quark)
        self.assertEqual(result['status'], 'cloud_complete')
        self.assertTrue(all(p['phase'] == 'released' for p in result['packets']))
        self.assertFalse(list((self.root / 'data').rglob('*.png')))
        self.assertFalse(list((self.root / 'archives').glob('*.7z')))
        with patch.object(quark, 'upload', side_effect=AssertionError('already uploaded')):
            relay.execute(self.plan, self.args, quark)

    def test_path_and_receipt_tampering_refuses_cleanup(self):
        ledger = self.seeded_execute(FakeQuark())
        packet = ledger['packets'][0]
        packet['cloud']['size'] += 1
        with self.assertRaises(RuntimeError):
            relay.release_packet(self.root, packet, packet['cloud'], FakeQuark())
        packet['tiles'][0]['path'] = '../production.png'
        with self.assertRaises(RuntimeError):
            relay.validate_ledger(self.plan, ledger)

    def test_os_lock_prevents_second_worker(self):
        with relay.worker_lock(self.root):
            with self.assertRaises(RuntimeError):
                with relay.worker_lock(self.root):
                    pass


if __name__ == '__main__':
    unittest.main()
