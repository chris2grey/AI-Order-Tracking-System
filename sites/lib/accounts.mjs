
import {DomainError,now,text} from './accounting.mjs';
import {requireUser,passwordHash,hex,hash} from './auth.mjs';
import {requestKey} from './database.mjs';
export async function resetPassword(env,user,request,id,data){
  requireUser(user,true);
  const password=String(data.password||''),reason=text(data.reason,'重置说明',240);
  if(password.length<12||password.length>200)throw new DomainError('新密码需12至200位。');
  const identity=await requestKey(user,request,{reason,password:await hash(env.SESSION_KEY+':'+password)});
  const replay=async()=>{
    const previous=await env.DB.prepare('SELECT * FROM mutations WHERE key=?').bind(identity.key).first();
    if(!previous)return null;
    if(previous.digest!==identity.digest||previous.operation!==identity.operation)throw new DomainError('提交编号冲突。',409);
    return JSON.parse(previous.response);
  };
  const prior=await replay();if(prior)return prior;
  const target=await env.DB.prepare('SELECT id,username,version FROM accounts WHERE id=?').bind(id).first();
  if(!target)throw new DomainError('账号不存在。',404);
  const salt=hex(crypto.getRandomValues(new Uint8Array(24))),digest=await passwordHash(password,salt);
  const response={ok:true,accountId:target.id,username:target.username,reason};
  try{
    await env.DB.batch([
      env.DB.prepare("UPDATE accounts SET password=?,salt=?,version=version+1 WHERE id=? AND version=? AND EXISTS (SELECT 1 FROM accounts WHERE id=? AND active=1 AND version=? AND role='admin')").bind(digest,salt,id,target.version,user.id,user.version),
      env.DB.prepare('INSERT INTO mutations (key,user,operation,digest,response,order_id,ok,created) VALUES (?,?,?,?,?,NULL,changes(),?)').bind(identity.key,user.id,identity.operation,identity.digest,JSON.stringify(response),now()),
    ]);
  }catch(error){
    const previous=await replay();if(previous)return previous;
    if(/CHECK constraint|mutation_committed_once|UNIQUE constraint failed: mutations.key|SQLITE_BUSY/i.test(String(error?.message)))throw new DomainError('账号状态已变化，请重新登录后重试。',409);
    throw error;
  }
  return response;
}
