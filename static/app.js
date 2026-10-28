document.querySelectorAll('[data-confirm]').forEach(button => button.addEventListener('click', event => {
  if (!window.confirm(button.dataset.confirm)) event.preventDefault();
}));
// All local money previews use fixed-point integers; the server remains authoritative.
function scaled(raw, places) {
  const text = String(raw ?? '').trim();
  if (!text || !/^\d*\.?\d*$/.test(text) || !/\d/.test(text) || text.length > 35) return null;
  const [whole='0', fraction=''] = text.split('.');
  if (fraction.length > places) return null;
  return BigInt(whole || '0') * 10n**BigInt(places) + BigInt(fraction.padEnd(places,'0') || '0');
}
function centsText(value) {
  return (value/100n).toLocaleString('en-US') + '.' + (value%100n).toString().padStart(2,'0');
}
function roundedDivide(value, divisor) { return (value + divisor/2n)/divisor; }

document.querySelectorAll('form[data-unsaved]').forEach(form => {
  let dirty = false;
  form.addEventListener('input', () => { dirty = true; });
  form.addEventListener('change', () => { dirty = true; });
  form.addEventListener('submit', event => {
    if (event.defaultPrevented) return;
    if (form.dataset.submitting) { event.preventDefault(); return; }
    form.dataset.submitting = 'true'; dirty = false; form.setAttribute('aria-busy','true');
    const button = event.submitter;
    if (button) { button.dataset.originalText=button.textContent; button.textContent = '正在保存…'; }
  });
  window.addEventListener('beforeunload', event => {
    if (dirty) { event.preventDefault(); event.returnValue = ''; }
  });
  window.addEventListener('pageshow', () => {
    delete form.dataset.submitting; form.removeAttribute('aria-busy');
    form.querySelectorAll('[data-original-text]').forEach(button=>{button.textContent=button.dataset.originalText;});
  });
});
document.querySelector('.error-summary')?.focus();

const previewForm = document.querySelector('form[data-preview="line"], form[data-preview="transaction"]');
if (previewForm) {
  const fields = previewForm.elements;
  const kind = previewForm.dataset.preview;
  const primary = document.getElementById('estimate-primary');
  const secondary = document.getElementById('estimate-secondary');
  function syncRate(changed=false) {
    const rate = kind === 'line' ? fields.quote_fx : fields.fx;
    const target = kind === 'line' ? 'CNY' : previewForm.dataset.target;
    const same = fields.currency.value === target;
    rate.readOnly = same;
    if (same) rate.value = '1'; else if (changed) {
      const saved = document.getElementById('order-rates');
      rate.value = saved ? (JSON.parse(saved.textContent)[fields.currency.value] || '') : '';
    }
    const label = previewForm.querySelector(`label[for="${rate.id}"]`);
    label.textContent = `${kind==='line'?'报价':'当日'}汇率：1 ${fields.currency.value} = 多少 ${target} *`;
  }
  function calculate() {
    if (kind === 'line') {
      const qty=scaled(fields.quantity.value,0), price=scaled(fields.price.value,4), cost=scaled(fields.cost.value,4), freight=scaled(fields.freight.value || '0',2);
      if (qty===null || price===null || qty<=0n || freight===null) {
        primary.textContent='填写数量与单价后自动计算'; secondary.textContent=''; return;
      }
      const sale=roundedDivide(qty*price,100n) + (fields.freight_included.checked ? 0n : freight);
      primary.textContent=`销售总额 ${centsText(sale)} ${fields.currency.value}`;
      secondary.textContent=cost===null ? '' : `产品采购总额 ${centsText(roundedDivide(qty*cost,100n))} CNY`;
    } else {
      const amount=scaled(fields.amount.value,2), rate=scaled(fields.fx.value,8);
      primary.textContent=amount!==null && rate!==null ? `当日等值 ${centsText(roundedDivide(amount*rate,100000000n))} ${previewForm.dataset.target}` : '填写原币金额与当日汇率后自动计算';
      secondary.textContent='同币种换算自动为 1，已有订单汇率会在分摊时自动复用。';
    }
  }
  fields.currency.addEventListener('change', () => { syncRate(true); calculate(); });
  previewForm.addEventListener('input',calculate);
  previewForm.addEventListener('change',calculate);
  syncRate(); calculate();
}

