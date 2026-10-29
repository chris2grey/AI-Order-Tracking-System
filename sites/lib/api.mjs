
import {DomainError,uuid,now,text,name,summary,suggestions,audit,available,fixed} from './accounting.mjs';
import {ensureAccounts,authenticate,login,cookie,requireUser,passwordHash,hex,hash} from './auth.mjs';
import {allOrders,getOrder,requestKey,createOrder,mutateOrder} from './database.mjs';
import {exportOrder} from './export.mjs';
import {productTemplate} from './spreadsheet.mjs';
const encoder=new TextEncoder();
function json(value,status=200,headers={}){return new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff',...headers}});}
async function bodyBytes(request,limit){
  const reader=request.body?.getReader();if(!reader)return new Uint8Array();
  const chunks=[];let size=0;
  while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>limit){await reader.cancel();throw new DomainError('提交内容过大。',413);}chunks.push(value);}
  const all=new Uint8Array(size);let at=0;for(const chunk of chunks){all.set(chunk,at);at+=chunk.length;}return all;
}
async function input(request){
  const type=request.headers.get('Content-Type')||'';
  if(!type.startsWith('application/json'))throw new DomainError('请使用JSON提交。');
  try{const data=JSON.parse(new TextDecoder().decode(await bodyBytes(request,1024*1024)));if(!data||typeof data!=='object'||Array.isArray(data))throw Error();return data;}
  catch(error){if(error instanceof DomainError)throw error;throw new DomainError('请求内容无效。');}
}
function origin(request,env){
  const current=new URL(request.url).origin,actual=request.headers.get('Origin');
  if(!actual||![current,env.SITE_ORIGIN].filter(Boolean).includes(actual))throw new DomainError('页面来源校验失败，请刷新后提交。',403);
}
function publicAccount(user){return {id:user.id,username:user.username,role:user.role,active:user.active};}
function protectedKey(state,key,category){if(!key.startsWith(state.id+'/'+category+'/')||key.includes('..'))throw new DomainError('文件引用无效。',404);return key;}
async function readVoucher(env,state,txn){
  const file=txn.attachment;if(!file)throw new DomainError('没有上传凭证。',404);
  const object=await env.BUCKET.get(protectedKey(state,file.key,'vouchers'));
  if(!object)throw new DomainError('凭证文件暂不可用，请核对备份。',404);
  return object;
}
async function transactionInput(env,request,orderId,user){
  if(!(request.headers.get('Content-Type')||'').startsWith('multipart/form-data')){
    const data=await input(request);data.attachment=null;return data;
  }
  const bytes=await bodyBytes(request,12*1024*1024);
  const form=await new Response(bytes,{headers:{'Content-Type':request.headers.get('Content-Type')}}).formData();
  let data;try{data=JSON.parse(String(form.get('payload')||''));}catch{throw new DomainError('流水内容无效。');}
  if(!data||typeof data!=='object'||Array.isArray(data))throw new DomainError('流水内容无效。');
  data.attachment=null;
  const file=form.get('voucher');
  if(file&&typeof file!=='string'&&file.size){
    if(file.size>10*1024*1024)throw new DomainError('凭证最大10MB。');
    const suffix=file.name.toLowerCase().split('.').at(-1),dataBytes=new Uint8Array(await file.arrayBuffer());
    const pdf=suffix==='pdf'&&String.fromCharCode(...dataBytes.slice(0,5))==='%PDF-';
    const png=suffix==='png'&&hex(dataBytes.slice(0,8))==='89504e470d0a1a0a';
    const jpg=['jpg','jpeg'].includes(suffix)&&hex(dataBytes.slice(0,3))==='ffd8ff';
    if(!pdf&&!png&&!jpg)throw new DomainError('凭证仅接受内容匹配的PDF、PNG或JPG。');
    const rawKey=request.headers.get('Idempotency-Key')||'';
    if(!/^[0-9a-f-]{36}$/i.test(rawKey))throw new DomainError('提交编号无效。');
    const key=orderId+'/vouchers/'+user.id+'-'+rawKey+'.'+suffix;
    const digest=hex(await crypto.subtle.digest('SHA-256',dataBytes));
    data.attachment={key,name:text(file.name,'凭证文件名',180),type:pdf?'application/pdf':png?'image/png':'image/jpeg',size:file.size,digest};
    // Upload only to this order's private namespace. An uncommitted upload is never downloadable.
    await env.BUCKET.put(key,dataBytes,{httpMetadata:{contentType:data.attachment.type}});
  }
  return data;
}
export async function handle(request,env){
  try{
    const url=new URL(request.url),path=url.pathname.replace(/\/$/,'');
    if(path==='/api/health')return json({status:env.DB&&env.BUCKET?'ok':'unavailable'},env.DB&&env.BUCKET?200:503);
    if(!env.DB||!env.BUCKET)throw new DomainError('云端数据服务尚未就绪。',503);
    await ensureAccounts(env);
    const user=await authenticate(env,request);
    if(request.method==='POST')origin(request,env);
    if(path==='/api/session'&&request.method==='GET')return json({user:user?publicAccount(user):null});
    if(path==='/api/login'&&request.method==='POST'){
      const result=await login(env,request,await input(request));
      return json({user:publicAccount(result.user)},200,{'Set-Cookie':cookie(request,result.token)});
    }
    if(path==='/api/logout'&&request.method==='POST')return json({ok:true},200,{'Set-Cookie':cookie(request,'',true)});
    requireUser(user);
    if(path==='/api/users'&&request.method==='GET'){
      const result=await (user.role==='admin'?env.DB.prepare('SELECT id,username,role,active FROM accounts ORDER BY username'):env.DB.prepare('SELECT id,username,role,active FROM accounts WHERE id=?').bind(user.id)).all();
      return json({users:result.results});
    }
    if(path==='/api/users'&&request.method==='POST'){
      requireUser(user,true);const data=await input(request),identity=await requestKey(user,request,{...data,password:await hash(env.SESSION_KEY+':'+String(data.password))});
      const previous=await env.DB.prepare('SELECT * FROM mutations WHERE key=?').bind(identity.key).first();
      if(previous){if(previous.digest!==identity.digest||previous.operation!==identity.operation)throw new DomainError('提交编号冲突。',409);return json(JSON.parse(previous.response));}
      const username=text(data.username,'账号',40),password=String(data.password||'');
      if(!/^[a-zA-Z0-9_]{3,40}$/.test(username)||password.length<8||password.length>200)throw new DomainError('账号需3至40位字母数字下划线，密码至少8位。');
      const salt=hex(crypto.getRandomValues(new Uint8Array(24))),id=uuid(),response={id};
      const digest=await passwordHash(password,salt);
      try{await env.DB.batch([
        env.DB.prepare("INSERT INTO accounts (id,username,password,salt,role,active,version,created) SELECT ?,?,?,?,'sales',1,1,? WHERE EXISTS (SELECT 1 FROM accounts WHERE id=? AND active=1 AND version=? AND role='admin')").bind(id,username,digest,salt,now(),user.id,user.version),
        env.DB.prepare('INSERT INTO mutations (key,user,operation,digest,response,order_id,ok,created) VALUES (?,?,?,?,?,NULL,changes(),?)').bind(identity.key,user.id,identity.operation,identity.digest,JSON.stringify(response),now()),
      ]);}catch(error){if(/UNIQUE/.test(String(error.message)))throw new DomainError('账号已存在。');throw error;}
      return json(response,201);
    }
    if(path==='/api/orders'&&request.method==='GET'){
      const states=await allOrders(env,user);
      return json({orders:states.map(s=>({id:s.id,number:s.number,customer:s.customer,owner:s.owner,date:s.date,status:s.status,summary:summary(s),
        shipments:s.shipments, transactions:s.transactions.map(t=>({...t,unallocated:fixed(available(s,t)),orderId:s.id,orderNumber:s.number})),search:s.lines.map(l=>l.product+' '+l.model+' '+l.supplier).join(' ')})),suggestions:suggestions(states)});
    }
    if(path==='/api/orders'&&request.method==='POST'){
      const data=await input(request),identity=await requestKey(user,request,data);
      return json(await createOrder(env,user,data,identity),201);
    }
    if(path==='/api/template'&&request.method==='GET')return new Response(productTemplate(),{headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','Content-Disposition':'attachment; filename="product-template.xlsx"','Cache-Control':'no-store'}});
    const match=path.match(/^\/api\/orders\/([a-zA-Z0-9-]+)(?:\/([a-z-]+))?(?:\/([a-zA-Z0-9-]+))?$/);
    if(!match)throw new DomainError('接口不存在。',404);
    const [,id,action,childId]=match;
    const {state}=await getOrder(env,user,id);
    if(!action&&request.method==='GET')return json({order:state,summary:summary(state)});
    if(action==='voucher'&&request.method==='GET'){
      const txn=state.transactions.find(t=>t.id===childId);if(!txn)throw new DomainError('凭证不存在。',404);
      const file=await readVoucher(env,state,txn);
      return new Response(file.body,{headers:{'Content-Type':txn.attachment.type,'Content-Disposition':"attachment; filename*=UTF-8''"+encodeURIComponent(txn.attachment.name),'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'}});
    }
    if(action==='archive'&&request.method==='GET'){
      const archive=state.archives.find(a=>a.id===childId);if(!archive)throw new DomainError('归档不存在。',404);
      const object=await env.BUCKET.get(protectedKey(state,archive.key,'archives'));if(!object)throw new DomainError('归档文件暂不可用。',404);
      return new Response(object.body,{headers:{'Content-Type':'application/zip','Content-Disposition':'attachment; filename="order-archive.zip"','X-Archive-SHA256':archive.digest,'Cache-Control':'private, no-store'}});
    }
    if(action==='export'&&request.method==='GET'){
      const bytes=await exportOrder(state,async t=>(await readVoucher(env,state,t)).arrayBuffer());
      return new Response(bytes,{headers:{'Content-Type':'application/zip','Content-Disposition':'attachment; filename="order-snapshot.zip"','Cache-Control':'private, no-store'}});
    }
    if(request.method!=='POST')throw new DomainError('请求方法无效。',405);
    if(['transaction','allocation','correction','freight','lock'].includes(action))requireUser(user,true);
    const data=action==='transaction'?await transactionInput(env,request,id,user):await input(request);
    const identity=await requestKey(user,request,data);
    let prepare;
    if(action==='lock'){
      prepare=async(updated,result)=>{
        const archiveId=uuid(),key=id+'/archives/'+archiveId+'.zip';
        const bytes=await exportOrder(updated,async t=>(await readVoucher(env,updated,t)).arrayBuffer());
        const digest=hex(await crypto.subtle.digest('SHA-256',bytes));
        await env.BUCKET.put(key,bytes,{httpMetadata:{contentType:'application/zip'}});
        updated.archives.push({id:archiveId,key,digest,created:now(),createdBy:user.username});
        result.archiveId=archiveId;
      };
    }
    return json(await mutateOrder(env,user,id,action,data,identity,prepare));
  }catch(error){
    const status=error instanceof DomainError?error.status:500;
    if(status===500)console.error('Order API failure:',error?.name||'Error');
    return json({error:error instanceof DomainError?error.message:'暂时无法保存，请稍后重试；已保存的记录会保留。'},status);
  }
}
