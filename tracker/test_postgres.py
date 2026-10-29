"""Run with compose.test.yaml against PostgreSQL, never claim SQLite proves locks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest import skipUnless
import json
import uuid

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase

from .models import Order, Line, Transaction, Allocation, Shipment
from .services import allocate, confirm_shipment, correct_transaction


@skipUnless(connection.vendor == 'postgresql', '需要 PostgreSQL 实例验证行锁')
class PostgresConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user('pg-test-admin', password='Fictional-pg-test-71', is_superuser=True)
        self.order = Order.objects.create(number='PG-CONCURRENCY', customer='Fictional', owner=self.user)
        self.line = Line.objects.create(order=self.order, label='001', product='Test', model='A', quantity=3,
                                       price='10', cost='0', supplier='Test Factory')

    def race(self, action):
        barrier = Barrier(2)

        def run():
            close_old_connections()
            try:
                user = User.objects.get(pk=self.user.pk)
                barrier.wait(timeout=10)
                try:
                    return action(user)
                except ValidationError:
                    return 'rejected'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run) for _ in range(2)]
            return [future.result(timeout=30) for future in futures]

    def test_concurrent_allocations_do_not_double_spend(self):
        txn = Transaction.objects.create(order=self.order, kind='receipt', amount='10', currency='USD', party='Fictional',
                                          platform='Bank', purpose='Test', reference='pg-race', created_by=self.user)
        results = self.race(lambda user: allocate(user, txn.pk, self.line.pk, '10', '1', 'Concurrent test').pk)
        self.assertEqual(results.count('rejected'), 1)
        self.assertEqual(Allocation.objects.count(), 1)
        self.assertEqual(Transaction.objects.get(pk=txn.pk).unallocated, 0)

    def test_concurrent_shipments_do_not_overship(self):
        data = {'date': date(2026, 10, 28), 'address': 'Fictional', 'carrier_type': 'own', 'carrier': 'Test', 'freight': 0}
        results = self.race(lambda user: confirm_shipment(user, self.order.pk, {self.line.pk: 3}, data, uuid.uuid4()).pk)
        self.assertEqual(results.count('rejected'), 1)
        self.assertEqual(Shipment.objects.count(), 1)
        self.assertEqual(self.line.shipment_items.get().quantity, 3)

    def test_concurrent_retries_create_one_committed_response(self):
        key = str(uuid.uuid4())

        def action(user):
            client = Client()
            client.force_login(user)
            response = client.post('/api/v1/orders/', data=json.dumps({'number': 'PG-IDEMPOTENT', 'customer': 'Fictional',
                                   'date': '2026-10-28', 'owner': user.pk}), content_type='application/json', HTTP_IDEMPOTENCY_KEY=key)
            return response.status_code, response.json()['id']

        results = self.race(action)
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][0], 201)
        self.assertEqual(Order.objects.filter(number='PG-IDEMPOTENT').count(), 1)

    def test_concurrent_automatic_allocations_do_not_double_cover_batch(self):
        ship=confirm_shipment(self.user,self.order.pk,{self.line.pk:3},
            {'date':date.today(),'address':'Fictional','carrier_type':'own','carrier':'Test'},uuid.uuid4())
        txn=Transaction.objects.create(order=self.order,kind='receipt',amount=30,currency='USD',party='Fictional',platform='Test',reference=uuid.uuid4().hex,created_by=self.user)
        results=self.race(lambda user: allocate(user,txn.pk,self.line.pk,30,1,'Auto race',auto_batches=True).pk)
        self.assertEqual(results.count('rejected'),1)
        self.assertEqual(txn.allocations.count(),1)
        self.assertEqual(ship.items.get().due('receipt'),0)
        self.assertEqual(txn.unallocated,0)

    def test_concurrent_voids_create_only_one_reversing_entry(self):
        txn=Transaction.objects.create(order=self.order,kind='receipt',amount=30,currency='USD',party='Fictional',platform='Test',reference=uuid.uuid4().hex,created_by=self.user)
        results=self.race(lambda user: correct_transaction(user,txn.pk,'void','','Mistake race').pk)
        self.assertEqual(results.count('rejected'),1)
        self.assertEqual(txn.corrections.count(),1)
        self.assertEqual(txn.unallocated,0)
