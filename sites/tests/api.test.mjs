
import test from 'node:test';
import assert from 'node:assert/strict';
import {handle} from '../lib/api.mjs';
import {database,bucket} from './adapters.mjs';
import {newOrder,applyAction,summary,uuid} from '../lib/accounting.mjs';
import {mutateOrder,getOrder} from '../lib/database.mjs';
const base='http://localhost:4180',password='Fictional-CI-password-Only-71';
async function setup(){
  const env={DB:database(),BUCKET:bucket(),SESSION_KEY:'Fictional-CI-signing-key-'.repeat(4),SITE_ORIGIN:base,BOOTSTRAP_ACCOUNTS:JSON.stringify([{username:'summer',password,role:'admin'},{username:'chris',password,role:'sales'},{username:'coco',password,role:'sales'}])};
  async function request(path,{data,cookie,key,method,headers={}}={}){
    return handle(new Request(base+'/api'+path,{method:method||(data!==undefined?'POST':'GET'),headers:{Origin:base,...(data!==undefined?{'Content-Type':'application/json','Idempotency-Key':key||uuid()}:{}),...(cookie?{Cookie:cookie}:{}),...headers},body:data!==undefined?JSON.stringify(data):undefined}),env);
  }
  const login=async username=>{const r=await request('/login',{data:{username,password}});assert.equal(r.status,200);return {cookie:r.headers.get('Set-Cookie').split(';')[0],user:(await r.json()).user};};
  const admin=await login('summer'),chris=await login('chris'),coco=await login('coco');
  const create=async(owner=chris.user.id)=>{const r=await request('/orders',{cookie:admin.cookie,data:{number:'Fictional-'+uuid(),customer:'Fictional',owner}});assert.equal(r.status,201);return (await r.json()).orderId;};
  return {env,request,login,admin,chris,coco,create};
}
test('anonymous and wrong owners cannot view orders, attachments or archives',async()=>{
  const t=await setup();try{
    const id=await t.create();
    assert.equal((await t.request('/orders')).status,401);
    for(const suffix of ['', '/export','/voucher/'+uuid(),'/archive/'+uuid()])assert.equal((await t.request('/orders/'+id+suffix,{cookie:t.coco.cookie})).status,404);
    assert.equal((await t.request('/orders',{cookie:t.coco.cookie})).status,200);
    assert.equal((await (await t.request('/orders',{cookie:t.coco.cookie})).json()).orders.length,0);
    assert.equal((await t.request('/orders/'+id+'/transaction',{cookie:t.chris.cookie,data:{}})).status,403);
  }finally{t.env.DB.close();}
});
test('origin, account checks and cookie flags protect session writes',async()=>{
  const t=await setup();try{
    const r=await t.request('/login',{data:{username:'summer',password}});const cookie=r.headers.get('Set-Cookie');
    assert(cookie.includes('HttpOnly'));assert(cookie.includes('SameSite=Lax'));
    assert.equal((await t.request('/logout',{cookie:t.admin.cookie,data:{},headers:{Origin:'https://untrusted.example'}})).status,403);
    await t.env.DB.prepare('UPDATE accounts SET active=0,version=version+1 WHERE id=?').bind(t.chris.user.id).run();
    assert.equal((await t.request('/orders',{cookie:t.chris.cookie})).status,401);
  }finally{t.env.DB.close();}
});
test('idempotency commits exactly once and detects reuse with changed contents',async()=>{
  const t=await setup();try{
    const key=uuid(),data={number:'Replay-'+uuid(),customer:'Fictional'};
    const a=await t.request('/orders',{cookie:t.admin.cookie,key,data}),b=await t.request('/orders',{cookie:t.admin.cookie,key,data});
    assert.deepEqual(await a.json(),await b.json());
    assert.equal((await t.request('/orders',{cookie:t.admin.cookie,key,data:{...data,customer:'Changed'}})).status,409);
    assert.equal((await t.env.DB.prepare('SELECT COUNT(*) AS n FROM orders').first()).n,1);
  }finally{t.env.DB.close();}
});
test('D1 transaction CHECK rolls back stale account guard and all writes',async()=>{
  const t=await setup();try{
    const id=await t.create();
    const user={...t.admin.user,active:1,version:1};
    await t.env.DB.prepare('UPDATE accounts SET version=version+1 WHERE id=?').bind(user.id).run();
    await assert.rejects(()=>mutateOrder(t.env,user,id,'lines',{rates:{USD:'7'},rows:[{product:'Fictional',model:'A',quantity:1,price:1,currency:'USD',cost:0,supplier:'F'}]},{key:user.id+':'+uuid(),operation:'/test',digest:'test'}),/账号|刷新|更新/);
    const state=JSON.parse((await t.env.DB.prepare('SELECT state FROM orders WHERE id=?').bind(id).first()).state);
    assert.equal(state.lines.length,0);
    assert.equal((await t.env.DB.prepare('SELECT COUNT(*) AS n FROM mutations WHERE operation=?').bind('/test').first()).n,0);
  }finally{t.env.DB.close();}
});
test('concurrent allocations do not spend the same source balance twice',async()=>{
  const t=await setup();try{
    const id=await t.create(),cookie=t.admin.cookie;
    await t.request('/orders/'+id+'/lines',{cookie,data:{rates:{USD:'7'},rows:[{product:'Fictional',model:'A',quantity:10,price:10,currency:'USD',cost:0,supplier:'F'}]}});
    const tx=await t.request('/orders/'+id+'/transaction',{cookie,data:{kind:'receipt',amount:'10',currency:'USD',party:'Customer',platform:'Bank',reference:uuid(),fx:'1'}});
    const transactionId=(await tx.json()).transactionId;
    const state=(await (await t.request('/orders/'+id,{cookie})).json()).order;
    const data={transactionId,entries:[{lineId:state.lines[0].id,amount:'10'}],rates:{USD:'1'}};
    const responses=await Promise.all([t.request('/orders/'+id+'/allocation',{cookie,data}),t.request('/orders/'+id+'/allocation',{cookie,data})]);
    assert.deepEqual(responses.map(r=>r.status).sort(),[200,400]);
    const updated=(await (await t.request('/orders/'+id,{cookie})).json()).order;
    assert.equal(updated.allocations.length,1);assert.equal(summary(updated).rows[0].receipt,'10.00');
  }finally{t.env.DB.close();}
});
test('concurrent retries return one response and do not duplicate serials',async()=>{
  const t=await setup();try{
    const id=await t.create(),key=uuid(),data={rates:{USD:'7'},rows:[{product:'Fictional',model:'A',quantity:1,price:1,currency:'USD',cost:0,supplier:'F'}]};
    const results=await Promise.all([t.request('/orders/'+id+'/lines',{cookie:t.admin.cookie,key,data}),t.request('/orders/'+id+'/lines',{cookie:t.admin.cookie,key,data})]);
    assert.deepEqual(await results[0].json(),await results[1].json());
    const updated=(await (await t.request('/orders/'+id,{cookie:t.admin.cookie})).json()).order;assert.equal(updated.lines.length,1);
  }finally{t.env.DB.close();}
});
test('session signing key is never exposed to the browser',async()=>{
  const t=await setup();try{
    const result=await (await t.request('/session',{cookie:t.admin.cookie})).text();
    assert(!result.includes(t.env.SESSION_KEY));assert(!result.includes('password'));assert(!result.includes('salt'));
  }finally{t.env.DB.close();}
});