const allocationForm=document.getElementById('allocation-form');
if (allocationForm) {
  const lines=JSON.parse(document.getElementById('allocation-lines').textContent);
  const batches=JSON.parse(document.getElementById('allocation-batches').textContent);
  const f=allocationForm.elements;
  function updateAllocation(changed=false) {
    const meta=lines[f.line.value];
    for (const option of f.shipment_item.options) {
      const eligible=!option.value || batches[option.value]?.line===f.line.value;
      option.hidden=!eligible; option.disabled=!eligible;
    }
    if (f.shipment_item.selectedOptions[0]?.disabled) f.shipment_item.value='';
    if (!meta) {
      document.getElementById('allocation-line-summary').textContent='选择小项后查看余额';
      document.getElementById('allocation-batch-hint').textContent='';
      document.getElementById('allocation-estimate').textContent='';
      f.shipment_item.required=false;
      return;
    }
    const same=allocationForm.dataset.source===meta.currency;
    f.rate.readOnly=same;
    if (same) f.rate.value='1'; else if (changed) f.rate.value='';
    allocationForm.querySelector('label[for="id_rate"]').textContent=`结算汇率：1 ${allocationForm.dataset.source} = 多少 ${meta.currency}`;
    document.getElementById('allocation-line-summary').textContent=`小项待结算 ${centsText(scaled(meta.due,2))} ${meta.currency} · 未发 ${meta.remaining} pcs`;
    const hasDebt=Object.values(batches).some(b=>b.line===f.line.value);
    f.shipment_item.required=hasDebt;
    f.shipment_item.options[0].textContent=hasDebt?'请选择要结算的发货批次':'未发货资金池（按剩余数量均摊）';
    document.getElementById('allocation-batch-hint').textContent=hasDebt ? '此小项有已发货欠款，请选择要结算的批次。' : '选择资金池后，这笔款将用于后续发货按剩余数量分摊。';
    const amount=scaled(f.amount.value,2), rate=scaled(f.rate.value,8);
    const estimate=document.getElementById('allocation-estimate');
    if (amount!==null && rate!==null) {
      const target=roundedDivide(amount*rate,100000000n);
      const limit=scaled(batches[f.shipment_item.value]?.due ?? meta.due,2);
      estimate.textContent=`预计抵扣 ${centsText(target)} ${meta.currency}${target>limit?' · 超过可结算余额，请调整金额':''}`;
      estimate.className=target>limit?'attention':'positive';
    } else estimate.textContent='填写结算汇率后查看预计抵扣金额。';
  }
  f.line.addEventListener('change',()=>updateAllocation(true));
  allocationForm.addEventListener('input',()=>updateAllocation());
  f.shipment_item.addEventListener('change',()=>updateAllocation());
  updateAllocation();
}
const canvas = document.getElementById('cash-chart');
if (canvas) {
  const rows = JSON.parse(document.getElementById('chart-data').textContent);
  function draw() {
    const box = canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = box.width * dpr; canvas.height = 228 * dpr;
    canvas.style.width = box.width + 'px'; canvas.style.height = '228px';
    const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr);
    const w = box.width, h = 228, left = 55, right = w - 60, top = 30, bottom = 193;
    ctx.font = '11px "Segoe UI",sans-serif';
    if (!rows.length) { ctx.fillStyle = '#899697'; ctx.textAlign = 'center'; ctx.fillText('录入流水后显示资金趋势', w/2, h/2); return; }
    const maxIn = Math.max(...rows.map(r=>r.receipt), 1) * 1.2;
    const maxOut = Math.max(...rows.map(r=>r.payment), 1) * 1.2;
    const compact = v => v >= 1000 ? (v/1000).toFixed(0)+'k' : v.toFixed(0);
    for(let j=0;j<=3;j++) {
      const y=top+(bottom-top)*j/3;
      ctx.strokeStyle='#edf1f1'; ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();
      ctx.fillStyle='#879495';ctx.textAlign='right';ctx.fillText('$'+compact(maxIn*(1-j/3)),left-9,y+4);
      ctx.textAlign='left';ctx.fillText('¥'+compact(maxOut*(1-j/3)),right+9,y+4);
    }
    const step=(right-left)/Math.max(rows.length,1);
    rows.forEach((r,j)=>{
      const x=left+step*(j+.5); const bw=Math.min(23,step*.25);
      [[r.receipt,maxIn,'#24877c',-bw-2],[r.payment,maxOut,'#b7d9d1',2]].forEach(([v,max,color,offset])=>{
        const bh=v/max*(bottom-top);ctx.fillStyle=color;ctx.beginPath();ctx.roundRect(x+offset,bottom-bh,bw,bh,3);ctx.fill();
      });
      if (step > 54 || j % Math.ceil(rows.length/3) === 0 || j === rows.length-1) {
        ctx.fillStyle='#7a8789';ctx.textAlign='center';ctx.fillText(w < 350 ? r.month.slice(5)+'月' : r.month,x,bottom+22);
      }
    });
    ctx.font='10px "Segoe UI",sans-serif';ctx.fillStyle='#879495';ctx.textAlign='left';ctx.fillText('USD',left,14);ctx.textAlign='right';ctx.fillText('CNY',right,14);
  }
  draw(); window.addEventListener('resize', draw);
}

