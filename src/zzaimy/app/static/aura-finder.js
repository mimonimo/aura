/* 공용 디렉터리 창(맥 파인더 꼴) — 부서 폴더·구글 드라이브를 한 창에서 고른다.
   AuraFinder.open({title, accept:'hwp'|'docs', multiple, openLabel, projectFolder:{id,url}})
   → Promise: 취소 null, 고르면 [{source:'dept', id, name, path} | {source:'drive', drive_id, name, mime, path}]
   여러 개 고르기: 줄 앞 동그라미를 누르거나 ⌘/Ctrl·Shift 누르고 클릭. 위 검색은 쓸 수 있는 부서 폴더 전부 + 드라이브. */
(function(){
  const el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e;};
  const icon=name=>{const i=document.createElement('iconify-icon');i.setAttribute('icon',name);i.setAttribute('aria-hidden','true');return i;};
  async function api(url){const r=await fetch(url,{headers:{Accept:'application/json'},credentials:'same-origin'});
    let j=null;try{j=await r.json();}catch(_){}
    if(!r.ok){const e=new Error((j&&j.detail)||'불러오지 못했습니다');e.status=r.status;throw e;}return j;}
  const HWP=/\.hwpx?$/i;
  const GDOC={'application/vnd.google-apps.document':'구글 문서','application/vnd.google-apps.spreadsheet':'구글 시트','application/vnd.google-apps.presentation':'구글 슬라이드'};
  function fileIcon(name,mime){
    if(GDOC[mime])return mime.endsWith('spreadsheet')?'solar:chart-square-linear':mime.endsWith('presentation')?'solar:presentation-graph-linear':'solar:document-text-linear';
    if(/\.pdf$/i.test(name))return 'solar:file-text-linear';
    if(/\.(xlsx?|csv)$/i.test(name))return 'solar:chart-square-linear';
    return 'solar:document-text-linear';
  }
  const stem=n=>String(n||'').replace(HWP,'');
  const kindOf=(name,mime)=>GDOC[mime]||(HWP.test(name)?'한글':((String(name).match(/\.([a-z0-9]+)$/i)||[])[1]||'').toUpperCase());

  function open(opts){
    opts=Object.assign({title:'파일 가져오기',accept:'hwp',multiple:false,openLabel:'열기'},opts||{});
    return new Promise(resolve=>{
      const d=el('dialog','aura-finder');d.setAttribute('aria-label',opts.title);
      const what=opts.accept==='docs'?'문서':'한글 파일';
      d.innerHTML='<header><h2></h2><form class="af-search" role="search"><iconify-icon icon="solar:magnifer-linear" aria-hidden="true"></iconify-icon><input type="search" name="q"></form><button type="button" class="af-x" aria-label="닫기">×</button></header>'
        +'<div class="af-body"><nav class="af-side" aria-label="위치"></nav><section class="af-main"><div class="af-bar"><button type="button" class="af-back" aria-label="뒤로" disabled>‹</button><div class="af-path"></div></div><div class="af-list" role="listbox"></div></section></div>'
        +'<footer><div class="af-sel"><span class="af-sel-main"></span><span class="af-sel-where"></span></div><span class="af-msg" role="status"></span><button type="button" class="secondary" data-cancel>취소</button><button type="button" data-open disabled></button></footer>';
      d.querySelector('h2').textContent=opts.title;
      const q=d.querySelector('input');q.placeholder='부서 폴더·드라이브에서 '+what+' 찾기';q.setAttribute('aria-label',q.placeholder);
      const list=d.querySelector('.af-list');list.setAttribute('aria-multiselectable',opts.multiple?'true':'false');list.setAttribute('aria-label','폴더와 파일');
      document.body.append(d);d.showModal();
      const side=d.querySelector('.af-side'),path=d.querySelector('.af-path'),back=d.querySelector('.af-back');
      const selMain=d.querySelector('.af-sel-main'),selWhere=d.querySelector('.af-sel-where'),msg=d.querySelector('.af-msg'),openBtn=d.querySelector('[data-open]');
      let done=false;const finish=v=>{if(done)return;done=true;resolve(v);d.close();};
      d.querySelector('.af-x').onclick=()=>finish(null);d.querySelector('[data-cancel]').onclick=()=>finish(null);
      d.addEventListener('close',()=>{if(!done){done=true;resolve(null);}d.remove();});
      d.addEventListener('click',e=>{if(e.target===d)finish(null);});
      let loc=null,hist=[],drives=[],picked=new Map(),lastIdx=-1,where='';
      const key=it=>it.source==='drive'?'g:'+it.drive_id:'d:'+it.id;
      function paintSel(){
        const n=picked.size,items=[...picked.values()];list.classList.toggle('multi-on',opts.multiple&&n>0);
        openBtn.disabled=!n;openBtn.textContent=opts.openLabel+(opts.multiple&&n>1?' ('+n+')':'');
        selMain.replaceChildren();selWhere.textContent='';
        if(!n){selMain.textContent=opts.multiple?what+'을 고르세요 · 여러 개는 동그라미를 누르세요':what+'을 고르세요';selMain.classList.add('is-hint');return;}
        selMain.classList.remove('is-hint');
        selMain.append(icon(n>1?'solar:documents-linear':fileIcon(items[0].name,items[0].mime)),el('b',null,n>1?n+'개 고름':stem(items[0].name)));
        selWhere.textContent=n>1?items.map(x=>stem(x.name)).slice(0,3).join(', ')+(n>3?' 외 '+(n-3)+'개':''):(items[0].path||'');
        if(n>1){const c=el('button','af-clear','선택 해제');c.type='button';c.onclick=()=>{picked.clear();list.querySelectorAll('.af-row.on').forEach(r=>{r.classList.remove('on');r.setAttribute('aria-selected','false');});paintSel();};selWhere.append(' ',c);}
      }
      const rows=()=>[...list.querySelectorAll('.af-row[data-kind=file]')];
      function toggle(r,on){const it=r._item;if(on){picked.set(key(it),it);}else picked.delete(key(it));r.classList.toggle('on',on);r.setAttribute('aria-selected',on?'true':'false');}
      function choose(r,e,viaCheck){
        const all=rows(),i=all.indexOf(r);
        if(!opts.multiple){all.forEach(x=>toggle(x,false));picked.clear();toggle(r,true);}
        else if(e&&e.shiftKey&&lastIdx>=0){const [a,b]=[Math.min(lastIdx,i),Math.max(lastIdx,i)];all.slice(a,b+1).forEach(x=>toggle(x,true));}
        else if(viaCheck||(e&&(e.metaKey||e.ctrlKey))){toggle(r,!r.classList.contains('on'));}
        else{all.forEach(x=>toggle(x,false));picked.clear();toggle(r,true);}
        lastIdx=i;paintSel();
      }
      const sideBtn=(label,ic,go,k)=>{const b=el('button','af-place');b.type='button';b.dataset.key=k;b.append(icon(ic),el('span',null,label));b.onclick=()=>{hist=[];go();};return b;};
      const markSide=k=>side.querySelectorAll('.af-place').forEach(b=>b.classList.toggle('on',b.dataset.key===k));
      function folderRow(name,meta,go){const r=el('button','af-row');r.type='button';r.dataset.kind='folder';r.append(el('span','af-check'),icon('solar:folder-bold'),el('span','af-name',name),el('span','af-meta',meta||''));r.onclick=()=>{hist.push(loc);go();};list.append(r);}
      function fileRow(item,meta){
        const r=el('div','af-row');r.dataset.kind='file';r.setAttribute('role','option');r.tabIndex=0;r._item=item;
        const ck=el('button','af-check');ck.type='button';ck.setAttribute('aria-label','고르기');if(!opts.multiple)ck.hidden=true;
        ck.onclick=e=>{e.stopPropagation();choose(r,e,true);};
        r.append(ck,icon(fileIcon(item.name,item.mime)),el('span','af-name',stem(item.name)),el('span','af-meta',meta||''));
        r.onclick=e=>choose(r,e,false);
        r.ondblclick=()=>{if(!opts.multiple||picked.size<=1){picked.clear();picked.set(key(item),item);submit();}};
        r.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();submit();}else if(e.key===' '){e.preventDefault();choose(r,e,opts.multiple);}
          else if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();const all=rows(),n=all[all.indexOf(r)+(e.key==='ArrowDown'?1:-1)];if(n)n.focus();}};
        if(picked.has(key(item))){r.classList.add('on');r.setAttribute('aria-selected','true');}else r.setAttribute('aria-selected','false');
        list.append(r);
      }
      const crumbs=parts=>{path.replaceChildren();parts.forEach((p,i)=>{if(i)path.append(el('span','af-sep','›'));const b=el('button',null,p.name);b.type='button';b.disabled=!p.go||i===parts.length-1;if(p.go)b.onclick=()=>{hist.push(loc);p.go();};path.append(b);});where=parts.map(p=>p.name).join(' › ');};
      const busy=t=>{list.replaceChildren(el('p','af-empty',t));lastIdx=-1;};
      const nowLoc=l=>{loc=l;back.disabled=!hist.length;};
      async function goDept(dept,folder){
        nowLoc({src:'dept',dept,folder});markSide('dept:'+dept);busy('불러오는 중…');
        let j;try{j=await api('/api/hwp/browse?dept='+encodeURIComponent(dept||'')+'&folder='+encodeURIComponent(folder||''));}catch(e){busy(e.message);return null;}
        if(!d.open)return j;const here=j.here;if(!here){busy('쓸 수 있는 부서 폴더가 없습니다');return j;}
        if(!dept){loc.dept=here.dept;markSide('dept:'+here.dept);}
        crumbs([{name:here.dept,go:()=>goDept(here.dept,'')},...(here.crumbs||[]).map(c=>({name:c.name,go:()=>goDept(here.dept,c.path)}))]);
        list.replaceChildren();const at=where;
        here.folders.forEach(f=>folderRow(f.name,'문서 '+f.count,()=>goDept(here.dept,f.path)));
        here.files.forEach(f=>fileRow({source:'dept',id:f.id,name:f.name,path:at},[f.saved,f.saved_by||f.owner].filter(Boolean).join(' · ')));
        if(!here.folders.length&&!here.files.length)busy('이 폴더는 비어 있습니다');
        return j;
      }
      async function goDrive(drive,folder,trail,rootName){
        nowLoc({src:'drive',drive,folder,trail,rootName});markSide(rootName==='프로젝트 폴더'?'drive:project':'drive:'+(drive||'my'));busy('불러오는 중…');
        const root=rootName||(drive?(drives.find(x=>x.id===drive)||{name:'공유 드라이브'}).name:'내 드라이브');
        crumbs([{name:root,go:()=>goDrive(drive,trail.length&&rootName?folder:'',[],rootName)},...trail.map((t,i)=>({name:t.name,go:()=>goDrive(drive,t.id,trail.slice(0,i+1),rootName)}))]);
        if(rootName&&!trail.length)crumbs([{name:root}]);
        const at=where;
        let j;try{j=await api('/api/finder/drive?accept='+opts.accept+'&drive='+encodeURIComponent(drive||'')+'&folder='+encodeURIComponent(folder||''));}
        catch(e){busy(e.status===409?'학교 구글 계정을 연결하면 드라이브 파일을 고를 수 있습니다 · 설정 › 내 학교 계정 연결':e.message);return;}
        if(!d.open)return;list.replaceChildren();
        j.folders.forEach(f=>folderRow(f.name,'폴더',()=>goDrive(drive,f.id,[...trail,f],rootName)));
        j.files.forEach(f=>fileRow({source:'drive',drive_id:f.id,name:f.name,mime:f.mimeType,path:at},[kindOf(f.name,f.mimeType),(f.modifiedTime||'').slice(0,10)].filter(Boolean).join(' · ')));
        if(!j.folders.length&&!j.files.length)busy('이 폴더에 '+what+'이 없습니다');
      }
      async function search(text){
        nowLoc({src:'search',text});markSide('');busy('찾는 중…');crumbs([{name:'「'+text+'」 찾은 결과'}]);
        let j;try{j=await api('/api/finder/search?accept='+opts.accept+'&q='+encodeURIComponent(text));}catch(e){busy(e.message);return;}
        if(!d.open)return;list.replaceChildren();
        if(j.dept.length){list.append(el('p','af-sect','부서 폴더 · '+j.dept.length));
          j.dept.forEach(f=>{const at=[f.dept,...(f.folder?f.folder.split('/'):[])].join(' › ');fileRow({source:'dept',id:f.id,name:f.name,path:at},at);});}
        if(j.drive.length){list.append(el('p','af-sect','구글 드라이브 · '+j.drive.length));
          j.drive.forEach(f=>fileRow({source:'drive',drive_id:f.id,name:f.name,mime:f.mimeType,path:'구글 드라이브'},['드라이브',kindOf(f.name,f.mimeType),(f.modifiedTime||'').slice(0,10)].filter(Boolean).join(' · ')));}
        if(!j.dept.length&&!j.drive.length)busy('맞는 '+what+'이 없습니다');
        if(j.drive_state==='no-account')list.append(el('p','af-note','드라이브는 학교 구글 계정을 연결하면 함께 찾습니다'));
        else if(j.drive_state==='fail')list.append(el('p','af-note','드라이브에서는 찾지 못했습니다(연결 확인 필요)'));
      }
      const goLoc=l=>l.src==='dept'?goDept(l.dept,l.folder):l.src==='drive'?goDrive(l.drive,l.folder,l.trail,l.rootName):search(l.text);
      back.onclick=()=>{const l=hist.pop();if(l)goLoc(l);};
      let timer=0;
      d.querySelector('.af-search').onsubmit=e=>{e.preventDefault();clearTimeout(timer);const t=q.value.trim();if(t){hist.push(loc);search(t);}};
      q.oninput=()=>{clearTimeout(timer);const t=q.value.trim();if(t.length<2)return;timer=setTimeout(()=>{if(loc&&loc.src!=='search')hist.push(loc);search(t);},350);};
      function submit(){if(!picked.size)return;finish([...picked.values()].map(({source,id,drive_id,name,mime,path})=>source==='drive'?{source,drive_id,name,mime,path}:{source,id,name,path}));}
      openBtn.onclick=submit;
      paintSel();
      // 왼쪽 — 부서 폴더(쓸 수 있는 곳), 구글 드라이브(내 드라이브·공유 드라이브·이 프로젝트 폴더)
      side.append(el('p','af-group','부서 폴더'));
      goDept('','').then(first=>{
        const anchor=el('span');side.append(anchor);
        ((first&&first.depts)||[]).forEach(x=>anchor.before(sideBtn(x.dept,'solar:folder-with-files-linear',()=>goDept(x.dept,''),'dept:'+x.dept)));
        if(first&&first.here)markSide('dept:'+first.here.dept);
        anchor.after(el('p','af-group','구글 드라이브'));
        const my=sideBtn('내 드라이브','solar:cloud-linear',()=>goDrive('','',[]),'drive:my');side.append(my);
        const pf=opts.projectFolder||{};
        const fid=pf.id||((String(pf.url||'').match(/folders\/([A-Za-z0-9_-]{10,128})/)||[])[1]);
        if(fid)side.append(sideBtn('프로젝트 폴더','solar:folder-cloud-linear',()=>goDrive('',fid,[],'프로젝트 폴더'),'drive:project'));
        else if(pf.url){const a=el('a','af-place');a.href=pf.url;a.target='_blank';a.rel='noopener';a.append(icon('solar:folder-cloud-linear'),el('span',null,'프로젝트 폴더 ↗'));side.append(a);}
        api('/api/finder/drive?accept='+opts.accept).then(j=>{drives=j.drives||[];drives.slice().reverse().forEach(x=>my.after(sideBtn(x.name,'solar:users-group-rounded-linear',()=>goDrive(x.id,'',[]),'drive:'+x.id)));}).catch(()=>{});
      });
    });
  }
  window.AuraFinder={open};
})();
