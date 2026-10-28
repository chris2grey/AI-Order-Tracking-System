"""Verify the files of a fresh local transfer package (before development edits)."""
from pathlib import Path, PurePosixPath
import hashlib
import json

BASE = Path(__file__).resolve().parent

def main():
    manifest = json.loads((BASE / 'TRANSFER_MANIFEST.json').read_text(encoding='utf-8'))
    for name, record in manifest['files'].items():
        relative = PurePosixPath(name)
        target = BASE.joinpath(*relative.parts).resolve()
        if relative.is_absolute() or '..' in relative.parts or not target.is_relative_to(BASE.resolve()):
            raise RuntimeError('Unsafe manifest path.')
        if not target.is_file():
            raise RuntimeError(f'Missing file: {name}')
        with target.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if target.stat().st_size != record['size'] or digest != record['sha256']:
            raise RuntimeError(f'File changed or corrupted: {name}')
    print(f'PASS: {len(manifest["files"])} transfer files verified. No account secrets were printed.')

if __name__ == '__main__':
    main()
