from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction as db_transaction
from django.db.models import Prefetch
from .models import Order, Line, Transaction, Allocation, Shipment, ShipmentLine, Audit, ZERO, money

def visible_orders(user):
    return Order.objects.all() if user.is_superuser else Order.objects.filter(owner=user)

def with_order_data(queryset):
    """A bounded number of queries for dashboards, independent of the order count."""
    return queryset.select_related('owner').prefetch_related(
        Prefetch('lines', queryset=Line.objects.prefetch_related(
            Prefetch('allocations', queryset=Allocation.objects.select_related('transaction')),
            'shipment_items')),
        Prefetch('transactions', queryset=Transaction.objects.prefetch_related(Prefetch('allocations',queryset=Allocation.objects.select_related('line','shipment_item')))))

def authorize(user, order, write=False, admin=False):
    if not user.is_active or (admin and not user.is_superuser) or (not user.is_superuser and order.owner_id != user.id):
        raise PermissionDenied
    if write and order.status != 'active':
        raise ValidationError('订单已锁定，不能新增或修改业务记录。')

def audit(order, user, action, detail=''):
    Audit.objects.create(order=order, actor=user, action=action, detail=detail)

def allocated(line, kind):
    return money(sum((a.target_amount for a in line.allocations.all() if a.transaction.kind == kind), ZERO))

def line_summary(line):
    shipped = sum((s.quantity for s in line.shipment_items.all()), 0)
    receipt = allocated(line, 'receipt')
    payment = allocated(line, 'payment')
    return {'line': line, 'sale': line.sale_total, 'cost': line.cost_total, 'external_cost': line.payable_total, 'internal_cost': line.cost_total if line.supply_type == 'self' else ZERO, 'receipt': receipt, 'payment': payment, 'receivable': money(line.sale_total - receipt), 'payable': money(line.payable_total - payment), 'receipt_pct': percent(receipt, line.sale_total), 'payment_pct': percent(payment, line.payable_total), 'shipped': shipped, 'remaining': line.quantity - shipped, 'ship_pct': percent(shipped, line.quantity)}

def percent(value, total):
    if not total:
        return 100.0
    rounded = (Decimal(value)/Decimal(total)*100).quantize(Decimal('0.1'),rounding=ROUND_HALF_UP)
    if 0 < value < total and rounded >= 100:
        rounded = Decimal('99.9')
    return float(rounded)

def summary(order):
    rows = [line_summary(l) for l in order.lines.all()]
    currencies = {}
    for r in rows:
        c = r['line'].currency
        t = currencies.setdefault(c, {'sale': ZERO, 'receipt': ZERO, 'receivable': ZERO})
        for k in t:
            t[k] += r[k]
    cost = sum((r['cost'] for r in rows), ZERO)
    external_cost = sum((r['external_cost'] for r in rows), ZERO)
    internal_cost = sum((r['internal_cost'] for r in rows), ZERO)
    payment = sum((r['payment'] for r in rows), ZERO)
    units = sum(r['line'].quantity for r in rows)
    shipped = sum(r['shipped'] for r in rows)
    unallocated = [t for t in order.transactions.all() if t.unallocated > 0]
    complete = bool(rows) and all(r['receivable'] == 0 and r['payable'] == 0 and r['remaining'] == 0 for r in rows) and not unallocated
    return {'rows': rows, 'currencies': currencies, 'cost': cost, 'external_cost': external_cost, 'internal_cost': internal_cost, 'payment': payment, 'payable': money(external_cost - payment), 'payment_pct': percent(payment, external_cost), 'quantity': units, 'shipped': shipped, 'ship_pct': percent(shipped, units), 'unallocated': unallocated, 'complete': complete}

@db_transaction.atomic
def allocate(user, txn_id, line_id, amount, rate, reason, shipment_item_id=None):
    # The shared order row serializes ALL fund and shipment mutations in PostgreSQL.
    txn = Transaction.objects.select_related('order').get(pk=txn_id)
    order = Order.objects.select_for_update().get(pk=txn.order_id)
    authorize(user, order, write=True, admin=True)
    txn = Transaction.objects.select_for_update().get(pk=txn_id)
    try:
        line = Line.objects.get(pk=line_id, order=order)
    except Line.DoesNotExist:
        raise ValidationError('小项不属于此订单。')
    amount, rate = Decimal(str(amount)), Decimal(str(rate))
    if not amount.is_finite() or not rate.is_finite() or amount <= 0 or rate <= 0 or amount != money(amount):
        raise ValidationError('分摊金额必须是正数，最多两位小数；结算汇率必须大于零。')
    target_currency = line.currency if txn.kind == 'receipt' else 'CNY'
    if txn.currency == target_currency and rate != 1:
        raise ValidationError('原币与结算币种相同，结算汇率必须为 1。')
    if txn.kind == 'payment' and line.supply_type == 'self':
        raise ValidationError('自供货只记录内部成本，不接受供应商付款分摊。')
    if txn.kind == 'payment' and txn.party.strip() != line.supplier.strip():
        raise ValidationError('付款供应商必须与小项供应商一致。')
    target = money(amount * rate)
    total = line.sale_total if txn.kind == 'receipt' else line.payable_total
    if amount > txn.unallocated:
        raise ValidationError('分摊金额超过这笔流水的未分配金额。')
    if target <= 0 or target > money(total - allocated(line, txn.kind)):
        raise ValidationError('结算金额超过小项待收 / 待付余额，或舍入后为零。')
    item = None
    if shipment_item_id:
        item = ShipmentLine.objects.filter(pk=shipment_item_id, line=line).first()
        if not item or target > item.due(txn.kind):
            raise ValidationError('发货批次不匹配，或超过本批待结算金额。')
    elif any(s.due(txn.kind) > 0 for s in line.shipment_items.all()):
        raise ValidationError('此小项存在已发货未结算批次，请指定该批次分摊；需要跨批时分多次操作。')
    if not str(reason).strip():
        raise ValidationError('请填写分摊说明。')
    allocation = Allocation.objects.create(transaction=txn, line=line, shipment_item=item, source_amount=amount, settlement_rate=rate, target_amount=target, reason=reason, created_by=user)
    audit(order, user, '款项分摊', f'流水 #{txn.id} → {line.label}：{amount} {txn.currency} = {target} {target_currency}；批次 {item.shipment_id if item else "未发货资金池"}；{reason}')
    remember_rate(order, txn.currency, target_currency, rate)
    return allocation

