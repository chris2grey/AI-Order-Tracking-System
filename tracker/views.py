from collections import defaultdict
from datetime import date
from decimal import Decimal
from io import BytesIO
import hashlib
import uuid
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction as db_transaction, IntegrityError
from django.db.models import Q, Prefetch, OuterRef, Subquery, Sum, Value, DecimalField, F
from django.db.models.functions import Coalesce
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from .models import CURRENCIES, Order, Line, Transaction, Shipment, ShipmentLine, Archive, Allocation, ZERO, money
from .forms import OrderForm, LineForm, TransactionForm, AllocationForm, ShipmentForm, SalespersonCreationForm
from .services import visible_orders, with_order_data, authorize, summary, line_summary, shipment_quote, confirm_shipment, allocate, audit, order_rates, rate_for, remember_rate
from .exports import archive_bytes
from .throttle import login_limited

class ThrottledLoginView(LoginView):
    def post(self, request, *args, **kwargs):
        if login_limited(request, request.POST.get('username', '')):
            return HttpResponse('尝试次数过多，请 10 分钟后重试。', status=429)
        return super().post(request, *args, **kwargs)

def accessible(request, pk):
    return get_object_or_404(visible_orders(request.user).select_related('owner'), pk=pk)

def error_text(exc):
    return '；'.join(exc.messages) if isinstance(exc, ValidationError) else '记录编号重复，请检查后重试。'

def paginate(request, queryset, size=20):
    page = Paginator(queryset, size).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    return page, {'page_obj':page,'result_count':page.paginator.count,'query_string':params.urlencode(),'has_filters':bool(params)}

@login_required
def dashboard(request):
    qs = with_order_data(visible_orders(request.user))
    cards = [(o, summary(o)) for o in qs]
    totals = defaultdict(lambda: {'sale': ZERO, 'receipt': ZERO, 'receivable': ZERO})
    cost = payment = ZERO
    for _, s in cards:
        for c, values in s['currencies'].items():
            for k, v in values.items():
                totals[c][k] += v
        cost += s['external_cost']
        payment += s['payment']
    tx = [t for o, _ in cards for t in o.transactions.all()]
    month = defaultdict(lambda: [ZERO, ZERO])
    for t in tx:
        # Each series has its own currency axis; never add USD and CNY.
        month[t.date.strftime('%Y-%m')][0 if t.kind == 'receipt' else 1] += t.equivalent
    chart = [{'month': k, 'receipt': float(v[0]), 'payment': float(v[1])} for k, v in sorted(month.items())[-6:]]
    alerts = [(o, s) for o, s in cards if o.status == 'active' and (s['unallocated'] or s['complete'])]
    active_cards = [(o,s) for o,s in cards if o.status=='active']
    return render(request, 'dashboard.html', {'title': '业务总览', 'cards': active_cards[:6], 'total_orders': len(cards), 'active_count': len(active_cards), 'totals': dict(totals), 'cost': cost, 'payment': payment, 'payable': money(cost-payment), 'receipt_usd': sum((t.equivalent for t in tx if t.kind == 'receipt'), ZERO), 'payment_cny': sum((t.equivalent for t in tx if t.kind == 'payment'), ZERO), 'chart_data': chart, 'alerts': alerts})

@login_required
def orders(request):
    qs = visible_orders(request.user).select_related('owner')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(customer__icontains=q) | Q(owner__username__icontains=q) | Q(owner__first_name__icontains=q) | Q(lines__model__icontains=q) | Q(lines__product__icontains=q)).distinct()
    status = request.GET.get('status')
    if status in ['active', 'locked']:
        qs = qs.filter(status=status)
    page, context = paginate(request, with_order_data(qs))
    return render(request, 'orders.html', {'title': '订单工作台', 'cards': [(o, summary(o)) for o in page], 'q': q, **context})

@login_required
def order_detail(request, pk):
    shipment_items = ShipmentLine.objects.select_related('line').prefetch_related(Prefetch('allocations',queryset=Allocation.objects.select_related('transaction')))
    queryset = with_order_data(visible_orders(request.user)).prefetch_related(
        Prefetch('shipments',queryset=Shipment.objects.prefetch_related(Prefetch('items',queryset=shipment_items))),
        Prefetch('archives',queryset=Archive.objects.defer('content')))
    order = get_object_or_404(queryset, pk=pk)
    s = summary(order)
    shipment_rows = []
    for ship in order.shipments.all():
        for i in ship.items.all():
            shipment_rows.append({'ship': ship, 'item': i, 'received': i.covered('receipt'), 'paid': i.covered('payment'), 'due_in': i.due('receipt'), 'due_out': i.due('payment')})
    return render(request, 'order.html', {'title': order.number, 'order': order, 's': s, 'shipment_rows': shipment_rows, 'audits':order.audits.select_related('actor')[:30]})

