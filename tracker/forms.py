from decimal import Decimal
from pathlib import Path
from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from .models import Order, Line, Transaction, Shipment, CURRENCIES

class StyledModelForm(forms.ModelForm):
    section_definitions = []

    @property
    def sections(self):
        definitions = self.section_definitions or [('基础信息', '', list(self.fields))]
        return [{'title':title,'description':description,'fields':[self[name] for name in names if name in self.fields]} for title,description,names in definitions]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field, forms.DateField):
                field.widget = forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'})
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs['rows'] = 3
            if isinstance(field, forms.DecimalField):
                field.widget.attrs['inputmode'] = 'decimal'

class OrderForm(StyledModelForm):
    section_definitions = [('客户与负责人','每个大订单归属于一名业务员。',['customer','owner']),('订单资料','后续补单会继续归入这个订单。',['number','date','notes'])]
    class Meta:
        model = Order
        fields = ['number', 'customer', 'owner', 'date', 'notes']

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['owner'].queryset = User.objects.filter(is_active=True)
        self.fields['owner'].label_from_instance = lambda u: u.get_full_name() or u.username
        self.fields['number'].widget.attrs['placeholder'] = '例如 ORD-20261023-001'
        self.fields['customer'].widget.attrs['placeholder'] = '填写客户公司全称'
        self.fields['owner'].help_text = '业务员仅能访问分配给自己的订单。'
        if not user.is_superuser:
            self.fields['owner'].queryset = User.objects.filter(pk=user.pk)
            self.fields['owner'].initial = user
            self.fields['owner'].disabled = True

class LineForm(StyledModelForm):
    preview_kind = 'line'
    section_definitions = [('产品与数量','同型号的不同补单也要分别记录。',['label','date','product','model','quantity']),('销售报价与运费','报价汇率用于记录定价依据，不自动抵扣收款。',['price','currency','quote_fx','freight_included','freight']),('供应商与结算约定','采购成本按人民币核算；付款约定分别保存。',['supply_type','supplier','cost','customer_terms','supplier_terms'])]
    class Meta:
        model = Line
        exclude = ['order']

    def __init__(self, *args, **kwargs):
        if args and args[0] is not None:
            data = args[0].copy()
            if 'supply_type' not in data:
                data['supply_type'] = 'external'
            args = (data, *args[1:])
        super().__init__(*args, **kwargs)
        self.fields['supplier'].required = False

    def clean(self):
        data = super().clean()
        for field in ['quantity', 'price', 'quote_fx']:
            if data.get(field) is not None and data[field] <= 0:
                self.add_error(field, '必须大于零。')
        for field in ['cost', 'freight']:
            if data.get(field) is not None and data[field] < 0:
                self.add_error(field, '不能小于零。')
        if 'quote_fx' in self.fields and data.get('currency') == 'CNY' and data.get('quote_fx') != 1:
            self.add_error('quote_fx', '销售币种为人民币，报价汇率必须为 1。')
        if data.get('supply_type') == 'self':
            data['supplier'] = '自供货'
        elif not data.get('supplier', '').strip():
            self.add_error('supplier', '外部供货请填写供应商名称。')
        return data

class SalespersonCreationForm(UserCreationForm):
    display_name = forms.CharField(label='业务员姓名', max_length=150)

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data['display_name']
        if commit:
            user.save()
        return user

