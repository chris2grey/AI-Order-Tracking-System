"""Versioned same-origin JSON API, sharing the web application's accounting rules."""
import hashlib
import json
import logging
import uuid
from functools import wraps

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError, RequestDataTooBig
from django.core.paginator import Paginator, EmptyPage
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.http import JsonResponse, Http404
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404

from .forms import OrderForm, LineForm, TransactionForm, AllocationForm, ShipmentForm
from .models import Order, Transaction, Shipment, ApiMutation
from .services import visible_orders, with_order_data, authorize, summary, audit, allocate, confirm_shipment, shipment_quote, rate_for, remember_rate
from .throttle import login_limited

logger = logging.getLogger(__name__)


def error(code, message, status=400, fields=None):
    return JsonResponse({'error': {'code': code, 'message': message, 'fields': fields or {}}}, status=status)


def payload(request):
    if hasattr(request, '_api_payload'):
        return request._api_payload
    if request.content_type == 'application/json':
        def invalid_constant(value):
            raise ValueError('non-finite JSON number')
        try:
            data = json.loads(request.body, parse_float=str, parse_constant=invalid_constant)
        except (ValueError, UnicodeError):
            raise ValidationError('请求体必须是有效 JSON。')
        if not isinstance(data, dict):
            raise ValidationError('JSON 顶层必须为对象。')
    elif request.content_type == 'multipart/form-data':
        data = request.POST.dict()
    else:
        raise ValidationError('请使用 application/json，上传凭证使用 multipart/form-data。')
    request._api_payload = data
    return data


def validate_form(form):
    if not form.is_valid():
        raise ValidationError({name: [item['message'] for item in items]
                               for name, items in form.errors.get_json_data().items()})
    return form.cleaned_data


def only_fields(data, names):
    extra = set(data) - set(names)
    if extra:
        raise ValidationError('不支持的字段：' + ', '.join(sorted(extra)))


