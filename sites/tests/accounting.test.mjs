
import test from 'node:test';
import assert from 'node:assert/strict';
import {applyAction,newOrder,summary,available,items,due,covered,money,decimal,fixed,quoteShipment,rateFor,suggestions,fillAllocations,currentFreight} from '../lib/accounting.mjs';
const admin={id:'admin',username:'summer',role:'admin',active:1,version:1},sales={id:'sales',username:'chris',role:'sales',active:1,version:1};
function order(){return newOrder({number:'Fictional-'+crypto.randomUUID(),customer:'Fictional Customer',owner:'sales'},admin);}
function action(s,type,data,user=admin){return applyAction(s,user,type,data).state;}
function line(s,overrides={}){return action(s,'lines',{date:'2026-10-29',rates:{USD:'7'},rows:[{product:'Fictional product',model:'MODEL',quantity:10,price:'10',currency:'USD',cost:'4',supplier:'Factory',supplyType:'external',...overrides}]});}
function txn(s,{kind='receipt',amount='100',currency=kind==='receipt'?'USD':'CNY',party='Factory',reference=crypto.randomUUID(),...extra}={}){
  return action(s,'transaction',{kind,amount,currency,party,reference,platform:'Bank',date:'2026-10-29',fx:currency===(kind==='receipt'?'USD':'CNY')?'1':'0.2',...extra});
}
function allocate(s,amount,options={}){const t=s.transactions.at(-1),l=s.lines[0];return action(s,'allocation',{transactionId:t.id,entries:[{lineId:l.id,amount,...options}],rates:{USD:'1',CNY:'1'}});}
function ship(s,qty,extra={}){return action(s,'shipment',{quantities:{[s.lines[0].id]:qty},date:'2026-10-29',address:'Fictional address',carrierType:'own',carrier:'Fictional carrier',...extra});}
test('fixed point rejects floating syntax, excessive scale and negative values',()=>{
  for(const value of ['NaN','Infinity','1e2','-1','0.001',''])assert.throws(()=>money(value));
  assert.equal(fixed(decimal('1234567890.1234',4,{max:16}),4),'1234567890.1234');
  assert.throws(()=>decimal('1.000000001',8,{max:16}));
});
test('whole-table errors leave original state untouched',()=>{
  const original=order();
  assert.throws(()=>action(original,'lines',{rates:{USD:'7'},rows:[{product:'A',model:'A',quantity:1,price:1,currency:'USD',cost:0,supplier:'F'},{product:'B',model:'B',quantity:0,price:1,currency:'USD',cost:0,supplier:'F'}]}));
  assert.equal(original.lines.length,0);assert.deepEqual(original.rates,{});
});
test('serials are assigned and duplicate models stay independent',()=>{
  let s=line(order());s=line(s,{model:'MODEL',label:'manual number'});
  assert.deepEqual(s.lines.map(l=>l.number),['001','002']);assert.equal(s.lines.length,2);
});
test('one remembered rate applies only to new entries',()=>{
  let s=line(order());const old=s.lines[0].quoteFx;
  s=action(s,'lines',{rates:{USD:'7.5'},rows:[{product:'B',model:'B',quantity:1,price:1,currency:'USD',cost:0,supplier:'F'}]});
  assert.equal(s.lines[0].quoteFx,old);assert.equal(rateFor(s,'USD','CNY'),'7.50000000');
  assert.equal(rateFor(s,'CNY','USD'),'0.13333333');assert.equal(rateFor(s,'CNY','CNY'),'1.00000000');
});
test('both purposes may be blank, references remain unique after correction',()=>{
  let s=txn(line(order()),{reference:'FictionalReference'});assert.equal(s.transactions[0].purpose,'');
  s=action(s,'correction',{transactionId:s.transactions[0].id,action:'void',reason:'Fictional mistake'});
  assert.throws(()=>txn(s,{reference:'FictionalReference'}),/已存在/);
  assert.equal(summary(s).unallocated,0);
});
test('prepayments precede shipments and reserves conserve both currencies',()=>{
  let s=allocate(txn(line(order())),'100');s=allocate(txn(s,{kind:'payment',amount:'40'}),'40');
  s=ship(s,3);s=ship(s,7);
  assert.deepEqual(s.shipments.map(s=>s.items[0].receiptReserved),['30.00','70.00']);
  assert.deepEqual(s.shipments.map(s=>s.items[0].paymentReserved),['12.00','28.00']);
  assert.equal(summary(s).complete,true);
});
test('product allocation covers all shipped debts then future pool, snapshots unchanged',()=>{
  let s=line(order());s=ship(s,2);s=ship(s,3);const snapshot=JSON.stringify(s.shipments);
  s=allocate(txn(s),'100');
  assert.equal(s.allocations.length,1);assert.equal(s.allocations[0].coverage.length,2);
  assert.equal(JSON.stringify(s.shipments),snapshot);
  assert.deepEqual(items(s,s.lines[0].id).map(i=>fixed(due(s,i,'receipt'))),['0.00','0.00']);
  s=ship(s,5);assert.equal(s.shipments.at(-1).items[0].receiptReserved,'50.00');
});
test('conversion occurs once even when source cents cannot split between batches',()=>{
  let s=line(order(),{currency:'CNY',price:'0.1'});s=ship(s,5);s=ship(s,5);
  s=txn(s,{amount:'0.01'});s=action(s,'allocation',{transactionId:s.transactions[0].id,entries:[{lineId:s.lines[0].id,amount:'0.01'}],rates:{CNY:'100'}});
  assert.deepEqual(s.allocations[0].coverage.map(c=>c.amount),['0.50','0.50']);
  assert.equal(available(s,s.transactions[0]),0n);
});
test('fractional prepayment reserves conserve remainder across three shipments',()=>{
  let s=line(order(),{quantity:3,price:'1'});s=allocate(txn(s,{amount:'1'}),'1');
  s=ship(s,1);s=ship(s,1);s=ship(s,1);
  assert.deepEqual(s.shipments.map(s=>s.items[0].receiptReserved),['0.33','0.34','0.33']);
  assert.equal(s.shipments.reduce((n,s)=>n+money(s.items[0].receiptReserved),0n),100n);
});
test('explicit batch selection obeys batch due limits',()=>{
  let s=ship(line(order()),5);s=txn(s);
  assert.throws(()=>allocate(s,'60',{shipmentId:s.shipments[0].id}),/批次/);
  s=allocate(s,'50',{shipmentId:s.shipments[0].id});
  assert.equal(due(s,items(s,s.lines[0].id)[0],'receipt'),0n);
});
test('self supply preserves cost but accepts no external payment',()=>{
  let s=line(order(),{supplyType:'self',supplier:''});
  assert.equal(summary(s).internalCost,'40.00');assert.equal(summary(s).payable,'0.00');
  s=txn(s,{kind:'payment',amount:'40',party:'自供货'});
  assert.throws(()=>allocate(s,'40'),/自供货/);
  s=ship(s,10);assert.equal(s.shipments[0].items[0].internalCost,'40.00');assert.equal(s.shipments[0].items[0].cost,'0.00');
});
test('unmatched supplier can be corrected, original name survives',()=>{
  let s=txn(line(order()),{kind:'payment',amount:'40',party:'Wrong Factory'});
  assert.throws(()=>allocate(s,'40'),/供应商/);
  const id=s.transactions[0].id;
  s=action(s,'correction',{transactionId:id,action:'party',party:'Factory',reason:'Fictional correction'});
  assert.equal(s.transactions[0].party,'Wrong Factory');s=allocate(s,'40');assert.equal(summary(s).payable,'0.00');
  assert.throws(()=>action(s,'correction',{transactionId:id,action:'void',reason:'Not allowed'}),/完全未分摊/);
});
test('reversing mistaken unused money keeps original and forbids reuse',()=>{
  let s=txn(line(order()));
  const id=s.transactions[0].id;s=action(s,'correction',{transactionId:id,action:'void',reason:'Fictional duplicate'});
  assert.equal(s.transactions[0].amount,'100.00');assert.equal(s.transactions[0].corrections[0].delta,'-100.00');
  assert.equal(available(s,s.transactions[0]),0n);assert.throws(()=>allocate(s,'1'),/冲销/);
});
test('pending freight differs from zero; revisions keep every bill and shipment snapshot',()=>{
  let s=ship(line(order()),5),id=s.shipments[0].id,original=JSON.stringify(s.shipments[0].items);
  assert.equal(currentFreight(s.shipments[0]).amount,null);
  s=action(s,'freight',{shipmentId:id,amount:'19.5',currency:'CNY',reason:'Fictional bill'});
  s=action(s,'freight',{shipmentId:id,amount:'3',currency:'USD',reason:'Fictional revision'});
  assert.equal(s.shipments[0].freight,null);assert.equal(s.shipments[0].freightRecords.length,2);
  assert.deepEqual(currentFreight(s.shipments[0]),{amount:'3.00',currency:'USD'});
  assert.equal(JSON.stringify(s.shipments[0].items),original);
  s=ship(s,5,{freight:'0'});assert.equal(currentFreight(s.shipments[1]).amount,'0.00');
});
test('Hong Kong waybill change retains previous number in audit',()=>{
  let s=ship(line(order()),5,{internationalTracking:'FAKE-OLD'});
  s=action(s,'logistics',{shipmentId:s.shipments[0].id,internationalTracking:'FAKE-HK',reason:'Fictional Hong Kong switch'},sales);
  assert.equal(s.shipments[0].internationalTracking,'FAKE-HK');
  assert.equal(s.audit.at(-1).detail.changes.internationalTracking.before,'FAKE-OLD');
});
test('permissions are checked in shared business mutations',()=>{
  const s=line(order()),outsider={...sales,id:'other'};
  assert.throws(()=>applyAction(s,outsider,'lines',{rows:[]}),/无权/);
  assert.throws(()=>applyAction(s,sales,'transaction',{}),/管理员/);
  const locked={...s,status:'locked'};assert.throws(()=>applyAction(locked,admin,'logistics',{}),/锁定/);
});
test('fill selected balances is bounded by source money and target rounding',()=>{
  let s=line(order());s=line(s,{product:'B'});s=txn(s,{amount:'150'});
  const fill=fillAllocations(s,s.transactions[0],s.lines.map(l=>l.id),{USD:'1'});
  assert.deepEqual(Object.values(fill),['100.00','50.00']);
});
test('name options appear once, products only after four occurrences',()=>{
  let s=line(order());s=txn(s);
  let hints=suggestions([s]);assert(hints.supplier.includes('Factory'));assert(hints.platform.includes('Bank'));assert(!hints.product.includes('Fictional product'));
  for(let i=0;i<3;i++)s=line(s);
  assert(suggestions([s]).product.includes('Fictional product'));assert.deepEqual(suggestions([]).party,[]);
});
test('shipping previews do not mutate or overbook quantity',()=>{
  const s=line(order()),before=JSON.stringify(s);
  quoteShipment(s,{[s.lines[0].id]:5});assert.equal(JSON.stringify(s),before);
  assert.throws(()=>quoteShipment(s,{[s.lines[0].id]:11}),/数量/);
});
test('mixed currencies never add unlike sales totals',()=>{
  let s=line(order());s=action(s,'lines',{rates:{EUR:'8'},rows:[{product:'Euro',model:'EUR',quantity:2,price:8,currency:'EUR',cost:1,supplier:'Factory'}]});
  assert.deepEqual(summary(s).currencies,{USD:{sale:'100.00',receipt:'0.00',receivable:'100.00'},EUR:{sale:'16.00',receipt:'0.00',receivable:'16.00'}});
});
test('rounding/fund conservation holds over repeated partial shipment partitions',()=>{
  for(let qty=2;qty<=15;qty++){
    let s=line(order(),{quantity:qty,price:'1.0021',cost:'0.1245'});s=allocate(txn(s,{amount:'0.71'}),'0.71');
    for(let i=0;i<qty;i++)s=ship(s,1);
    assert.equal(s.shipments.reduce((n,x)=>n+money(x.items[0].sale),0n),money(summary(s).rows[0].sale));
    assert.equal(s.shipments.reduce((n,x)=>n+money(x.items[0].receiptReserved),0n),71n);
  }
});
