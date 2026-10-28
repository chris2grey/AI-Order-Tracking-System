"""Restore the optional local transfer snapshot, never overwriting existing data."""
from pathlib import Path, PurePosixPath
from zipfile import ZipFile
import argparse
import hashlib
import json
import os
import shutil
import tempfile

BASE = Path(__file__).resolve().parent

def restore_if_needed():
    snapshot = BASE / 'private-data.zip'
    if not snapshot.exists():
        print('No transfer snapshot; existing startup behavior retained.')
        return False
    if os.environ.get('DATA_DIR'):
        raise RuntimeError('DATA_DIR is set. Restore explicitly into a new project folder without DATA_DIR.')
    if os.environ.get('POSTGRES_DB'):
        raise RuntimeError('POSTGRES_DB is set. The transfer snapshot is SQLite; choose the intended database explicitly.')
    destination = (BASE / 'data').resolve()
    if destination.parent != BASE.resolve():
        raise RuntimeError('data/ resolves outside this project. Nothing was overwritten.')
    if (destination / 'db.sqlite3').exists():
        print('Existing database retained; transfer snapshot was not restored again.')
        return False
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError('data/ is not empty. Nothing was overwritten. Use a fresh extracted project folder.')
    with ZipFile(snapshot) as archive:
        manifest = json.loads(archive.read('DATA_MANIFEST.json'))
        expected = manifest['files']
        if set(archive.namelist()) != set(expected) | {'DATA_MANIFEST.json'}:
            raise RuntimeError('Data snapshot file list does not match its manifest.')
        if 'data/db.sqlite3' not in expected or 'data/.secret' not in expected:
            raise RuntimeError('Database or local signing key missing from snapshot.')
        if len(set(archive.namelist())) != len(archive.namelist()):
            raise RuntimeError('Duplicate archive entries are not allowed.')
        stage = Path(tempfile.mkdtemp(prefix='.restore-', dir=BASE)).resolve()
        if stage.parent != BASE.resolve():
            raise RuntimeError('Unexpected staging location.')
        try:
            for name, record in expected.items():
                relative = PurePosixPath(name)
                if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0] != 'data' or any(':' in part for part in relative.parts):
                    raise RuntimeError('Unsafe path in snapshot.')
                target = stage.joinpath(*relative.parts).resolve()
                if not target.is_relative_to(stage):
                    raise RuntimeError('Unsafe destination in snapshot.')
                content = archive.read(name)
                if len(content) != record['size'] or hashlib.sha256(content).hexdigest() != record['sha256']:
                    raise RuntimeError(f'Checksum verification failed: {name}')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            # Validated absolute paths remain under this extracted project directory.
            if destination.exists():
                destination.rmdir()  # Empty only; never removes populated data.
            (stage / 'data').rename(destination)
        finally:
            if stage.parent == BASE.resolve() and stage.name.startswith('.restore-'):
                shutil.rmtree(stage)
    print('Transfer snapshot verified and restored. Accounts and orders are preserved.')
    return True

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--if-needed', action='store_true', help='Restore only when no database exists.')
    parser.parse_args()
    restore_if_needed()
