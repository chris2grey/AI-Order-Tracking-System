
/** Fixed-point accounting shared by the server, browser previews and regression tests. */
export class DomainError extends Error { constructor(message,status=400){super(message);this.status=status;} }
export const currencies=['USD','CNY','EUR','HKD','GBP'];
export const clone=value=>structuredClone(value);
export const uuid=()=>crypto.randomUUID();
export const now=()=>new Date().toISOString();
export const today=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai'}).format(new Date());
export function text(value,label='内容',max=240,required=true){
  const result=String(value??'').normalize('NFKC').trim();
  if((required&&!result)||result.length>max)throw new DomainError(label+'不能为空且不能超过'+max+'字。');
  return result;
}
export const name=value=>text(value,'名称',160).replace(/\s+/g,' ');
export function day(value){
  const result=String(value||today());
  if(!/^\d{4}-\d{2}-\d{2}$/.test(result)||isNaN(Date.parse(result+'T00:00:00Z'))||new Date(result+'T00:00:00Z').toISOString().slice(0,10)!==result)throw new DomainError('请选择有效日期。');
  return result;
}
export function decimal(raw,places=2,{positive=false,max=18}={}){
  const value=String(raw??'').trim();
  if(!/^\d+(\.\d+)?$/.test(value))throw new DomainError('请填写有效的非负数值。');
  let [whole,fraction='']=value.split('.');
  whole=whole.replace(/^0+(?=\d)/,'');
  if(fraction.length>places || whole.length>max-places)throw new DomainError('金额或汇率超出精度范围。');
  const result=BigInt(whole)*10n**BigInt(places)+BigInt(fraction.padEnd(places,'0')||'0');
  if(positive&&result<=0n)throw new DomainError('金额、数量或汇率必须大于零。');
  return result;
}
export function fixed(value,places=2){
  const negative=value<0n;value=negative?-value:value;const divisor=10n**BigInt(places);
  return (negative?'-':'')+(value/divisor)+(places?'.'+(value%divisor).toString().padStart(places,'0'):'');
}
export const money=value=>decimal(value,2);
export const rounded=(value,divisor)=>(value+divisor/2n)/divisor;
export const sum=(values)=>values.reduce((n,v)=>n+v,0n);
export const minimum=(a,b)=>a<b?a:b;
export function quantity(raw,allowZero=false){
  const result=decimal(raw,0,{positive:!allowZero,max:9});
  if(result>2147483647n)throw new DomainError('数量过大。');
  return Number(result);
}
export function currency(value){if(!currencies.includes(value))throw new DomainError('币种无效。');return value;}
export function sale(line){return rounded(BigInt(line.quantity)*decimal(line.price,4,{max:16}),100n)+(line.freightIncluded?0n:money(line.freight));}
export function internalCost(line){return rounded(BigInt(line.quantity)*decimal(line.cost,4,{max:16}),100n);}
export const payable=line=>line.supplyType==='self'?0n:internalCost(line);
export const effectiveParty=txn=>txn.corrections.filter(c=>c.action==='party').at(-1)?.party||txn.party;
export const voided=txn=>txn.corrections.some(c=>c.action==='void');
export const available=(state,txn)=>voided(txn)?0n:money(txn.amount)-sum(state.allocations.filter(a=>a.transactionId===txn.id).map(a=>money(a.source)));
export const allocated=(state,lineId,kind)=>sum(state.allocations.filter(a=>a.lineId===lineId&&a.kind===kind).map(a=>money(a.target)));
export const items=(state,lineId)=>state.shipments.flatMap(s=>s.items.filter(i=>i.lineId===lineId).map(i=>({...i,shipmentId:s.id,date:s.date,created:s.created})));
export function covered(state,item,kind){
  const field=kind==='receipt'?'receiptReserved':'paymentReserved';
  let total=money(item[field]);
  for(const a of state.allocations.filter(a=>a.lineId===item.lineId&&a.kind===kind)){
    if(a.shipmentId===item.shipmentId)total+=money(a.target);
    total+=sum((a.coverage||[]).filter(c=>c.shipmentId===item.shipmentId).map(c=>money(c.amount)));
  }
  return total;
}
export const due=(state,item,kind)=>money(kind==='receipt'?item.sale:item.cost)-covered(state,item,kind);
export function lineSummary(state,line){
  const previous=items(state,line.id),shipped=previous.reduce((n,i)=>n+i.quantity,0);
  const receipt=allocated(state,line.id,'receipt'),payment=allocated(state,line.id,'payment');
  return {lineId:line.id,quantity:line.quantity,shipped,remaining:line.quantity-shipped,
    sale:fixed(sale(line)),cost:fixed(internalCost(line)),externalCost:fixed(payable(line)),
    receipt:fixed(receipt),payment:fixed(payment),receivable:fixed(sale(line)-receipt),payable:fixed(payable(line)-payment)};
}
export function summary(state){
  const rows=state.lines.map(line=>({...line,unitCost:line.cost,...lineSummary(state,line)}));
  const totals={};
  for(const r of rows){
    const t=totals[r.currency]??={sale:0n,receipt:0n,receivable:0n};
    for(const k of ['sale','receipt','receivable'])t[k]+=money(r[k]);
  }
  const unallocated=state.transactions.filter(t=>available(state,t)>0n).length;
  return {rows,currencies:Object.fromEntries(Object.entries(totals).map(([c,t])=>[c,Object.fromEntries(Object.entries(t).map(([k,v])=>[k,fixed(v)]))])),
    externalCost:fixed(sum(rows.map(r=>money(r.externalCost)))),internalCost:fixed(sum(rows.filter(r=>r.supplyType==='self').map(r=>money(r.cost)))),
    payment:fixed(sum(rows.map(r=>money(r.payment)))),payable:fixed(sum(rows.map(r=>money(r.payable)))),
    quantity:rows.reduce((n,r)=>n+r.quantity,0),shipped:rows.reduce((n,r)=>n+r.shipped,0),unallocated,
    complete:!!rows.length&&!unallocated&&rows.every(r=>money(r.receivable)===0n&&money(r.payable)===0n&&!r.remaining)};
}
export function rateFor(state,source,target){
  if(source===target)return '1.00000000';
  if(state.rates[source+':'+target])return state.rates[source+':'+target];
  const a=source==='CNY'?100000000n:state.rates[source+':CNY']?decimal(state.rates[source+':CNY'],8,{max:16}):null;
  const b=target==='CNY'?100000000n:state.rates[target+':CNY']?decimal(state.rates[target+':CNY'],8,{max:16}):null;
  return a&&b?fixed(rounded(a*100000000n,b),8):'';
}
function remember(state,source,target,value){
  const rate=decimal(value,8,{positive:true,max:16});
  if(source===target&&rate!==100000000n)throw new DomainError('同币种汇率必须为1。');
  state.rates[source+':'+target]=fixed(rate,8);return rate;
}
export function newOrder(data,user){
  return {id:uuid(),number:text(data.number,'订单编号',60),customer:name(data.customer),owner:data.owner||user.id,date:day(data.date),notes:text(data.notes,'备注',2000,false),
    status:'active',created:now(),lines:[],transactions:[],allocations:[],shipments:[],archives:[],audit:[],rates:{}};
}
function lineOf(state,id){const line=state.lines.find(l=>l.id===id);if(!line)throw new DomainError('产品不属于此订单。');return line;}
function txnOf(state,id){const txn=state.transactions.find(t=>t.id===id);if(!txn)throw new DomainError('流水不属于此订单。');return txn;}
function shipOf(state,id){const ship=state.shipments.find(s=>s.id===id);if(!ship)throw new DomainError('批次不属于此订单。');return ship;}
export function quoteShipment(state,quantities){
  const quotes=[];
  if(!quantities||typeof quantities!=='object'||Array.isArray(quantities))throw new DomainError('发货数量无效。');
  for(const [id,raw]of Object.entries(quantities)){
    const line=lineOf(state,id),qty=quantity(raw,true);
    if(!qty)continue;
    const previous=items(state,id),remaining=line.quantity-previous.reduce((n,i)=>n+i.quantity,0);
    if(qty>remaining)throw new DomainError(line.product+'：本次数量超过未发数量。');
    const internalRemaining=internalCost(line)-sum(previous.map(i=>money(i.internalCost)));
    const quote={lineId:id,quantity:qty,internalCost:fixed(qty===remaining?internalRemaining:rounded(internalRemaining*BigInt(qty),BigInt(remaining)))};
    for(const [kind,total,field,reserved]of [['receipt',sale(line),'sale','receiptReserved'],['payment',payable(line),'cost','paymentReserved']]){
      const remainingValue=total-sum(previous.map(i=>money(i[field])));
      const value=qty===remaining?remainingValue:rounded(remainingValue*BigInt(qty),BigInt(remaining));
      const allocations=state.allocations.filter(a=>a.lineId===id&&a.kind===kind&&!a.shipmentId);
      let pool=sum(allocations.map(a=>money(a.target)))-sum(allocations.flatMap(a=>(a.coverage||[]).map(c=>money(c.amount))))-sum(previous.map(i=>money(i[reserved])));
      if(pool<0n)throw new DomainError('预付款占用数据异常，请核对记录。');
      const used=minimum(value,qty===remaining?pool:rounded(pool*BigInt(qty),BigInt(remaining)));
      quote[kind]={total:fixed(value),covered:fixed(used),due:fixed(value-used)};
    }
    quotes.push(quote);
  }
  if(!quotes.length)throw new DomainError('请至少填写一个产品的发货数量。');
  return quotes;
}
export function audit(state,user,action,detail){state.audit.push({id:uuid(),time:now(),actor:user.username,action,detail});}
const adminActions=new Set(['transaction','allocation','correction','freight','lock']);
export function applyAction(original,user,action,data){
  if(!user.active||(user.role!=='admin'&&original.owner!==user.id))throw new DomainError('无权访问此订单。',403);
  if(original.status!=='active')throw new DomainError('订单已锁定，不能修改历史业务记录。');
  if(adminActions.has(action)&&user.role!=='admin')throw new DomainError('此操作需要管理员权限。',403);
  const state=clone(original);let result={};
  if(action==='lines'){
    if(!Array.isArray(data.rows)||!data.rows.length||data.rows.length>100)throw new DomainError('每批请填写1至100行产品。');
    const date=day(data.date),customerTerms=text(data.customerTerms||'发货前结清','客户条款',200),supplierTerms=text(data.supplierTerms||'发货前结清','供应商条款',200);
    for(const raw of data.rows){
      const c=currency(raw.currency),supplyType=raw.supplyType||'external';
      if(!['external','self'].includes(supplyType))throw new DomainError('供货方式无效。');
      const rate=(data.rates||{})[c]||rateFor(state,c,'CNY');
      remember(state,c,'CNY',c==='CNY'?'1':rate);
      const line={id:uuid(),number:String(state.lines.length+1).padStart(3,'0'),date,product:text(raw.product,'产品名称',160),model:text(raw.model,'型号',100),
        quantity:quantity(raw.quantity),price:fixed(decimal(raw.price,4,{positive:true,max:16}),4),currency:c,
        quoteFx:state.rates[c+':CNY'],cost:fixed(decimal(raw.cost,4,{max:16}),4),supplyType,
        supplier:supplyType==='self'?'自供货':name(raw.supplier),freightIncluded:data.freightIncluded!==false,
        freight:fixed(money(raw.freight||'0')),customerTerms,supplierTerms};
      // Financial totals are bounded to the same 18-digit, 2-decimal ledger.
      decimal(fixed(sale(line)),2);decimal(fixed(internalCost(line)),2);
      state.lines.push(line);
    }
    result.count=data.rows.length;audit(state,user,'新增产品',result.count+'行；系统编号；同型号补单独立核算');
  }else if(action==='transaction'){
    const kind=data.kind;if(!['receipt','payment'].includes(kind))throw new DomainError('流水类型无效。');
    const c=currency(data.currency),target=kind==='receipt'?'USD':'CNY';
    const rate=remember(state,c,target,data.fx||rateFor(state,c,target));
    const reference=text(data.reference,'平台流水号',120);
    if(state.transactions.some(t=>t.kind===kind&&t.reference===reference))throw new DomainError('同类型平台流水号已存在，请勿重复录入。');
    const txn={id:uuid(),kind,date:day(data.date),amount:fixed(decimal(data.amount,2,{positive:true})),currency:c,party:name(data.party),
      platform:text(data.platform,'平台',100),fx:fixed(rate,8),fxSource:text(data.fxSource||'人工确认','汇率来源',160),
      purpose:text(data.purpose,'款项用途',240,false),reference,attachment:data.attachment||null,corrections:[],created:now(),createdBy:user.username};
    state.transactions.push(txn);result.transactionId=txn.id;audit(state,user,kind==='receipt'?'录入客户收款':'录入供应商付款',{id:txn.id,amount:txn.amount,currency:c,party:txn.party});
  }else if(action==='allocation'){
    const txn=txnOf(state,data.transactionId);
    if(voided(txn))throw new DomainError('已冲销流水不能分摊。');
    if(!Array.isArray(data.entries)||!data.entries.length||data.entries.length>200)throw new DomainError('请勾选1至200行产品。');
    const reason=text(data.reason||'按订单约定分摊','分摊说明',240);
    const selected=new Set();
    for(const entry of data.entries){
      const line=lineOf(state,entry.lineId),key=line.id+':'+(entry.shipmentId||'auto');
      if(selected.has(key))throw new DomainError('不能重复选择同一分摊对象。');selected.add(key);
      if(txn.kind==='payment'&&(line.supplyType==='self'||effectiveParty(txn)!==line.supplier))throw new DomainError('供应商名称须匹配产品；自供货不接受对外付款分摊。');
      const targetCurrency=txn.kind==='receipt'?line.currency:'CNY';
      const rate=remember(state,txn.currency,targetCurrency,(data.rates||{})[targetCurrency]||rateFor(state,txn.currency,targetCurrency));
      const source=decimal(entry.amount,2,{positive:true}),target=rounded(source*rate,100000000n);
      const total=txn.kind==='receipt'?sale(line):payable(line);
      if(source>available(state,txn))throw new DomainError('分摊原币金额超过流水的未分配金额。');
      if(target<=0n||target>total-allocated(state,line.id,txn.kind))throw new DomainError('抵扣金额超过产品待结算余额，或舍入后为零。');
      const a={id:uuid(),transactionId:txn.id,lineId:line.id,kind:txn.kind,source:fixed(source),rate:fixed(rate,8),target:fixed(target),reason,
        shipmentId:entry.shipmentId||null,coverage:[],created:now(),createdBy:user.username};
      if(a.shipmentId){
        const item=items(state,line.id).find(i=>i.shipmentId===a.shipmentId);
        if(!item||target>due(state,item,txn.kind))throw new DomainError('指定批次不匹配，或超过本批余额。');
      }else{
        let left=target;
        const batches=items(state,line.id).sort((a,b)=>a.date.localeCompare(b.date)||a.created.localeCompare(b.created)||a.shipmentId.localeCompare(b.shipmentId));
        for(const item of batches){
          const used=minimum(left,due(state,item,txn.kind));
          if(used>0n){a.coverage.push({shipmentId:item.shipmentId,amount:fixed(used)});left-=used;}
          if(!left)break;
        }
      }
      state.allocations.push(a);
    }
    result.count=data.entries.length;audit(state,user,'批量款项分摊',{transactionId:txn.id,entries:data.entries});
  }else if(action==='shipment'){
    const quotes=quoteShipment(state,data.quantities),carrierType=data.carrierType;
    if(!['own','customer'].includes(carrierType))throw new DomainError('请选择运输方。');
    const ship={id:uuid(),number:state.shipments.length+1,date:day(data.date),address:text(data.address,'收货地址',2000),carrierType,
      carrier:name(data.carrier),domesticDate:data.domesticDate?day(data.domesticDate):'',domesticTracking:text(data.domesticTracking,'国内单号',500,false),
      internationalTracking:text(data.internationalTracking,'国际单号',500,false),notes:text(data.notes,'装箱备注',2000,false),
      freight:data.freight===undefined||data.freight===null||data.freight===''?null:fixed(money(data.freight)),
      freightCurrency:currency(data.freightCurrency||'CNY'),freightRecords:[],created:now(),createdBy:user.username,
      items:quotes.map(q=>({lineId:q.lineId,quantity:q.quantity,sale:q.receipt.total,cost:q.payment.total,internalCost:q.internalCost,receiptReserved:q.receipt.covered,paymentReserved:q.payment.covered}))};
    state.shipments.push(ship);result.shipmentId=ship.id;audit(state,user,'确认发货',{number:ship.number,items:ship.items});
  }else if(action==='logistics'){
    const ship=shipOf(state,data.shipmentId),allowed=['address','carrierType','carrier','domesticDate','domesticTracking','internationalTracking','notes'];
    const reason=text(data.reason,'修改说明',240),changes={};
    for(const k of allowed){
      if(!(k in data))continue;
      let value=data[k];
      if(k==='domesticDate')value=value?day(value):'';
      else if(k==='carrierType'){if(!['own','customer'].includes(value))throw new DomainError('运输方无效。');}
      else value=text(value,'物流信息',k.includes('Tracking')?500:2000,['carrier','address'].includes(k));
      if(ship[k]!==value){changes[k]={before:ship[k],after:value};ship[k]=value;}
    }
    audit(state,user,'更新物流 / 换单号',{number:ship.number,reason,changes});
  }else if(action==='freight'){
    const ship=shipOf(state,data.shipmentId),before=currentFreight(ship);
    const bill={id:uuid(),amount:fixed(money(data.amount)),currency:currency(data.currency),reason:text(data.reason,'账单说明',240),created:now(),createdBy:user.username};
    ship.freightRecords.push(bill);audit(state,user,'补录 / 更正运费',{number:ship.number,before,after:bill});
  }else if(action==='correction'){
    const txn=txnOf(state,data.transactionId);
    if(voided(txn)||state.allocations.some(a=>a.transactionId===txn.id))throw new DomainError('只可处理完全未分摊、未冲销的误录流水。');
    const reason=text(data.reason,'更正原因',240);
    if(!['party','void'].includes(data.action))throw new DomainError('更正类型无效。');
    const party=data.action==='party'?name(data.party):'';
    if(data.action==='party'&&party===effectiveParty(txn))throw new DomainError('请输入正确的新名称。');
    const c={id:uuid(),action:data.action,party,delta:data.action==='void'?'-'+txn.amount:'0.00',reason,created:now(),createdBy:user.username};
    txn.corrections.push(c);audit(state,user,data.action==='void'?'冲销未分摊误录':'更正公司名称',{transactionId:txn.id,originalParty:txn.party,correction:c});
  }else if(action==='lock'){
    if(!summary(state).complete)throw new DomainError('产品收付款、发货或流水分摊尚未全部完成，不能归档。');
    if(data.freightConfirmed!==true)throw new DomainError('请先人工核对本单实际货代运费。');
    state.status='locked';audit(state,user,'锁定归档','已核对产品款、发货及货代运费');
  }else throw new DomainError('不支持的操作。');
  return {state,result};
}
export function currentFreight(ship){const last=ship.freightRecords.at(-1);return last?{amount:last.amount,currency:last.currency}:{amount:ship.freight,currency:ship.freightCurrency};}
export function allocationRows(state,txn){
  return state.lines.map(line=>({...line,...lineSummary(state,line)})).filter(l=>
    money(txn.kind==='receipt'?l.receivable:l.payable)>0n&&(txn.kind==='receipt'||(l.supplyType!=='self'&&l.supplier===effectiveParty(txn))));
}
export function suggestions(states){
  const fields={product:[],model:[],supplier:[],party:[],platform:[],customer:[]};
  for(const s of states){
    fields.customer.push(s.customer);
    for(const l of s.lines){fields.product.push(l.product);fields.model.push(l.model);if(l.supplyType!=='self'){fields.supplier.push(l.supplier);fields.party.push(l.supplier);}}
    for(const t of s.transactions)if(!voided(t)){fields.party.push(effectiveParty(t));fields.platform.push(t.platform);if(t.kind==='payment')fields.supplier.push(effectiveParty(t));}
  }
  return Object.fromEntries(Object.entries(fields).map(([field,values])=>{
    const counts=new Map();for(const v of values)counts.set(v,(counts.get(v)||0)+1);
    return [field,[...counts].filter(([,n])=>n>=(['product','model'].includes(field)?4:1)).sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).slice(0,500).map(([v])=>v)];
  }));
}
export function fillAllocations(state,txn,selected,rates){
  let left=available(state,txn);const result={};
  for(const line of allocationRows(state,txn)){
    if(!selected.includes(line.id)||left<=0n)continue;
    const targetCurrency=txn.kind==='receipt'?line.currency:'CNY';
    const raw=rates[targetCurrency]||rateFor(state,txn.currency,targetCurrency);
    if(!raw)throw new DomainError('请先填写结算汇率。');
    const rate=decimal(raw,8,{positive:true,max:16}),balance=money(txn.kind==='receipt'?line.receivable:line.payable);
    const cap=(balance*100000000n+49999999n)/rate,amount=minimum(cap,left);
    if(amount>0n&&rounded(amount*rate,100000000n)>0n){result[line.id]=fixed(amount);left-=amount;}
  }
  return result;
}
