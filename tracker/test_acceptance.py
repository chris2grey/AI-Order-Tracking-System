"""Acceptance cases complementing the core calculation tests."""
from datetime import date
from decimal import Decimal
from io import BytesIO
import hashlib
import json
import tempfile
import uuid
from zipfile import ZipFile
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from openpyxl import load_workbook
from .models import Order, Line, Transaction, Shipment, Archive
from .services import allocate, confirm_shipment, shipment_quote
from .exports import workbook


class AcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('qa_admin', password='Acceptance-Only-Secret!', is_superuser=True)
        cls.sales = User.objects.create_user('qa_sales', password='Acceptance-Only-Secret!')
        cls.other = User.objects.create_user('qa_other', password='Acceptance-Only-Secret!')
        cls.order = Order.objects.create(number='QA-001', customer='验收客户', owner=cls.sales)
        cls.line = Line.objects.create(order=cls.order, label='补单-01', product='验收独立产品名称', model='MODEL-QA', quantity=100, price=10, cost=40, supplier='验收供应商')

    def setUp(self):
        self.client.force_login(self.admin)

    def payload(self, **overrides):
        data = {'date': '2026-10-23', 'amount': '1000', 'currency': 'USD', 'party': '验收付款公司', 'platform': '验收银行', 'fx': '1', 'fx_source': '假数据', 'purpose': '验收预付款', 'reference': uuid.uuid4().hex}
        data.update(overrides)
        return data

    def transaction(self, **overrides):
        data = dict(order=self.order, kind='receipt', amount=1000, currency='USD', party='验收付款公司', platform='验收银行', fx=1, fx_source='假数据', purpose='验收预付款', reference=uuid.uuid4().hex, created_by=self.admin)
        data.update(overrides)
        return Transaction.objects.create(**data)

    def fully_fund_and_ship(self):
        receipt = self.transaction()
        payment = self.transaction(kind='payment', amount=4000, currency='CNY', party=self.line.supplier)
        allocate(self.admin, receipt.id, self.line.id, 1000, 1, 'Full')
        allocate(self.admin, payment.id, self.line.id, 4000, 1, 'Full')
        ship = confirm_shipment(self.admin, self.order.id, {self.line.id: 100}, {'date': date(2026,10,23), 'address': '假数据测试仓库', 'carrier_type': 'own', 'carrier': '验收货代', 'freight': 0}, uuid.uuid4())
        return ship

    def test_valid_voucher_upload_download_and_export(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            content = b'%PDF-1.4\n% FICTIONAL TEST VOUCHER\n%%EOF'
            data = self.payload(attachment=SimpleUploadedFile('fake-voucher.pdf', content, content_type='application/pdf'))
            response = self.client.post(f'/orders/{self.order.id}/transactions/receipt/new/', data)
            self.assertEqual(response.status_code, 302)
            txn = self.order.transactions.get()
            response = self.client.get(f'/transactions/{txn.id}/attachment/')
            self.assertEqual(b''.join(response.streaming_content), content)
            self.assertIn('attachment', response['Content-Disposition'])
            response = self.client.get(f'/orders/{self.order.id}/export/')
            with ZipFile(BytesIO(b''.join(response.streaming_content))) as z:
                names = [n for n in z.namelist() if n.startswith('凭证/')]
                self.assertEqual(len(names), 1)
                self.assertEqual(z.read(names[0]), content)
            self.client.force_login(self.other)
            self.assertEqual(self.client.get(f'/transactions/{txn.id}/attachment/').status_code, 404)
            self.assertEqual(self.client.get('/media/' + txn.attachment.name).status_code, 404)

    def test_rejected_upload_creates_no_transaction(self):
        data = self.payload(attachment=SimpleUploadedFile('fake.pdf', b'<script>bad</script>'))
        response = self.client.post(f'/orders/{self.order.id}/transactions/receipt/new/', data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.order.transactions.count(), 0)

    def test_split_receipt_across_lines(self):
        second = Line.objects.create(order=self.order, label='补单-02', product='第二款', model='MODEL-QA', quantity=10, price=10, cost=20, supplier=self.line.supplier)
        t = self.transaction(amount=200)
        allocate(self.admin, t.id, self.line.id, 150, 1, 'First')
        allocate(self.admin, t.id, second.id, 50, 1, 'Second')
        self.assertEqual(t.unallocated, 0)
        self.assertEqual(t.allocations.count(), 2)
        self.assertEqual(shipment_quote(second, 10)['receipt']['covered'], 50)

    def test_finance_search_product_name(self):
        t = self.transaction()
        allocate(self.admin, t.id, self.line.id, 1000, 1, 'Test')
        response = self.client.get('/finance/', {'q': self.line.product})
        self.assertContains(response, t.reference)

    def test_finance_search_line_label(self):
        t = self.transaction()
        allocate(self.admin, t.id, self.line.id, 1000, 1, 'Test')
        self.assertContains(self.client.get('/finance/', {'q': self.line.label}), t.reference)

    def test_finance_combined_filters(self):
        first = self.transaction(date=date(2026,9,10))
        second = self.transaction(date=date(2026,10,10), currency='EUR', fx='1.1')
        self.assertContains(self.client.get('/finance/', {'kind':'receipt','currency':'EUR','start':'2026-10-01','end':'2026-10-31'}), second.reference)
        self.assertNotContains(self.client.get('/finance/', {'currency':'EUR'}), first.reference)

    def test_invalid_dates_and_values_do_not_crash(self):
        self.assertEqual(self.client.get('/finance/', {'start': 'invalid-date'}).status_code, 200)
        for field, value in [('amount', '-1'), ('amount','0'), ('amount','NaN'), ('fx','0'), ('fx','-1')]:
            response = self.client.post(f'/orders/{self.order.id}/transactions/receipt/new/', self.payload(**{field:value}))
            self.assertEqual(response.status_code,200)
        self.assertFalse(self.order.transactions.exists())

    def test_same_currency_reporting_rate_rejected(self):
        response = self.client.post(f'/orders/{self.order.id}/transactions/receipt/new/', self.payload(fx='2'))
        self.assertEqual(response.status_code,200)
        self.assertFalse(self.order.transactions.exists())

    def test_sale_cny_quote_rate_must_be_one(self):
        from .forms import LineForm
        form = LineForm({'label':'CNY','product':'CNY商品','model':'CNY','date':'2026-10-23','quantity':10,'price':10,'currency':'CNY','quote_fx':7,'cost':4,'supplier':'验收供应商','freight':0,'customer_terms':'全款','supplier_terms':'全款'})
        self.assertFalse(form.is_valid())
        self.assertIn('quote_fx',form.errors)

    def test_multi_currency_dashboard_keeps_balances_separate(self):
        Line.objects.create(order=self.order,label='EUR',product='欧元商品',model='EUR',quantity=10,price=20,currency='EUR',cost=30,supplier='工厂')
        response = self.client.get('/')
        self.assertEqual(response.context['totals']['USD']['sale'],1000)
        self.assertEqual(response.context['totals']['EUR']['sale'],200)

    def test_xlsx_handles_control_characters_and_precision(self):
        data = workbook([('测试',['产品','单位价格','汇率','数量'],[['假数据\x01产品', Decimal('1.2345'),Decimal('0.14285714'),100]])])
        sheet = load_workbook(BytesIO(data)).active
        self.assertNotIn('\x01',sheet['A2'].value)
        self.assertIn('0000',sheet['B2'].number_format)
        self.assertIn('00000000',sheet['C2'].number_format)
        self.assertEqual(sheet['D2'].number_format,'#,##0')

    def test_locked_order_rejects_all_business_writes(self):
        self.fully_fund_and_ship()
        self.client.post(f'/orders/{self.order.id}/lock/')
        before = (self.order.lines.count(), self.order.transactions.count(), self.order.shipments.count())
        for suffix in ['lines/new/','transactions/receipt/new/','transactions/payment/new/','shipments/new/']:
            response = self.client.post(f'/orders/{self.order.id}/{suffix}', {})
            self.assertEqual(response.status_code,302)
        self.assertEqual(before,(self.order.lines.count(),self.order.transactions.count(),self.order.shipments.count()))

    def test_archive_is_immutable_and_private(self):
        self.fully_fund_and_ship()
        self.client.post(f'/orders/{self.order.id}/lock/')
        a = self.order.archives.get()
        first = b''.join(self.client.get(f'/archives/{a.id}/download/').streaming_content)
        second = b''.join(self.client.get(f'/archives/{a.id}/download/').streaming_content)
        self.assertEqual(first,second)
        self.assertEqual(hashlib.sha256(first).hexdigest(),a.digest)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/archives/{a.id}/download/').status_code,404)

    def test_duplicate_final_shipment_http_is_idempotent(self):
        token=str(uuid.uuid4())
        data = {'action':'confirm','token':token,f'qty_{self.line.id}':100,'date':'2026-10-23','address':'假数据仓库','carrier_type':'own','carrier':'假数据货代','freight':0,'freight_currency':'CNY'}
        url=f'/orders/{self.order.id}/shipments/new/'
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(self.order.shipments.count(),1)

    def test_sales_cannot_archive_or_create_accounts(self):
        self.client.force_login(self.sales)
        self.assertEqual(self.client.post(f'/orders/{self.order.id}/lock/').status_code,403)
        self.assertEqual(self.client.post('/team/',{}).status_code,403)

    def test_login_failure_limit_and_logout(self):
        cache.clear()
        c=Client()
        for _ in range(8):
            self.assertEqual(c.post('/login/',{'username':'qa_sales','password':'wrong'}).status_code,200)
        self.assertEqual(c.post('/login/',{'username':'qa_sales','password':'Acceptance-Only-Secret!'}).status_code,429)
        cache.clear()
        self.assertEqual(c.post('/login/',{'username':'qa_sales','password':'Acceptance-Only-Secret!'}).status_code,302)
        self.assertEqual(c.get('/').status_code,200)
        self.assertEqual(c.post('/logout/').status_code,302)
        self.assertEqual(c.get('/').status_code,302)

    def test_team_create_sales_and_require_name(self):
        data = {'username':'created_sales','display_name':'验收业务员','password1':'Fresh-Testing-Only-7392!','password2':'Fresh-Testing-Only-7392!'}
        self.assertEqual(self.client.post('/team/',data).status_code,302)
        user = User.objects.get(username='created_sales')
        self.assertEqual(user.first_name,'验收业务员')
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)
        data.update(username='missing_name',display_name='')
        self.assertEqual(self.client.post('/team/',data).status_code,200)
        self.assertFalse(User.objects.filter(username='missing_name').exists())

    def test_sensitive_responses_never_cache(self):
        for url in ['/','/orders/','/finance/']:
            response=self.client.get(url)
            self.assertIn('no-store',response['Cache-Control'])
            self.assertEqual(response['X-Frame-Options'],'DENY')

    def test_scenario_seed_is_repeatable_and_balanced(self):
        from django.core.management import call_command
        from pathlib import Path
        from io import StringIO
        from .services import summary
        with tempfile.TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder, DATA_DIR=Path(folder)):
            call_command('seed_scenarios', stdout=StringIO())
            before=(Order.objects.count(),Transaction.objects.count(),Shipment.objects.count())
            call_command('seed_scenarios', stdout=StringIO())
            self.assertEqual(before,(Order.objects.count(),Transaction.objects.count(),Shipment.objects.count()))
            self.assertEqual(Order.objects.filter(number__startswith='QA-DEMO-').count(),6)
            mixed=Order.objects.get(number='QA-DEMO-001')
            self.assertEqual(mixed.lines.count(),5)
            self.assertEqual(mixed.lines.filter(model='QA-B').count(),2)
            first_a=mixed.shipments.order_by('id').first().items.get(line__model='QA-A')
            self.assertEqual(first_a.due('receipt'),0)
            self.assertEqual(first_a.due('payment'),0)
            self.assertTrue(summary(Order.objects.get(number='QA-DEMO-003'))['complete'])
            self.assertEqual(Order.objects.get(number='QA-DEMO-004').status,'locked')
            self.assertFalse(summary(Order.objects.get(number='QA-DEMO-006'))['complete'])
            self.assertEqual(Transaction.objects.filter(order__number__startswith='QA-DEMO-').exclude(attachment='').count(),16)
