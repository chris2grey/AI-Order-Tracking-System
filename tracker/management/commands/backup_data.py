from django.core.management.base import BaseCommand
from tracker.backups import create_backup


class Command(BaseCommand):
    help = '一致性导出数据库、私有凭证及 SHA-256 校验清单；不覆盖已有备份'

    def add_arguments(self, parser):
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        manifest = create_backup(options['output'])
        self.stdout.write(self.style.SUCCESS(f'备份与校验完成，共 {len(manifest["files"])} 个文件。'))
