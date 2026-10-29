from collections import Counter
import unicodedata
from .models import Line, Transaction
from .services import visible_orders


def normalize_name(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value)).split())


def suggestions(user):
    orders = visible_orders(user)
    def choices(values, minimum=1):
        counts = Counter(normalize_name(value) for value in values if normalize_name(value))
        return [name for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])) if count >= minimum][:500]
    lines = list(Line.objects.filter(order__in=orders).values('product', 'model', 'supplier', 'supply_type'))
    txns = list(Transaction.objects.filter(order__in=orders).prefetch_related('corrections'))
    active = [t for t in txns if not t.is_void]
    suppliers = [line['supplier'] for line in lines if line['supply_type'] == 'external']
    suppliers += [t.effective_party for t in active if t.kind == 'payment']
    return {
        'product': choices([line['product'] for line in lines], 4)[:80],
        'model': choices([line['model'] for line in lines], 4)[:80],
        'supplier': choices(suppliers),
        'customer': choices(orders.values_list('customer', flat=True)),
        'party': choices([t.effective_party for t in active] + suppliers + list(orders.values_list('customer', flat=True))),
        'platform': choices([t.platform for t in active]),
    }
