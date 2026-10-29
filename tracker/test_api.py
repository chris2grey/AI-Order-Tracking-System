import json
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from .models import Order, Line, Transaction, Allocation, Shipment, Audit, Archive


class ApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('api-admin', password='Fictional-api-test-71', is_superuser=True)
        cls.alice = User.objects.create_user('api-alice', password='Fictional-api-test-71')
        cls.bob = User.objects.create_user('api-bob', password='Fictional-api-test-71')
        cls.order = Order.objects.create(number='API-001', customer='Fictional Customer', owner=cls.alice)
        cls.other = Order.objects.create(number='API-PRIVATE', customer='Other Customer', owner=cls.bob)
        cls.line = Line.objects.create(order=cls.order, label='001', product='Widget', model='A', quantity=3,
                                       price='10.0000', cost='0.0000', supplier='Factory', currency='USD')
        cls.hidden_line = Line.objects.create(order=cls.other, label='001', product='Private', model='Z', quantity=2,
                                              price='1', cost='1', supplier='Private Factory')

    def setUp(self):
        cache.clear()
        self.client.force_login(self.admin)

    def post(self, path, data, key=None):
        return self.client.post('/api/v1/' + path, data=json.dumps(data), content_type='application/json',
                                HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))

    def receipt(self, amount='10.00', reference=None):
        return self.post(f'orders/{self.order.pk}/transactions/', {
            'kind': 'receipt', 'date': '2026-10-28', 'amount': amount, 'currency': 'USD',
            'party': 'Fictional Customer', 'platform': 'Bank', 'fx': '1', 'fx_source': 'manual',
            'purpose': 'test deposit', 'reference': reference or uuid.uuid4().hex,
        })

    def allocation(self, txn, amount='10.00', key=None, **extra):
        return self.post(f'transactions/{txn}/allocations/', {
            'line': self.line.pk, 'amount': amount, 'rate': '1', 'reason': 'test allocation', **extra}, key)

    def shipping(self, quantity=1, **extra):
        return {'token': str(uuid.uuid4()), 'date': '2026-10-28', 'address': 'Fictional address',
                'carrier_type': 'own', 'carrier': 'Test carrier', 'freight': '0.00', 'freight_currency': 'CNY',
                'quantities': {str(self.line.pk): quantity}, **extra}

    def test_anonymous_is_json_401(self):
        self.client.logout()
        response = self.client.get('/api/v1/orders/')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error']['code'], 'authentication_required')

    def test_session_login_rotates_csrf_and_logout_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        token = client.get('/api/v1/auth/session/').json()['csrf_token']
        response = client.post('/api/v1/auth/login/', data=json.dumps({'username': self.alice.username, 'password': 'Fictional-api-test-71'}),
                               content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['code'], 'csrf_failed')
        response = client.post('/api/v1/auth/login/', data=json.dumps({'username': self.alice.username, 'password': 'Fictional-api-test-71'}),
                               content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.json()['csrf_token'], token)
        self.assertEqual(client.get('/api/v1/orders/').status_code, 200)
        response = client.post('/api/v1/auth/logout/', HTTP_X_CSRFTOKEN=response.json()['csrf_token'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get('/api/v1/orders/').status_code, 401)

    def test_login_limits_shared_with_web(self):
        self.client.logout()
        for _ in range(8):
            self.assertEqual(self.post('auth/login/', {'username': 'bad-user', 'password': 'bad-password'}).status_code, 401)
        self.assertEqual(self.client.post('/login/', {'username': 'bad-user', 'password': 'bad-password'}).status_code, 429)
        self.assertEqual(self.post('auth/login/', {'username': 'bad-user', 'password': 'bad-password'}).status_code, 429)

    def test_lists_and_search_are_owner_scoped(self):
        self.client.force_login(self.alice)
        response = self.client.get('/api/v1/orders/')
        self.assertEqual([o['id'] for o in response.json()['results']], [self.order.pk])
        self.assertEqual(self.client.get('/api/v1/orders/?q=PRIVATE').json()['count'], 0)
        for suffix in ['', 'lines/', 'transactions/', 'shipments/', 'export/']:
            self.assertEqual(self.client.get(f'/api/v1/orders/{self.other.pk}/{suffix}').status_code, 404)

    def test_amounts_are_strings_and_multicurrency_totals_separate(self):
        Line.objects.create(order=self.order, label='002', product='Euro', model='E', quantity=1, price='7.1234', cost='2', supplier='Factory', currency='EUR')
        result = self.client.get(f'/api/v1/orders/{self.order.pk}/').json()
        self.assertEqual(result['totals']['currencies']['USD']['sale'], '30.00')
        self.assertEqual(result['totals']['currencies']['EUR']['sale'], '7.12')
        self.assertEqual(self.client.get(f'/api/v1/orders/{self.order.pk}/lines/').json()['results'][0]['price'], '10.0000')

    def test_pagination_and_filters_validate(self):
        for query in ['page=0', 'page=999', 'page_size=101', 'page=bad', 'status=deleted']:
            self.assertEqual(self.client.get('/api/v1/orders/?' + query).status_code, 400)
        self.assertEqual(len(self.client.get('/api/v1/orders/?page_size=1').json()['results']), 1)

    def test_create_order_owner_protected_and_replay(self):
        self.client.force_login(self.alice)
        data = {'number': 'API-NEW', 'customer': 'New fictional', 'date': '2026-10-28', 'owner': self.bob.pk}
        self.assertEqual(self.post('orders/', data).status_code, 403)
        data.pop('owner')
        key = str(uuid.uuid4())
        first = self.post('orders/', data, key)
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(first.json()['owner_id'], self.alice.pk)
        replay = self.post('orders/', data, key)
        self.assertEqual(replay.json(), first.json())
        self.assertEqual(replay['Idempotency-Replayed'], 'true')
        self.assertEqual(Order.objects.filter(number='API-NEW').count(), 1)
        data['customer'] = 'Changed'
        self.assertEqual(self.post('orders/', data, key).status_code, 409)

    def test_replayed_write_rechecks_owner(self):
        self.client.force_login(self.alice)
        key = str(uuid.uuid4())
        data = {'number': 'API-TRANSFER', 'customer': 'Fictional', 'date': '2026-10-28'}
        first = self.post('orders/', data, key)
        Order.objects.filter(pk=first.json()['id']).update(owner=self.bob)
        self.assertEqual(self.post('orders/', data, key).status_code, 404)

    def test_key_required_and_financial_records_cannot_be_edited(self):
        response = self.client.post('/api/v1/orders/', data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], 'idempotency_key_required')
        self.assertEqual(self.client.delete(f'/api/v1/orders/{self.order.pk}/').status_code, 405)
        self.assertEqual(self.client.patch(f'/api/v1/orders/{self.order.pk}/').status_code, 405)

    def test_invalid_json_unknown_fields_and_nonfinite_values(self):
        for body in ['[1]', '{broken', '{"price": NaN}', '{"price": Infinity}']:
            response = self.client.post('/api/v1/orders/', data=body, content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.post('orders/', {'is_superuser': True}).status_code, 400)
        self.assertEqual(self.receipt(amount='NaN').status_code, 400)

    def test_duplicate_model_supplements_and_locked_order(self):
        data = {'label': '002', 'product': 'Widget', 'model': 'A', 'date': '2026-10-28', 'quantity': 5,
                'price': '11.1250', 'currency': 'USD', 'quote_fx': '7', 'cost': '2.1234', 'supplier': 'Factory',
                'freight_included': True, 'freight': '1.00', 'customer_terms': 'test', 'supplier_terms': 'test'}
        key=str(uuid.uuid4())
        self.assertEqual(self.post(f'orders/{self.order.pk}/lines/', data,key).status_code, 201)
        self.assertEqual(self.order.lines.count(), 2)
        self.assertEqual(self.post(f'orders/{self.order.pk}/lines/', data,key).status_code, 201)
        self.assertEqual(self.order.lines.count(), 2)
        self.order.status = 'locked'
        self.order.save()
        data['label'] = '003'
        self.assertEqual(self.post(f'orders/{self.order.pk}/lines/', data).status_code, 400)
        self.assertEqual(self.receipt().status_code, 400)

    def test_financial_write_permission_and_duplicate_reference(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.receipt().status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.receipt(reference='unique-ref').status_code, 201)
        self.assertEqual(self.receipt(reference='unique-ref').status_code, 400)
        self.assertEqual(self.order.transactions.count(), 1)

    def test_allocation_idempotency_overallocation_and_cross_order(self):
        txn = self.receipt('20.00').json()['id']
        key = str(uuid.uuid4())
        self.assertEqual(self.allocation(txn, '12.00', key).status_code, 201)
        self.assertEqual(self.allocation(txn, '12.00', key).status_code, 201)
        self.assertEqual(Allocation.objects.count(), 1)
        self.assertEqual(self.allocation(txn, '12.00', key).get('Idempotency-Replayed'), 'true')
        self.assertEqual(self.allocation(txn, '13.00', key).status_code, 409)
        self.assertEqual(self.allocation(txn, '9.00').status_code, 400)
        self.assertEqual(self.allocation(txn, '1.00', line=self.hidden_line.pk).status_code, 400)
        self.assertEqual(self.allocation(txn, '1.00', rate='2').status_code, 400)
        self.client.force_login(self.alice)
        self.assertEqual(self.allocation(txn, '1.00').status_code, 403)

    def test_shipment_preview_no_mutation_and_input_validation(self):
        for qty in [-1, 0, 1.5, '1', True, 4]:
            response = self.post(f'orders/{self.order.pk}/shipments/preview/', {'quantities': {str(self.line.pk): qty}})
            self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(Shipment.objects.count(), 0)
        response = self.post(f'orders/{self.order.pk}/shipments/preview/', {'quantities': {str(self.line.pk): 1}})
        self.assertEqual(response.json()['items'][0]['receipt']['due'], '10.00')
        self.assertEqual(Shipment.objects.count(), 0)

    def test_rounding_batch_tail_and_fund_conservation(self):
        txn = self.receipt('10.00').json()['id']
        self.assertEqual(self.allocation(txn).status_code, 201)
        data = self.shipping()
        key = str(uuid.uuid4())
        first = self.post(f'orders/{self.order.pk}/shipments/', data, key)
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(first.json()['items'][0]['receipt_reserved'], '3.33')
        self.assertEqual(self.post(f'orders/{self.order.pk}/shipments/', data, key).json(), first.json())
        self.assertEqual(Shipment.objects.count(), 1)
        # Product allocation automatically covers the shipped batch first.
        tail = self.receipt('6.67').json()['id']
        self.assertEqual(self.allocation(tail, '6.67').status_code, 201)
        self.assertEqual(self.allocation(tail, '6.67', shipment_item=first.json()['items'][0]['id']).status_code, 400)
        second = self.post(f'orders/{self.order.pk}/shipments/', self.shipping(2))
        self.assertEqual(second.json()['items'][0]['receipt_reserved'], '6.67')
        self.assertEqual(second.json()['items'][0]['receivable'], '13.33')
        self.assertEqual(self.post(f'orders/{self.order.pk}/shipments/', self.shipping()).status_code, 400)

    def test_archive_requires_completion_and_admin(self):
        self.assertEqual(self.post(f'orders/{self.order.pk}/lock/', {}).status_code, 400)
        self.client.force_login(self.alice)
        self.assertEqual(self.post(f'orders/{self.order.pk}/lock/', {}).status_code, 403)
        self.client.force_login(self.admin)
        txn = self.receipt('30.00').json()['id']
        self.assertEqual(self.allocation(txn, '30.00').status_code, 201)
        self.assertEqual(self.post(f'orders/{self.order.pk}/shipments/', self.shipping(3)).status_code, 201)
        response = self.post(f'orders/{self.order.pk}/lock/', {})
        self.assertEqual(response.status_code, 200, response.content)
        url = response.json()['download_url']
        self.assertEqual(len(response.json()['sha256']), 64)
        download = self.client.get(url)
        self.assertEqual(download.status_code, 200)
        # Consume Django's streaming wrapper so request_finished is handled safely.
        self.assertTrue(b''.join(download.streaming_content).startswith(b'PK'))
        self.client.force_login(self.bob)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_private_attachment_validation_download_and_owner_scope(self):
        with tempfile.TemporaryDirectory() as temp, override_settings(MEDIA_ROOT=temp):
            txn = self.receipt().json()['id']
            item = Transaction.objects.get(pk=txn)
            item.attachment.save('test.pdf', SimpleUploadedFile('test.pdf', b'%PDF-fictional'))
            url = f'/api/v1/transactions/{txn}/attachment/'
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(temp, response['Content-Disposition'])
            self.assertEqual(b''.join(response.streaming_content), b'%PDF-fictional')
            self.client.force_login(self.bob)
            self.assertEqual(self.client.get(url).status_code, 404)
            self.client.force_login(self.admin)
            Path(item.attachment.path).unlink()
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_multipart_upload_and_retry(self):
        with tempfile.TemporaryDirectory() as temp, override_settings(MEDIA_ROOT=temp):
            data = {'kind': 'receipt', 'date': '2026-10-28', 'amount': '1.00', 'currency': 'USD', 'party': 'Test',
                    'platform': 'Bank', 'fx': '1', 'fx_source': 'manual', 'purpose': 'test', 'reference': 'upload-ref'}
            url = f'/api/v1/orders/{self.order.pk}/transactions/'
            key = str(uuid.uuid4())
            for _ in range(2):
                response = self.client.post(url, {**data, 'attachment': SimpleUploadedFile('test.pdf', b'%PDF-fictional')}, HTTP_IDEMPOTENCY_KEY=key)
                self.assertEqual(response.status_code, 201, response.content)
            self.assertEqual(self.order.transactions.count(), 1)
            self.assertEqual(len(list(Path(temp).rglob('*.pdf'))), 1)
            data['reference'] = 'bad-signature'
            response = self.client.post(url, {**data, 'attachment': SimpleUploadedFile('bad.pdf', b'not-a-pdf')}, HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
            self.assertEqual(response.status_code, 400)

    def test_health_and_admin_system_page(self):
        self.assertEqual(self.client.get('/health/live/').json(), {'status': 'ok'})
        self.assertEqual(self.client.get('/health/ready/').status_code, 200)
        with patch('tracker.api.cache.set', side_effect=RuntimeError('fictional failure')):
            self.assertEqual(self.client.get('/health/ready/').status_code, 503)
        self.assertEqual(self.client.get('/system/').status_code, 200)
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get('/system/').status_code, 403)

    @override_settings(TRUST_PROXY=True)
    def test_proxy_uses_verified_real_ip(self):
        self.client.logout()
        for index in range(41):
            response = self.client.post('/api/v1/auth/login/', data=json.dumps({'username': f'test-{index}', 'password': 'bad'}),
                                        content_type='application/json', HTTP_X_REAL_IP='192.0.2.1')
        self.assertEqual(response.status_code, 429)
        response = self.client.post('/api/v1/auth/login/', data=json.dumps({'username': 'another', 'password': 'bad'}),
                                    content_type='application/json', HTTP_X_REAL_IP='192.0.2.2')
        self.assertEqual(response.status_code, 401)