@login_required
def order_new(request):
    from .catalog import suggestions
    hints = suggestions(request.user)
    form = OrderForm(request.POST or None, user=request.user)
    form.fields['customer'].widget.attrs['list'] = 'entry-customer-options'
    if request.method == 'POST' and form.is_valid():
        with db_transaction.atomic():
            order = form.save()
            audit(order, request.user, '创建订单')
        messages.success(request, '大订单已创建，下一步录入产品小项。')
        return redirect('line_new', pk=order.pk)
    return render(request, 'form.html', {'title': '新建大订单', 'form': form, 'intro': '先建立客户与负责人，再录入首次下单及后续补单。', 'entry_hints': hints})

@login_required
def line_new(request, pk):
    order = accessible(request, pk)
    if order.status != 'active':
        return redirect('order_detail', pk=pk)
    if request.method == 'GET' or request.POST.get('bulk_lines') == '1':
        from .entry import batch_lines
        return batch_lines(request, order)
    next_number = order.lines.count()+1
    existing_labels = set(order.lines.values_list('label',flat=True))
    while f'{next_number:03d}' in existing_labels:
        next_number += 1
    line_data = request.POST.copy() if request.method == 'POST' else None
    if line_data is not None and not line_data.get('quote_fx'):
        line_data['quote_fx'] = rate_for(order, line_data.get('currency', 'USD'), 'CNY') or ''
    form = LineForm(line_data, initial={'label':f'{next_number:03d}'})
    if request.method == 'POST' and form.is_valid():
        try:
            with db_transaction.atomic():
                order = Order.objects.select_for_update().get(pk=pk)
                authorize(request.user, order, write=True)
                line = form.save(commit=False)
                line.order = order
                line.full_clean()
                line.save()
                remember_rate(order, line.currency, 'CNY', line.quote_fx)
                audit(order, request.user, '新增订单小项', f'{line.label} / {line.model} / {line.quantity} pcs')
            messages.success(request, '订单小项已保存。同型号补单仍保持独立核算。')
            if request.POST.get('action') == 'another':
                return redirect('line_new', pk=pk)
            return redirect('order_detail', pk=pk)
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, error_text(exc))
    return render(request, 'form.html', {'title': '新增产品 / 补单', 'order': order, 'form': form, 'intro': '确认后不直接改写历史金额。请仔细核对数量、销售价格及成本。'})

@login_required
def transaction_new(request, pk, kind):
    order = accessible(request, pk)
    if kind not in ['receipt', 'payment']:
        return HttpResponse(status=404)
    if not request.user.is_superuser:
        raise PermissionDenied
    if order.status != 'active':
        return redirect('order_detail', pk=pk)
    target = 'USD' if kind == 'receipt' else 'CNY'
    source = 'USD' if kind == 'receipt' else 'CNY'
    txn_data = request.POST.copy() if request.method == 'POST' else None
    if txn_data is not None and not txn_data.get('fx'):
        txn_data['fx'] = rate_for(order, txn_data.get('currency', source), target) or ''
    form = TransactionForm(txn_data, request.FILES or None, kind=kind, initial={'currency': source, 'fx': rate_for(order, source, target)})
    if request.method == 'POST' and form.is_valid():
        try:
            with db_transaction.atomic():
                locked = Order.objects.select_for_update().get(pk=pk)
                authorize(request.user, locked, write=True, admin=True)
                t = form.save(commit=False)
                t.order, t.kind, t.created_by = locked, kind, request.user
                if t.currency == t.target_currency and t.fx != 1:
                    raise ValidationError('原币与等值币种相同，当日汇率须为 1。')
                t.full_clean()
                t.save()
                remember_rate(locked, t.currency, t.target_currency, t.fx)
                audit(order, request.user, '录入收款' if kind == 'receipt' else '录入付款', f'#{t.id} / {t.amount} {t.currency} / {t.reference}')
            messages.success(request, '流水已保存，请将款项分配到对应小项。')
            return redirect('allocation_new', pk=t.pk)
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, error_text(exc))
    from .catalog import suggestions
    hints = suggestions(request.user)
    for field in ['party', 'platform']:
        form.fields[field].widget.attrs['list'] = f'entry-{field}-options'
    target = 'USD' if kind == 'receipt' else 'CNY'
    return render(request, 'form.html', {'title': '录入客户收款' if kind == 'receipt' else '录入供应商付款', 'order': order, 'form': form, 'intro': f'本订单已填写的同币种汇率自动复用。实际汇率有变化时可修改，仅影响新记录。凭证支持 PDF / PNG / JPG，最大 10MB。', 'entry_hints': hints, 'order_rates': {c: str(rate_for(order, c, target) or '') for c, _ in CURRENCIES}})