def fingerprint(request):
    digest = hashlib.sha256(json.dumps(payload(request), sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode())
    for name in sorted(request.FILES):
        upload = request.FILES[name]
        digest.update(json.dumps([name, upload.name, upload.size]).encode())
        for chunk in upload.chunks():
            digest.update(chunk)
        upload.seek(0)
    return digest.hexdigest()


def endpoint(methods=('GET',), public=False, mutation=False):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method not in methods:
                response = error('method_not_allowed', '此接口不支持该请求方法。', 405)
                response['Allow'] = ', '.join(methods)
                return response
            if not public and (not request.user.is_authenticated or not request.user.is_active):
                return error('authentication_required', '请先登录。', 401)
            try:
                if mutation and request.method != 'GET':
                    try:
                        key = uuid.UUID(request.headers.get('Idempotency-Key', ''))
                    except (ValueError, AttributeError):
                        return error('idempotency_key_required', '写入接口必须携带 UUID 格式的 Idempotency-Key。')
                    digest = fingerprint(request)
                    with transaction.atomic():
                        # Serialize retries for this account, then lock the business order.
                        account = get_user_model().objects.select_for_update(no_key=True).get(pk=request.user.pk)
                        if not account.is_active:
                            raise PermissionDenied
                        request.user = account
                        previous = ApiMutation.objects.filter(user=account, key=key).first()
                        if previous:
                            if previous.operation != request.path or previous.digest != digest:
                                return error('idempotency_conflict', '此请求编号已经用于其他内容，请核对后使用新的编号。', 409)
                            # Recheck current ownership even when returning a committed response.
                            order_id = previous.response.get('order_id')
                            if order_id:
                                get_object_or_404(visible_orders(account), pk=order_id)
                            response = JsonResponse(previous.response, status=previous.status_code)
                            response['Idempotency-Replayed'] = 'true'
                            return response
                        response = view(request, *args, **kwargs)
                        if response.status_code < 400:
                            ApiMutation.objects.create(user=account, key=key, operation=request.path, digest=digest,
                                                       response=json.loads(response.content), status_code=response.status_code)
                        return response
                return view(request, *args, **kwargs)
            except ValidationError as exc:
                fields = exc.message_dict if hasattr(exc, 'message_dict') else {}
                return error('validation_error', '；'.join(exc.messages), fields=fields)
            except PermissionDenied:
                return error('permission_denied', '当前账号无权执行此操作。', 403)
            except Http404:
                return error('not_found', '记录不存在或无权访问。', 404)
            except IntegrityError:
                return error('conflict', '记录编号重复或数据冲突，请核对后重试。', 409)
            except RequestDataTooBig:
                return error('payload_too_large', '请求内容过大。', 413)
            except Exception:
                logger.exception('API request failed: %s %s', request.method, request.path)
                return error('server_error', '暂时无法完成操作，请稍后重试。', 500)
        return wrapped
    return decorate


def accessible(user, pk, write=False, admin=False):
    queryset = visible_orders(user)
    if write:
        queryset = queryset.select_for_update()
    order = get_object_or_404(queryset, pk=pk)
    authorize(user, order, write=write, admin=admin)
    return order


def values(instance, names):
    return {name: getattr(instance, name) for name in names.split()}


def order_data(order):
    s = summary(order)
    return {**values(order, 'id number customer owner_id date notes status created_at'),
            'totals': {**{k: s[k] for k in ['currencies', 'cost', 'external_cost', 'internal_cost', 'payment', 'payable', 'quantity', 'shipped', 'complete']},
                       'unallocated_count': len(s['unallocated'])}}


def line_data(line):
    return {**values(line, 'id order_id label sequence number product model date quantity price currency quote_fx cost supply_type supplier freight_included freight customer_terms supplier_terms'),
            'sale_total': line.sale_total, 'cost_total': line.cost_total, 'payable_total': line.payable_total}


def allocation_data(item):
    return {**values(item, 'id line_id transaction_id shipment_item_id source_amount settlement_rate target_amount reason created_by_id created_at'),
            'batch_coverages': [values(c, 'shipment_item_id amount') for c in item.coverages.all()]}


def transaction_data(item):
    return {**values(item, 'id order_id kind date amount currency party platform fx fx_source purpose reference created_by_id created_at'),
            'effective_party': item.effective_party, 'effective_amount': item.effective_amount, 'is_void': item.is_void,
            'corrections': [values(c, 'id action party source_delta reason created_by_id created_at') for c in item.corrections.all()],
            'equivalent': item.equivalent, 'target_currency': item.target_currency, 'unallocated': item.unallocated,
            'attachment_url': f'/api/v1/transactions/{item.pk}/attachment/' if item.attachment else None,
            'allocations': [allocation_data(a) for a in item.allocations.all()]}


def shipment_data(ship):
    return {**values(ship, 'id order_id reference date address carrier_type carrier domestic_date domestic_tracking international_tracking freight freight_currency notes created_by_id'),
            'current_freight': ship.current_freight, 'current_freight_currency': ship.current_freight_currency,
            'freight_records': [values(r, 'id amount currency reason created_by_id created_at') for r in ship.freight_records.all()],
            'items': [{**values(i, 'id line_id quantity sale_amount cost_amount internal_cost_amount receipt_reserved payment_reserved'),
                       'receivable': i.due('receipt'), 'payable': i.due('payment')} for i in ship.items.all()]}


def paginated(request, queryset, serialize):
    try:
        page_number = int(request.GET.get('page', '1'))
        size = int(request.GET.get('page_size', '20'))
        if page_number < 1 or not 1 <= size <= 100:
            raise ValueError
        page = Paginator(queryset, size).page(page_number)
    except (ValueError, EmptyPage):
        raise ValidationError('分页参数无效：page 从1开始，page_size 为1至100。')
    return JsonResponse({'count': page.paginator.count, 'page': page.number, 'page_size': size,
                         'pages': page.paginator.num_pages, 'results': [serialize(o) for o in page]})


@endpoint(public=True)
def session(request):
    user = request.user
    return JsonResponse({'csrf_token': get_token(request), 'authenticated': user.is_authenticated,
                         'user': {'id': user.pk, 'username': user.username, 'is_admin': user.is_superuser} if user.is_authenticated else None})


@endpoint(('POST',), public=True)
def sign_in(request):
    data = payload(request)
    only_fields(data, ['username', 'password'])
    if not isinstance(data.get('username'), str) or not isinstance(data.get('password'), str):
        raise ValidationError('请填写账号和密码。')
    if login_limited(request, data['username']):
        response = error('rate_limited', '尝试次数过多，请10分钟后重试。', 429)
        response['Retry-After'] = '600'
        return response
    user = authenticate(request, username=data['username'], password=data['password'])
    if user is None:
        return error('invalid_credentials', '账号或密码不正确。', 401)
    login(request, user)
    return JsonResponse({'csrf_token': get_token(request), 'user': {'id': user.pk, 'username': user.username, 'is_admin': user.is_superuser}})


@endpoint(('POST',))
def sign_out(request):
    logout(request)
    return JsonResponse({'signed_out': True})


@endpoint(('GET', 'POST'), mutation=True)
def orders(request):
    if request.method == 'GET':
        qs = visible_orders(request.user)
        q = request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(Q(number__icontains=q) | Q(customer__icontains=q) | Q(lines__model__icontains=q) | Q(lines__product__icontains=q)).distinct()
        status = request.GET.get('status')
        if status:
            if status not in ('active', 'locked'):
                raise ValidationError('订单状态无效。')
            qs = qs.filter(status=status)
        return paginated(request, with_order_data(qs), order_data)
    data = payload(request)
    only_fields(data, OrderForm.Meta.fields)
    if not request.user.is_superuser and data.get('owner') not in (None, request.user.pk, str(request.user.pk)):
        raise PermissionDenied
    form = OrderForm(data, user=request.user)
    validate_form(form)
    order = form.save()
    audit(order, request.user, '创建订单', 'API')
    return JsonResponse({**order_data(order), 'order_id': order.pk}, status=201)


@endpoint()
def order_detail(request, pk):
    return JsonResponse(order_data(accessible(request.user, pk)))


@endpoint(('GET', 'POST'), mutation=True)
def lines(request, pk):
    order = accessible(request.user, pk, write=request.method == 'POST')
    if request.method == 'GET':
        return paginated(request, order.lines.all(), line_data)
    data = payload(request)
    data = dict(data)
    if not data.get('quote_fx'):
        data['quote_fx'] = rate_for(order, data.get('currency', 'USD'), 'CNY') or ''
    form = LineForm(data)
    only_fields(data, form.fields)
    validate_form(form)
    line = form.save(commit=False)
    line.order = order
    line.assign_number(order)
    line.full_clean()
    line.save()
    remember_rate(order, line.currency, 'CNY', line.quote_fx)
    audit(order, request.user, '新增订单小项', f'API / {line.label} / {line.quantity} pcs')
    return JsonResponse(line_data(line), status=201)


@endpoint(('GET', 'POST'), mutation=True)
def transactions(request, pk):
    order = accessible(request.user, pk, write=request.method == 'POST', admin=request.method == 'POST')
    if request.method == 'GET':
        return paginated(request, order.transactions.prefetch_related('corrections', 'allocations__coverages'), transaction_data)
    data = payload(request)
    kind = data.get('kind')
    if kind not in ('receipt', 'payment'):
        raise ValidationError('kind 必须是 receipt 或 payment。')
    data = dict(data)
    if not data.get('fx'):
        data['fx'] = rate_for(order, data.get('currency', ''), 'USD' if kind == 'receipt' else 'CNY') or ''
    form = TransactionForm(data, request.FILES, kind=kind)
    only_fields(data, [*form.fields, 'kind'])
    only_fields(request.FILES, ['attachment'])
    validate_form(form)
    item = form.save(commit=False)
    item.order, item.kind, item.created_by = order, kind, request.user
    if item.currency == item.target_currency and item.fx != 1:
        raise ValidationError('原币与等值币种相同，当日汇率须为1。')
    item.full_clean()
    item.save()
    remember_rate(order, item.currency, item.target_currency, item.fx)
    audit(order, request.user, '录入收款' if kind == 'receipt' else '录入付款', f'API / #{item.pk} / {item.reference}')
    return JsonResponse(transaction_data(item), status=201)


@endpoint(('POST',), mutation=True)
def allocations(request, pk):
    txn = get_object_or_404(Transaction, pk=pk, order__in=visible_orders(request.user))
    order = accessible(request.user, txn.order_id, write=True, admin=True)
    data = payload(request)
    data = dict(data)
    if not data.get('rate'):
        selected = order.lines.filter(pk=data.get('line')).first() if str(data.get('line', '')).isdigit() else None
        if selected:
            data['rate'] = rate_for(order, txn.currency, selected.currency if txn.kind == 'receipt' else 'CNY') or ''
    form = AllocationForm(data, order=order, txn=txn)
    only_fields(data, form.fields)
    d = validate_form(form)
    item = allocate(request.user, txn.pk, d['line'].pk, d['amount'], d['rate'], d['reason'], d['shipment_item'] or None, auto_batches=not bool(d['shipment_item']))
    return JsonResponse({**allocation_data(item), 'order_id': order.pk}, status=201)


def quantities(data, order):
    raw = data.get('quantities')
    if not isinstance(raw, dict) or not raw:
        raise ValidationError('quantities 必须是小项编号到数量的对象。')
    valid_ids = set(order.lines.values_list('pk', flat=True))
    result = {}
    for key, value in raw.items():
        if not str(key).isascii() or not str(key).isdecimal() or int(key) not in valid_ids:
            raise ValidationError('发货小项不属于此订单。')
        if type(value) is not int or value < 0:
            raise ValidationError('发货数量必须为非负整数。')
        if int(key) in result:
            raise ValidationError('发货小项编号重复。')
        result[int(key)] = value
    if not any(result.values()):
        raise ValidationError('请至少填写一个大于零的发货数量。')
    return result


@endpoint(('POST',))
def preview_shipment(request, pk):
    order = accessible(request.user, pk)
    data = payload(request)
    only_fields(data, ['quantities'])
    qty = quantities(data, order)
    result = []
    for line in order.lines.all():
        if qty.get(line.pk):
            quote = shipment_quote(line, qty[line.pk])
            quote.pop('line')
            result.append({'line_id': line.pk, 'currency': line.currency, **quote})
    return JsonResponse({'order_id': order.pk, 'preview_only': True, 'items': result})


@endpoint(('GET', 'POST'), mutation=True)
def shipments(request, pk):
    order = accessible(request.user, pk, write=request.method == 'POST')
    if request.method == 'GET':
        return paginated(request, order.shipments.prefetch_related('items__allocations__transaction'), shipment_data)
    data = payload(request)
    form = ShipmentForm(data)
    only_fields(data, [*form.fields, 'quantities'])
    d = validate_form(form)
    qty = quantities(data, order)
    token = d.pop('token')
    ship = confirm_shipment(request.user, pk, qty, d, token)
    return JsonResponse(shipment_data(ship), status=201)


@endpoint(('POST',), mutation=True)
def lock(request, pk):
    from .views import freeze_order
    order = accessible(request.user, pk, admin=True)
    only_fields(payload(request), [])
    freeze_order(request.user, pk)
    archive = order.archives.order_by('-pk').first()
    return JsonResponse({'order_id': pk, 'status': 'locked', 'archive_id': archive.pk,
                         'sha256': archive.digest, 'download_url': f'/api/v1/archives/{archive.pk}/download/'})


@endpoint()
def attachment(request, pk):
    from .views import attachment as web_attachment
    txn = get_object_or_404(Transaction, pk=pk, order__in=visible_orders(request.user))
    if not txn.attachment or not txn.attachment.storage.exists(txn.attachment.name):
        raise Http404
    return web_attachment(request, pk)


@endpoint()
def export(request, pk):
    from .views import export_order
    accessible(request.user, pk)
    response = export_order(request, pk)
    if response.status_code == 302:
        raise ValidationError('凭证文件暂不可用，无法生成完整导出，请核对文件备份。')
    return response


@endpoint()
def archive_download(request, pk):
    from .views import download_archive
    return download_archive(request, pk)


@endpoint(public=True)
def live(request):
    return JsonResponse({'status': 'ok'})


@endpoint(public=True)
def ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        Order.objects.values('pk').first()
        key = 'readiness:' + uuid.uuid4().hex
        cache.set(key, 'ok', 10)
        if cache.get(key) != 'ok':
            raise RuntimeError('cache unavailable')
        cache.delete(key)
    except Exception:
        return JsonResponse({'status': 'unavailable'}, status=503)
    return JsonResponse({'status': 'ok'})


@endpoint(('POST',), mutation=True)
def transaction_correction(request, pk):
    from .forms import TransactionCorrectionForm
    from .services import correct_transaction
    txn = get_object_or_404(Transaction, pk=pk, order__in=visible_orders(request.user))
    accessible(request.user, txn.order_id, write=True, admin=True)
    data = payload(request)
    only_fields(data, ['action', 'party', 'reason'])
    form = TransactionCorrectionForm(data)
    form.fields.pop('token')
    d = validate_form(form)
    correct_transaction(request.user, pk, d['action'], d['party'], d['reason'])
    txn.refresh_from_db()
    return JsonResponse(transaction_data(txn))


@endpoint(('POST',), mutation=True)
def shipment_logistics(request, pk):
    from .forms import LogisticsForm
    from .services import update_logistics
    ship = get_object_or_404(Shipment, pk=pk, order__in=visible_orders(request.user))
    accessible(request.user, ship.order_id, write=True)
    data = payload(request)
    form = LogisticsForm(data, instance=ship)
    only_fields(data, form.fields)
    d = validate_form(form)
    reason = d.pop('reason')
    ship = update_logistics(request.user, pk, d, reason)
    return JsonResponse(shipment_data(ship))


@endpoint(('POST',), mutation=True)
def shipment_freight(request, pk):
    from .forms import FreightForm
    from .services import record_freight
    ship = get_object_or_404(Shipment, pk=pk, order__in=visible_orders(request.user))
    accessible(request.user, ship.order_id, write=True, admin=True)
    data = payload(request)
    only_fields(data, ['amount', 'currency', 'reason'])
    form = FreightForm(data)
    form.fields.pop('token')
    d = validate_form(form)
    record_freight(request.user, pk, d['amount'], d['currency'], d['reason'])
    return JsonResponse(shipment_data(ship))
