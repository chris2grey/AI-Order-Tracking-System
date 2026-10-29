
import {spawn,spawnSync}from'node:child_process';
import {mkdirSync,writeFileSync,readdirSync,readFileSync}from'node:fs';
import {randomBytes}from'node:crypto';
import {chromium}from'playwright';
const base='http://127.0.0.1:4180',output='browser-artifacts';
mkdirSync(output,{recursive:true});
const password='CI-'+randomBytes(18).toString('hex'),secret=randomBytes(48).toString('hex');
const accounts=[{username:'summer',password,role:'admin'},{username:'chris',password,role:'sales'},{username:'coco',password,role:'sales'}];
const vars="SESSION_KEY='"+secret+"'\nBOOTSTRAP_ACCOUNTS='"+JSON.stringify(accounts)+"'\nSITE_ORIGIN='"+base+"'\n";
writeFileSync('.dev.vars',vars);writeFileSync('dist/server/.dev.vars',vars);
for(const file of readdirSync('drizzle').filter(n=>n.endsWith('.sql')).sort()){
  const result=spawnSync(process.execPath,['--import','./scripts/sites-env.mjs','node_modules/wrangler/bin/wrangler.js','d1','execute','DB','--local','--config','dist/server/wrangler.json','--persist-to','.wrangler/state','--file','drizzle/'+file],{encoding:'utf8'});
  if(result.status)throw Error('D1 preview migration failed: '+result.stderr.slice(-2000));
}
const server=spawn(process.execPath,['--import','./scripts/sites-env.mjs','node_modules/wrangler/bin/wrangler.js','dev','--config','dist/server/wrangler.json','--local','--persist-to','.wrangler/state','--ip','127.0.0.1','--port','4180','--inspector-port','0'],{stdio:['ignore','pipe','pipe']});
let log='';server.stdout.on('data',x=>log+=x);server.stderr.on('data',x=>log+=x);
let browser;
try{
  let ready=false;for(let i=0;i<100;i++){try{const response=await fetch(base+'/api/health');if(response.ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,500));}
  if(!ready)throw Error('Built preview failed: '+log.slice(-3000));
  browser=await chromium.launch();const context=await browser.newContext({viewport:{width:1500,height:1050}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(String(e)));page.on('dialog',d=>d.accept());
  await page.goto(base);await page.getByLabel('账号',{exact:true}).fill('summer');await page.getByLabel('密码',{exact:true}).fill(password);await page.getByRole('button',{name:'进入工作台 →',exact:true}).click();await page.getByRole('heading',{name:'业务总览',exact:true}).waitFor();
  await page.getByRole('button',{name:'＋ 新建订单',exact:true}).click();
  await page.getByLabel('客户名称 *',{exact:true}).fill('虚构 Sites 验收客户');
  await page.getByLabel('订单编号 *',{exact:true}).fill('SITES-CI-'+Date.now());
  await page.getByLabel('负责业务员 *',{exact:true}).selectOption({label:'chris'});
  await page.getByRole('button',{name:'创建订单，录入产品 →',exact:true}).click();
  await page.getByRole('heading',{name:'表格录入产品 / 补单',exact:true}).waitFor();
  const rows=page.locator('.entry-table tbody tr');
  for(let i=0;i<3;i++){
    const row=rows.nth(i);
    await row.locator('td').nth(1).locator('input').fill('虚构产品 '+i);
    await row.locator('td').nth(2).locator('input').fill('MODEL-'+i);
    await row.locator('td').nth(3).locator('input').fill('100');
    await row.locator('td').nth(4).locator('input').fill('10');
    await row.locator('td').nth(8).locator('input').fill('4');
    if(i===1)await row.locator('td').nth(6).locator('select').selectOption('self');
    else await row.locator('td').nth(7).locator('input').fill('虚构供应商');
  }
  const qty=rows.first().locator('td').nth(3).locator('input');await qty.focus();await qty.hover();await page.mouse.wheel(0,-500);
  if(await qty.inputValue()!=='100')throw Error('Mouse wheel changed quantity');
  if(await rows.first().locator('td').first().innerText()!=='自动')throw Error('System serial is editable');
  await page.screenshot({path:output+'/01-product-table.png',fullPage:true});
  await page.getByRole('button',{name:'保存全部产品 →',exact:true}).click();
  await page.getByRole('heading',{name:'产品小项',exact:true}).waitFor();
  let orderId=new URL(page.url()).hash.split('/').at(-1);
  const api=async(path,data,key=crypto.randomUUID())=>{
    const response=await context.request.post(base+'/api'+path,{headers:{Origin:base,'Idempotency-Key':key},data});
    if(!response.ok())throw Error(path+': '+await response.text());return response.json();
  };
  const read=async()=>((await (await context.request.get(base+'/api/orders/'+orderId)).json()).order);
  let state=await read();if(state.lines.length!==3)throw Error('Table did not persist');
  await page.getByRole('button',{name:'＋ 客户收款',exact:true}).click();
  await page.getByLabel('原币金额 *',{exact:true}).fill('1500');
  await page.getByLabel('平台流水号 *',{exact:true}).fill('SITES-RECEIPT-1');
  await page.getByLabel('收款平台 *',{exact:true}).fill('虚构银行');
  if(await page.getByLabel('款项用途（选填）').getAttribute('required')!==null)throw Error('Purpose is required');
  await page.getByRole('button',{name:'保存流水，去分摊 →',exact:true}).click();
  await page.getByRole('heading',{name:'批量款项分摊',exact:true}).waitFor();
  await page.getByRole('checkbox',{name:'选择全部产品',exact:true}).check();
  await page.getByRole('button',{name:'填满所选余额',exact:true}).click();
  await page.screenshot({path:output+'/02-product-allocation.png',fullPage:true});
  await page.getByRole('button',{name:'确认所选分摊 →',exact:true}).click();await page.getByRole('heading',{name:'产品小项',exact:true}).waitFor();
  await page.getByRole('button',{name:'新建发货 →',exact:true}).click();
  await page.getByRole('button',{name:'一键全发',exact:true}).click();
  await page.getByRole('button',{name:'计算本批应收应付 ↓',exact:true}).click();
  await page.getByRole('heading',{name:'本批结算预览',exact:true}).waitFor();
  await page.getByLabel('最终收货地址 *',{exact:true}).fill('虚构验收仓库');
  await page.getByLabel('货代 / 运输公司 *',{exact:true}).fill('虚构货代');
  await page.screenshot({path:output+'/03-shipment-preview.png',fullPage:true});
  await page.getByRole('button',{name:'确认实际发货 →',exact:true}).click();await page.getByRole('heading',{name:'产品小项',exact:true}).waitFor();
  state=await read();if(state.shipments[0].freight!==null)throw Error('Missing freight became zero');
  await page.getByRole('button',{name:'补录运费 / 修改物流 →',exact:true}).first().click();
  await page.getByLabel('本批实际运费总额 *',{exact:true}).fill('128.5');
  await page.getByLabel('补录 / 更正说明 *',{exact:true}).fill('虚构后补账单');
  await page.getByRole('button',{name:'保存运费',exact:true}).click();
  await page.getByText('当前运费 128.50 CNY',{exact:true}).waitFor();
  await page.getByLabel('国际单号',{exact:true}).fill('FAKE-HK-REPLACED');
  await page.getByLabel('修改说明 *',{exact:true}).fill('虚构香港换单');
  await page.getByRole('button',{name:'保存物流信息',exact:true}).click();
  await page.getByText(/国际单号：空 → FAKE-HK-REPLACED/).first().waitFor();
  await page.screenshot({path:output+'/04-shipment-followup.png',fullPage:true});
  state=await read();if(state.shipments[0].freightRecords[0].amount!=='128.50'||state.shipments[0].internationalTracking!=='FAKE-HK-REPLACED')throw Error('Followup not persisted');
  const wrong=await api('/orders/'+orderId+'/transaction',{kind:'payment',date:'2026-10-29',amount:'400',currency:'CNY',party:'虚构错名',platform:'虚构银行',fx:'1',reference:'SITES-WRONG'});
  await page.goto(base+'/#/allocation/'+orderId+'/'+wrong.transactionId);await page.getByRole('heading',{name:'批量款项分摊',exact:true}).waitFor();
  await page.getByRole('button',{name:'更正公司名称 / 冲销误录 →',exact:true}).click();
  await page.getByLabel('正确的公司 / 供应商名称 *',{exact:true}).fill('虚构供应商');
  await page.getByLabel('更正 / 冲销原因 *',{exact:true}).fill('虚构名称更正');
  await page.getByRole('button',{name:'保存更正 →',exact:true}).click();
  await page.getByRole('columnheader',{name:'总数量',exact:true}).waitFor();
  await page.screenshot({path:output+'/05-corrected-allocation.png',fullPage:true});
  const later=await api('/orders/'+orderId+'/transaction',{kind:'receipt',amount:'1500',currency:'USD',party:'虚构客户',platform:'虚构银行',fx:'1',reference:'SITES-LATER'});
  state=await read();
  const receipt=state.transactions.find(t=>t.id===later.transactionId);
  await api('/orders/'+orderId+'/allocation',{transactionId:receipt.id,entries:[{lineId:state.lines[1].id,amount:'500'},{lineId:state.lines[2].id,amount:'1000'}],rates:{USD:'1'},reason:'虚构发货后按产品分摊'});
  await page.goto(base+'/#/lines/'+orderId);await page.getByRole('heading',{name:'表格录入产品 / 补单',exact:true}).waitFor();
  const downloadPromise=page.waitForEvent('download');await page.getByRole('button',{name:'↓ 下载 Excel 模板',exact:true}).click();const file=await downloadPromise;
  await file.saveAs(output+'/product-template.xlsx');
  // Real XLSX produced by Excel-compatible XML, tested against openpyxl outside the browser.
  const upload=readFileSync('test-artifacts/import-products.xlsx');
  await page.getByLabel('产品模板文件',{exact:true}).setInputFiles({name:'products.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:upload});
  await page.getByRole('button',{name:'导入并预览',exact:true}).click();
  await page.getByText('已导入 1 行草稿，尚未保存到订单。',{exact:true}).waitFor();
  if((await read()).lines.length!==3)throw Error('Import silently saved rows');
  await page.screenshot({path:output+'/06-import-draft.png',fullPage:true});
  await page.goto(base+'/#/order/'+orderId);await page.getByRole('heading',{name:'产品小项',exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});
  if(!await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))throw Error('Mobile viewport overflows');
  await page.screenshot({path:output+'/07-mobile-order.png',fullPage:true});
  if(errors.length)throw Error('Browser errors: '+errors.join('; '));
  console.log('Sites browser passed: persisted table entry, wheel guard, optional purpose, product allocations before/after shipment, late freight, Hong Kong tracking, name correction, real XLSX import and mobile layout.');
}catch(error){
  if(browser){try{for(const context of browser.contexts())for(const page of context.pages()){await page.screenshot({path:output+'/failure.png',fullPage:true});writeFileSync(output+'/failure.txt',(await page.locator('body').innerText()).slice(0,15000));}}catch{}}
  throw error;
}finally{
  if(browser)await browser.close();
  server.kill('SIGTERM');await new Promise(r=>setTimeout(r,1000));
  writeFileSync(output+'/preview.log',log.replaceAll(password,'[redacted]').replaceAll(secret,'[redacted]'));
}
