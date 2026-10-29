
import {DomainError,uuid,now,text} from './accounting.mjs';
const encoder=new TextEncoder();
export const hex=bytes=>[...new Uint8Array(bytes)].map(v=>v.toString(16).padStart(2,'0')).join('');
const unhex=value=>new Uint8Array((value.match(/../g)||[]).map(v=>parseInt(v,16)));
export async function hash(value){return hex(await crypto.subtle.digest('SHA-256',encoder.encode(value)));}
export async function passwordHash(password,salt){
  const key=await crypto.subtle.importKey('raw',encoder.encode(password),'PBKDF2',false,['deriveBits']);
  return hex(await crypto.subtle.deriveBits({name:'PBKDF2',hash:'SHA-256',salt:unhex(salt),iterations:100000},key,256));
}
function secret(env){if(typeof env.SESSION_KEY!=='string'||env.SESSION_KEY.length<50)throw new DomainError('试用环境尚未完成配置。',503);return env.SESSION_KEY;}
async function signingKey(env){return crypto.subtle.importKey('raw',encoder.encode(secret(env)),{name:'HMAC',hash:'SHA-256'},false,['sign','verify']);}
const base64=bytes=>btoa(String.fromCharCode(...new Uint8Array(bytes))).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
function decode(value){try{return Uint8Array.from(atob(value.replaceAll('-','+').replaceAll('_','/')),c=>c.charCodeAt(0));}catch{throw new DomainError('请重新登录。',401);}}
export async function signSession(env,user){
  const payload=base64(encoder.encode(JSON.stringify({id:user.id,version:user.version,expires:Date.now()+8*3600*1000,nonce:uuid()})));
  return payload+'.'+base64(await crypto.subtle.sign('HMAC',await signingKey(env),encoder.encode(payload)));
}
export function cookieName(request){return ['localhost','127.0.0.1'].includes(new URL(request.url).hostname)?'trial_session':'__Host-trial_session';}
export function cookie(request,value,logout=false){
  const secure=cookieName(request).startsWith('__Host')?'; Secure':'';
  return cookieName(request)+'='+value+'; Path=/; HttpOnly; SameSite=Lax; Max-Age='+(logout?0:28800)+secure;
}
export async function authenticate(env,request){
  const raw=(request.headers.get('Cookie')||'').split(';').map(v=>v.trim()).find(v=>v.startsWith(cookieName(request)+'='))?.slice(cookieName(request).length+1);
  if(!raw)return null;
  try{
    const [body,signature,...rest]=raw.split('.');
    if(rest.length||!signature||!(await crypto.subtle.verify('HMAC',await signingKey(env),decode(signature),encoder.encode(body))))return null;
    const data=JSON.parse(new TextDecoder().decode(decode(body)));
    if(typeof data.id!=='string'||!Number.isInteger(data.version)||!Number.isFinite(data.expires)||data.expires<Date.now())return null;
    const user=await env.DB.prepare('SELECT id,username,role,active,version FROM accounts WHERE id=?').bind(data.id).first();
    return user&&user.active&&user.version===data.version?user:null;
  }catch{return null;}
}
export async function ensureAccounts(env){
  secret(env);
  if(await env.DB.prepare('SELECT id FROM accounts LIMIT 1').first())return;
  let list;try{list=JSON.parse(env.BOOTSTRAP_ACCOUNTS||'[]');}catch{throw new DomainError('初始账号配置无效。',503);}
  if(!Array.isArray(list)||!list.length||!list.some(u=>u.role==='admin'))throw new DomainError('请先配置初始管理员。',503);
  const statements=[];
  for(const u of list){
    const username=text(u.username,'账号',40),salt=hex(crypto.getRandomValues(new Uint8Array(24)));
    if(!/^[a-zA-Z0-9_]{3,40}$/.test(username)||typeof u.password!=='string'||u.password.length<12||!['admin','sales'].includes(u.role))throw new DomainError('初始账号配置无效。',503);
    const password=await passwordHash(u.password,salt);
    statements.push(env.DB.prepare('INSERT OR IGNORE INTO accounts (id,username,password,salt,role,active,version,created) VALUES (?,?,?,?,?,1,1,?)').bind(uuid(),username,password,salt,u.role,now()));
  }
  await env.DB.batch(statements);
}
export async function login(env,request,data){
  const username=text(data.username,'账号',40),password=String(data.password||'');
  if(password.length>200)throw new DomainError('账号或密码不正确。',401);
  const ip=request.headers.get('CF-Connecting-IP')||'unknown';
  const key=await hash(secret(env)+':'+username.toLowerCase()+':'+ip),expires=Date.now()+600000;
  const attempt=await env.DB.prepare('INSERT INTO login_attempts (key,attempts,expires) VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET attempts=CASE WHEN expires<? THEN 1 ELSE attempts+1 END, expires=CASE WHEN expires<? THEN ? ELSE expires END RETURNING attempts').bind(key,expires,Date.now(),Date.now(),expires).first();
  if(attempt.attempts>8)throw new DomainError('尝试过多，请10分钟后重试。',429);
  const account=await env.DB.prepare('SELECT * FROM accounts WHERE username=?').bind(username).first();
  const candidate=await passwordHash(password,account?.salt||'0'.repeat(48)),expected=account?.password||'0'.repeat(64);
  let difference=candidate.length^expected.length;for(let i=0;i<64;i++)difference|=(candidate.charCodeAt(i)||0)^(expected.charCodeAt(i)||0);
  if(!account||!account.active||difference)throw new DomainError('账号或密码不正确。',401);
  await env.DB.prepare('DELETE FROM login_attempts WHERE key=?').bind(key).run();
  const user={id:account.id,username:account.username,role:account.role,active:account.active,version:account.version};
  return {user,token:await signSession(env,user)};
}
export function requireUser(user,admin=false){if(!user)throw new DomainError('请先登录。',401);if(admin&&user.role!=='admin')throw new DomainError('此操作需要管理员权限。',403);}
