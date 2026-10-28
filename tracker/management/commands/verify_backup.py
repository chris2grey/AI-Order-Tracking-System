from django.core.management.base import BaseCommand
from tracker.backups import verify_backup


class Command(BaseCommand):
    help = '只读校验备份的 SHA-256 和文件清单'

    def add_arguments(self, parser):
        parser.add_argument('source')

    def handle(self, *args, **options):
        manifest = verify_backup(options['source'])
        self.stdout.write(self.style.SUCCESS(f'校验通过，共 {len(manifest["files"])} 个文件。'))