test('private voucher namespace rejects foreign downloads and does not trust JSON file references',async()=>{
  const t=await setup();try{
    const id=await t.create(),cookie=t.admin.cookie;
    const data={kind:'receipt',amount:'1',currency:'USD',party:'Fictional',platform:'Bank',fx:'1',reference:'FILE-'+uuid()};
    const form=new FormData();form.append('payload',JSON.stringify(data));form.append('voucher',new Blob(['%PDF-fictional-private-voucher'],{type:'application/pdf'}),'fictional.pdf');
    const response=await handle(new Request(base+'/api/orders/'+id+'/transaction',{method:'POST',headers:{Origin:base,Cookie:cookie,'Idempotency-Key':uuid()},body:form}),t.env);
    assert.equal(response.status,200);const txn=(await response.json()).transactionId;
    const voucher=await t.request('/orders/'+id+'/voucher/'+txn,{cookie});assert.equal(voucher.status,200);assert((await voucher.text()).startsWith('%PDF-'));
    assert.equal((await t.request('/orders/'+id+'/voucher/'+txn,{cookie:t.coco.cookie})).status,404);
    const result=await t.request('/orders/'+id+'/transaction',{cookie,data:{...data,reference:uuid(),attachment:{key:'foreign/private.pdf',name:'forged.pdf'}}});assert.equal(result.status,200);
    const state=(await (await t.request('/orders/'+id,{cookie})).json()).order;assert.equal(state.transactions.at(-1).attachment,null);
    const exported=await t.request('/orders/'+id+'/export',{cookie});assert.equal(exported.status,200);assert.equal((await exported.arrayBuffer()).byteLength>100,true);
  }finally{t.env.DB.close();}
});
