
import {workbook,zip} from './spreadsheet.mjs';
import {summary,available,effectiveParty,voided,items,covered,due,currentFreight,fixed} from './accounting.mjs';
export async function exportOrder(state,readAttachment){
  const s=summary(state),files={};
  const commonHeaders=['系统序号','产品','型号','日期','数量','销售单价','币种','销售总额','单位成本 CNY','成本总额 CNY','供货方式','供应商','对外货款 CNY','客户运费','报价汇率','客户条款','供应商条款'];
  const common=s.rows.map(l=>[l.number,l.product,l.model,l.date,l.quantity,l.price,l.currency,l.sale,l.unitCost,l.cost,l.supplyType==='self'?'自供货':'外部供应商',l.supplier,l.externalCost,l.freight,l.quoteFx,l.customerTerms,l.supplierTerms]);
  for(const kind of ['receipt','payment']){
    const txns=state.transactions.filter(t=>t.kind===kind);
    const rows=txns.map(t=>[t.id,t.date,t.amount,t.currency,effectiveParty(t),t.platform,t.purpose,t.reference,fixed(available(state,t)),voided(t)?'已冲销误录':'有效',t.party,t.fx,t.fxSource]);
    const allocations=state.allocations.filter(a=>a.kind===kind).map(a=>{const l=state.lines.find(l=>l.id===a.lineId);return [a.transactionId,l.number,l.product,l.model,a.source,a.rate,a.target,kind==='receipt'?l.currency:'CNY',a.shipmentId||'按产品分摊',JSON.stringify(a.coverage),a.reason];});
    files[kind==='receipt'?'客户收款表.xlsx':'供应商付款表.xlsx']=workbook([['流水',['ID','日期','原币金额','币种','公司/供应商','平台','用途','平台流水号','未分配','状态','最初名称','当日汇率','汇率来源'],rows],['款项分摊',['流水ID','序号','产品','型号','分摊原币','汇率','抵扣金额','目标币种','指定批次','自动批次覆盖','说明'],allocations],['订单小项快照',commonHeaders,common]]);
  }
  const shipments=state.shipments.flatMap(ship=>ship.items.map(item=>{
    const l=state.lines.find(l=>l.id===item.lineId),i=items(state,l.id).find(i=>i.shipmentId===ship.id);
    return [ship.number,ship.date,l.number,l.product,l.model,item.quantity,item.sale,l.currency,fixed(covered(state,i,'receipt')),fixed(due(state,i,'receipt')),item.cost,item.internalCost,fixed(covered(state,i,'payment')),fixed(due(state,i,'payment')),ship.carrier,ship.domesticTracking,ship.internationalTracking,ship.address,ship.notes];
  }));
  files['发货表.xlsx']=workbook([['发货明细',['批次','日期','序号','产品','型号','数量','货值','销售币种','客户覆盖','待收','对外货款','内部成本','供应商覆盖','待付','货代','国内单号','国际单号','地址','备注'],shipments],
    ['运费批次汇总',['批次','实际运费','币种','状态'],state.shipments.map(ship=>[ship.number,currentFreight(ship).amount??'',currentFreight(ship).currency,currentFreight(ship).amount===null?'待补录':'已记录'])],
    ['运费账单历史',['批次','金额','币种','原因','操作人','时间'],state.shipments.flatMap(ship=>ship.freightRecords.map(r=>[ship.number,r.amount,r.currency,r.reason,r.createdBy,r.created]))],['订单小项快照',commonHeaders,common]]);
  files['订单完整记录.json']=JSON.stringify(state,null,2);
  files['审计与更正.json']=JSON.stringify({audit:state.audit,corrections:state.transactions.map(t=>({id:t.id,original:t.party,corrections:t.corrections}))},null,2);
  let total=0;
  for(const t of state.transactions)if(t.attachment){
    const data=new Uint8Array(await readAttachment(t));total+=data.byteLength;
    if(total>25*1024*1024)throw Error('凭证合计超过25MB，请分开下载核验。');
    files['凭证/'+t.id+'-'+t.attachment.name.replace(/[^\p{L}\p{N}._-]/gu,'_')]=data;
  }
  const hashes={};
  for(const [name,data]of Object.entries(files)){
    const bytes=typeof data==='string'?new TextEncoder().encode(data):data;
    hashes[name]=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');
  }
  files['校验清单.json']=JSON.stringify({order:state.number,complete:s.complete,files:hashes},null,2);
  return zip(files);
}
