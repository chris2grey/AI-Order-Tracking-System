import secrets
from datetime import timedelta
from decimal import Decimal
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from tracker.models import Order, Line, Transaction
from tracker.services import allocate, confirm_shipment, audit
import uuid

class Command(BaseCommand):
    help = 'Create fictional demo data and random local account passwords, once only.'

    @transaction.atomic
    def handle(self, **options):
        if User.objects.exists() or Order.objects.exists():
            self.stdout.write('已有账号或数据，未写入示例，未修改任何密码。')
            return
        credentials = []
        users = []
        for username, name, admin in [('admin', '管理员', True), ('alice', '林晓', False), ('bob', '陈宇', False)]:
            password = secrets.token_urlsafe(12)
            u = User.objects.create_user(username=username, first_name=name, password=password, is_superuser=admin, is_staff=admin)
            credentials.append(f'{username} ({name}) : {password}')
            users.append(u)
        admin, alice, bob = users
        today = timezone.localdate()
        o = Order.objects.create(number='DEMO-2026-001', customer='Northstar Trading（示例客户）', owner=alice, date=today-timedelta(days=145), notes='虚构示例数据。首次下单、加订及第二次 B 产品订单独立核算。')
        specs = [('001 · 首次下单', 'A', 5000, 10, 40, 145), ('002 · 第一次加订', 'B', 1000, 20, 80, 110), ('003 · 加订全款', 'C', 1000, 30, 120, 80), ('004 · 第二次 B 补单', 'B', 2000, 22, 85, 35), ('005 · 新增型号', 'D', 4100, 12, 50, 5)]
        lines = []
        for label, model, qty, price, cost, days in specs:
            l = Line.objects.create(order=o, label=label, product=f'{model} 型控制模块', model=f'CTRL-{model}', date=today-timedelta(days=days), quantity=qty, price=price, cost=cost, quote_fx=7, supplier=f'示例工厂 {model}', customer_terms='A 预付30%，B 未预付，C 已全款' if model in ['A','B','C'] else '发货前结清')
            lines.append(l)
        for idx, amount, kind, days in [(0,15000,'receipt',140),(0,100000,'payment',125),(2,30000,'receipt',75),(2,120000,'payment',65),(3,8800,'receipt',25),(4,10000,'receipt',3)]:
            l = lines[idx]
            t = Transaction.objects.create(order=o, kind=kind, date=today-timedelta(days=days), amount=amount, currency='USD' if kind=='receipt' else 'CNY', party='Northstar Trading Ltd.' if kind=='receipt' else l.supplier, platform='银行电汇' if kind=='receipt' else '企业银行', fx=1, purpose='示例预付款 / 货款', reference=f'DEMO-{kind}-{idx}-{days}', created_by=admin)
            if idx != 4:
                allocate(admin, t.pk, l.pk, amount, 1, '示例款项分配')
        confirm_shipment(admin, o.pk, {lines[0].pk:500, lines[1].pk:200, lines[2].pk:100}, {'date':today-timedelta(days=45), 'address':'Example Warehouse, Hamburg, Germany（虚构地址）', 'carrier_type':'customer', 'carrier':'客户指定货代（示例）', 'domestic_date':today-timedelta(days=47), 'domestic_tracking':'DEMO-DOM-0001', 'international_tracking':'DEMO-INT-0001', 'freight':2400, 'freight_currency':'CNY', 'notes':'共 8 箱；示例物流信息，不对应真实运输。'}, uuid.uuid4())
        other = Order.objects.create(number='DEMO-2026-002', customer='Blue Harbor（示例客户）', owner=bob, date=today-timedelta(days=20), notes='用于验证业务员之间的数据隔离。alice 不能查看此订单。')
        Line.objects.create(order=other, label='001 · 新订单', product='工业电源', model='PWR-24', quantity=800, price=45, cost=180, supplier='示例电源工厂', quote_fx=7)
        done = Order.objects.create(number='DEMO-2026-003', customer='Greenfield Labs（示例客户）', owner=alice, date=today-timedelta(days=40), notes='已完成示例，可测试锁定归档与三表下载。')
        dl = Line.objects.create(order=done, label='001', product='样品模块', model='SAMPLE-X', quantity=100, price=10, cost=40, supplier='示例样品工厂', quote_fx=7)
        for kind, amount in [('receipt',1000),('payment',4000)]:
            t = Transaction.objects.create(order=done, kind=kind, amount=amount, currency='USD' if kind=='receipt' else 'CNY', party='Greenfield Labs' if kind=='receipt' else dl.supplier, platform='银行电汇', fx=1, purpose='示例全款', reference=f'DONE-{kind}', created_by=admin, date=today-timedelta(days=30))
            allocate(admin,t.pk,dl.pk,amount,1,'全额分配')
        confirm_shipment(admin,done.pk,{dl.pk:100},{'date':today-timedelta(days=10),'address':'Example Sample Warehouse','carrier_type':'own','carrier':'自有货代（示例）','freight':0,'notes':'已完成示例'},uuid.uuid4())
        for order in [o,other,done]:
            audit(order,admin,'初始化虚构示例','不包含真实客户、资金或物流信息')
        path = settings.DATA_DIR / 'demo-accounts.txt'
        path.write_text('本地演示账号（随机密码，未上传云端）\n登录地址：http://127.0.0.1:8765/\n\n'+'\n'.join(credentials)+'\n\n仅用于虚构数据试用；正式使用前创建新账号并停用演示账号。\n', encoding='utf-8')
        self.stdout.write('示例初始化完成。随机密码已保存到 data/demo-accounts.txt。')