class TransactionForm(StyledModelForm):
    preview_kind = 'transaction'
    section_definitions = [('收付款信息','原币金额与流水号请按实际记录填写。',['date','amount','currency','reference']),('公司、平台与用途','一笔流水可以在下一步分给多个小项。',['party','platform','purpose']),('汇率与凭证','当日折算汇率与后续分摊的结算汇率相互独立。',['fx','fx_source','attachment'])]

    def __init__(self, *args, kind=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_currency = 'CNY' if kind == 'payment' else 'USD'
        self.fields['date'].label = '付款日期' if kind == 'payment' else '收到款的日期'
        self.fields['party'].label = '供应商名称' if kind == 'payment' else '付款公司'
        self.fields['platform'].label = '付款平台' if kind == 'payment' else '收款平台'
        self.fields['fx'].help_text = f'1 原币 = 多少 {self.target_currency}。相同币种填 1。'
        self.fields['attachment'].widget.attrs['accept'] = '.pdf,.png,.jpg,.jpeg'
        self.fields['attachment'].help_text = 'PDF / PNG / JPG，最大 10MB；仅订单授权人员可下载。'
        self.fields['reference'].help_text = '填写平台流水编号，用于检查同一订单的重复录入。'
    class Meta:
        model = Transaction
        exclude = ['order', 'kind', 'created_by', 'created_at']

    def clean(self):
        data = super().clean()
        for field in ['amount', 'fx']:
            if data.get(field) is not None and data[field] <= 0:
                self.add_error(field, '必须大于零。')
        return data

    def clean_attachment(self):
        f = self.cleaned_data.get('attachment')
        if f:
            if f.size > 10 * 1024 * 1024:
                raise ValidationError('凭证最大 10MB。')
            suffix = Path(f.name).suffix.lower()
            signature = f.read(8)
            f.seek(0)
            valid = {'.pdf': signature.startswith(b'%PDF-'), '.png': signature.startswith(b'\x89PNG\r\n\x1a\n'), '.jpg': signature.startswith(b'\xff\xd8\xff'), '.jpeg': signature.startswith(b'\xff\xd8\xff')}
            if not valid.get(suffix):
                raise ValidationError('仅接受文件签名匹配的 PDF、PNG、JPG 凭证。')
        return f

class ShipmentForm(StyledModelForm):
    token = forms.UUIDField(widget=forms.HiddenInput())

    class Meta:
        model = Shipment
        exclude = ['order', 'created_by', 'reference']

class AllocationForm(forms.Form):
    line = forms.ModelChoiceField(queryset=Line.objects.none(), label='分摊到订单小项')
    shipment_item = forms.ChoiceField(label='资金用途', required=False)
    amount = forms.DecimalField(label='分摊原币金额', max_digits=18, decimal_places=2, min_value=Decimal('0.01'))
    rate = forms.DecimalField(label='结算汇率：1 原币 = 多少小项币种（付款为 CNY）', max_digits=16, decimal_places=8, min_value=Decimal('0.00000001'), initial=1)
    reason = forms.CharField(label='分摊说明', max_length=240, initial='按订单约定分摊')

    def __init__(self, *args, order, txn=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.line_metadata = {}
        self.batch_metadata = {}
        eligible = []
        from .services import line_summary
        for line in order.lines.all():
            s = line_summary(line)
            due = s['receivable'] if not txn or txn.kind == 'receipt' else s['payable']
            if txn and (due <= 0 or (txn.kind == 'payment' and txn.party.strip() != line.supplier.strip())):
                continue
            eligible.append(line.pk)
            self.line_metadata[str(line.pk)] = {'currency':line.currency if not txn or txn.kind=='receipt' else 'CNY','due':str(due),'remaining':s['remaining'],'supplier':line.supplier,'label':str(line)}
        self.fields['line'].queryset = order.lines.filter(pk__in=eligible)
        self.fields['line'].label_from_instance = lambda line: f'{line.label} · {line.model} · 待结算 {self.line_metadata[str(line.pk)]["due"]} {self.line_metadata[str(line.pk)]["currency"]}'
        choices = [('', '未发货资金池（按剩余数量均摊）')]
        for ship in order.shipments.all():
            for item in ship.items.select_related('line'):
                if item.line_id not in eligible:
                    continue
                due = item.due(txn.kind if txn else 'receipt')
                if due <= 0:
                    continue
                choices.append((str(item.pk),f'批次 #{ship.pk} / {item.line.label} / 待结算 {due}'))
                self.batch_metadata[str(item.pk)] = {'line':str(item.line_id),'due':str(due)}
        self.fields['shipment_item'].choices = choices
        if txn:
            self.fields['amount'].initial = txn.unallocated
            self.fields['amount'].widget.attrs['max'] = str(txn.unallocated)
            self.fields['amount'].help_text = f'这笔流水尚可分摊 {txn.unallocated} {txn.currency}。'
        if len(eligible)==1:
            self.fields['line'].initial = eligible[0]

class BatchLineForm(LineForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['label'].required = False
        self.fields['label'].widget.attrs['placeholder'] = '自动编号'
        for name in ['product', 'model', 'supplier']:
            self.fields[name].widget.attrs['list'] = f'entry-{name}-options'
            self.fields[name].widget.attrs['autocomplete'] = 'off'

    class Meta:
        model = Line
        fields = ['label', 'product', 'model', 'quantity', 'price', 'currency', 'supply_type', 'supplier', 'cost', 'freight']

class LineDefaultsForm(forms.Form):
    date = forms.DateField(label='本次下单日期', widget=forms.DateInput(attrs={'type': 'date'}))
    freight_included = forms.BooleanField(label='单价已含运费', required=False, initial=True)
    customer_terms = forms.CharField(label='客户付款约定', max_length=200, initial='发货前结清')
    supplier_terms = forms.CharField(label='供应商付款约定', max_length=200, initial='发货前结清')
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for currency, label in CURRENCIES:
            if currency == 'CNY':
                continue
            self.fields[f'rate_{currency}'] = forms.DecimalField(label=f'1 {currency} = CNY', max_digits=16, decimal_places=8, min_value=Decimal('0.00000001'), required=False)