@login_required
def allocation_new(request, pk):
    txn = get_object_or_404(Transaction, pk=pk, order__in=visible_orders(request.user))
    if not request.user.is_superuser:
        raise PermissionDenied
    if txn.order.status != 'active':
        return redirect('order_detail', pk=txn.order_id)
    from .entry import allocation_rows, bulk_allocation
    rows = allocation_rows(txn)
    bulk_error = ''
    if request.method == 'POST' and request.POST.get('bulk_allocation') == '1':
        try:
            count = bulk_allocation(request, txn, rows)
            messages.success(request, f'已完成 {count} 行分摊，余额与批次同步更新。')
            return redirect('allocation_new', pk=pk)
        except (ValidationError, IntegrityError) as exc:
            bulk_error = error_text(exc)
    allocation_data = request.POST.copy() if request.method == 'POST' else None
    if allocation_data is not None and not allocation_data.get('rate'):
        selected = txn.order.lines.filter(pk=allocation_data.get('line') or None).first() if str(allocation_data.get('line', '')).isdigit() else None
        if selected:
            allocation_data['rate'] = rate_for(txn.order, txn.currency, selected.currency if txn.kind == 'receipt' else 'CNY') or ''
    form = AllocationForm(allocation_data, order=txn.order, txn=txn)
    if request.method == 'POST' and not request.POST.get('bulk_allocation') and form.is_valid():
        d = form.cleaned_data
        try:
            allocate(request.user, txn.pk, d['line'].pk, d['amount'], d['rate'], d['reason'], d['shipment_item'] or None)
            messages.success(request, '分摊成功，订单进度与批次余额已更新。')
            return redirect('allocation_new', pk=pk)
        except ValidationError as exc:
            form.add_error(None, error_text(exc))
    currencies = sorted({row['currency'] for row in rows})
    rates = [{'currency': c, 'rate': request.POST.get(f'rate_{c}', str(rate_for(txn.order, txn.currency, c) or '')), 'same': c == txn.currency} for c in currencies]
    for row in rows:
        row['entered'] = request.POST.get(f"amount_{row['key']}", '')
        row['selected'] = row['key'] in request.POST.getlist('selected')
    return render(request, 'allocation.html', {'title': '批量款项分摊', 'order': txn.order, 'txn': txn, 'form': form, 'rows': rows, 'rates': rates, 'bulk_error': bulk_error, 'token': request.POST.get('token') or uuid.uuid4(), 'reason': request.POST.get('reason', '按订单约定分摊')})

@login_required
def attachment(request, pk):
    txn = get_object_or_404(Transaction, pk=pk, order__in=visible_orders(request.user))
    if not txn.attachment:
        return HttpResponse(status=404)
    try:
        file = txn.attachment.open('rb')
    except (FileNotFoundError, OSError):
        messages.error(request, '凭证文件暂不可用，请联系管理员核对文件备份。')
        return redirect('order_detail', pk=txn.order_id)
    audit(txn.order, request.user, '下载凭证', f'流水 #{txn.pk}')
    return FileResponse(file, as_attachment=True, filename=f'voucher-{txn.pk}{__import__("pathlib").Path(txn.attachment.name).suffix}')

