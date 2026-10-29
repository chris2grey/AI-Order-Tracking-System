import json
import uuid
from datetime import date
from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase
from .models import Order, Line, Transaction, Allocation, AllocationCoverage, ShipmentFreightRecord
from .services import allocate, confirm_shipment, correct_transaction, record_freight, update_logistics, summary
from .entry import allocation_rows
from .catalog import suggestions
from .exports import archive_bytes


class FollowupTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user('followup_admin', is_superuser=True)
        self.sales = User.objects.create_user('followup_sales')
        self.outsider = User.objects.create_user('followup_outsider')
        self.order = Order.objects.create(number='FOLLOWUP', customer='虚构客户公司', owner=self.sales)
        self.line = Line.objects.create(order=self.order, label='旧人工号-X', product='虚构产品', model='MODEL', quantity=10, price=10, cost=4, supplier='虚构供应商')
        self.client.force_login(self.admin)

    def txn(self, kind='receipt', amount=100, currency=None, party='虚构供应商'):
        return Transaction.objects.create(order=self.order, kind=kind, amount=amount, currency=currency or ('USD' if kind == 'receipt' else 'CNY'), party=party, platform='虚构银行', fx=1, reference=uuid.uuid4().hex, created_by=self.admin)

    def ship(self, qty=5, **extra):
        return confirm_shipment(self.admin, self.order.pk, {self.line.pk:qty},
            dict(date=date.today(), address='虚构地址', carrier_type='own', carrier='虚构货代', **extra), uuid.uuid4())

    def post_product(self, txn, amount):
        return self.client.post(f'/transactions/{txn.pk}/allocate/', {
            'bulk_allocation':'1', 'token':str(uuid.uuid4()), 'selected':[f'{self.line.pk}_auto'],
            f'amount_{self.line.pk}_auto':str(amount), 'rate_USD':'1', 'rate_CNY':'1', 'reason':'虚构按产品分摊'})

    def test_pre_shipment_receipt_and_payment_then_partial_shipments(self):
        receipt, payment = self.txn(), self.txn('payment', 40)
        self.assertEqual(self.post_product(receipt, 100).status_code,302)
        self.assertEqual(self.post_product(payment, 40).status_code,302)
        first, second = self.ship(3), self.ship(7)
        self.assertEqual(first.items.get().receipt_reserved,30)
        self.assertEqual(second.items.get().receipt_reserved,70)
        self.assertEqual(first.items.get().payment_reserved,12)
        self.assertEqual(second.items.get().payment_reserved,28)
        self.assertTrue(summary(self.order)['complete'])

    def test_one_product_row_covers_old_debts_and_future_pool(self):
        a, b = self.ship(2), self.ship(3)
        receipt, payment = self.txn(), self.txn('payment',40)
        self.assertEqual(len(allocation_rows(receipt)),1)
        self.assertEqual(allocation_rows(receipt)[0]['remaining'],5)
        for txn, amount in [(receipt,100),(payment,40)]:
            self.assertEqual(self.post_product(txn,amount).status_code,302)
        self.assertEqual(receipt.allocations.count(),1)
        self.assertEqual(a.items.get().due('receipt'),0)
        self.assertEqual(b.items.get().due('payment'),0)
        a.items.get().refresh_from_db()
        self.assertEqual(a.items.get().receipt_reserved,0)  # Snapshot remains immutable.
        final=self.ship(5).items.get()
        self.assertEqual((final.receipt_reserved,final.payment_reserved),(50,20))
        self.assertTrue(summary(self.order)['complete'])

    def test_convert_once_when_target_debt_is_smaller_than_source_cent(self):
        # 0.01 USD -> 1 CNY; two 0.50 CNY batches must not each round source money.
        self.line.currency='CNY';self.line.price=Decimal('0.1');self.line.save()
        a,b=self.ship(5),self.ship(5)
        txn=self.txn(amount=Decimal('0.01'))
        allocation=allocate(self.admin,txn.pk,self.line.pk,'0.01',100,'虚构高汇率',auto_batches=True)
        self.assertEqual(allocation.source_amount,Decimal('0.01'))
        self.assertEqual(list(allocation.coverages.values_list('amount',flat=True)),[Decimal('0.5'),Decimal('0.5')])
        self.assertEqual(a.items.get().due('receipt'),0)
        self.assertEqual(b.items.get().due('receipt'),0)
        self.assertEqual(txn.unallocated,0)

    def test_bulk_atomicity_rolls_back_generated_coverages(self):
        self.ship(5)
        txn=self.txn(amount=1)
        response=self.post_product(txn,100)
        self.assertContains(response,'未分配金额')
        self.assertFalse(AllocationCoverage.objects.exists())
        self.assertFalse(txn.allocations.exists())

    def test_blank_purpose_accepted_for_both_finance_forms(self):
        for kind,currency in [('receipt','USD'),('payment','CNY')]:
            response=self.client.post(f'/orders/{self.order.pk}/transactions/{kind}/new/',dict(
                date='2026-10-29',amount=10,currency=currency,party='虚构供应商',platform='虚构银行',
                fx=1,fx_source='虚构来源',reference=uuid.uuid4().hex))
            self.assertEqual(response.status_code,302)
        self.assertEqual(self.order.transactions.filter(purpose='').count(),2)

    def test_freight_pending_zero_and_revision_keep_original_and_snapshot(self):
        ship=self.ship()
        self.assertIsNone(ship.freight)
        item=ship.items.get()
        before=(item.quantity,item.sale_amount,item.cost_amount,item.receipt_reserved)
        first=record_freight(self.admin,ship.pk,Decimal('99.5'),'CNY','虚构补录账单')
        record_freight(self.admin,ship.pk,Decimal('15'),'USD','虚构账单币种更正')
        ship.refresh_from_db();item.refresh_from_db()
        self.assertIsNone(ship.freight)
        self.assertEqual((ship.current_freight,ship.current_freight_currency),(15,'USD'))
        self.assertEqual((item.quantity,item.sale_amount,item.cost_amount,item.receipt_reserved),before)
        self.assertEqual(ShipmentFreightRecord.objects.get(pk=first.pk).amount,Decimal('99.5'))
        self.assertEqual(self.ship(5,freight=0).current_freight,0)
        self.assertEqual(ship.freight_records.count(),2)
        with self.assertRaises(ValidationError):
            record_freight(self.admin,ship.pk,-1,'CNY','虚构无效')

    def test_hong_kong_tracking_change_keeps_audit_and_existing_money(self):
        ship=self.ship(international_tracking='FAKE-OLD')
        update_logistics(self.sales,ship.pk,{'international_tracking':'FAKE-HK'},'虚构香港换单')
        ship.refresh_from_db()
        self.assertEqual(ship.international_tracking,'FAKE-HK')
        audit=self.order.audits.get(action='修改物流信息')
        self.assertIn('FAKE-OLD',audit.detail)
        self.assertIn('FAKE-HK',audit.detail)

    def test_mismatched_supplier_can_be_corrected_then_allocated(self):
        txn=self.txn('payment',40,party='虚构录错名称')
        self.assertEqual(allocation_rows(txn),[])
        correction=correct_transaction(self.admin,txn.pk,'party','虚构供应商','虚构名称更正')
        txn.refresh_from_db()
        self.assertEqual(txn.party,'虚构录错名称')
        self.assertEqual(txn.effective_party,'虚构供应商')
        self.assertEqual(correction.source_delta,0)
        self.assertEqual(self.post_product(txn,40).status_code,302)
        self.assertEqual(txn.unallocated,0)
        with self.assertRaises(ValidationError):
            correct_transaction(self.admin,txn.pk,'void','','不允许冲销已分摊')

    def test_void_is_reversing_entry_not_deletion_and_excluded_from_pending(self):
        txn=self.txn('payment',40,party='虚构误录')
        correction=correct_transaction(self.admin,txn.pk,'void','','虚构重复误录')
        txn.refresh_from_db()
        self.assertEqual((txn.amount,correction.source_delta),(40,-40))
        self.assertEqual((txn.unallocated,txn.equivalent,txn.effective_amount),(0,0,0))
        self.assertTrue(txn.is_void)
        self.assertEqual(summary(self.order)['unallocated'],[])
        self.assertNotContains(self.client.get('/finance/',{'allocation':'pending'}),txn.reference)
        with self.assertRaises(ValidationError):
            allocate(self.admin,txn.pk,self.line.pk,1,1,'冲销后不允许',auto_batches=True)
        with self.assertRaises(ValidationError):
            correct_transaction(self.admin,txn.pk,'void','','不可重复')
        with ZipFile(BytesIO(archive_bytes(self.order))) as archive:
            exported=json.loads(archive.read('流水更正及冲销.json'))[0]
            self.assertEqual(exported['corrections'][0]['source_delta'],'-40.00')

    def test_new_names_suggested_after_one_use_scoped_to_ownership(self):
        txn=self.txn('payment',40)
        hints=suggestions(self.sales)
        for key,value in [('supplier','虚构供应商'),('party','虚构供应商'),('platform','虚构银行')]:
            self.assertIn(value,hints[key])
            self.assertNotIn(value,suggestions(self.outsider)[key])
        self.assertNotIn('虚构产品',hints['product'])
        correct_transaction(self.admin,txn.pk,'void','','虚构误录')
        self.assertNotIn('虚构银行',suggestions(self.sales)['platform'])

    def test_serial_is_stable_and_original_label_preserved(self):
        second=Line.objects.create(order=self.order,label='自定义-17',product='虚构',model='OTHER',quantity=1,price=1,cost=0,supply_type='self')
        self.assertEqual((self.line.number,second.number),('001','002'))
        self.assertEqual(self.line.label,'旧人工号-X')
        page=self.client.get(f'/transactions/{self.txn().pk}/allocate/')
        for text in ['总数量','已发 / 未发','序号','虚构产品','MODEL']:
            self.assertContains(page,text)
        self.assertNotContains(page,'旧人工号-X')

    def test_new_endpoints_enforce_permissions_and_locked_orders(self):
        ship=self.ship()
        txn=self.txn()
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(f'/shipments/{ship.pk}/edit/').status_code,404)
        self.assertEqual(self.client.get(f'/transactions/{txn.pk}/correct/').status_code,404)
        for user in [self.sales,self.outsider]:
            with self.assertRaises(PermissionDenied):
                record_freight(user,ship.pk,10,'CNY','权限验收')
            with self.assertRaises(PermissionDenied):
                correct_transaction(user,txn.pk,'void','','权限验收')
        self.order.status='locked';self.order.save()
        with self.assertRaises(ValidationError):
            update_logistics(self.admin,ship.pk,{'international_tracking':'BLOCK'},'锁定测试')
        with self.assertRaises(ValidationError):
            record_freight(self.admin,ship.pk,10,'CNY','锁定测试')
        with self.assertRaises(ValidationError):
            correct_transaction(self.admin,txn.pk,'void','','锁定测试')

    def test_freight_and_correction_web_retries_are_idempotent(self):
        ship=self.ship()
        data={'action':'freight','amount':'9.99','currency':'CNY','reason':'虚构账单','token':str(uuid.uuid4())}
        for _ in range(2):
            self.assertEqual(self.client.post(f'/shipments/{ship.pk}/edit/',data).status_code,302)
        self.assertEqual(ship.freight_records.count(),1)
        txn=self.txn('payment',40,party='虚构误录')
        data={'action':'party','party':'虚构供应商','reason':'虚构名称更正','token':str(uuid.uuid4())}
        for _ in range(2):
            self.assertEqual(self.client.post(f'/transactions/{txn.pk}/correct/',data).status_code,302)
        self.assertEqual(txn.corrections.count(),1)

    def test_new_api_operations_and_idempotency(self):
        ship=self.ship()
        def post(url,data,key=None):
            return self.client.post(url,json.dumps(data),content_type='application/json',HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))
        url=f'/api/v1/shipments/{ship.pk}/freight/'
        key=str(uuid.uuid4());data={'amount':'20.5','currency':'CNY','reason':'虚构API账单'}
        self.assertEqual(post(url,data,key).json()['current_freight'],'20.50')
        self.assertEqual(post(url,data,key).status_code,200)
        self.assertEqual(ship.freight_records.count(),1)
        data={'carrier_type':'own','carrier':'虚构货代','address':'虚构地址','international_tracking':'FAKE-NEW','reason':'虚构换单'}
        self.assertEqual(post(f'/api/v1/shipments/{ship.pk}/logistics/',data).status_code,200)
        txn=self.txn('payment',40,party='虚构误录')
        self.assertEqual(post(f'/api/v1/transactions/{txn.pk}/corrections/',{'action':'party','party':'虚构供应商','reason':'虚构更正'}).json()['effective_party'],'虚构供应商')
        self.assertEqual(post(f'/api/v1/transactions/{txn.pk}/allocations/',{'line':self.line.pk,'amount':'40','rate':'1','reason':'虚构API按产品'}).status_code,201)
        self.assertEqual(ship.items.get().due('payment'),0)
