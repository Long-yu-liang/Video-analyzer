/* Bounded display copy only. Downloads and persisted records keep the original data. */
(function(root) {
  'use strict';
  function format(record) {
    if(!record)return '暂无分析记录，请先分析视频或打开已有项目。';
    let budget=12000,nodes=0;
    const seen=new WeakSet();
    function copy(value,key='',depth=0) {
      if(++nodes>180||budget<=0)return '[预览已省略]';
      if(typeof value==='string') {
        if(/base64|data_?url/i.test(key)||/^data:[^,]*;base64,/.test(value.slice(0,160)))return `[媒体编码已省略，${value.length} 字符]`;
        const limit=Math.min(2000,budget),text=value.slice(0,limit);budget-=text.length;
        return value.length>limit?text+`…[已省略 ${value.length-limit} 字符]`:text;
      }
      if(value===null||typeof value!=='object')return value;
      if(depth>=7)return '[嵌套内容已省略]';
      if(seen.has(value))return '[重复引用]';seen.add(value);
      if(Array.isArray(value)) {
        const result=[];
        for(let i=0;i<Math.min(value.length,12)&&nodes<180&&budget>0;i++)result.push(copy(value[i],key,depth+1));
        if(result.length<value.length)result.push(`[其余 ${value.length-result.length} 项已省略]`);
        return result;
      }
      const result=Object.create(null);let count=0;
      for(const name in value) {
        if(!Object.hasOwn(value,name))continue;
        if(count++>=40||nodes>=180||budget<=0){result['…']='其余字段已省略';break;}
        result[name.slice(0,100)]=copy(value[name],name,depth+1);
      }
      return result;
    }
    const text=JSON.stringify(copy(record),null,2);
    return '显示限长预览，媒体编码及过长内容已省略；完整内容请导出记录。\n\n'+text.slice(0,24000)+(text.length>24000?'\n…[预览已截断]':'');
  }
  function create(details,host,getRecord) {
    let cachedRecord,cachedText;
    function refresh() {
      if(!host)return;
      if(details&&!details.open){host.textContent='展开后显示记录预览，完整内容可通过导出查看。';return;}
      const record=getRecord();
      if(record!==cachedRecord||cachedText===undefined){cachedText=format(record);cachedRecord=record;}
      if(host.textContent!==cachedText)host.textContent=cachedText;
    }
    details?.addEventListener('toggle',refresh);
    return {refresh};
  }
  root.RecordPreview={format,create};
})(typeof window==='undefined'?globalThis:window);
