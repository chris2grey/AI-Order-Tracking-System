
import {DomainError,now,uuid,newOrder,applyAction,audit,summary,suggestions} from './accounting.mjs';
import {hash,requireUser} from './auth.mjs';

export function readable(user,record){
  if(!record||!user||!user.active||(user.role!=='admin'&&record.owner!==user.id))throw new DomainError('订单不存在或无权访问。',404);
  return JSON.parse(record.state);
}
export async function getOrder(env,user,id){
  requireUser(user);
  const record=await env.DB.prepare('SELECT * FROM orders WHERE id=?').bind(id).first();
  return {record,state:readable(user,record)};
}
export async function allOrders(env,user){
  requireUser(user);
  const query=user.role==='admin'?env.DB.prepare('SELECT * FROM orders ORDER BY updated DESC'):env.DB.prepare('SELECT * FROM orders WHERE owner=? ORDER BY updated DESC').bind(user.id);
  const result=await query.all();
  return result.results.map(r=>JSON.parse(r.state));
}
export async function requestKey(user,request,payload){
  const key=request.headers.get('Idempotency-Key')||'';
  if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(key))throw new DomainError('提交编号无效，请刷新页面后重试。');
  return {key:user.id+':'+key,operation:new URL(request.url).pathname,digest:await hash(JSON.stringify(payload))};
}
async function replay(env,user,identity){
  const previous=await env.DB.prepare('SELECT * FROM mutations WHERE key=?').bind(identity.key).first();
  if(!previous)return null;
  if(previous.operation!==identity.operation||previous.digest!==identity.digest)throw new DomainError('提交编号已用于其他内容，请刷新后重新核对。',409);
  if(previous.order_id)await getOrder(env,user,previous.order_id);
  return JSON.parse(previous.response);
}
const receipt=(env,user,identity,response,id)=>env.DB.prepare('INSERT INTO mutations (key,user,operation,digest,response,order_id,ok,created) VALUES (?,?,?,?,?,?,changes(),?)').bind(identity.key,user.id,identity.operation,identity.digest,JSON.stringify(response),id,now());
function conflict(error){return /mutation_committed_once|CHECK constraint|UNIQUE constraint failed: mutations.key|SQLITE_BUSY/i.test(String(error?.message));}
function bounded(state){if(new TextEncoder().encode(JSON.stringify(state)).length>1500000)throw new DomainError('此订单历史记录已较多，请拆分后续新订单并保留本单归档。');}
export async function createOrder(env,user,data,identity){
  const previous=await replay(env,user,identity);if(previous)return previous;
  const owner=data.owner||user.id;
  if(owner!==user.id&&user.role!=='admin')throw new DomainError('不能把订单分配给其他账号。',403);
  const account=await env.DB.prepare('SELECT id FROM accounts WHERE id=? AND active=1').bind(owner).first();
  if(!account)throw new DomainError('请选择有效业务员。');
  const state=newOrder({...data,owner},user);audit(state,user,'创建订单',state.number);
  const response={orderId:state.id};
  try{
    await env.DB.batch([
      env.DB.prepare('INSERT INTO orders (id,number,customer,owner,state,version,updated) SELECT ?,?,?,?,?,1,? WHERE EXISTS (SELECT 1 FROM accounts WHERE id=? AND version=? AND active=1)').bind(state.id,state.number,state.customer,owner,JSON.stringify(state),now(),user.id,user.version),
      receipt(env,user,identity,response,state.id),
    ]);
  }catch(error){
    const previous=await replay(env,user,identity);if(previous)return previous;
    if(/UNIQUE constraint failed: orders.number/.test(String(error.message)))throw new DomainError('订单编号已存在。');
    if(conflict(error))throw new DomainError('账号状态已变化，请刷新后重试。',409);
    throw error;
  }
  return response;
}
export async function mutateOrder(env,user,id,action,data,identity,prepare){
  const previous=await replay(env,user,identity);if(previous)return previous;
  for(let retry=0;retry<5;retry++){
    const {record,state}=await getOrder(env,user,id);
    const updated=applyAction(state,user,action,data);
    if(prepare)await prepare(updated.state,updated.result);
    bounded(updated.state);
    const response={orderId:id,...updated.result};
    try{
      await env.DB.batch([
        env.DB.prepare('UPDATE orders SET state=?,version=version+1,updated=? WHERE id=? AND version=? AND EXISTS (SELECT 1 FROM accounts WHERE id=? AND version=? AND active=1)').bind(JSON.stringify(updated.state),now(),id,record.version,user.id,user.version),
        receipt(env,user,identity,response,id),
      ]);
      return response;
    }catch(error){
      const previous=await replay(env,user,identity);if(previous)return previous;
      if(conflict(error))continue;
      throw error;
    }
  }
  throw new DomainError('其他人刚更新此订单，请刷新余额后重试。',409);
}
