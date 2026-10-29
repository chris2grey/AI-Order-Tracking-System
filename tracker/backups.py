"""Portable backups containing a consistent database fixture and private attachments."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

from django.apps import apps
from django.conf import settings
from django.core.management import call_command, CommandError
from django.db import connection, transaction
from django.utils import timezone

from .models import Transaction, Line

EXCLUDED_MODELS = {'contenttypes.contenttype', 'auth.permission', 'sessions.session'}


def digest_file(stream):
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(1024 * 1024):
        size += len(chunk)
        digest.update(chunk)
    return {'size': size, 'sha256': digest.hexdigest()}


def safe_name(name):
    path = PurePosixPath(name)
    if '\\' in name or ':' in name or path.is_absolute() or '..' in path.parts or str(path) != name:
        raise CommandError('备份文件路径不安全。')
    if name not in ('database.json', 'manifest.json') and not (name.startswith('private/') and len(path.parts) > 1):
        raise CommandError('备份包含不支持的文件。')
    return name


def verify_backup(path):
    try:
        with ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or 'manifest.json' not in names or 'database.json' not in names:
                raise CommandError('备份缺少清单、数据库或包含重名文件。')
            for entry in archive.infolist():
                safe_name(entry.filename)
                if entry.is_dir() or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise CommandError('备份不能包含目录或符号链接。')
            if archive.getinfo('manifest.json').file_size > 16 * 1024 * 1024:
                raise CommandError('备份清单过大。')
            manifest = json.loads(archive.read('manifest.json'))
            if manifest.get('format') != 'order-tracker-backup-v1' or not isinstance(manifest.get('files'), dict):
                raise CommandError('备份格式不受支持。')
            expected = manifest['files']
            if set(names) != set(expected) | {'manifest.json'}:
                raise CommandError('备份文件与清单不一致。')
            for name, metadata in expected.items():
                with archive.open(name) as stream:
                    if digest_file(stream) != metadata:
                        raise CommandError('备份文件校验失败：' + name)
            return manifest
    except CommandError:
        raise
    except Exception as exc:
        raise CommandError('无法读取或校验备份文件。') from exc


def create_backup(target):
    target = Path(target).resolve()
    if target.exists():
        raise CommandError('目标文件已经存在，不会覆盖。')
    if not target.parent.is_dir():
        raise CommandError('备份目标目录不存在。')
    with tempfile.TemporaryDirectory(prefix='.tracker-backup-', dir=target.parent) as temp:
        temp = Path(temp)
        os.chmod(temp, 0o700)
        fixture = temp / 'database.json'
        bundle = temp / 'backup.zip'
        files = {}
        with transaction.atomic():
            if connection.vendor == 'postgresql':
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            # Use base managers so future default filters cannot silently omit records.
            with fixture.open('w', encoding='utf-8', newline='\n') as stream:
                call_command('dumpdata', exclude=sorted(EXCLUDED_MODELS), natural_foreign=True,
                             natural_primary=True, use_base_manager=True, stdout=stream, verbosity=0)
            counts = {model._meta.label_lower: model._base_manager.count()
                      for model in apps.get_models() if model._meta.label_lower not in EXCLUDED_MODELS}
            attachment_names = set(Transaction.objects.exclude(attachment='').values_list('attachment', flat=True))
            with ZipFile(bundle, 'w', ZIP_DEFLATED) as archive:
                archive.write(fixture, 'database.json')
                with fixture.open('rb') as stream:
                    files['database.json'] = digest_file(stream)
                storage = Transaction._meta.get_field('attachment').storage
                for name in sorted(attachment_names):
                    zip_name = safe_name('private/' + name)
                    try:
                        with storage.open(name, 'rb') as source, archive.open(zip_name, 'w', force_zip64=True) as destination:
                            digest = hashlib.sha256()
                            size = 0
                            while chunk := source.read(1024 * 1024):
                                destination.write(chunk)
                                digest.update(chunk)
                                size += len(chunk)
                            files[zip_name] = {'size': size, 'sha256': digest.hexdigest()}
                    except OSError as exc:
                        raise CommandError('存在缺失或不可读取的凭证，已停止备份。') from exc
                manifest = {'format': 'order-tracker-backup-v1', 'created_at': timezone.now().isoformat(),
                            'database_vendor': connection.vendor, 'counts': counts, 'files': files}
                archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        verify_backup(bundle)
        os.chmod(bundle, 0o600)
        # Windows rename refuses replacement; POSIX links provide the same guarantee.
        try:
            if os.name == 'nt':
                os.rename(bundle, target)
            else:
                os.link(bundle, target)
        except FileExistsError as exc:
            raise CommandError('目标文件已经存在，不会覆盖。') from exc
    return manifest


def restore_backup(source):
    source = Path(source).resolve()
    manifest = verify_backup(source)
    models = [m for m in apps.get_models() if m._meta.label_lower not in EXCLUDED_MODELS]
    media = Path(settings.MEDIA_ROOT).resolve()
    if any(m._base_manager.exists() for m in models):
        raise CommandError('目标数据库不是空库，已停止以保护现有数据。')
    if media.exists() and (not media.is_dir() or any(media.iterdir())):
        raise CommandError('凭证目录非空，已停止以保护现有文件。')
    media.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.tracker-restore-', dir=media.parent) as temp:
        temp = Path(temp)
        staged_media = temp / 'private'
        staged_media.mkdir(mode=0o700)
        with ZipFile(source) as archive:
            for name in manifest['files']:
                dest = temp / safe_name(name)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as src, dest.open('xb') as dst:
                    while chunk := src.read(1024 * 1024):
                        dst.write(chunk)
        published = False
        try:
            with transaction.atomic():
                if any(m._base_manager.exists() for m in models):
                    raise CommandError('目标数据库已有数据，停止恢复。')
                call_command('loaddata', str(temp / 'database.json'), verbosity=0)
                # Older fixtures predate system serials; preserve original labels and IDs.
                for order_id in Line.objects.filter(sequence__isnull=True).values_list('order_id', flat=True).distinct():
                    used = set(Line.objects.filter(order_id=order_id, sequence__isnull=False).values_list('sequence', flat=True))
                    number = 1
                    for line in Line.objects.filter(order_id=order_id, sequence__isnull=True).order_by('pk'):
                        while number in used:
                            number += 1
                        Line.objects.filter(pk=line.pk).update(sequence=number)
                        used.add(number)
                actual_counts = {m._meta.label_lower: m._base_manager.count() for m in models}
                expected_counts = dict(manifest.get('counts') or {})
                # Only these newly introduced empty tables may be absent in an old backup.
                for label in ['tracker.transactioncorrection', 'tracker.shipmentfreightrecord', 'tracker.allocationcoverage']:
                    if label not in expected_counts and actual_counts.get(label) == 0:
                        expected_counts[label] = 0
                if actual_counts != expected_counts:
                    raise CommandError('恢复后记录数量与备份不一致，已回滚。')
                referenced = {'private/' + name for name in Transaction.objects.exclude(attachment='').values_list('attachment', flat=True)}
                if referenced != {name for name in manifest['files'] if name.startswith('private/')}:
                    raise CommandError('凭证与数据库引用不匹配，已回滚。')
                if media.exists():
                    media.rmdir()  # Only an empty directory may be removed.
                os.replace(staged_media, media)
                published = True
        except Exception:
            if published:
                os.replace(media, staged_media)
            raise
    return manifest
