"""Exercise the actual table UI against disposable CI data, with screenshots."""
import os
import subprocess
import sys
import time
import uuid
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from tracker.models import Order, Line, Transaction
from tracker.product_import import template_bytes
from openpyxl import load_workbook
from playwright.sync_api import sync_playwright

db_pool = ThreadPoolExecutor(max_workers=1)
def db(fn):
    return db_pool.submit(fn).result()

output = Path('browser-artifacts')
output.mkdir(exist_ok=True)
call_command('migrate', verbosity=0)
User = get_user_model()
admin = User.objects.create_user('browser-check-' + uuid.uuid4().hex[:8], is_superuser=True)
owner = User.objects.create_user('browser-sales-' + uuid.uuid4().hex[:8], first_name='chris')
order = Order.objects.create(number='BROWSER-' + uuid.uuid4().hex[:8], customer='虚构界面验收客户', owner=owner)
client = Client()
client.force_login(admin)
cookie = client.cookies[settings.SESSION_COOKIE_NAME].value
server = subprocess.Popen([sys.executable, 'manage.py', 'runserver', '127.0.0.1:8876', '--noreload', '--insecure'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
base = 'http://127.0.0.1:8876'
try:
    for attempt in range(60):
        try:
            with urlopen(base + '/health/live/', timeout=2) as response:
                if response.status == 200:
                    break
        except Exception:
            time.sleep(.5)
    else:
        raise RuntimeError('Browser test server did not start')
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={'width':1440,'height':1000})
        context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':cookie,'url':base}])
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'{base}/orders/{order.pk}/lines/new/')
        for index in range(3):
            page.locator(f'#id_lines-{index}-product').fill('虚构产品 ' + str(index))
            page.locator(f'#id_lines-{index}-model').fill('UI-' + str(index))
            page.locator(f'#id_lines-{index}-quantity').fill('100')
            page.locator(f'#id_lines-{index}-price').fill('10')
            page.locator(f'#id_lines-{index}-cost').fill('4')
        page.locator('#id_rate_USD').fill('7.1')
        page.locator('#id_lines-0-supplier').fill('虚构工厂')
        page.locator('#id_lines-2-supplier').fill('虚构工厂')
        page.locator('#id_lines-1-supply_type').select_option('self')
        assert page.locator('#id_lines-1-supplier').input_value() == '自供货'
        page.get_by_role('button',name='＋ 添加一行',exact=True).click()
        assert page.locator('.product-row').count()==4
        page.locator('.product-row').last.get_by_role('button',name='移除此行').click()
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(output/'01-product-table.png'),full_page=True)
        invalid = page.locator('#line-table-form').evaluate("form => [...form.querySelectorAll(':invalid')].map(e => [e.name,e.validationMessage])")
        assert not invalid, invalid
        page.get_by_role('button',name='保存全部产品 →',exact=True).click()
        page.screenshot(path=str(output/'01b-product-save-result.png'),full_page=True)
        assert page.url.endswith(f'/orders/{order.pk}/'), page.locator('body').inner_text()[-7000:]
        assert db(lambda: order.lines.count())==3
        first,second,third=db(lambda: list(order.lines.all()))
        assert first.quote_fx==second.quote_fx
        assert second.supply_type=='self' and second.payable_total==0

        # Currency change uses the remembered/derived order rate automatically.
        page.goto(f'{base}/orders/{order.pk}/transactions/receipt/new/')
        page.locator('#id_currency').select_option('CNY')
        assert page.locator('#id_fx').input_value() not in ['', '1']
        txn=db(lambda: Transaction.objects.create(order=order,kind='receipt',amount=1500,currency='USD',party='虚构客户',platform='测试平台',fx=1,purpose='虚构界面验收',reference=uuid.uuid4().hex,created_by=admin))
        page.goto(f'{base}/transactions/{txn.pk}/allocate/')
        page.locator('#allocation-select-all').check()
        page.get_by_role('button',name='填满所选余额',exact=True).click()
        assert page.locator(f'[name="amount_{first.pk}_pool"]').input_value()=='1000.00'
        assert page.locator(f'[name="amount_{second.pk}_pool"]').input_value()=='500.00'
        assert not page.locator(f'[name="selected"][value="{third.pk}_pool"]').is_checked()
        page.screenshot(path=str(output/'02-bulk-allocation.png'),full_page=True)
        page.get_by_role('button',name='确认所选分摊 →',exact=True).click()
        page.get_by_role('heading',name='这笔流水已全部分摊').wait_for()
        assert db(lambda: txn.allocations.count())==2 and db(lambda: txn.unallocated)==0

        page.goto(f'{base}/orders/{order.pk}/shipments/new/')
        page.get_by_role('button',name='一键全发',exact=True).click()
        assert page.locator(f'[name="qty_{first.pk}"]').input_value()=='100'
        assert page.locator(f'[name="qty_{second.pk}"]').input_value()=='100'
        page.get_by_role('button',name='计算本批应收应付 ↓',exact=True).click()
        page.get_by_role('heading',name='本批结算预览',exact=True).wait_for()
        assert db(lambda: order.shipments.count())==0
        page.screenshot(path=str(output/'03-shipment-preview.png'),full_page=True)
        page.locator('#id_address').fill('虚构验收地址')
        page.locator('#id_carrier').fill('虚构验收货代')
        page.locator('#id_carrier_type').select_option('own')
        page.on('dialog',lambda dialog:dialog.accept())
        page.get_by_role('button',name='确认实际发货 →',exact=True).click()
        page.wait_for_url(f'**/orders/{order.pk}/')
        item=db(lambda: order.shipments.get().items.get(line=second))
        assert item.cost_amount==0 and item.internal_cost_amount==400
        page.screenshot(path=str(output/'04-order-overview.png'),full_page=True)

        # Template upload populates a draft and never silently commits products.
        page.goto(f'{base}/orders/{order.pk}/lines/new/')
        with page.expect_download() as info:
            page.get_by_role('link',name='↓ 下载 Excel 模板',exact=True).click()
        template=output/'product-template.xlsx'
        info.value.save_as(str(template))
        book=load_workbook(template)
        book['产品录入'].append(['','虚构导入产品','IMPORT',5,8,'USD','自供货','',2,0])
        book.save(template)
        page.locator('[name="product_file"]').set_input_files(str(template))
        page.get_by_role('button',name='导入并预览',exact=True).click()
        assert page.locator('#id_lines-0-product').input_value()=='虚构导入产品'
        assert db(lambda: order.lines.count())==3
        page.screenshot(path=str(output/'05-import-preview.png'),full_page=True)

        page.goto(f'{base}/orders/{order.pk}/')
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile page overflows viewport'
        page.screenshot(path=str(output/'06-mobile-order.png'),full_page=True)
        assert not errors, errors
        browser.close()
    print('Browser workflow passed: table entry, remembered FX, bulk allocation, shipment preview/confirm, template import, mobile layout.')
finally:
    db_pool.shutdown(wait=True)
    server.terminate()
    server.wait(timeout=10)
