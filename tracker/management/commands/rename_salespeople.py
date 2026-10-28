from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

class Command(BaseCommand):
    help = 'Rename legacy demo salespeople to chris/coco, preserving IDs, passwords and order ownership.'
    @transaction.atomic
    def handle(self, **options):
        User = get_user_model()
        users = {u.username: u for u in User.objects.select_for_update().filter(username__in=['alice', 'bob', 'chris', 'coco'])}
        for old, new in [('alice', 'chris'), ('bob', 'coco')]:
            if old in users and new in users and users[old].pk != users[new].pk:
                raise CommandError(f'{old} 与 {new} 同时存在，未修改任何账号；请先核对负责人。')
            user = users.get(new) or users.get(old)
            if user is None:
                continue
            if user.is_superuser:
                raise CommandError(f'{user.username} 是管理员，已停止改名。')
            user.username = new
            user.first_name = new
            user.last_name = ''
            user.save(update_fields=['username', 'first_name', 'last_name'])
            self.stdout.write(f'业务员已更新：{new}；密码与订单归属保持不变。')