const productForm = document.getElementById('line-table-form');
if (productForm) {
  const body = document.getElementById('product-rows');
  const total = document.getElementById('id_lines-TOTAL_FORMS');
  function refreshProducts() {
    const totals = {};
    let costs = 0n, count = 0;
    const used = new Set();
    for (const row of body.querySelectorAll('.product-row')) {
      const field = n => row.querySelector('[name$="-'+n+'"]');
      const self = field('supply_type').value === 'self';
      field('supplier').readOnly = self;
      field('supplier').required = false;
      if (self) { if(field('supplier').value!=='自供货') field('supplier').dataset.external=field('supplier').value; field('supplier').value='自供货'; }
      else if(field('supplier').value==='自供货') field('supplier').value=field('supplier').dataset.external || '';
      const active = ['product','model','quantity','price','cost','label'].some(n=>field(n).value);
      if (!active) continue;
      count++;
      const currency=field('currency').value;
      used.add(currency);
      const qty=scaled(field('quantity').value,0), price=scaled(field('price').value,4), cost=scaled(field('cost').value,4), freight=scaled(field('freight').value || '0',2);
      if(qty!==null && price!==null && freight!==null) totals[currency]=(totals[currency]||0n)+roundedDivide(qty*price,100n)+(productForm.elements.freight_included.checked?0n:freight);
      if(qty!==null && cost!==null) costs+=roundedDivide(qty*cost,100n);
    }
    productForm.querySelectorAll('[data-rate-currency]').forEach(label=>{
      const needed=used.has(label.dataset.rateCurrency);
      label.hidden=!needed; label.querySelector('input').required=needed;
    });
    document.getElementById('line-table-estimate').textContent=count+' 行 · '+Object.entries(totals).map(([c,v])=>c+' '+centsText(v)).join(' / ')+' · 成本 CNY '+centsText(costs);
  }
  function reindex() {
    body.querySelectorAll('.product-row').forEach((row,index)=>{
      row.querySelectorAll('[name]').forEach(input=>{
        input.name=input.name.replace(/lines-\d+-/,'lines-'+index+'-');
        input.id=input.id.replace(/lines-\d+-/,'lines-'+index+'-');
      });
    });
    total.value=body.children.length;
  }
  document.getElementById('add-product-row').addEventListener('click',()=>{
    if(Number(total.value)>=100) return;
    const markup=document.getElementById('empty-product-row').innerHTML.replaceAll('__prefix__', total.value);
    body.insertAdjacentHTML('beforeend',markup);
    total.value=Number(total.value)+1;
    body.lastElementChild.querySelector('[name$="-product"]').focus();
    refreshProducts();
  });
  body.addEventListener('click',event=>{
    if(event.target.closest('.row-remove')) {
      event.target.closest('.product-row').remove(); reindex(); refreshProducts();
      productForm.dispatchEvent(new Event('input',{bubbles:true}));
    }
  });
  productForm.addEventListener('input',refreshProducts);
  productForm.addEventListener('change',refreshProducts);
  refreshProducts();
}

