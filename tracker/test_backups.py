import hashlib
import json
from io import StringIO
from pathlib import Path
import tempfile
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management import call_command, CommandError
from django.test import TransactionTestCase, override_settings

from .backups import create_backup, verify_backup, restore_backup
from .models import Order, Line, Transaction, Archive


class BackupTests(TransactionTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.override = override_settings(MEDIA_ROOT=self.root / 'original-private')
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.admin = User.objects.create_user('backup-test', password='Fictional-backup-71', is_superuser=True)
        self.order = Order.objects.create(number='BACKUP-TEST', customer='虚构客户 · 恢复验证', owner=self.admin)
        Line.objects.create(order=self.order, label='001', product='Test', model='A', quantity=1, price='0.1234', cost='0', supplier='Test Factory')
        self.txn = Transaction.objects.create(order=self.order, amount='0.12', kind='receipt', currency='USD',
                                              party='Test', platform='Bank', purpose='test', reference='test', created_by=self.admin)
        self.txn.attachment.save('fictional.pdf', ContentFile(b'%PDF-fictional-backup'))
        Archive.objects.create(order=self.order, created_by=self.admin, digest='a' * 64, content=b'fictional-archive-bytes')
        self.backup = self.root / 'backup.zip'

    def test_backup_verify_restore_round_trip_and_binary_archive(self):
        manifest = create_backup(self.backup)
        self.assertEqual(verify_backup(self.backup), manifest)
        self.assertEqual(manifest['counts']['tracker.order'], 1)
        with ZipFile(self.backup) as archive:
            self.assertNotIn('.secret', archive.namelist())
            self.assertEqual(archive.read('private/' + self.txn.attachment.name), b'%PDF-fictional-backup')
        call_command('flush', interactive=False, verbosity=0)
        with override_settings(MEDIA_ROOT=self.root / 'restored-private'):
            restore_backup(self.backup)
            self.assertEqual(Order.objects.get().number, 'BACKUP-TEST')
            self.assertEqual(Order.objects.get().customer, '虚构客户 · 恢复验证')
            self.assertEqual(str(Line.objects.get().price), '0.1234')
            self.assertTrue(User.objects.get().check_password('Fictional-backup-71'))
            self.assertEqual(bytes(Archive.objects.get().content), b'fictional-archive-bytes')
            with Transaction.objects.get().attachment.open('rb') as stream:
                self.assertEqual(stream.read(), b'%PDF-fictional-backup')

    def test_existing_database_and_backup_never_overwritten(self):
        create_backup(self.backup)
        original = self.backup.read_bytes()
        with self.assertRaises(CommandError):
            create_backup(self.backup)
        self.assertEqual(original, self.backup.read_bytes())
        with self.assertRaises(CommandError):
            restore_backup(self.backup)
        self.assertEqual(Order.objects.count(), 1)

    def test_missing_attachment_aborts_publication(self):
        Path(self.txn.attachment.path).unlink()
        with self.assertRaises(CommandError):
            create_backup(self.backup)
        self.assertFalse(self.backup.exists())

    def test_corruption_and_extra_paths_rejected(self):
        create_backup(self.backup)
        original = self.backup.read_bytes()
        for name in ['private/../outside.txt', 'private/../../outside.txt', 'private\\outside.txt', '/outside.txt', 'unexpected.txt']:
            self.backup.write_bytes(original)
            with ZipFile(self.backup, 'a') as archive:
                archive.writestr(name, 'bad')
            with self.assertRaises(CommandError):
                verify_backup(self.backup)
        self.backup.write_bytes(original)
        with ZipFile(self.backup) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries['database.json'] += b'corrupt'
        with ZipFile(self.backup, 'w') as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        with self.assertRaises(CommandError):
            verify_backup(self.backup)

    def test_count_mismatch_rolls_back_database(self):
        create_backup(self.backup)
        with ZipFile(self.backup) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(entries['manifest.json'])
        manifest['counts']['tracker.order'] = 999
        entries['manifest.json'] = json.dumps(manifest).encode()
        with ZipFile(self.backup, 'w') as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        call_command('flush', interactive=False, verbosity=0)
        with override_settings(MEDIA_ROOT=self.root / 'restored-private'):
            with self.assertRaises(CommandError):
                restore_backup(self.backup)
            self.assertFalse(Order.objects.exists())
            self.assertFalse(User.objects.exists())
            self.assertFalse((self.root / 'restored-private').exists())

    def test_nonempty_private_directory_and_missing_confirm_rejected(self):
        create_backup(self.backup)
        with self.assertRaises(CommandError):
            call_command('restore_server_data', str(self.backup), stdout=StringIO())
        call_command('flush', interactive=False, verbosity=0)
        with self.assertRaises(CommandError):
            restore_backup(self.backup)
        self.assertTrue(Path(self.txn.attachment.path).exists())

    def test_publish_failure_rolls_back_database(self):
        create_backup(self.backup)
        call_command('flush', interactive=False, verbosity=0)
        with override_settings(MEDIA_ROOT=self.root / 'restore-target'), patch('tracker.backups.os.replace', side_effect=OSError('test failure')):
            with self.assertRaises(OSError):
                restore_backup(self.backup)
            self.assertFalse(Order.objects.exists())
