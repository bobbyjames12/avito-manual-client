import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile
from test_core import listing
from avito_client.store import Store
from avito_client.model import build_xml
from avito_client.updater import extract_release, latest, install_script


class RemovalTests(unittest.TestCase):
    def test_failure_retry_last_ad_and_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            ad = listing()
            store.enqueue(ad)
            store.complete_job(store.begin_job(store.snapshot()), 'url')
            store.remove_from_feed(ad['id'])
            self.assertEqual(store.snapshot(), [])
            self.assertIn(b'<Ads', build_xml([], lambda name: name))
            job = store.begin_job([])
            store.fail_job(job, 'network')
            store.close()
            store = Store(temp)
            self.assertEqual(store.pending_removals(), [ad['id']])
            store.complete_job(store.begin_job([]), 'url')
            self.assertEqual(store.get(ad['id'])['removal'], 'removed')
            self.assertIsNone(store.get(ad['id'])['sent'])
            store.restore(ad['id'])
            self.assertEqual(store.snapshot(), [])
            store.enqueue(ad)
            self.assertEqual(store.snapshot(), [ad])
            store.close()

    def test_remove_one_retains_other_and_cancel(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            one, two = listing(), listing()
            store.enqueue(one); store.enqueue(two)
            store.complete_job(store.begin_job(store.snapshot()), 'url')
            store.remove_from_feed(one['id'])
            self.assertEqual(store.snapshot(), [two])
            store.restore(one['id'])
            self.assertEqual(len(store.snapshot()), 2)
            store.remove_from_feed(one['id'])
            store.complete_job(store.begin_job(store.snapshot()), 'url')
            self.assertEqual(store.snapshot(), [two])
            store.close()


class UpdateTests(unittest.TestCase):
    def test_archive_rejects_traversal_and_foreign_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            for name in ['AvitoManualClient/../../escape.exe', 'other/file', 'AvitoManualClient/x:ads', 'AvitoManualClient\\..\\x']:
                archive = Path(temp) / 'bad.zip'
                with zipfile.ZipFile(archive, 'w') as z:
                    z.writestr(name, 'bad')
                with self.assertRaises(ValueError):
                    extract_release(archive, Path(temp) / 'out')

    def test_valid_archive_requires_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / 'ok.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('AvitoManualClient/AvitoManualClient.exe', 'test')
                z.writestr('AvitoManualClient/_internal/runtime.dll', 'test')
            folder = extract_release(archive, Path(temp) / 'out')
            self.assertTrue((folder / 'AvitoManualClient.exe').exists())

    def test_installer_quotes_paths_and_rejects_wrong_destination(self):
        parent = Path(tempfile.gettempdir()) / "test ' unicode глеб"
        script = install_script(parent / '.avito-update-abc' / 'AvitoManualClient', parent / 'client', 123)
        self.assertIn("test '' unicode", script)
        self.assertIn('Wait-Process -Id 123', script)
        self.assertNotIn('Remove-Item', script)
        with self.assertRaises(ValueError):
            install_script(parent / 'wrong' / 'AvitoManualClient', parent / 'client', 123)

    def test_download_verifies_bytes_and_preserves_profile(self):
        import hashlib
        import io
        import sys
        from avito_client.updater import download
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "client"
            target.mkdir()
            (target / "settings.avitoconfig").write_text("private-fixture")
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as z:
                z.writestr('AvitoManualClient/AvitoManualClient.exe', 'test')
                z.writestr('AvitoManualClient/_internal/runtime.dll', 'test')
            raw = buffer.getvalue()
            info = {'url':'https://github.com/test', 'sha256':hashlib.sha256(raw).hexdigest(), 'size':len(raw)}
            def response(*args, **kwargs):
                stream = io.BytesIO(raw)
                stream.url = info['url']
                return stream
            def smoke(args, **kwargs):
                Path(args[2]).write_text('PASS: fixture')
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(target/'AvitoManualClient.exe')), patch('avito_client.updater.urlopen', side_effect=response), patch('avito_client.updater.subprocess.run', side_effect=smoke):
                folder = download(info)
                self.assertEqual((folder/'settings.avitoconfig').read_text(), 'private-fixture')
                info['sha256'] = '0'*64
                with self.assertRaises(ValueError):
                    download(info)
                self.assertEqual((target/'settings.avitoconfig').read_text(), 'private-fixture')

    def test_metadata_requires_digest_and_trusted_url(self):
        import io
        release = {'tag_name':'v9.0.0','assets':[{'name':'AvitoManualClient-Windows.zip','digest':None}]}
        with patch('avito_client.updater.urlopen', return_value=io.BytesIO(json.dumps(release).encode())):
            with self.assertRaises(ValueError): latest()


if __name__ == '__main__':
    unittest.main()
