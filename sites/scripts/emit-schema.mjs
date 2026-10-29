
import {readdirSync,readFileSync}from'node:fs';
for(const name of readdirSync('drizzle').filter(n=>n.endsWith('.sql')))console.log('SITES_GENERATED_SOURCE '+JSON.stringify({path:'sites/drizzle/'+name,content:readFileSync('drizzle/'+name,'utf8')}));
for(const name of readdirSync('drizzle/meta').filter(n=>n.endsWith('.json')))console.log('SITES_GENERATED_SOURCE '+JSON.stringify({path:'sites/drizzle/meta/'+name,content:readFileSync('drizzle/meta/'+name,'utf8')}));
