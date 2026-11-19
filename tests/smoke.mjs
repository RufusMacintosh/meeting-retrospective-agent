import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import worker from '../worker/index.js';

const db=new DatabaseSync(':memory:');
db.exec(readFileSync(new URL('../drizzle/0000_meeting_studio.sql',import.meta.url),'utf8').replaceAll('--> statement-breakpoint',''));
db.exec(readFileSync(new URL('../drizzle/0001_demo_keys.sql',import.meta.url),'utf8'));
class Statement {
  constructor(q,args=[]){this.q=q;this.args=args}
  bind(...args){return new Statement(this.q,args)}
  first(){return db.prepare(this.q).get(...this.args)||null}
  all(){return {results:db.prepare(this.q).all(...this.args)}}
  run(){return db.prepare(this.q).run(...this.args)}
}
const env={DB:{prepare:q=>new Statement(q),async batch(cmds){db.exec('BEGIN');try {const out=[];for(const c of cmds)out.push(c.run());db.exec('COMMIT');return out}catch(e){db.exec('ROLLBACK');throw e}}},KEY_ENCRYPTION_SECRET:Buffer.from(crypto.getRandomValues(new Uint8Array(32))).toString('base64url')};
const origin='https://example.test';
async function request(path,method='GET',body,cookie=''){
  const headers={origin,cookie};if(body)headers['content-type']='application/json';
  const req=new Request(origin+path,{method,headers,body:body?JSON.stringify(body):undefined});
  const res=await worker.fetch(req,env);return {status:res.status,data:await res.json(),cookie:res.headers.get('set-cookie')?.split(';')[0]||cookie};
}
const a=await request('/api/auth/register','POST',{email:'qa-a@example.test',password:'test-password-123'});
assert.equal(a.status,200);assert.ok(a.cookie.startsWith('__Host-mr_session='));
const b=await request('/api/auth/register','POST',{email:'qa-b@example.test',password:'test-password-123'});
assert.equal(b.status,200);
const logged=await request('/api/auth/login','POST',{email:'qa-a@example.test',password:'test-password-123'});
assert.equal(logged.status,200);
const p=await request('/api/projects','POST',{name:'测试项目'},a.cookie);assert.equal(p.status,201);
const forbidden=await request('/api/projects/'+p.data.id+'/meetings','GET',undefined,b.cookie);assert.equal(forbidden.status,404);
const m=await request('/api/projects/'+p.data.id+'/meetings','POST',{title:'产品周会',date:'2026-11-19',transcript:'00:01:20 张三：我负责下周整理访谈提纲。\n00:02:30 李四：目前接口还没有联调完成。'},a.cookie);
assert.equal(m.status,201);
const origFetch=globalThis.fetch;
globalThis.fetch=async()=>new Response(JSON.stringify({choices:[{message:{content:JSON.stringify({summary:'确认访谈提纲和接口进度。',decisions:[{text:'整理提纲',segment_id:'S-001'}],risks:[{text:'无证据',segment_id:'S-999'}],items:[{title:'整理访谈提纲',description:'张三负责整理访谈提纲',module:'user_insights',category:'action',status:'open',owner:'张三',due_date:'下周',segment_id:'S-001'},{title:'虚构事项',segment_id:'S-999'}]})}}]}),{headers:{'content-type':'application/json'}});
try {
  const key=await request('/api/keys/openai','PUT',{key:'sk-test-not-real-12345'},a.cookie);assert.equal(key.status,200);
  const listed=await request('/api/keys','GET',undefined,a.cookie);assert.equal(listed.data.length,1);assert.equal(JSON.stringify(listed.data).includes('sk-test'),false);
  const analyzed=await request('/api/meetings/'+m.data.id+'/analyze','POST',{provider:'openai',model:'gpt-4o-mini'},a.cookie);
  assert.equal(analyzed.status,200,JSON.stringify(analyzed.data));assert.equal(analyzed.data.items.length,1);assert.equal(analyzed.data.discarded,1);assert.equal(analyzed.data.risks.length,0);
  const matters=await request('/api/projects/'+p.data.id+'/matters','GET',undefined,a.cookie);assert.equal(matters.data.length,1);
  assert.equal(matters.data[0].evidence[0].quote,'我负责下周整理访谈提纲。');
  const hidden=await request('/api/matters/'+matters.data[0].id+'/history','GET',undefined,b.cookie);assert.equal(hidden.status,404);
  const removed=await request('/api/meetings/'+m.data.id,'DELETE',undefined,a.cookie);assert.equal(removed.status,200);
  const after=await request('/api/projects/'+p.data.id+'/matters','GET',undefined,a.cookie);assert.equal(after.data.length,0);
} finally {globalThis.fetch=origFetch}
console.log('Smoke passed: register, login, isolation, meeting import, encrypted key, model evidence, history and deletion');

const demoCredentials={email:'demo@meeting-retrospective.example',password:'test-password-123'};
const demo=await request('/api/auth/register','POST',demoCredentials);
assert.equal(demo.status,200);
const otherSession=await request('/api/auth/login','POST',demoCredentials);
assert.equal(otherSession.status,200);
assert.equal((await request('/api/keys/openai','PUT',{key:'sk-demo-test-value'},demo.cookie)).status,200);
assert.equal((await request('/api/keys','GET',undefined,demo.cookie)).data.length,1);
assert.deepEqual((await request('/api/keys','GET',undefined,otherSession.cookie)).data,[]);
assert.equal((await request('/api/auth/logout','POST',{},demo.cookie)).status,200);
assert.equal(db.prepare('SELECT COUNT(*) AS n FROM demo_provider_keys').get().n,0);
assert.equal((await request('/api/auth/register','POST',demoCredentials)).status,409);
console.log('Demo key isolation, logout cleanup and duplicate registration passed');
