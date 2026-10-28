import uuid
from datetime import date
from decimal import Decimal
from io import StringIO
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from .models import Order, Line, Transaction, Allocation
from .services import allocate, confirm_shipment, summary, rate_for, remember_rate
from .views import freeze_order

class WorkflowTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user('workflow_admin', is_superuser=True)
        self.sales = User.objects.create_user('workflow_sales')
        self.order = Order.objects.create(number='WORKFLOW', customer='虚构验收客户', owner=self.sales)
        self.client.force_login(self.admin)

    def line(self, label='001', **values):
        return Line.objects.create(order=self.order, label=label, product='假数据产品', model='WF', quantity=10, price=10, cost=4, supplier='假数据工厂', **values)

    def txn(self, amount=100, kind='receipt', currency=None):
        return Transaction.objects.create(order=self.order, kind=kind, amount=amount, currency=currency or ('USD' if kind=='receipt' else 'CNY'), party='假数据工厂', platform='测试', fx=1, purpose='假数据测试', reference=uuid.uuid4().hex, created_by=self.admin)

    def batch_data(self, **overrides):
        data = {'bulk_lines':'1', 'lines-TOTAL_FORMS':'2', 'lines-INITIAL_FORMS':'0', 'lines-MIN_NUM_FORMS':'0', 'lines-MAX_NUM_FORMS':'100', 'date':'2026-10-28', 'customer_terms':'发货前结清', 'supplier_terms':'按合同', 'freight_included':'on', 'rate_USD':'7.1'}
        for i in range(2):
            data.update({f'lines-{i}-{k}':v for k,v in {'label':'', 'product':f'假数据{i}', 'model':'SAME', 'quantity':'10', 'price':'10', 'currency':'USD', 'cost':'4', 'supplier':'假数据工厂', 'supply_type':'external', 'freight':'0'}.items()})
        data.update(overrides)
        return data

    def test_table_saves_rows_with_one_rate_and_keeps_duplicate_models_separate(self):
        response = self.client.post(f'/orders/{self.order.pk}/lines/new/', self.batch_data())
        self.assertEqual(response.status_code,302)
        lines=list(self.order.lines.all())
        self.assertEqual(len(lines),2)
        self.assertNotEqual(lines[0].label,lines[1].label)
        self.assertEqual([l.quote_fx for l in lines],[Decimal('7.1')]*2)
        self.order.refresh_from_db()
        self.assertEqual(rate_for(self.order,'USD','CNY'),Decimal('7.1'))

    def test_one_invalid_row_rolls_back_whole_table(self):
        response=self.client.post(f'/orders/{self.order.pk}/lines/new/',self.batch_data(**{'lines-1-quantity':'-1'}))
        self.assertContains(response,'尚未保存')
        self.assertFalse(self.order.lines.exists())

    def test_duplicate_labels_do_not_partially_save(self):
        response=self.client.post(f'/orders/{self.order.pk}/lines/new/',self.batch_data(**{'lines-0-label':'DUP','lines-1-label':'DUP'}))
        self.assertContains(response,'重复')
        self.assertFalse(self.order.lines.exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.exchange_rates,{})

    def test_self_supply_in_table_preserves_internal_cost(self):
        response=self.client.post(f'/orders/{self.order.pk}/lines/new/',self.batch_data(**{'lines-1-supply_type':'self','lines-1-supplier':''}))
        self.assertEqual(response.status_code,302)
        line=self.order.lines.get(supply_type='self')
        self.assertEqual(line.supplier,'自供货')
        self.assertEqual(line.cost_total,Decimal('40'))
        self.assertEqual(line.payable_total,0)
        s=summary(self.order)
        self.assertEqual(s['cost'],80)
        self.assertEqual(s['external_cost'],40)
        self.assertEqual(s['internal_cost'],40)
        self.assertEqual(s['payable'],40)

    def test_self_supply_can_ship_and_archive_without_fake_payment(self):
        line=self.line(supply_type='self')
        t=self.txn()
        allocate(self.admin,t.pk,line.pk,100,1,'测试收款')
        ship=confirm_shipment(self.admin,self.order.pk,{line.pk:10},{'date':date.today(),'address':'假数据地址','carrier_type':'own','carrier':'假数据货代','freight':0},uuid.uuid4())
        item=ship.items.get()
        self.assertEqual(item.internal_cost_amount,40)
        self.assertEqual(item.cost_amount,0)
        self.assertEqual(item.due('payment'),0)
        self.assertTrue(summary(self.order)['complete'])
        self.assertTrue(freeze_order(self.admin,self.order.pk))

    def test_self_supply_rejects_supplier_allocation(self):
        line=self.line(supply_type='self')
        t=self.txn(40,'payment')
        with self.assertRaisesMessage(ValidationError,'自供货'):
            allocate(self.admin,t.pk,line.pk,40,1,'不允许')
        self.assertFalse(t.allocations.exists())

    def test_order_rates_reused_without_changing_historical_lines(self):
        line=self.line()
        remember_rate(self.order,'USD','CNY',Decimal('7.2'))
        response=self.client.post(f'/orders/{self.order.pk}/lines/new/',{'label':'002','product':'假数据','model':'SAME','date':'2026-10-28','quantity':10,'price':10,'currency':'USD','cost':4,'supplier':'工厂','freight':0,'customer_terms':'全款','supplier_terms':'全款'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.order.lines.get(label='002').quote_fx,Decimal('7.2'))
        line.refresh_from_db()
        self.assertEqual(line.quote_fx,7)

    def test_cross_rate_derived_and_same_currency_is_one(self):
        remember_rate(self.order,'USD','CNY',7)
        remember_rate(self.order,'EUR','CNY',8)
        self.assertEqual(rate_for(self.order,'EUR','USD'),Decimal('1.14285714'))
        self.assertEqual(rate_for(self.order,'USD','USD'),1)

    def test_allocation_uses_remembered_rate_when_omitted(self):
        line=self.line()
        remember_rate(self.order,'CNY','USD',Decimal('0.2'))
        txn=self.txn(100,currency='CNY')
        response=self.client.post(f'/transactions/{txn.pk}/allocate/',{'line':line.pk,'amount':100,'reason':'复用'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(txn.allocations.get().target_amount,20)

    def bulk_data(self, txn, entries, **overrides):
        data={'bulk_allocation':'1','token':str(uuid.uuid4()),'selected':list(entries),'rate_USD':'1','reason':'批量假数据'}
        data.update({f'amount_{key}':amount for key,amount in entries.items()})
        data.update(overrides)
        return data

    def test_bulk_allocation_two_lines_and_replay_is_idempotent(self):
        a=self.line();b=self.line('002');txn=self.txn(150)
        data=self.bulk_data(txn,{f'{a.pk}_pool':'100',f'{b.pk}_pool':'50'})
        url=f'/transactions/{txn.pk}/allocate/'
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(txn.allocations.count(),2)
        self.assertEqual(txn.unallocated,0)

    def test_bulk_overallocation_rolls_back_every_row(self):
        a=self.line();b=self.line('002');txn=self.txn(100)
        data=self.bulk_data(txn,{f'{a.pk}_pool':'80',f'{b.pk}_pool':'80'})
        self.assertContains(self.client.post(f'/transactions/{txn.pk}/allocate/',data),'未分配金额')
        self.assertFalse(txn.allocations.exists())

    def test_bulk_foreign_line_rejected(self):
        a=self.line();txn=self.txn()
        data=self.bulk_data(txn,{f'{a.pk}_pool':'10','999999_pool':'10'})
        self.assertContains(self.client.post(f'/transactions/{txn.pk}/allocate/',data),'无效')
        self.assertFalse(txn.allocations.exists())

    def test_bulk_debt_then_pool_in_one_atomic_submission(self):
        line=self.line()
        ship=confirm_shipment(self.admin,self.order.pk,{line.pk:5},{'date':date.today(),'address':'假数据地址','carrier_type':'own','carrier':'假数据','freight':0},uuid.uuid4())
        item=ship.items.get();txn=self.txn()
        data=self.bulk_data(txn,{f'{line.pk}_{item.pk}':'50',f'{line.pk}_pool':'50'})
        self.assertEqual(self.client.post(f'/transactions/{txn.pk}/allocate/',data).status_code,302)
        self.assertEqual(item.due('receipt'),0)
        self.assertEqual(txn.unallocated,0)

    def test_bulk_cannot_put_money_in_pool_before_debt_is_cleared(self):
        line=self.line()
        confirm_shipment(self.admin,self.order.pk,{line.pk:5},{'date':date.today(),'address':'假数据地址','carrier_type':'own','carrier':'假数据','freight':0},uuid.uuid4())
        txn=self.txn()
        data=self.bulk_data(txn,{f'{line.pk}_pool':'10'})
        self.assertContains(self.client.post(f'/transactions/{txn.pk}/allocate/',data),'已发货未结算')
        self.assertFalse(txn.allocations.exists())

    def test_salesperson_cannot_batch_allocate_or_see_foreign_order(self):
        line=self.line();txn=self.txn()
        self.client.force_login(self.sales)
        data=self.bulk_data(txn,{f'{line.pk}_pool':'10'})
        self.assertEqual(self.client.post(f'/transactions/{txn.pk}/allocate/',data).status_code,403)
        outsider=get_user_model().objects.create_user('outside')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(f'/orders/{self.order.pk}/lines/new/').status_code,404)

    def test_rename_salespeople_keeps_password_and_owner_ids(self):
        User=get_user_model()
        old=User.objects.create_user('alice',first_name='旧姓名',password='fixture-password')
        other=User.objects.create_user('bob',first_name='旧姓名')
        order=Order.objects.create(number='RENAME',customer='假数据',owner=old)
        call_command('rename_salespeople',stdout=StringIO())
        old.refresh_from_db();other.refresh_from_db();order.refresh_from_db()
        self.assertEqual((old.username,old.first_name),('chris','chris'))
        self.assertEqual((other.username,other.first_name),('coco','coco'))
        self.assertTrue(old.check_password('fixture-password'))
        self.assertEqual(order.owner_id,old.pk)
        call_command('rename_salespeople',stdout=StringIO())

    def test_rename_collision_rolls_back_all_accounts(self):
        User=get_user_model()
        User.objects.create_user('alice')
        User.objects.create_user('chris')
        with self.assertRaises(CommandError):
            call_command('rename_salespeople',stdout=StringIO())
        self.assertTrue(User.objects.filter(username='alice').exists())

    def test_self_supply_partial_shipments_preserve_cost_remainder(self):
        line=self.line(supply_type='self')
        quantities=[3,3,4];costs=[]
        for qty in quantities:
            ship=confirm_shipment(self.admin,self.order.pk,{line.pk:qty},{'date':date.today(),'address':'假数据地址','carrier_type':'own','carrier':'假数据','freight':0},uuid.uuid4())
            item=ship.items.get();costs.append(item.internal_cost_amount)
            self.assertEqual(item.due('payment'),0)
        self.assertEqual(sum(costs),line.cost_total)

    def test_suggestions_start_after_four_entries_and_obey_ownership(self):
        from .catalog import suggestions
        for i in range(3):
            self.line(f'F{i}')
        self.assertNotIn('假数据产品', suggestions(self.sales)['product'])
        self.line('F3')
        self.assertIn('假数据产品', suggestions(self.sales)['product'])
        outsider = get_user_model().objects.create_user('suggestion_outsider')
        self.assertNotIn('假数据产品', suggestions(outsider)['product'])

    def xlsx(self, rows, header=None):
        from io import BytesIO
        from openpyxl import Workbook
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .product_import import COLUMNS
        book=Workbook();sheet=book.active;sheet.title='产品录入'
        sheet.append(header or [label for key,label in COLUMNS])
        for row in rows: sheet.append(row)
        out=BytesIO();book.save(out)
        return SimpleUploadedFile('products.xlsx',out.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def test_template_download_scoped_to_order(self):
        response=self.client.get(f'/orders/{self.order.pk}/lines/template/')
        self.assertEqual(response.status_code,200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'PK'))
        outsider=get_user_model().objects.create_user('template_outsider')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(f'/orders/{self.order.pk}/lines/template/').status_code,404)

    def test_import_only_populates_draft_until_save(self):
        data=self.batch_data()
        # No existing rows: only the uploaded spreadsheet is previewed.
        data.update({'lines-TOTAL_FORMS':'0','action':'import','product_file':self.xlsx([['','假数据导入','IMP',10,5,'USD','自供货','',2,0]])})
        response=self.client.post(f'/orders/{self.order.pk}/lines/new/',data)
        self.assertContains(response,'尚未写入订单')
        self.assertContains(response,'假数据导入')
        self.assertFalse(self.order.lines.exists())
        self.assertEqual(response.context['formset'].total_form_count(),1)

    def test_import_formula_wrong_headers_and_more_than_100_rows_rejected(self):
        from .product_import import import_rows
        with self.assertRaisesMessage(ValidationError,'公式'):
            import_rows(self.xlsx([['','=1+1','IMP',1,5,'USD','自供货','',2,0]]))
        with self.assertRaisesMessage(ValidationError,'列标题'):
            import_rows(self.xlsx([],header=['错误标题']))
        with self.assertRaisesMessage(ValidationError,'100行'):
            import_rows(self.xlsx([['','假数据','IMP',1,5,'USD','自供货','',2,0]]*101))

    def test_import_keeps_existing_draft_and_does_not_write(self):
        data=self.batch_data()
        data.update({'action':'import','product_file':self.xlsx([['','假数据新增','IMP',1,5,'CNY','外部供应商','假数据',2,0]])})
        response=self.client.post(f'/orders/{self.order.pk}/lines/new/',data)
        self.assertEqual(response.context['formset'].total_form_count(),3)
        self.assertFalse(self.order.lines.exists())

    def test_rate_change_does_not_reprice_existing_allocations(self):
        line=self.line();txn=self.txn()
        a=allocate(self.admin,txn.pk,line.pk,20,1,'历史分摊')
        remember_rate(self.order,'USD','CNY',Decimal('7.4'))
        a.refresh_from_db()
        self.assertEqual((a.source_amount,a.settlement_rate,a.target_amount),(20,1,20))