const bulkForm=document.getElementById('bulk-allocation-form');
if(bulkForm) {
  const rows=[...bulkForm.querySelectorAll('[data-allocation-row]')];
  const available=scaled(bulkForm.dataset.available,2);
  const checks=()=>rows.map(r=>r.querySelector('[name="selected"]'));
  const amount=row=>row.querySelector('input[type="number"]');
  const rate=row=>scaled(bulkForm.elements['rate_'+row.dataset.currency].value,8);
  const all=document.getElementById('allocation-select-all');
  function updateBulk(){
    let used=0n,count=0;
    rows.forEach(row=>{
      const selected=row.querySelector('[name="selected"]').checked;
      row.classList.toggle('is-selected',selected);
      const input=amount(row), a=scaled(input.value,2), r=rate(row), due=scaled(row.dataset.due,2);
      input.required=selected; input.setCustomValidity('');
      let text='—';
      if(selected){
        count++;
        if(a!==null) used+=a;
        if(a!==null && r!==null && r>0n){
          const target=roundedDivide(a*r,100000000n);
          text=centsText(target)+' '+row.dataset.currency;
          if(target>due || target<=0n) input.setCustomValidity('抵扣金额须大于零且不能超过本行待结算金额');
        }
      }
      row.querySelector('.allocation-row-estimate').textContent=text;
    });
    bulkForm.querySelectorAll('[data-allocation-rate]').forEach(input=>{input.required=rows.some(row=>row.dataset.currency===input.dataset.allocationRate && row.querySelector('[name="selected"]').checked);});
    all.checked=count>0 && count===rows.length; all.indeterminate=count>0 && count<rows.length;
    const status=document.getElementById('bulk-allocation-total');
    status.textContent='已选 '+count+' 行 · 分摊 '+centsText(used)+' '+bulkForm.dataset.source+' / 可用 '+centsText(available)+(used>available?' · 超过流水余额':'');
    status.classList.toggle('attention',used>available);
    return {count,used};
  }
  function selectAll(value){checks().forEach(c=>c.checked=value);updateBulk();bulkForm.dispatchEvent(new Event('input',{bubbles:true}));}
  all.addEventListener('change',()=>selectAll(all.checked));
  bulkForm.querySelector('[data-select-all]').addEventListener('click',()=>selectAll(!checks().every(c=>c.checked)));
  document.getElementById('fill-allocations').addEventListener('click',()=>{
    let left=available;
    for(const row of rows){
      if(!row.querySelector('[name="selected"]').checked) continue;
      const r=rate(row), due=scaled(row.dataset.due,2), input=amount(row);
      if(r===null || r<=0n){input.value='';continue;}
      if(left<=0n){input.value='';row.querySelector('[name="selected"]').checked=false;continue;}
      const cap=(due*100000000n+49999999n)/r;
      const a=cap<left?cap:left;
      input.value=a>0n && roundedDivide(a*r,100000000n)>0n ? (a/100n)+'.'+(a%100n).toString().padStart(2,'0') : '';
      if(input.value) left-=a;
    }
    bulkForm.dispatchEvent(new Event('input',{bubbles:true}));updateBulk();
  });
  document.getElementById('clear-allocations').addEventListener('click',()=>{rows.forEach(row=>amount(row).value='');bulkForm.dispatchEvent(new Event('input',{bubbles:true}));updateBulk();});
  bulkForm.addEventListener('input',event=>{if(event.target!==all)updateBulk();});
  bulkForm.addEventListener('change',updateBulk);
  bulkForm.addEventListener('submit',event=>{
    const s=updateBulk();
    if(!s.count || s.used>available || !bulkForm.reportValidity()){
      event.preventDefault();
      if(!s.count) document.getElementById('bulk-allocation-total').textContent='请先勾选分摊对象';
    }
  },{capture:true});
  updateBulk();
}

const shipForm=document.getElementById('shipment-entry-form');
if(shipForm){
  const rows=[...shipForm.querySelectorAll('[data-shipment-row]')];
  const all=document.getElementById('shipment-select-all');
  function updateShipment(){
    let count=0,units=0;
    rows.forEach(row=>{
      const input=row.querySelector('[data-shipment-quantity]');
      const qty=Number(input.value || 0);
      if(qty>0){count++;units+=qty;}
      row.classList.toggle('is-selected',qty>0);
    });
    document.getElementById('shipment-selection-summary').textContent='本次 '+count+' 个小项 · '+units.toLocaleString()+' 件。请计算预览后确认实际发货。';
  }
  all.addEventListener('change',()=>rows.forEach(row=>{const c=row.querySelector('[data-shipment-select]');if(!c.disabled)c.checked=all.checked;}));
  function fillShipment(mode){
    rows.forEach(row=>{
      const input=row.querySelector('[data-shipment-quantity]'),checked=row.querySelector('[data-shipment-select]').checked;
      if(mode==='clear') input.value=0;
      else if(mode==='all'||checked) input.value=input.max;
    });
    shipForm.dispatchEvent(new Event('input',{bubbles:true}));updateShipment();
  }
  document.getElementById('ship-all').addEventListener('click',()=>fillShipment('all'));
  document.getElementById('ship-selected').addEventListener('click',()=>fillShipment('selected'));
  document.getElementById('ship-clear').addEventListener('click',()=>fillShipment('clear'));
  shipForm.addEventListener('input',updateShipment);
  updateShipment();
}
