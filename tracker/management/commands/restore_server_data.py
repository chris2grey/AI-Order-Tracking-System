from django.core.management.base import BaseCommand, CommandError
from tracker.backups import restore_backup


class Command(BaseCommand):
    help = '将已校验的备份恢复至空数据库；必须先停止业务服务'

    def add_arguments(self, parser):
        parser.add_argument('source')
        parser.add_argument('--confirm-empty-target', action='store_true')

    def handle(self, *args, **options):
        if not options['confirm_empty_target']:
            raise CommandError('请先停止业务服务，并提供 --confirm-empty-target；恢复只接受空目标。')
        restore_backup(options['source'])
        self.stdout.write(self.style.SUCCESS('数据库及私有凭证恢复完成；原备份保持不变。'))
