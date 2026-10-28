from decimal import Decimal, ROUND_HALF_UP
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

CURRENCIES = [('USD', 'USD 美元'), ('CNY', 'CNY 人民币'), ('EUR', 'EUR 欧元'), ('HKD', 'HKD 港币'), ('GBP', 'GBP 英镑')]
ZERO = Decimal('0')

def money(value):
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

def private_path(instance, filename):
    return f'{instance.order_id}/{uuid.uuid4().hex}{__import__("pathlib").Path(filename).suffix.lower()}'

class Order(models.Model):
    number = models.CharField('订单编号', max_length=60, unique=True)
    customer = models.CharField('客户名称', max_length=160)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='orders', verbose_name='业务员')
    date = models.DateField('下单日期', default=timezone.localdate)
    notes = models.TextField('备注', blank=True)
    status = models.CharField(max_length=16, choices=[('active', '进行中'), ('locked', '已锁定归档')], default='active')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return f'{self.number} · {self.customer}'

class Line(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='lines')
    label = models.CharField('小项编号 / 补单说明', max_length=100)
    product = models.CharField('产品名称', max_length=160)
    model = models.CharField('产品型号', max_length=100)
    date = models.DateField('下单 / 补单日期', default=timezone.localdate)
    quantity = models.PositiveIntegerField('数量')
    price = models.DecimalField('销售单价', max_digits=16, decimal_places=4)
    currency = models.CharField('销售币种', max_length=3, choices=CURRENCIES, default='USD')
    quote_fx = models.DecimalField('报价汇率：1 销售币种 = 多少 CNY', max_digits=16, decimal_places=8, default=Decimal('7'))
    cost = models.DecimalField('人民币单位成本', max_digits=16, decimal_places=4)
    supplier = models.CharField('供应商名称', max_length=160)
    freight_included = models.BooleanField('销售单价已含运费', default=True)
    freight = models.DecimalField('客户运费总额（已含则仅备注）', max_digits=16, decimal_places=2, default=ZERO)
    customer_terms = models.CharField('客户付款约定', max_length=200, default='发货前结清')
    supplier_terms = models.CharField('供应商付款约定', max_length=200, default='发货前结清')

    class Meta:
        ordering = ['date', 'id']
        constraints = [models.UniqueConstraint(fields=['order', 'label'], name='unique_order_line_label'), models.CheckConstraint(condition=Q(quantity__gt=0) & Q(price__gt=0) & Q(cost__gte=0) & Q(quote_fx__gt=0) & Q(freight__gte=0), name='line_positive_values')]

    @property
    def sale_total(self):
        return money(self.price * self.quantity + (ZERO if self.freight_included else self.freight))

    @property
    def cost_total(self):
        return money(self.cost * self.quantity)

    def __str__(self):
        return f'{self.label} · {self.model} · {self.quantity} pcs'

class Transaction(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='transactions')
    kind = models.CharField('类型', max_length=8, choices=[('receipt', '客户收款'), ('payment', '供应商付款')])
    date = models.DateField('收付款日期', default=timezone.localdate)
    amount = models.DecimalField('原币金额', max_digits=18, decimal_places=2)
    currency = models.CharField('币种', max_length=3, choices=CURRENCIES, default='USD')
    party = models.CharField('付款公司 / 供应商名称', max_length=160)
    platform = models.CharField('收款 / 付款平台', max_length=100)
    fx = models.DecimalField('当日汇率：1 原币 = 多少目标币', max_digits=16, decimal_places=8, default=1)
    fx_source = models.CharField('汇率来源', max_length=160, default='人工确认')
    purpose = models.CharField('款项用途', max_length=240)
    reference = models.CharField('平台流水号（用于防止重复录入）', max_length=120)
    attachment = models.FileField('付款水单 / 收款凭证', upload_to=private_path, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']
        constraints = [models.UniqueConstraint(fields=['order', 'kind', 'reference'], name='unique_transaction_reference'), models.CheckConstraint(condition=Q(amount__gt=0) & Q(fx__gt=0), name='transaction_positive_values')]

    @property
    def equivalent(self):
        return money(self.amount * self.fx)

    @property
    def target_currency(self):
        return 'USD' if self.kind == 'receipt' else 'CNY'

    @property
    def unallocated(self):
        return money(self.amount - sum((a.source_amount for a in self.allocations.all()), ZERO))

class Shipment(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='shipments')
    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    date = models.DateField('发货日期', default=timezone.localdate)
    address = models.TextField('最终收货地址')
    carrier_type = models.CharField('运输方', max_length=10, choices=[('customer', '客户货代'), ('own', '我们的货代')])
    carrier = models.CharField('货代 / 运输公司名称', max_length=160)
    domestic_date = models.DateField('工厂到货代日期', blank=True, null=True)
    domestic_tracking = models.CharField('国内运单 / 货拉拉记录（多个可换行）', max_length=500, blank=True)
    international_tracking = models.CharField('国际快递单号（多个可换行）', max_length=500, blank=True)
    freight = models.DecimalField('实际运费', max_digits=16, decimal_places=2, default=ZERO)
    freight_currency = models.CharField('运费币种', max_length=3, choices=CURRENCIES, default='CNY')
    notes = models.TextField('装箱 / 标签 / 产品内容备注', blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        ordering = ['-date', '-id']
        constraints = [models.CheckConstraint(condition=Q(freight__gte=0), name='freight_nonnegative')]

class ShipmentLine(models.Model):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name='items')
    line = models.ForeignKey(Line, on_delete=models.PROTECT, related_name='shipment_items')
    quantity = models.PositiveIntegerField()
    sale_amount = models.DecimalField(max_digits=18, decimal_places=2)
    cost_amount = models.DecimalField(max_digits=18, decimal_places=2)
    receipt_reserved = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    payment_reserved = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['shipment', 'line'], name='unique_shipment_line'), models.CheckConstraint(condition=Q(quantity__gt=0), name='shipment_quantity_positive')]

    def covered(self, kind):
        reserved = self.receipt_reserved if kind == 'receipt' else self.payment_reserved
        return money(reserved + sum((a.target_amount for a in self.allocations.all() if a.transaction.kind == kind), ZERO))

    def due(self, kind):
        return money((self.sale_amount if kind == 'receipt' else self.cost_amount) - self.covered(kind))

class Allocation(models.Model):
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name='allocations')
    line = models.ForeignKey(Line, on_delete=models.PROTECT, related_name='allocations')
    shipment_item = models.ForeignKey(ShipmentLine, on_delete=models.PROTECT, related_name='allocations', null=True, blank=True)
    source_amount = models.DecimalField(max_digits=18, decimal_places=2)
    settlement_rate = models.DecimalField(max_digits=16, decimal_places=8)
    target_amount = models.DecimalField(max_digits=18, decimal_places=2)
    reason = models.CharField(max_length=240)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(source_amount__gt=0) & Q(settlement_rate__gt=0) & Q(target_amount__gt=0), name='allocation_positive')]

class Audit(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='audits')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=100)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

class Archive(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='archives')
    digest = models.CharField(max_length=64)
    content = models.BinaryField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class ApiMutation(models.Model):
    """Committed API responses; the business write and receipt commit together."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    key = models.UUIDField()
    operation = models.CharField(max_length=240)
    digest = models.CharField(max_length=64)
    response = models.JSONField()
    status_code = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'key'], name='unique_api_user_key')]
