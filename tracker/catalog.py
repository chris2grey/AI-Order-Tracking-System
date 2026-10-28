from django.db.models import Count
from .models import Line, Transaction
from .services import visible_orders

def suggestions(user):
    orders = visible_orders(user)
    def choices(qs, field):
        return list(qs.exclude(**{field: ''}).values(field).annotate(uses=Count('pk')).filter(uses__gte=4).order_by('-uses', field).values_list(field, flat=True)[:80])
    lines = Line.objects.filter(order__in=orders)
    txns = Transaction.objects.filter(order__in=orders)
    return {
        'product': choices(lines, 'product'),
        'model': choices(lines, 'model'),
        'supplier': choices(lines.filter(supply_type='external'), 'supplier'),
        'customer': choices(orders, 'customer'),
        'party': choices(txns, 'party'),
        'platform': choices(txns, 'platform'),
    }
