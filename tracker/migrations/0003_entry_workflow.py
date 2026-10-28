from django.db import migrations, models
from django.db.models import F

def copy_costs(apps, schema_editor):
    apps.get_model('tracker', 'ShipmentLine').objects.using(schema_editor.connection.alias).update(internal_cost_amount=F('cost_amount'))

class Migration(migrations.Migration):
    dependencies = [('tracker', '0002_apimutation')]
    operations = [
        migrations.AddField(model_name='order', name='exchange_rates', field=models.JSONField(blank=True, default=dict, verbose_name='本订单常用汇率')),
        migrations.AddField(model_name='line', name='supply_type', field=models.CharField(choices=[('external', '外部供应商'), ('self', '自供货')], default='external', max_length=10, verbose_name='供货方式')),
        migrations.AlterField(model_name='line', name='supplier', field=models.CharField(blank=True, max_length=160, verbose_name='供应商名称')),
        migrations.AddField(model_name='shipmentline', name='internal_cost_amount', field=models.DecimalField(decimal_places=2, default=0, max_digits=18)),
        migrations.RunPython(copy_costs, migrations.RunPython.noop),
    ]
