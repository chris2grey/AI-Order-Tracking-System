from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from .models import Order, Line, Transaction
from .services import allocate, confirm_shipment
from .forms import AllocationForm, LineForm, TransactionForm
from .templatetags.formatting import price, pct
import uuid

class DetailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin=User.objects.create_user('detail_admin',is_superuser=True)
        cls.sales=User.objects.create_user('detail_sales')
        cls.order=Order.objects.create(number='DETAIL-001',customer='细节验收客户',owner=cls.sales)
        cls.line=Line.objects.create(order=cls.order,label='001',product='产品',model='D1',quantity=100,price=10,cost=40,supplier='工厂甲')

    def setUp(self):
        self.client.force_login(self.admin)

    def txn(self,amount=100,kind='receipt',party=None):
        return Transaction.objects.create(order=self.order,kind=kind,amount=amount,currency='USD' if kind=='receipt' else 'CNY',party=party or ('客户' if kind=='receipt' else '工厂甲'),platform='银行',fx=1,purpose='验收',reference=uuid.uuid4().hex,created_by=self.admin)

    def test_grouped_form_and_contextual_finance_labels(self):
        self.assertContains(self.client.get('/orders/new/'),'客户与负责人')
        response=self.client.get(f'/orders/{self.order.id}/lines/new/')
        for text in ['产品与数量','销售报价与运费','供应商与结算约定','保存并继续新增','金额预览']:
            self.assertContains(response,text)
        form=TransactionForm(kind='payment')
        self.assertEqual(form.fields['party'].label,'供应商名称')
        self.assertEqual(form.target_currency,'CNY')

    def test_save_and_add_another_line(self):
        data={'label':'002','product':'新产品','model':'D2','date':'2026-10-23','quantity':10,'price':10,'currency':'USD','quote_fx':7,'cost':4,'supplier':'工厂甲','freight':0,'customer_terms':'全款','supplier_terms':'全款','action':'another'}
        response=self.client.post(f'/orders/{self.order.id}/lines/new/',data)
        self.assertRedirects(response,f'/orders/{self.order.id}/lines/new/')
        response=self.client.get(f'/orders/{self.order.id}/lines/new/')
        self.assertEqual(response.context['form']['label'].value(),'003')

    def test_completed_allocation_has_clear_next_step(self):
        t=self.txn();allocate(self.admin,t.id,self.line.id,100,1,'预付')
        response=self.client.get(f'/transactions/{t.id}/allocate/')
        self.assertContains(response,'这笔流水已全部分摊')
        self.assertNotContains(response,'id="allocation-form"')

    def test_payment_only_offers_matching_unsettled_supplier_lines(self):
        other=Line.objects.create(order=self.order,label='002',product='其他',model='D2',quantity=100,price=10,cost=40,supplier='工厂乙')
        t=self.txn(kind='payment')
        form=AllocationForm(order=self.order,txn=t)
        self.assertIn(self.line.id,list(form.fields['line'].queryset.values_list('id',flat=True)))
        self.assertNotIn(other.id,list(form.fields['line'].queryset.values_list('id',flat=True)))
        self.assertEqual(form.fields['amount'].initial,100)

    def test_allocation_metadata_keeps_only_debt_batches(self):
        s=confirm_shipment(self.admin,self.order.id,{self.line.id:10},{'date':date.today(),'address':'测试地址','carrier_type':'own','carrier':'测试货代','freight':0},uuid.uuid4())
        t=self.txn()
        form=AllocationForm(order=self.order,txn=t)
        self.assertEqual(form.batch_metadata[str(s.items.get().id)]['line'],str(self.line.id))
        self.assertEqual(form.line_metadata[str(self.line.id)]['remaining'],90)

    def test_finance_pending_filter_uses_whole_transaction_balance(self):
        partial=self.txn(100);allocate(self.admin,partial.id,self.line.id,50,1,'部分')
        full=self.txn(100);allocate(self.admin,full.id,self.line.id,100,1,'全额')
        response=self.client.get('/finance/',{'allocation':'pending','q':self.line.model})
        self.assertContains(response,partial.reference)
        self.assertNotContains(response,full.reference)

    def test_reverse_date_range_explained(self):
        self.txn()
        response=self.client.get('/finance/',{'start':'2026-10-30','end':'2026-10-01'})
        self.assertContains(response,'开始日期不能晚于结束日期')
        self.assertEqual(response.context['result_count'],0)

    def test_pagination_preserves_search(self):
        Order.objects.bulk_create([Order(number=f'DETAIL-{i:03d}',customer='分页客户',owner=self.sales) for i in range(2,25)])
        response=self.client.get('/orders/',{'q':'DETAIL','page':'1'})
        self.assertEqual(response.context['result_count'],24)
        self.assertEqual(len(response.context['cards']),20)
        self.assertContains(response,'q=DETAIL&amp;page=2')
        self.assertEqual(len(self.client.get('/orders/',{'q':'DETAIL','page':'2'}).context['cards']),4)
        self.assertEqual(self.client.get('/orders/',{'page':'not-a-number'}).status_code,200)

    def test_filtered_empty_state(self):
        response=self.client.get('/orders/',{'q':'no-such-order'})
        self.assertContains(response,'没有符合条件的订单')
        self.assertContains(response,'清除筛选')

    def test_blank_order_not_shown_as_paid(self):
        Order.objects.create(number='EMPTY',customer='空订单',owner=self.sales)
        self.assertContains(self.client.get('/orders/',{'q':'EMPTY'}),'待录入产品')

    def test_money_display_precision(self):
        self.assertEqual(price(Decimal('10.1234')),'10.1234')
        self.assertEqual(pct(Decimal('1'),Decimal('3')),'33.3')

    def test_small_unpaid_balance_is_not_rounded_to_complete(self):
        from .services import percent
        self.assertEqual(pct(Decimal('999.99'),Decimal('1000')),'99.9')
        self.assertEqual(percent(Decimal('999.99'),Decimal('1000')),99.9)
        self.assertEqual(percent(Decimal('1000'),Decimal('1000')),100.0)

    def test_summary_queries_remain_bounded_with_many_lines(self):
        Line.objects.bulk_create([Line(order=self.order,label=f'L{i}',product='产品',model='M',quantity=100,price=10,cost=40,supplier='工厂甲') for i in range(40)])
        for url in ['/','/orders/','/finance/']:
            with CaptureQueriesContext(connection) as queries:
                response=self.client.get(url)
            self.assertEqual(response.status_code,200)
            self.assertLessEqual(len(queries),12,f'{url} issued {len(queries)} queries')

    def test_missing_voucher_is_explained_instead_of_server_error(self):
        t=self.txn();t.attachment='not-existing/file.pdf';t.save()
        response=self.client.get(f'/transactions/{t.id}/attachment/',follow=True)
        self.assertContains(response,'凭证文件暂不可用')
        response=self.client.get(f'/orders/{self.order.id}/export/',follow=True)
        self.assertContains(response,'已停止导出')

    def test_missing_voucher_cannot_create_incomplete_archive(self):
        r=self.txn(1000);allocate(self.admin,r.id,self.line.id,1000,1,'全款')
        p=self.txn(4000,'payment');allocate(self.admin,p.id,self.line.id,4000,1,'全款')
        confirm_shipment(self.admin,self.order.id,{self.line.id:100},{'date':date.today(),'address':'测试地址','carrier_type':'own','carrier':'测试货代','freight':0},uuid.uuid4())
        r.attachment='not-existing/file.pdf';r.save()
        response=self.client.post(f'/orders/{self.order.id}/lock/',follow=True)
        self.assertContains(response,'已停止导出')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status,'active')
        self.assertFalse(self.order.archives.exists())
        self.assertFalse(self.order.audits.filter(action='锁定归档').exists())

    def test_readable_permission_and_not_found_pages(self):
        self.assertContains(self.client.get('/nothing-here/'),'页面或记录不可访问',status_code=404)
        self.client.force_login(self.sales)
        self.assertContains(self.client.get('/team/'),'暂无访问权限',status_code=403)

    def test_detail_queries_bounded_across_batches_and_audits(self):
        from .services import audit
        t=self.txn(500)
        allocate(self.admin,t.id,self.line.id,500,1,'预付')
        for i in range(5):
            confirm_shipment(self.admin,self.order.id,{self.line.id:10},{'date':date.today(),'address':'测试地址','carrier_type':'own','carrier':'测试货代','freight':0},uuid.uuid4())
        for i in range(30):
            audit(self.order,self.admin,'查询验收',str(i))
        with CaptureQueriesContext(connection) as queries:
            response=self.client.get(f'/orders/{self.order.id}/')
        self.assertEqual(response.status_code,200)
        self.assertLessEqual(len(queries),15)