@login_required
def finance(request):
    qs = Transaction.objects.filter(order__in=visible_orders(request.user)).select_related('order', 'created_by').prefetch_related(Prefetch('allocations',queryset=Allocation.objects.select_related('line','shipment_item')))
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(order__number__icontains=q) | Q(order__customer__icontains=q) | Q(party__icontains=q) | Q(platform__icontains=q) | Q(purpose__icontains=q) | Q(reference__icontains=q) | Q(allocations__line__model__icontains=q) | Q(allocations__line__product__icontains=q) | Q(allocations__line__label__icontains=q)).distinct()
    for field in ['kind', 'currency']:
        if request.GET.get(field):
            qs = qs.filter(**{field: request.GET[field]})
    dates = {}
    for field, lookup in [('start', 'date__gte'), ('end', 'date__lte')]:
        if request.GET.get(field):
            try:
                dates[field] = date.fromisoformat(request.GET[field])
                qs = qs.filter(**{lookup: dates[field]})
            except ValueError:
                messages.error(request, '日期格式无效，请选择有效日期。')
                qs = qs.none()
    if dates.get('start') and dates.get('end') and dates['start'] > dates['end']:
        messages.error(request, '开始日期不能晚于结束日期，请调整筛选条件。')
        qs = qs.none()
    if request.GET.get('allocation') == 'pending':
        used = Allocation.objects.filter(transaction_id=OuterRef('pk')).values('transaction_id').annotate(total=Sum('source_amount')).values('total')[:1]
        qs = qs.annotate(used_amount=Coalesce(Subquery(used),Value(0),output_field=DecimalField(max_digits=18,decimal_places=2))).filter(amount__gt=F('used_amount'))
    page, context = paginate(request, qs)
    return render(request, 'finance.html', {'title': '财务流水', 'transactions': page.object_list, 'q': q, **context})

@login_required
def shipments(request):
    qs = Shipment.objects.filter(order__in=visible_orders(request.user)).select_related('order').prefetch_related('items__line')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(order__number__icontains=q) | Q(order__customer__icontains=q) | Q(address__icontains=q) | Q(international_tracking__icontains=q) | Q(domestic_tracking__icontains=q) | Q(carrier__icontains=q) | Q(items__line__model__icontains=q) | Q(items__line__product__icontains=q) | Q(items__line__label__icontains=q)).distinct()
    page, context = paginate(request, qs)
    return render(request, 'shipments.html', {'title': '发货与物流', 'shipments': page.object_list, 'q': q, **context})

@login_required
def shipment_new(request, pk):
    order = accessible(request, pk)
    if order.status != 'active':
        return redirect('order_detail', pk=pk)
    if request.method == 'POST' and request.POST.get('action') == 'confirm':
        try:
            previous_token = uuid.UUID(request.POST.get('token', ''))
        except (ValueError, TypeError, AttributeError):
            previous_token = None
        if previous_token and order.shipments.filter(reference=previous_token).exists():
            messages.success(request, '该发货批次已经确认，未重复扣减数量或占用款项。')
            return redirect('order_detail', pk=pk)
    form = ShipmentForm(request.POST or None, initial={'token': uuid.uuid4()})
    if request.method == 'POST' and request.POST.get('action') == 'preview':
        # Quantity-only previews must work before logistics details are known.
        for name, field in form.fields.items():
            if name != 'token':
                field.required = False
    rows = [line_summary(l) for l in order.lines.all()]
    quotes = []
    quantities = {}
    if request.method == 'POST':
        try:
            for r in rows:
                qty = int(request.POST.get(f'qty_{r["line"].pk}', '0') or '0')
                if qty < 0:
                    raise ValidationError('发货数量不能为负。')
                quantities[r['line'].pk] = qty
                r['entered'] = qty
                if qty:
                    quotes.append(shipment_quote(r['line'], qty))
            if not quotes:
                raise ValidationError('请至少填写一个小项的发货数量。')
            if form.is_valid() and request.POST.get('action') == 'confirm':
                data = dict(form.cleaned_data)
                token = data.pop('token')
                ship = confirm_shipment(request.user, pk, quantities, data, token)
                messages.success(request, f'发货批次 #{ship.pk} 已确认，款项占用已保存。')
                return redirect('order_detail', pk=pk)
            form.is_valid()
        except (ValidationError, ValueError) as exc:
            form.is_valid()
            form.add_error(None, error_text(exc) if isinstance(exc, ValidationError) else '数量必须为整数。')
    quote_receipts = defaultdict(lambda: {'total': ZERO, 'covered': ZERO, 'due': ZERO})
    quote_payments = {'total': ZERO, 'covered': ZERO, 'due': ZERO}
    for quote in quotes:
        for key in quote_payments:
            quote_receipts[quote['line'].currency][key] += quote['receipt'][key]
            quote_payments[key] += quote['payment'][key]
    return render(request, 'shipment_form.html', {'title': '新建发货批次', 'order': order, 'form': form, 'rows': rows, 'quotes': quotes, 'quote_receipts': dict(quote_receipts), 'quote_payments': quote_payments})

