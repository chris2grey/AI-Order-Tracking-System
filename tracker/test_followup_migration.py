from decimal import Decimal
import uuid
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class FollowupMigrationTests(TransactionTestCase):
    def test_legacy_labels_accounts_finances_and_zero_freight_are_preserved(self):
        before=[('tracker','0003_entry_workflow')]
        after=[('tracker','0004_workflow_followup')]
        executor=MigrationExecutor(connection)
        executor.migrate(before)
        apps=executor.loader.project_state(before).apps
        try:
            User=apps.get_model('auth','User')
            Order=apps.get_model('tracker','Order')
            Line=apps.get_model('tracker','Line')
            Transaction=apps.get_model('tracker','Transaction')
            Shipment=apps.get_model('tracker','Shipment')
            ShipmentLine=apps.get_model('tracker','ShipmentLine')
            Allocation=apps.get_model('tracker','Allocation')
            owner=User.objects.create(username='migration-fictional',password='test-only-hash')
            order=Order.objects.create(number='MIGRATION-FAKE',customer='虚构迁移客户',owner_id=owner.pk)
            a=Line.objects.create(order_id=order.pk,label='004',product='虚构一',model='FAKE',quantity=10,price=10,cost=4,supplier='虚构工厂')
            b=Line.objects.create(order_id=order.pk,label='1',product='虚构二',model='FAKE',quantity=10,price=10,cost=4,supplier='虚构工厂')
            txn=Transaction.objects.create(order_id=order.pk,kind='receipt',amount=20,currency='USD',party='虚构公司',platform='虚构银行',fx=1,purpose='旧用途',reference='fake-migration',created_by_id=owner.pk)
            allocation=Allocation.objects.create(transaction_id=txn.pk,line_id=a.pk,source_amount=20,settlement_rate=1,target_amount=20,reason='虚构旧分摊',created_by_id=owner.pk)
            ship=Shipment.objects.create(order_id=order.pk,reference=uuid.uuid4(),address='虚构地址',carrier_type='own',carrier='虚构',freight=0,created_by_id=owner.pk)
            item=ShipmentLine.objects.create(shipment_id=ship.pk,line_id=a.pk,quantity=5,sale_amount=50,cost_amount=20,receipt_reserved=10,payment_reserved=0,internal_cost_amount=20)
            executor=MigrationExecutor(connection);executor.migrate(after)
            apps=executor.loader.project_state(after).apps
            Line=apps.get_model('tracker','Line')
            self.assertEqual(list(Line.objects.filter(order_id=order.pk).values_list('id','label','sequence')),[(a.pk,'004',1),(b.pk,'1',2)])
            self.assertEqual(apps.get_model('auth','User').objects.get(pk=owner.pk).password,'test-only-hash')
            self.assertEqual(apps.get_model('tracker','Transaction').objects.get(pk=txn.pk).amount,Decimal('20'))
            self.assertEqual(apps.get_model('tracker','Allocation').objects.get(pk=allocation.pk).target_amount,Decimal('20'))
            self.assertEqual(apps.get_model('tracker','ShipmentLine').objects.get(pk=item.pk).receipt_reserved,Decimal('10'))
            self.assertEqual(apps.get_model('tracker','Shipment').objects.get(pk=ship.pk).freight,Decimal('0'))
        finally:
            MigrationExecutor(connection).migrate(after)
