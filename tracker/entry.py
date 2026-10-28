"""Table entry and atomic batch allocation, sharing the existing accounting services."""
import hashlib
import json
import uuid
from decimal import Decimal
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.forms import formset_factory
from django.shortcuts import render, redirect
from django.utils import timezone
from .forms import BatchLineForm, LineDefaultsForm, LineForm
from .models import Order, ApiMutation, ZERO
from .catalog import suggestions
from .services import authorize, audit, allocate, line_summary, rate_for, remember_rate, order_rates

def batch_lines(request, order):
    initial = {'date': timezone.localdate()}
    for c in ['USD', 'EUR', 'HKD', 'GBP']:
        initial[f'rate_{c}'] = rate_for(order, c, 'CNY')
    import_error = ''
    import_preview = False
    form_data = request.POST or None
    if request.method == 'POST' and request.POST.get('action') == 'import':
        from .product_import import import_rows, COLUMNS
        try:
            incoming = import_rows(request.FILES.get('product_file'))
            existing = []
            try:
                total = min(int(request.POST.get('lines-TOTAL_FORMS', '0')), 100)
            except ValueError:
                total = 0
            for i in range(max(0, total)):
                row = {key: request.POST.get(f'lines-{i}-{key}', '') for key, _ in COLUMNS}
                if any(row[key] for key in ['label', 'product', 'model', 'quantity', 'price', 'cost']):
                    existing.append(row)
            incoming = existing + incoming
            if len(incoming) > 100:
                raise ValidationError('现有草稿与导入产品合计超过100行，请分批录入。')
            form_data = request.POST.copy()
            form_data['lines-TOTAL_FORMS'] = str(len(incoming))
            form_data['lines-INITIAL_FORMS'] = '0'
            for i, row in enumerate(incoming):
                for key, value in row.items():
                    form_data[f'lines-{i}-{key}'] = value
            import_preview = True
            messages.success(request, f'已导入 {len(incoming)-len(existing)} 行产品草稿，尚未写入订单。请核对并保存。')
        except ValidationError as exc:
            import_error = '；'.join(exc.messages)
    defaults = LineDefaultsForm(form_data, initial=initial)
    FormSet = formset_factory(BatchLineForm, extra=3, max_num=100, validate_max=True, absolute_max=100)
    formset = FormSet(form_data, prefix='lines')
    hints = suggestions(request.user)
    for form in [*formset.forms, formset.empty_form]:
        for name in ['product', 'model', 'supplier']:
            form.fields[name].widget.attrs['list'] = f'entry-{name}-options'
            form.fields[name].widget.attrs['autocomplete'] = 'off'
    valid_defaults = defaults.is_valid() if request.method == 'POST' else False
    if request.method == 'POST' and request.POST.get('action') != 'import' and valid_defaults and formset.is_valid():
        rows = [f for f in formset if f.has_changed()]
        if not rows:
            defaults.add_error(None, '请至少填写一行产品。')
        else:
            try:
                with transaction.atomic():
                    locked = Order.objects.select_for_update().get(pk=order.pk)
                    authorize(request.user, locked, write=True)
                    labels = set(locked.lines.values_list('label', flat=True))
                    number = locked.lines.count() + 1
                    for form in rows:
                        line = form.save(commit=False)
                        while f'{number:03d}' in labels:
                            number += 1
                        line.label = line.label.strip() or f'{number:03d}'
                        if line.label in labels:
                            raise ValidationError(f'小项编号 {line.label} 重复，请修改后再提交。')
                        labels.add(line.label)
                        line.order = locked
                        line.date = defaults.cleaned_data['date']
                        line.customer_terms = defaults.cleaned_data['customer_terms']
                        line.supplier_terms = defaults.cleaned_data['supplier_terms']
                        line.freight_included = defaults.cleaned_data['freight_included']
                        rate = Decimal('1') if line.currency == 'CNY' else defaults.cleaned_data.get(f'rate_{line.currency}')
                        if rate is None:
                            raise ValidationError(f'请在统一设置中填写 {line.currency} 对 CNY 汇率，本订单后续会自动复用。')
                        line.quote_fx = rate
                        line.full_clean()
                        line.save()
                        remember_rate(locked, line.currency, 'CNY', rate)
                        audit(locked, request.user, '新增订单小项', f'表格录入 / {line.label} / {line.model} / {line.quantity} pcs / {line.get_supply_type_display()}')
                messages.success(request, f'已保存 {len(rows)} 行产品，同型号补单保持独立核算。')
                return redirect('line_new' if request.POST.get('action') == 'another' else 'order_detail', pk=order.pk)
            except (ValidationError, IntegrityError) as exc:
                defaults.add_error(None, '；'.join(exc.messages) if isinstance(exc, ValidationError) else '小项编号重复，整批尚未保存。')
    # Also keep the existing single-item context for API/older clients.
    next_number = order.lines.count() + 1
    while order.lines.filter(label=f'{next_number:03d}').exists():
        next_number += 1
    return render(request, 'line_table.html', {'title': '表格录入产品 / 补单', 'order': order, 'defaults': defaults, 'formset': formset, 'form': LineForm(initial={'label': f'{next_number:03d}'}), 'import_error': import_error, 'import_preview': import_preview, 'entry_hints': hints, 'rate_fields': [defaults[f'rate_{c}'] for c in ['USD', 'EUR', 'HKD', 'GBP']]})

