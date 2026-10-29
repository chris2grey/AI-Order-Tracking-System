
/** Small dependency-free XLSX/ZIP writer; imports are parsed only in the browser. */
const encoder=new TextEncoder(),decoder=new TextDecoder();
const crcTable=Array.from({length:256},(_,n)=>{for(let k=0;k<8;k++)n=(n&1)?0xedb88320^(n>>>1):n>>>1;return n>>>0;});
export function crc32(bytes){let crc=0xffffffff;for(const b of bytes)crc=crcTable[(crc^b)&255]^(crc>>>8);return (crc^0xffffffff)>>>0;}
function record(size,values){const bytes=new Uint8Array(size),view=new DataView(bytes.buffer);for(const [offset,length,value]of values)length===2?view.setUint16(offset,value,true):view.setUint32(offset,value,true);return bytes;}
function join(parts){const size=parts.reduce((n,p)=>n+p.length,0);if(size>50*1024*1024)throw Error('导出文件过大，请拆分订单或单独下载凭证。');const out=new Uint8Array(size);let offset=0;for(const p of parts){out.set(p,offset);offset+=p.length;}return out;}
export function zip(files){
  const local=[],central=[];let offset=0,count=0;
  for(const [name,value]of Object.entries(files)){
    const filename=encoder.encode(name),data=typeof value==='string'?encoder.encode(value):new Uint8Array(value),crc=crc32(data);
    const header=record(30,[[0,4,0x04034b50],[4,2,20],[6,2,0x800],[8,2,0],[12,2,33],[14,4,crc],[18,4,data.length],[22,4,data.length],[26,2,filename.length]]);
    local.push(header,filename,data);
    central.push(record(46,[[0,4,0x02014b50],[4,2,20],[6,2,20],[8,2,0x800],[10,2,0],[14,2,33],[16,4,crc],[20,4,data.length],[24,4,data.length],[28,2,filename.length],[42,4,offset]]),filename);
    offset+=header.length+filename.length+data.length;count++;
  }
  const centralSize=central.reduce((n,p)=>n+p.length,0);
  return join([...local,...central,record(22,[[0,4,0x06054b50],[8,2,count],[10,2,count],[12,4,centralSize],[16,4,offset]])]);
}
export const xml=value=>String(value??'').replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g,'�').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
function letter(index){let s='';for(index++;index;index=Math.floor((index-1)/26))s=String.fromCharCode(65+(index-1)%26)+s;return s;}
const header='<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';
export function workbook(sheets){
  const files={};
  let types='<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>';
  const names=[],rels=[];
  sheets.forEach(([name,columns,rows],index)=>{
    const id=index+1;name=String(name).replace(/[\\/?*\[\]:]/g,'').slice(0,31)||'Sheet'+id;
    names.push('<sheet name="'+xml(name)+'" sheetId="'+id+'" r:id="rId'+id+'"/>');
    rels.push('<Relationship Id="rId'+id+'" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet'+id+'.xml"/>');
    types+='<Override PartName="/xl/worksheets/sheet'+id+'.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>';
    const cells=[columns,...rows].map((row,r)=>'<row r="'+(r+1)+'">'+row.map((v,c)=>{
      // All user and financial text stays text. Numeric quantities remain exact integers.
      const ref=letter(c)+(r+1),style=r===0?' s="1"':'';
      if(typeof v==='number'&&Number.isSafeInteger(v))return '<c r="'+ref+'"'+style+'><v>'+v+'</v></c>';
      return '<c r="'+ref+'" t="inlineStr"'+style+'><is><t xml:space="preserve">'+xml(v)+'</t></is></c>';
    }).join('')+'</row>').join('');
    files['xl/worksheets/sheet'+id+'.xml']=header+'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>'+columns.map((_,i)=>'<col min="'+(i+1)+'" max="'+(i+1)+'" width="22" customWidth="1"/>').join('')+'</cols><sheetData>'+cells+'</sheetData><autoFilter ref="A1:'+letter(columns.length-1)+(rows.length+1)+'"/></worksheet>';
  });
  files['[Content_Types].xml']=header+'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'+types+'</Types>';
  files['_rels/.rels']=header+'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>';
  files['xl/workbook.xml']=header+'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+names.join('')+'</sheets></workbook>';
  files['xl/_rels/workbook.xml.rels']=header+'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+rels.join('')+'</Relationships>';
  files['xl/styles.xml']=header+'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF2763D6"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFill="1" applyFont="1"/></cellXfs></styleSheet>';
  return zip(files);
}
export const productColumns=['产品名称','产品型号','数量','销售单价','销售币种','供货方式','供应商名称','单位成本CNY','客户运费'];
export function productTemplate(){
  return workbook([['产品录入',productColumns,[]],['填写说明',['项目','要求'],[
    ['流程','填写后上传 → 核对草稿和统一汇率 → 保存全部产品。序号由系统生成。'],
    ['供货方式','填写 外部供应商 或 自供货。自供货只统计内部成本，不产生对外待付。'],
    ['币种','USD / CNY / EUR / HKD / GBP；报价汇率在网页统一填写。'],
    ['必填','产品名称、型号、数量、单价、币种、供货方式和单位成本；外部供货填写供应商。'],
    ['可空','客户运费留空为0；单价是否含运费及条款在网页统一填写。'],
    ['限制','最多100行，不支持公式、宏或合并单元格。相同型号的补单不会合并。'],
  ]]]);
}
async function unzip(input){
  const bytes=new Uint8Array(input),view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  let end=-1;for(let i=bytes.length-22;i>=Math.max(0,bytes.length-65557);i--)if(view.getUint32(i,true)===0x06054b50){end=i;break;}
  if(end<0)throw Error('无法识别Excel文件。');
  const count=view.getUint16(end+10,true);let pos=view.getUint32(end+16,true),total=0;const files={};
  if(count>250)throw Error('工作簿文件过多，请使用固定模板。');
  for(let n=0;n<count;n++){
    if(pos+46>bytes.length||view.getUint32(pos,true)!==0x02014b50)throw Error('工作簿目录损坏。');
    const flags=view.getUint16(pos+8,true),method=view.getUint16(pos+10,true),crc=view.getUint32(pos+16,true),packed=view.getUint32(pos+20,true),size=view.getUint32(pos+24,true);
    const nameLength=view.getUint16(pos+28,true),extra=view.getUint16(pos+30,true),comment=view.getUint16(pos+32,true),offset=view.getUint32(pos+42,true);
    const name=decoder.decode(bytes.subarray(pos+46,pos+46+nameLength));pos+=46+nameLength+extra+comment;total+=size;
    if(flags&1||total>25*1024*1024||name.includes('..')||name.startsWith('/')||/vbaproject/i.test(name))throw Error('不接受加密、宏或过大的工作簿。');
    if(offset+30>bytes.length||view.getUint32(offset,true)!==0x04034b50)throw Error('工作簿内容损坏。');
    const start=offset+30+view.getUint16(offset+26,true)+view.getUint16(offset+28,true);
    if(start+packed>bytes.length)throw Error('工作簿内容不完整。');
    let data=bytes.slice(start,start+packed);
    if(method===8){
      const stream=new Blob([data]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
      const reader=stream.getReader(),chunks=[];let actual=0;
      while(true){const {done,value}=await reader.read();if(done)break;actual+=value.length;if(actual>size||actual>25*1024*1024){await reader.cancel();throw Error('工作簿内容过大。');}chunks.push(value);}
      data=join(chunks);
    }else if(method!==0)throw Error('不支持该工作簿压缩格式，请另存为普通xlsx。');
    if(data.length!==size||crc32(data)!==crc)throw Error('工作簿校验失败。');
    files[name]=data;
  }
  return files;
}
function parseXML(bytes){
  const value=decoder.decode(bytes||new Uint8Array());
  if(/<!DOCTYPE|<!ENTITY/i.test(value))throw Error('不支持包含外部实体的工作簿。');
  const doc=new DOMParser().parseFromString(value,'application/xml');
  if(doc.querySelector('parsererror'))throw Error('工作簿XML格式无效。');
  return doc;
}
export async function importProducts(file){
  if(!file||!file.name.toLowerCase().endsWith('.xlsx')||file.size>5*1024*1024)throw Error('请选择不超过5MB的固定xlsx模板。');
  const files=await unzip(await file.arrayBuffer());
  const book=parseXML(files['xl/workbook.xml']),sheet=[...book.querySelectorAll('sheet')].find(s=>s.getAttribute('name')==='产品录入');
  if(!sheet)throw Error('缺少“产品录入”工作表。');
  const rel=sheet.getAttribute('r:id'),rels=parseXML(files['xl/_rels/workbook.xml.rels']);
  const target=[...rels.querySelectorAll('Relationship')].find(r=>r.getAttribute('Id')===rel)?.getAttribute('Target');
  if(!target||target.includes('..')||target.includes('://'))throw Error('工作表引用无效。');
  const path=target.startsWith('/')?target.slice(1):'xl/'+target;
  const doc=parseXML(files[path]);
  if(doc.querySelector('f,mergeCells'))throw Error('模板不支持公式或合并单元格，请粘贴为实际数值。');
  const strings=files['xl/sharedStrings.xml']?[...parseXML(files['xl/sharedStrings.xml']).querySelectorAll('si')].map(n=>[...n.querySelectorAll('t')].map(t=>t.textContent).join('')):[];
  const rows=[...doc.querySelectorAll('sheetData > row')].map(row=>{
    const result=[];
    for(const c of row.querySelectorAll('c')){
      const letters=(c.getAttribute('r')||'').match(/^[A-Z]+/)?.[0];
      if(!letters)throw Error('单元格坐标无效。');
      let column=0;for(const l of letters)column=column*26+l.charCodeAt(0)-64;column--;
      if(column>9)throw Error('列数与固定模板不一致。');
      const type=c.getAttribute('t'),value=c.querySelector('v')?.textContent||'';
      result[column]=type==='s'?strings[Number(value)]||'':type==='inlineStr'?[...c.querySelectorAll('t')].map(t=>t.textContent).join(''):value;
    }
    return Array.from({length:Math.max(result.length,9)},(_,i)=>result[i]||'');
  });
  let headers=rows.shift()||[],legacy=headers[0]==='小项编号（可空）';
  if(legacy)headers=headers.slice(1);
  if(JSON.stringify(headers)!==JSON.stringify(productColumns))throw Error('列标题与固定模板不一致，请重新下载模板。');
  const valid=rows.filter(row=>row.some(v=>v!==''));
  if(!valid.length||valid.length>100)throw Error('模板须包含1至100行产品。');
  return valid.map(row=>{
    if(legacy)row=row.slice(1);
    const [product,model,quantity,price,currency,supplyType,supplier,cost,freight]=row;
    return {product,model,quantity,price,currency:currency.toUpperCase(),supplyType:({'自供货':'self','外部供应商':'external',self:'self',external:'external'})[supplyType]||supplyType,supplier,cost,freight:freight||'0'};
  });
}
export function download(bytes,filename,type='application/octet-stream'){
  const url=URL.createObjectURL(new Blob([bytes],{type})),anchor=document.createElement('a');
  anchor.href=url;anchor.download=filename;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),20000);
}
