import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def number_lines(apps, schema_editor):
    Line = apps.get_model('tracker', 'Line')
    current_order = None
    number = 0
    for line in Line.objects.order_by('order_id', 'id').iterator():
        if line.order_id != current_order:
            current_order, number = line.order_id, 0
        number += 1
        Line.objects.filter(pk=line.pk).update(sequence=number)


class Migration(migrations.Migration):
    dependencies = [('tracker', '0003_entry_workflow'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(model_name='line', name='sequence', field=models.PositiveIntegerField(editable=False, null=True, verbose_name='系统序号')),
        migrations.RunPython(number_lines, migrations.RunPython.noop),
        migrations.AlterModelOptions(name='line', options={'ordering': ['sequence', 'id']}),
        migrations.AddConstraint(model_name='line', constraint=models.UniqueConstraint(fields=('order', 'sequence'), name='unique_order_line_sequence')),
        migrations.AlterField(model_name='transaction', name='purpose', field=models.CharField(blank=True, max_length=240, verbose_name='款项用途')),
        migrations.AlterField(model_name='shipment', name='freight', field=models.DecimalField(blank=True, decimal_places=2, default=None, max_digits=16, null=True, verbose_name='实际运费')),
        migrations.CreateModel(name='TransactionCorrection', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('action', models.CharField(choices=[('party', '更正公司名称'), ('void', '冲销误录')], max_length=8)),
            ('party', models.CharField(blank=True, max_length=160)),
            ('source_delta', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
            ('reason', models.CharField(max_length=240)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ('transaction', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='corrections', to='tracker.transaction')),
        ], options={'ordering': ['id'], 'constraints': [
            models.UniqueConstraint(fields=('transaction',), condition=models.Q(action='void'), name='unique_transaction_void'),
            models.CheckConstraint(condition=(models.Q(action='party') & models.Q(source_delta=0)) | (models.Q(action='void') & models.Q(source_delta__lt=0)), name='correction_valid_delta'),
        ]}),
        migrations.CreateModel(name='ShipmentFreightRecord', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('amount', models.DecimalField(decimal_places=2, max_digits=16, verbose_name='实际运费')),
            ('currency', models.CharField(choices=[('USD', 'USD 美元'), ('CNY', 'CNY 人民币'), ('EUR', 'EUR 欧元'), ('HKD', 'HKD 港币'), ('GBP', 'GBP 英镑')], max_length=3, verbose_name='运费币种')),
            ('reason', models.CharField(max_length=240, verbose_name='补录 / 更正说明')),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ('shipment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='freight_records', to='tracker.shipment')),
        ], options={'ordering': ['id'], 'constraints': [models.CheckConstraint(condition=models.Q(amount__gte=0), name='freight_record_nonnegative')]}),
        migrations.CreateModel(name='AllocationCoverage', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('amount', models.DecimalField(decimal_places=2, max_digits=18)),
            ('allocation', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='coverages', to='tracker.allocation')),
            ('shipment_item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='coverages', to='tracker.shipmentline')),
        ], options={'constraints': [
            models.UniqueConstraint(fields=('allocation', 'shipment_item'), name='unique_allocation_coverage'),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='coverage_positive'),
        ]}),
    ]
