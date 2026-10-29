/* 공용 디렉터리 창(맥 파인더 꼴) — 부서 폴더·구글 드라이브를 한 창에서 고른다. 열기와 저장이 같은 창을 쓴다.
   AuraFinder.open({title, accept:'hwp'|'docs', multiple, openLabel, projectFolder:{id,url}, start:'drive'|'recent', at:{dept,folder}, note, recent})
   recent: true 면 왼쪽 맨 위에 「최근 문서」(기안기로 최근 연 한글 문서, /api/finder/recent)
   → Promise: 취소 null, 고르면 [{source:'dept', id, name, path} | {source:'drive', drive_id, name, mime, path}]
   여러 개 고르기: 줄 앞 동그라미를 누르거나 ⌘/Ctrl·Shift 누르고 클릭. 위 검색은 쓸 수 있는 부서 폴더 전부 + 드라이브.
   AuraFinder.save({title:'다른 이름으로 저장', name:'기본 이름', accept:'hwp', saveLabel, at:{dept,folder}, note, driveNote, onSave})
   → Promise: 취소 null, 고르면 {target:'dept', dept, folder, name} | {target:'drive', drive, folder, name}
   name 은 확장자 없는 이름(아래 이름 칸 뒤에 .hwpx 가 붙어 보인다). 폴더만 고르고, 파일을 누르면 이름 칸에 그 이름이 들어간다.
   onSave(choice, {note}) 를 주면 창을 연 채 저장을 기다린다 — 실패하면 그 까닭을 창에 보이고 다시 고칠 수 있다.
   부서 폴더에서는 「새 폴더」 로 그 자리에 폴더를 만든다(문서함과 같은 /hwp/folder). */