def allocation_rows(txn):
    rows = []
    for line in txn.order.lines.all():
        s = line_summary(line)
        total_due = s['receivable'] if txn.kind == 'receipt' else s['payable']
        if total_due <= 0 or (txn.kind == 'payment' and (line.supply_type == 'self' or line.supplier.strip() != txn.party.strip())):
            continue
        currency = line.currency if txn.kind == 'receipt' else 'CNY'
        debt = ZERO
        for item in line.shipment_items.select_related('shipment').order_by('shipment__date', 'shipment_id', 'pk'):
            due = item.due(txn.kind)
            if due > 0:
                debt += due
                rows.append({'key': f'{line.pk}_{item.pk}', 'line': line, 'item': item, 'due': due, 'currency': currency, 'purpose': f'发货批次 #{item.shipment_id}'})
        if s['remaining'] > 0 and total_due > debt:
            rows.append({'key': f'{line.pk}_pool', 'line': line, 'item': None, 'due': total_due-debt, 'currency': currency, 'purpose': '未发货资金池'})
    return rows

def bulk_allocation(request, txn, rows):
    """Never trust row identifiers, rates, balances or permissions from the browser."""
    if len(request.POST.getlist('selected')) > 200:
        raise ValidationError('单次最多选择 200 行。')
    selected = request.POST.getlist('selected')
    if not selected or len(selected) != len(set(selected)):
        raise ValidationError('请勾选至少一行，且不能重复选择。')
    known = {r['key']: r for r in rows}
    try:
        token = uuid.UUID(request.POST.get('token', ''))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError('提交编号无效，请重新打开分摊页面。')
    body = {'selected': sorted(selected), 'amounts': {k: request.POST.get(f'amount_{k}', '') for k in selected}, 'rates': {name[5:]: request.POST[name] for name in request.POST if name.startswith('rate_')}, 'reason': request.POST.get('reason', '').strip()}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    if not body['reason'] or len(body['reason']) > 240:
        raise ValidationError('请填写 1 至 240 字的分摊说明。')
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=txn.order_id)
        authorize(request.user, locked, write=True, admin=True)
        previous = ApiMutation.objects.filter(user=request.user, key=token).first()
        operation = f'web:allocation:{txn.pk}'
        if previous:
            if previous.operation != operation or previous.digest != digest:
                raise ValidationError('此提交编号已用于其他内容，请刷新后重新核对。')
            return previous.response['count']
        if any(key not in known for key in selected):
            raise ValidationError('所选小项或批次无效，请刷新页面。')
        # Debts precede the pool for each line; allocate() rechecks all amounts.
        count = 0
        for row in rows:
            if row['key'] not in selected:
                continue
            raw_rate = request.POST.get(f"rate_{row['currency']}", '')
            rate = raw_rate or rate_for(locked, txn.currency, row['currency'])
            try:
                amount = Decimal(body['amounts'][row['key']])
                rate = Decimal(str(rate))
                if not rate.is_finite() or rate.as_tuple().exponent < -8:
                    raise ValueError
            except (ValueError, ArithmeticError):
                raise ValidationError('所选行请填写有效金额和最多 8 位小数的汇率。')
            allocate(request.user, txn.pk, row['line'].pk, amount, rate, body['reason'], row['item'].pk if row['item'] else None)
            count += 1
        ApiMutation.objects.create(user=request.user, key=token, operation=operation, digest=digest, response={'count': count}, status_code=200)
    return count
