from django import template
from decimal import Decimal, ROUND_HALF_UP
register = template.Library()

@register.filter
def cash(value):
    try:
        return f'{value:,.2f}'
    except (ValueError, TypeError):
        return value

@register.filter
def units(value):
    try:
        return f'{value:,}'
    except (ValueError, TypeError):
        return value

@register.filter
def price(value):
    try:
        return f'{value:,.4f}'.rstrip('0').rstrip('.')
    except (ValueError, TypeError):
        return value

@register.filter
def pct(value, total):
    if not total:
        return '0.0'
    rounded=(Decimal(value)*100/Decimal(total)).quantize(Decimal('0.1'),rounding=ROUND_HALF_UP)
    if 0 < Decimal(value) < Decimal(total) and rounded >= 100:
        rounded=Decimal('99.9')
    return str(rounded)
