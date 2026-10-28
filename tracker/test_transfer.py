from pathlib import Path
import hashlib
import json
import os
import tempfile
from unittest.mock import patch
from zipfile import ZipFile
from django.test import SimpleTestCase
import restore_data

class TransferRestoreTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.base_patch = patch.object(restore_data, 'BASE', self.base)
        self.base_patch.start()
        self.addCleanup(self.base_patch.stop)
        self.environment = patch.dict(os.environ, {}, clear=False)
        self.environment.start()
        os.environ.pop('DATA_DIR', None)
        self.addCleanup(self.environment.stop)

    def write_snapshot(self, extra=None, corrupt=False):
        files={'data/db.sqlite3':b'fictional-test-db','data/.secret':b'fictional-test-key','data/private/example.pdf':b'%PDF-test'}
        if extra:
            files.update(extra)
        manifest={'files':{name:{'size':len(content),'sha256':hashlib.sha256(content).hexdigest()} for name,content in files.items()}}
        if corrupt:
            manifest['files']['data/db.sqlite3']['sha256']='0'*64
        with ZipFile(self.base/'private-data.zip','w') as archive:
            for name,content in files.items():
                archive.writestr(name,content)
            archive.writestr('DATA_MANIFEST.json',json.dumps(manifest))

    def test_restore_preserves_files_and_is_repeatable(self):
        self.write_snapshot()
        self.assertTrue(restore_data.restore_if_needed())
        self.assertEqual((self.base/'data/private/example.pdf').read_bytes(),b'%PDF-test')
        (self.base/'data/db.sqlite3').write_bytes(b'existing-data-must-stay')
        self.assertFalse(restore_data.restore_if_needed())
        self.assertEqual((self.base/'data/db.sqlite3').read_bytes(),b'existing-data-must-stay')

    def test_nonempty_folder_is_not_overwritten(self):
        self.write_snapshot()
        (self.base/'data').mkdir()
        (self.base/'data/.secret').write_text('existing-key')
        with self.assertRaises(RuntimeError):
            restore_data.restore_if_needed()
        self.assertEqual((self.base/'data/.secret').read_text(),'existing-key')

    def test_corruption_aborts_before_publishing(self):
        self.write_snapshot(corrupt=True)
        with self.assertRaises(RuntimeError):
            restore_data.restore_if_needed()
        self.assertFalse((self.base/'data').exists())
        self.assertFalse(list(self.base.glob('.restore-*')))

    def test_path_traversal_is_rejected(self):
        self.write_snapshot(extra={'data/../outside.txt':b'blocked'})
        with self.assertRaises(RuntimeError):
            restore_data.restore_if_needed()
        self.assertFalse((self.base/'outside.txt').exists())

    def test_unexpected_entries_are_rejected(self):
        self.write_snapshot()
        with ZipFile(self.base/'private-data.zip','a') as archive:
            archive.writestr('unexpected.txt',b'blocked')
        with self.assertRaises(RuntimeError):
            restore_data.restore_if_needed()
        self.assertFalse((self.base/'data').exists())

    def test_external_data_dir_requires_explicit_handling(self):
        self.write_snapshot()
        for key in ['DATA_DIR','POSTGRES_DB']:
            with self.subTest(key=key), patch.dict(os.environ,{key:'other-database'}):
                with self.assertRaises(RuntimeError):
                    restore_data.restore_if_needed()
