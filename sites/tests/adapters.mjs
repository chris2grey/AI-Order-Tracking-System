
import {DatabaseSync} from 'node:sqlite';
import {readdirSync,readFileSync} from 'node:fs';
export function database(){
  const db=new DatabaseSync(':memory:');
  for(const file of readdirSync('drizzle').filter(n=>n.endsWith('.sql')).sort())db.exec(readFileSync('drizzle/'+file,'utf8'));
  function prepared(sql,args=[]){
    const run=()=>{const statement=db.prepare(sql),result=statement.run(...args);return {success:true,meta:{changes:Number(result.changes)},results:[]};};
    return {sql,args,bind(...values){return prepared(sql,values);},async first(){return db.prepare(sql).get(...args)||null;},async all(){return {results:db.prepare(sql).all(...args)};},async run(){return run();},_run:run};
  }
  return {prepare:prepared,async batch(statements){db.exec('BEGIN');try{const results=statements.map(s=>s._run());db.exec('COMMIT');return results;}catch(e){db.exec('ROLLBACK');throw e;}},close(){db.close();}};
}
export function bucket(){
  const files=new Map();
  return {async put(key,value){files.set(key,new Uint8Array(value));return {};},async get(key){const value=files.get(key);return value?{body:new ReadableStream({start(c){c.enqueue(value);c.close();}}),async arrayBuffer(){return value.buffer.slice(value.byteOffset,value.byteOffset+value.byteLength);}}:null;},files};
}
