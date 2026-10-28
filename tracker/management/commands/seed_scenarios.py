"""Add labeled, repeatable fictional scenarios without changing existing orders."""
from datetime import timedelta
from decimal import Decimal
import hashlib
import json
import uuid
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from tracker.models import Order, Line, Transaction, Archive
from tracker.services import allocate, confirm_shipment, audit, summary
from tracker.exports import archive_bytes


def fake_voucher(reference):
    """Small valid PDF, prominently labeled as fictional, with no real bank branding."""
    text = f'BT /F1 17 Tf 45 740 Td (FICTIONAL TEST VOUCHER) Tj 0 -35 Td /F1 11 Tf (NOT A REAL PAYMENT OR BANK RECEIPT) Tj 0 -35 Td ({reference}) Tj ET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>', b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>', f'<< /Length {len(text)} >>\nstream\n{text}\nendstream'.encode('ascii')]
    out = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out.extend(f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n')
    start = len(out)
    out.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    for pos in offsets[1:]:
        out.extend(f'{pos:010d} 00000 n \n'.encode())
    out.extend(f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode())
    return bytes(out)


class Command(BaseCommand):
    help = 'Append six labeled QA-DEMO fictional scenarios, safely repeatable.'

    @transaction.atomic
    def handle(self, **options):
        self.admin = User.objects.filter(is_superuser=True, is_active=True).first()
        if not self.admin:
            raise CommandError('请先创建管理员。此命令不创建或重置任何账号密码。')
        self.alice = User.objects.filter(username='chris', is_active=True).first() or User.objects.filter(username='alice', is_active=True).first() or self.admin
        self.bob = User.objects.filter(username='coco', is_active=True).first() or User.objects.filter(username='bob', is_active=True).first() or self.admin
        self.today = timezone.localdate()
        self.created = []
        self.created_files = []
        try:
            self.mixed()
            self.cross_currency()
            self.simple_complete('003', '假数据·星河样品客户（已完成待归档）', self.alice, ship=True)
            self.simple_complete('004', '假数据·远帆客户（已归档）', self.bob, ship=True, lock=True)
            self.new_order_only()
            self.simple_complete('006', '假数据·海岚客户（全款待发货）', self.alice, ship=False)
        except Exception:
            # Filesystem writes are not transactional; remove only files created here.
            for storage, name in self.created_files:
                storage.delete(name)
            raise
        dataset = []
        for o in Order.objects.filter(number__startswith='QA-DEMO-').select_related('owner'):
            s = summary(o)
            dataset.append({'id':o.id, 'number':o.number, 'customer':o.customer, 'owner':o.owner.username, 'status':o.status, 'lines':o.lines.count(), 'transactions':o.transactions.count(), 'shipments':o.shipments.count(), 'vouchers':sum(bool(t.attachment) for t in o.transactions.all()), 'complete':s['complete']})
        (settings.DATA_DIR / 'qa-fixtures.json').write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding='utf-8')
        self.stdout.write(json.dumps({'created_orders':len(self.created),'fixture_orders':len(dataset),'line_count':sum(x['lines'] for x in dataset),'transaction_count':sum(x['transactions'] for x in dataset),'shipment_count':sum(x['shipments'] for x in dataset),'voucher_count':sum(x['vouchers'] for x in dataset)}, ensure_ascii=False))

    def new_order(self, suffix, customer, owner, days):
        number = f'QA-DEMO-{suffix}'
        if Order.objects.filter(number=number).exists():
            return None
        o = Order.objects.create(number=number, customer=customer, owner=owner, date=self.today-timedelta(days=days), notes='【假数据 / 验收用】全部客户、供应商、汇率、金额及物流均为虚构，不对应真实交易。可用于操作演练。')
        self.created.append(o)
        audit(o,self.admin,'新增验收假数据','专用于功能验证，未修改既有订单和账号')
        return o

    def line(self,o,label,model,qty,price,cost,supplier,currency='USD',days=30,freight=0,included=True):
        return Line.objects.create(order=o,label=label,product=f'假数据·{model} 控制模块',model=model,quantity=qty,price=Decimal(str(price)),cost=Decimal(str(cost)),supplier=supplier,currency=currency,quote_fx=Decimal('1' if currency=='CNY' else '7.7' if currency=='EUR' else '7'),date=self.today-timedelta(days=days),freight=freight,freight_included=included,customer_terms='假数据付款约定：预付比例见款项分摊，发货前结清',supplier_terms='假数据付款约定：分批结算')

    def txn(self,o,kind,amount,party,currency=None,fx=1,days=20,purpose='假数据·预付款'):
        reference=f'{o.number}-{kind}-{o.transactions.count()+1:02d}'
        t=Transaction.objects.create(order=o,kind=kind,amount=Decimal(str(amount)),currency=currency or ('USD' if kind=='receipt' else 'CNY'),party=party,platform='假数据·银行电汇' if kind=='receipt' else '假数据·企业银行',fx=Decimal(str(fx)),fx_source='假数据，人工设定，仅供测试',purpose=purpose,reference=reference,created_by=self.admin,date=self.today-timedelta(days=days))
        t.attachment.save('fictional-voucher.pdf',ContentFile(fake_voucher(reference)),save=True)
        self.created_files.append((t.attachment.storage,t.attachment.name))
        audit(o,self.admin,'录入假数据流水',f'{reference}；附虚构凭证，不代表真实付款')
        return t

    def fund(self,t,line,amount,rate=1,item=None):
        return allocate(self.admin,t.id,line.id,Decimal(str(amount)),Decimal(str(rate)),'假数据·按指定小项 / 批次分摊',item.id if item else None)

    def ship(self,o,quantities,days=7):
        index=o.shipments.count()+1
        return confirm_shipment(self.admin,o.id,quantities,{'date':self.today-timedelta(days=days),'address':'假数据收货地址：示例市测试区验收仓 100 号（非真实地址）','carrier_type':'customer' if index%2 else 'own','carrier':'假数据·客户指定货代' if index%2 else '假数据·我司合作货代','domestic_date':self.today-timedelta(days=days+2),'domestic_tracking':f'FAKE-DOM-{o.number}-{index}','international_tracking':f'FAKE-INT-{o.number}-{index}','freight':Decimal('1200'),'freight_currency':'CNY','notes':f'【假数据】第 {index} 批；纸箱标签 TEST ONLY；非真实物流。'},uuid.uuid4())

    def mixed(self):
        o=self.new_order('001','假数据·极光贸易（补单及混合预付）',self.alice,150)
        if not o:return
        same='假数据·华辰电子厂'
        a=self.line(o,'001·首次A','QA-A',6000,10,40,same,days=150)
        b=self.line(o,'002·首次B','QA-B',7500,20,80,same,days=110)
        c=self.line(o,'003·全款C','QA-C',500,30,120,'假数据·云峰工厂',days=90)
        b2=self.line(o,'004·第二次B补单','QA-B',2000,22,85,same,days=50)
        d=self.line(o,'005·新增D','QA-D',4100,12,50,'假数据·青禾工厂',days=20)
        r=self.txn(o,'receipt',33000,'假数据·极光贸易付款公司',days=100)
        self.fund(r,a,18000);self.fund(r,c,15000)
        r=self.txn(o,'receipt',10000,'假数据·极光贸易欧元账户',currency='EUR',fx='1.1',days=40)
        self.fund(r,b2,10000,'1.1')
        p=self.txn(o,'payment',240000,same,days=80)
        self.fund(p,a,120000);self.fund(p,b,120000)
        p=self.txn(o,'payment',60000,c.supplier,days=70);self.fund(p,c,60000)
        p=self.txn(o,'payment',85000,same,days=35);self.fund(p,b2,85000)
        first=self.ship(o,{a.id:1000,b.id:1500,c.id:100},days=25)
        ai=first.items.get(line=a)
        r=self.txn(o,'receipt',7000,'假数据·极光贸易付款公司',days=23,purpose='假数据·第一批 A 尾款')
        self.fund(r,a,7000,item=ai)
        p=self.txn(o,'payment',20000,same,days=23,purpose='假数据·第一批 A 供应商尾款')
        self.fund(p,a,20000,item=ai)
        self.ship(o,{a.id:2000,b2.id:500,d.id:1000},days=5)
        self.txn(o,'receipt',2500,'假数据·极光贸易付款公司',days=2,purpose='假数据·待确认用途，演练未分配提醒')

    def cross_currency(self):
        o=self.new_order('002','假数据·洲际贸易（跨币种及额外运费）',self.bob,80)
        if not o:return
        l=self.line(o,'001·美元订单人民币收款','QA-FX',1000,25,100,'假数据·跨境工厂',days=80,freight=500,included=False)
        r=self.txn(o,'receipt',70000,'假数据·洲际贸易人民币账户',currency='CNY',fx='0.14',days=65)
        self.fund(r,l,70000,'0.14285714')
        p=self.txn(o,'payment',2000,l.supplier,currency='EUR',fx='7.7',days=55)
        self.fund(p,l,2000,'7.7')
        self.ship(o,{l.id:250},days=15)

    def simple_complete(self,suffix,customer,owner,ship,lock=False):
        o=self.new_order(suffix,customer,owner,60)
        if not o:return
        l=self.line(o,'001·全款小项',f'QA-FULL-{suffix}',300,15,45,'假数据·全款工厂',days=60)
        r=self.txn(o,'receipt',4500,customer,days=45,purpose='假数据·客户全款');self.fund(r,l,4500)
        p=self.txn(o,'payment',13500,l.supplier,days=40,purpose='假数据·供应商全款');self.fund(p,l,13500)
        if ship:self.ship(o,{l.id:300},days=10)
        if lock:
            audit(o,self.admin,'锁定假数据归档','用于测试固化归档下载，未删除任何数据')
            content=archive_bytes(o)
            Archive.objects.create(order=o,content=content,digest=hashlib.sha256(content).hexdigest(),created_by=self.admin)
            o.status='locked';o.save(update_fields=['status'])

    def new_order_only(self):
        o=self.new_order('005','假数据·晨光设备（未收款及多币种）',self.bob,4)
        if not o:return
        self.line(o,'001·人民币产品','QA-CNY',800,150,60,'假数据·晨光工厂',currency='CNY',days=4)
        self.line(o,'002·欧元补单','QA-EUR',200,35,120,'假数据·晨光工厂',currency='EUR',days=2)