@login_required
def archives(request):
    return render(request, 'archives.html', {'title': '归档中心', 'cards': [(o, summary(o)) for o in with_order_data(visible_orders(request.user))]})

@login_required
def export_order(request, pk):
    order = accessible(request, pk)
    try:
        with db_transaction.atomic():
            order = Order.objects.select_for_update().get(pk=pk)
            audit(order, request.user, '导出对账快照', '快照不是销毁授权')
            data = archive_bytes(order)
    except ValidationError as exc:
        messages.error(request, error_text(exc))
        return redirect('order_detail', pk=pk)
    response = FileResponse(BytesIO(data), as_attachment=True, filename=f'order-{pk}-snapshot.zip')
    response['X-Archive-SHA256'] = hashlib.sha256(data).hexdigest()
    return response

@login_required
@require_POST
def lock_order(request, pk):
    order = accessible(request, pk)
    if not request.user.is_superuser:
        raise PermissionDenied
    try:
        frozen = freeze_order(request.user, pk)
    except ValidationError as exc:
        messages.error(request, error_text(exc))
        return redirect('order_detail', pk=pk)
    if frozen:
        messages.success(request, '归档快照已固化。请下载到本地核验；本地版没有启用自动销毁。')
    return redirect('order_detail', pk=pk)

def freeze_order(user, pk):
    with db_transaction.atomic():
        order = Order.objects.select_for_update().get(pk=pk)
        authorize(user, order, admin=True)
        if order.status == 'locked':
            return False
        if not summary(order)['complete']:
            raise ValidationError('仍有未结清小项、未分配款项或未发货数量，不能锁定归档。')
        audit(order, user, '锁定归档', '产品收付款及发货均完成；实际货代运费另行核对')
        data = archive_bytes(order)
        Archive.objects.create(order=order, content=data, digest=hashlib.sha256(data).hexdigest(), created_by=user)
        order.status = 'locked'
        order.save(update_fields=['status'])
    return True

def error_response(request, status, title, description):
    if request.path.startswith('/api/'):
        from .api import error
        return error({400: 'bad_request', 403: 'permission_denied', 404: 'not_found', 500: 'server_error'}[status], description, status)
    return render(request, 'error.html', {'status':status,'title':title,'description':description}, status=status)

def bad_request(request, exception):
    return error_response(request,400,'请求暂时无法处理','请重新打开页面后重试。已保存的订单数据不会因本次请求改变。')

def forbidden(request, exception):
    return error_response(request,403,'暂无访问权限','请使用有权限的账号，或联系管理员核对订单负责人。')

def not_found(request, exception):
    return error_response(request,404,'页面或记录不可访问','链接可能已失效，或当前账号没有查看权限。请从订单列表重新进入。')

def server_error(request):
    return error_response(request,500,'暂时无法完成操作','请稍后重试；若持续出现，请联系管理员核对运行日志。')

def csrf_failure(request, reason=''):
    if request.path.startswith('/api/'):
        from .api import error
        return error('csrf_failed', '页面校验已失效，请重新获取 CSRF token。', 403)
    return error_response(request,403,'页面校验已失效','请刷新页面或重新登录，再确认并提交填写内容。')

@login_required
def download_archive(request, pk):
    a = get_object_or_404(Archive, pk=pk, order__in=visible_orders(request.user))
    response = FileResponse(BytesIO(bytes(a.content)), as_attachment=True, filename=f'order-{a.order_id}-archive.zip')
    response['X-Archive-SHA256'] = a.digest
    audit(a.order, request.user, '下载固化归档', a.digest)
    return response

@login_required
def team(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    form = SalespersonCreationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save(commit=False)
        # New users are salespeople; superuser creation stays in an explicit CLI command.
        user.save()
        messages.success(request, '业务员账号已创建。')
        return redirect('team')
    return render(request, 'team.html', {'title': '账号与权限', 'form': form, 'users': get_user_model().objects.all()})


@login_required
def system_status(request):
    from django.conf import settings
    from django.db import connection
    if not request.user.is_superuser:
        raise PermissionDenied
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
    return render(request, 'system.html', {'title': '服务器与数据', 'database_vendor': connection.vendor,
                                         'production': settings.PRODUCTION,
                                         'shared_cache': 'redis' in settings.CACHES['default']['BACKEND'].lower()})

@login_required
def line_template(request, pk):
    accessible(request, pk)
    from .product_import import template_bytes
    return FileResponse(BytesIO(template_bytes()), as_attachment=True, filename='product-entry-template.xlsx', content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