(function(){
  const el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e;};
  const icon=name=>{const i=document.createElement('iconify-icon');i.setAttribute('icon',name);i.setAttribute('aria-hidden','true');return i;};
  async function api(url){const r=await fetch(url,{headers:{Accept:'application/json'},credentials:'same-origin',cache:'no-store'});
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

  function finder(opts,saving){
    return new Promise(resolve=>{
      const d=el('dialog','aura-finder'+(saving?' af-saving':''));d.setAttribute('aria-label',opts.title);
      const what=opts.accept==='docs'?'문서':'한글 파일';
      const foot=saving
        ?'<footer><label class="af-namebox"><span class="af-nm-label">이름</span><span class="af-nm"><input name="af-name" maxlength="80" autocomplete="off"><span class="af-ext"></span></span></label><button type="button" class="af-btn" data-cancel>취소</button><button type="button" class="af-btn primary" data-open></button><span class="af-msg" role="status" aria-live="polite"></span></footer>'
        :'<footer><div class="af-sel"><span class="af-sel-main"></span><span class="af-sel-where"></span></div><span class="af-msg" role="status"></span><button type="button" class="af-btn" data-cancel>취소</button><button type="button" class="af-btn primary" data-open disabled></button></footer>';
      d.innerHTML='<header><h2></h2>'+(saving?'<span class="af-grow"></span>':'<form class="af-search" role="search"><iconify-icon icon="solar:magnifer-linear" aria-hidden="true"></iconify-icon><input type="search" name="q"></form>')+'<button type="button" class="af-x" aria-label="닫기">×</button></header>'
        +'<div class="af-body"><nav class="af-side" aria-label="위치"></nav><section class="af-main"><div class="af-bar"><button type="button" class="af-back" aria-label="뒤로" disabled>‹</button><div class="af-path"></div>'
        +(saving?'<button type="button" class="af-newfolder" hidden><iconify-icon icon="solar:add-folder-linear" aria-hidden="true"></iconify-icon>새 폴더</button>':'')
        +'</div><div class="af-list" role="listbox"></div></section></div>'+foot;
      d.querySelector('h2').textContent=opts.title;
      const q=d.querySelector('.af-search input');
      if(q){q.placeholder='부서 폴더·드라이브에서 '+what+' 찾기';q.setAttribute('aria-label',q.placeholder);}
      const list=d.querySelector('.af-list');list.setAttribute('aria-multiselectable',opts.multiple?'true':'false');list.setAttribute('aria-label',saving?'저장할 폴더':'폴더와 파일');
      document.body.append(d);d.showModal();
      const side=d.querySelector('.af-side'),path=d.querySelector('.af-path'),back=d.querySelector('.af-back');
      const selMain=d.querySelector('.af-sel-main'),selWhere=d.querySelector('.af-sel-where'),msg=d.querySelector('.af-msg'),openBtn=d.querySelector('[data-open]');
      const nameIn=d.querySelector('[name=af-name]'),newBtn=d.querySelector('.af-newfolder');
      let done=false,busyNow=false;const finish=v=>{if(done)return;done=true;resolve(v);d.close();};
      const cancel=()=>{if(!busyNow)finish(null);};
      d.querySelector('.af-x').onclick=cancel;d.querySelector('[data-cancel]').onclick=cancel;
      d.addEventListener('cancel',e=>{if(busyNow)e.preventDefault();});
      d.addEventListener('close',()=>{if(!done){done=true;resolve(null);}d.remove();});
      d.addEventListener('click',e=>{if(e.target===d)cancel();});
      const say=(t,bad)=>{msg.textContent=t||'';msg.classList.toggle('bad',!!bad);};
      let loc=null,hist=[],drives=[],picked=new Map(),lastIdx=-1,where='',locOk=false;
      const key=it=>it.source==='drive'?'g:'+it.drive_id:'d:'+it.id;
      function paintSel(){
        if(saving){openBtn.disabled=busyNow||!locOk;return;}
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
      const sideBtn=(label,ic,go,k)=>{const b=el('button','af-place');b.type='button';b.dataset.key=k;b.append(icon(ic),el('span',null,label));b.title=label;b.onclick=()=>{hist=[];go();};return b;};
      const markSide=k=>side.querySelectorAll('.af-place').forEach(b=>b.classList.toggle('on',b.dataset.key===k));
      function folderRow(name,meta,go){const r=el('button','af-row');r.type='button';r.dataset.kind='folder';r.append(el('span','af-check'),icon('solar:folder-bold'),el('span','af-name',name),el('span','af-meta',meta||''));r.onclick=()=>{hist.push(loc);go();};list.append(r);}
      function fileRow(item,meta){
        if(saving){                                  // 저장 창 — 파일은 고르지 않고 그 이름만 이름 칸에
          const r=el('button','af-row af-dim');r.type='button';r.dataset.kind='name';r.title='이 이름 쓰기';
          r.append(el('span','af-check'),icon(fileIcon(item.name,item.mime)),el('span','af-name',stem(item.name)),el('span','af-meta',meta||''));
          r.onclick=()=>{nameIn.value=stem(item.name);nameIn.focus();nameIn.select();};
          list.append(r);return;
        }
        const r=el('div','af-row');r.dataset.kind='file';r.setAttribute('role','option');r.tabIndex=0;r._item=item;
        const ck=el('button','af-check');ck.type='button';ck.setAttribute('aria-label','고르기');if(!opts.multiple){ck.style.visibility='hidden';ck.tabIndex=-1;}/* 한 개 고르기 — 자리는 남겨 칸이 밀리지 않게 */
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
      const nowLoc=l=>{loc=l;back.disabled=!hist.length;locOk=false;if(newBtn)newBtn.hidden=l.src!=='dept';
        if(saving)say(l.src==='drive'?(opts.driveNote||opts.note||''):(opts.note||''));paintSel();};
      function showDept(j){
        if(!d.open)return;const here=j&&j.here;if(!here){busy('쓸 수 있는 부서 폴더가 없습니다');if(newBtn)newBtn.hidden=true;return;}
        loc.dept=here.dept;loc.folder=here.folder||'';markSide('dept:'+here.dept);
        if(newBtn)newBtn.hidden=here.may_make===false;      // 새 폴더는 쓸 수 있는 곳만(공통·양식 자리는 숨김, 서버도 거절)
        crumbs([{name:here.label||here.dept,go:()=>goDept(here.dept,'')},...(here.crumbs||[]).map(c=>({name:c.name,go:()=>goDept(here.dept,c.path)}))]);
        list.replaceChildren();const at=where;
        here.folders.forEach(f=>folderRow(f.name,'문서 '+f.count,()=>goDept(here.dept,f.path)));
        here.files.forEach(f=>fileRow({source:'dept',id:f.id,name:f.name,path:at},[f.saved,f.saved_by||f.owner].filter(Boolean).join(' · ')));
        if(!here.folders.length&&!here.files.length)busy(saving?'빈 폴더 · 여기에 저장합니다':'이 폴더는 비어 있습니다');
        locOk=true;paintSel();
      }
      async function goDept(dept,folder,pre){
        nowLoc({src:'dept',dept,folder});markSide('dept:'+dept);
        let j=pre;
        if(!j){busy('불러오는 중…');try{j=await api('/api/hwp/browse?dept='+encodeURIComponent(dept||'')+'&folder='+encodeURIComponent(folder||''));}catch(e){busy(e.message);return null;}}
        showDept(j);return j;
      }
      async function goDrive(drive,folder,trail,rootName){
        nowLoc({src:'drive',drive,folder,trail,rootName});markSide(rootName==='프로젝트 폴더'?'drive:project':'drive:'+(drive||'my'));busy('불러오는 중…');
        const root=rootName||(drive?(drives.find(x=>x.id===drive)||{name:'공유 드라이브'}).name:'내 드라이브');
        crumbs([{name:root,go:()=>goDrive(drive,trail.length&&rootName?folder:'',[],rootName)},...trail.map((t,i)=>({name:t.name,go:()=>goDrive(drive,t.id,trail.slice(0,i+1),rootName)}))]);
        if(rootName&&!trail.length)crumbs([{name:root}]);
        const at=where;
        let j;try{j=await api('/api/finder/drive?accept='+opts.accept+'&drive='+encodeURIComponent(drive||'')+'&folder='+encodeURIComponent(folder||''));}
        catch(e){busy(e.status===409?(saving?'드라이브에 저장하려면 학교 구글 계정 연결 · 설정 › 구글 계정':'드라이브 파일을 고르려면 학교 구글 계정 연결 · 설정 › 구글 계정'):e.message);return;}
        if(!d.open)return;list.replaceChildren();
        j.folders.forEach(f=>folderRow(f.name,'폴더',()=>goDrive(drive,f.id,[...trail,f],rootName)));
        j.files.forEach(f=>fileRow({source:'drive',drive_id:f.id,name:f.name,mime:f.mimeType,path:at},[kindOf(f.name,f.mimeType),(f.modifiedTime||'').slice(0,10)].filter(Boolean).join(' · ')));
        if(!j.folders.length&&!j.files.length)busy(saving?'빈 폴더 · 여기에 저장합니다':'이 폴더에 '+what+'이 없습니다');
        if(j.drives&&!drives.length)addDrives(j.drives);
        locOk=true;paintSel();
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
      // 프로젝트(저장 창, opts.projects) — 고르면 그 프로젝트의 작업 문서로(서버가 곳을 정한다)
      function goProject(p){nowLoc({src:'project',id:p.id,name:p.name});markSide('project:'+p.id);crumbs([{name:p.name}]);
        busy('프로젝트 「'+p.name+'」 의 작업 문서로 넣습니다');locOk=true;paintSel();}
      async function goRecent(){
        nowLoc({src:'recent'});markSide('recent');crumbs([{name:'최근 문서'}]);busy('불러오는 중…');
        let j;try{j=await api('/api/finder/recent');}catch(e){busy(e.message);return;}
        if(!d.open)return;list.replaceChildren();
        (j.files||[]).forEach(f=>{const at=[f.label||f.dept,...(f.folder?f.folder.split('/'):[])].join(' › ');fileRow({source:'dept',id:f.id,name:f.name,path:at},[at,f.saved].filter(Boolean).join(' · '));});
        if(!(j.files||[]).length)busy('최근 연 문서가 없습니다');
      }
      const goLoc=l=>l.src==='dept'?goDept(l.dept,l.folder):l.src==='drive'?goDrive(l.drive,l.folder,l.trail,l.rootName):l.src==='project'?goProject(l):l.src==='recent'?goRecent():search(l.text);
      back.onclick=()=>{const l=hist.pop();if(l)goLoc(l);};
      if(q){
        let timer=0;
        d.querySelector('.af-search').onsubmit=e=>{e.preventDefault();clearTimeout(timer);const t=q.value.trim();if(t){hist.push(loc);search(t);}};
        q.oninput=()=>{clearTimeout(timer);const t=q.value.trim();if(t.length<2)return;timer=setTimeout(()=>{if(loc&&loc.src!=='search')hist.push(loc);search(t);},350);};
      }
      // 새 폴더(부서 폴더만) — 목록 맨 위에 이름 칸을 열고 Enter 로 만든다. 문서함의 새 폴더와 같은 경로(/hwp/folder)
      if(newBtn)newBtn.onclick=()=>{
        if(!loc||loc.src!=='dept'||!locOk)return;
        const open_=list.querySelector('.af-newrow input');if(open_){open_.focus();return;}
        list.querySelectorAll('.af-empty').forEach(x=>x.remove());
        const r=el('form','af-row af-newrow');r.dataset.kind='folder';
        const inp=el('input');inp.maxLength=60;inp.placeholder='새 폴더 이름';inp.setAttribute('aria-label','새 폴더 이름');inp.autocomplete='off';
        const have=[...list.querySelectorAll('.af-row[data-kind=folder] .af-name')].map(x=>x.textContent);let nm='새 폴더',k=2;while(have.includes(nm))nm='새 폴더 '+(k++);inp.value=nm;
        r.append(el('span','af-check'),icon('solar:folder-bold'),inp,el('span','af-meta','Enter 로 만들기'));list.prepend(r);inp.focus();inp.select();
        inp.addEventListener('blur',()=>setTimeout(()=>{if(r.isConnected&&!inp.disabled&&inp.value.trim())r.requestSubmit();},0));
        const at=loc;
        inp.onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();r.remove();}};
        r.onsubmit=async e=>{
          e.preventDefault();const nm=inp.value.trim();if(!nm){r.remove();return;}
          inp.disabled=true;say('폴더를 만드는 중');
          try{
            const res=await fetch('/hwp/folder',{method:'POST',credentials:'same-origin',body:new URLSearchParams({dept:at.dept,folder:at.folder||'',name:nm})});
            if(!res.ok){let j=null;try{j=await res.json();}catch(_){}throw new Error((j&&j.detail)||'폴더를 만들지 못했습니다');}
            const u=new URL(res.url,location.href),err=u.searchParams.get('err');
            if(err)throw new Error(err);
            say('');hist.push(at);goDept(at.dept,u.searchParams.get('folder')||at.folder||'');
          }catch(x){inp.disabled=false;inp.focus();say(x.message,true);}
        };
      };
      function submit(){
        if(saving){
          if(busyNow||!locOk||!loc||(loc.src!=='dept'&&loc.src!=='drive'&&loc.src!=='project'))return;
          const nm=stem(nameIn.value.trim());
          if(!nm){say('이름을 적어 주세요',true);nameIn.focus();return;}
          const choice=loc.src==='project'?{target:'project',project_id:loc.id,project:loc.name,name:nm}:loc.src==='dept'?{target:'dept',dept:loc.dept,folder:loc.folder||'',name:nm}:{target:'drive',drive:loc.drive||'',folder:loc.folder||'',name:nm};
          if(typeof opts.onSave!=='function'){finish(choice);return;}
          busyNow=true;paintSel();d.classList.add('is-busy');say(choice.target==='drive'?'드라이브에 올리는 중':'저장하는 중');
          Promise.resolve().then(()=>opts.onSave(choice,{note:t=>say(t)}))
            .then(()=>{busyNow=false;finish(choice);})
            .catch(e=>{busyNow=false;d.classList.remove('is-busy');paintSel();say((e&&e.message)||'저장하지 못했습니다',true);});
          return;
        }
        if(!picked.size)return;finish([...picked.values()].map(({source,id,drive_id,name,mime,path})=>source==='drive'?{source,drive_id,name,mime,path}:{source,id,name,path}));
      }
      openBtn.onclick=submit;
      if(saving){
        openBtn.textContent=opts.saveLabel||'저장';
        d.querySelector('.af-ext').textContent=opts.accept==='docs'?'':'.hwpx';
        nameIn.value=stem(opts.name||'');nameIn.setAttribute('aria-label','이름');
        nameIn.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();submit();}};
        setTimeout(()=>{nameIn.focus();nameIn.select();},30);
      }
      paintSel();
      if(!saving&&opts.note)say(opts.note);
      // 왼쪽 — 부서 폴더(쓸 수 있는 곳), 구글 드라이브(내 드라이브·공유 드라이브·이 프로젝트 폴더)
      if(opts.recent&&!saving)side.append(sideBtn('최근 문서','solar:clock-circle-linear',()=>goRecent(),'recent'));
      side.append(el('p','af-group','부서 폴더'));
      const anchor=el('span');side.append(anchor);
      let my=null,drivesAdded=false;
      function addDrives(list_){if(drivesAdded||!my)return;drivesAdded=true;drives=list_||[];drives.slice().reverse().forEach(x=>my.after(sideBtn(x.name,'solar:users-group-rounded-linear',()=>goDrive(x.id,'',[]),'drive:'+x.id)));if(loc&&loc.src==='drive')markSide(loc.rootName==='프로젝트 폴더'?'drive:project':'drive:'+(loc.drive||'my'));}
      const at0=opts.at||{};
      const toRecent=opts.recent&&!saving&&opts.start==='recent',toDrive=opts.start==='drive'||toRecent;   // 최근 문서로 시작해도 부서 폴더 첫 화면은 그리지 않는다
      if(toRecent)goRecent();else if(opts.start==='drive')goDrive('','',[]);else{nowLoc({src:'dept',dept:at0.dept||'',folder:at0.folder||''});busy('불러오는 중…');}
      api('/api/hwp/browse?dept='+encodeURIComponent(at0.dept||'')+'&folder='+encodeURIComponent(at0.folder||'')).catch(e=>{if(!toDrive)busy(e.message);return null;}).then(first=>{
        if(!d.open)return;
        ((first&&first.depts)||[]).forEach(x=>anchor.before(sideBtn(x.label||x.dept,x.dept.startsWith('~')?'solar:user-rounded-linear':'solar:folder-with-files-linear',()=>goDept(x.dept,''),'dept:'+x.dept)));
        if(saving&&opts.projects)api('/api/hwp/projects').then(j=>{const ps=(j&&j.projects)||[];if(!ps.length||!d.open)return;
          anchor.before(el('p','af-group','프로젝트'));ps.forEach(p=>anchor.before(sideBtn(p.name,'solar:case-round-linear',()=>goProject(p),'project:'+p.id)));}).catch(()=>{});
        anchor.after(el('p','af-group','구글 드라이브'));
        my=sideBtn('내 드라이브','solar:cloud-linear',()=>goDrive('','',[]),'drive:my');side.append(my);
        const pf=opts.projectFolder||{};
        const fid=pf.id||((String(pf.url||'').match(/folders\/([A-Za-z0-9_-]{10,128})/)||[])[1]);
        if(fid)side.append(sideBtn('프로젝트 폴더','solar:folder-cloud-linear',()=>goDrive('',fid,[],'프로젝트 폴더'),'drive:project'));
        else if(pf.url){const a=el('a','af-place');a.href=pf.url;a.target='_blank';a.rel='noopener';a.append(icon('solar:folder-cloud-linear'),el('span',null,'프로젝트 폴더 ↗'));side.append(a);}
        if(toRecent)markSide('recent');else if(toDrive)markSide('drive:'+(loc&&loc.drive||'my'));
        else if(first)goDept(at0.dept||'',at0.folder||'',first);
        api('/api/finder/drive?accept='+opts.accept).then(j=>addDrives(j.drives||[])).catch(()=>{});
      });
    });
  }
  function open(opts){return finder(Object.assign({title:'파일 가져오기',accept:'hwp',multiple:false,openLabel:'열기'},opts||{}),false);}
  function save(opts){return finder(Object.assign({title:'다른 이름으로 저장',accept:'hwp',name:'',saveLabel:'저장'},opts||{},{multiple:false}),true);}
  window.AuraFinder={open,save};
})();