def shipment_quote(line, quantity):
    quantity = int(quantity)
    previous = list(line.shipment_items.all())
    remaining = line.quantity - sum(s.quantity for s in previous)
    if quantity <= 0 or quantity > remaining:
        raise ValidationError(f'{line.label}：发货数量须大于零且不超过未发数量 {remaining}。')
    remaining_cost = line.cost_total - sum((s.internal_cost_amount for s in previous), ZERO)
    internal_cost = remaining_cost if quantity == remaining else money(remaining_cost * quantity / remaining)
    result = {'line': line, 'quantity': quantity, 'internal_cost': internal_cost}
    for kind, total, field, reserved in [('receipt', line.sale_total, 'sale_amount', 'receipt_reserved'), ('payment', line.payable_total, 'cost_amount', 'payment_reserved')]:
        remaining_value = total - sum((getattr(s, field) for s in previous), ZERO)
        batch_value = remaining_value if quantity == remaining else money(remaining_value * quantity / remaining)
        pool = sum((a.target_amount for a in line.allocations.filter(transaction__kind=kind, shipment_item__isnull=True)), ZERO)
        pool -= sum((getattr(s, reserved) for s in previous), ZERO)
        covered = pool if quantity == remaining else money(pool * quantity / remaining)
        covered = min(batch_value, covered)
        result[kind] = {'total': batch_value, 'covered': covered, 'due': money(batch_value - covered)}
    return result

@db_transaction.atomic
def confirm_shipment(user, order_id, quantities, data, token):
    order = Order.objects.select_for_update().get(pk=order_id)
    authorize(user, order, write=True)
    existing = Shipment.objects.filter(reference=token).first()
    if existing:
        if existing.order_id != order.id:
            raise ValidationError('重复请求编号无效。')
        return existing
    lines = {l.id: l for l in order.lines.all()}
    quotes = []
    for line_id, qty in quantities.items():
        if line_id not in lines:
            raise ValidationError('发货小项不属于此订单。')
        if qty:
            quotes.append(shipment_quote(lines[line_id], qty))
    if not quotes:
        raise ValidationError('请至少选择一个发货小项并填写数量。')
    shipment = Shipment.objects.create(order=order, created_by=user, reference=token, **data)
    for q in quotes:
        ShipmentLine.objects.create(shipment=shipment, line=q['line'], quantity=q['quantity'], sale_amount=q['receipt']['total'], cost_amount=q['payment']['total'], internal_cost_amount=q['internal_cost'], receipt_reserved=q['receipt']['covered'], payment_reserved=q['payment']['covered'])
    audit(order, user, '确认发货', f'批次 #{shipment.id}；共 {sum(q["quantity"] for q in quotes)} pcs；占款快照已保存')
    return shipment

def order_rates(order):
    """Defaults for new entries only; historical records and rates stay unchanged."""
    rates = {}
    for line in order.lines.all():
        rates[f'{line.currency}:CNY'] = str(line.quote_fx)
    # Old orders acquire sensible defaults without rewriting their accounting.
    for txn in order.transactions.order_by('date', 'id'):
        rates[f'{txn.currency}:{txn.target_currency}'] = str(txn.fx)
    rates.update(order.exchange_rates or {})
    return rates

def rate_for(order, source, target):
    if source == target:
        return Decimal('1')
    rates = order_rates(order)
    direct = rates.get(f'{source}:{target}')
    if direct:
        return Decimal(direct)
    # A rate can be derived through CNY only when both legs are known.
    source_cny = Decimal('1') if source == 'CNY' else Decimal(rates[f'{source}:CNY']) if f'{source}:CNY' in rates else None
    target_cny = Decimal('1') if target == 'CNY' else Decimal(rates[f'{target}:CNY']) if f'{target}:CNY' in rates else None
    if source_cny and target_cny:
        return (source_cny / target_cny).quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP)
    return None

def remember_rate(order, source, target, rate):
    rate = Decimal(str(rate))
    if not rate.is_finite() or rate <= 0 or (source == target and rate != 1):
        raise ValidationError('汇率必须大于零，同币种汇率必须为 1。')
    rates = dict(order.exchange_rates or {})
    key = f'{source}:{target}'
    if rates.get(key) != str(rate):
        rates[key] = str(rate)
        order.exchange_rates = rates
        order.save(update_fields=['exchange_rates'])
