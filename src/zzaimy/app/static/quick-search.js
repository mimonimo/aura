/* 빠른 찾기 창 — Ctrl+K(⌘K) 또는 사이드바 「검색」. 이름·제목을 치는 대로 보여 주고, 탭으로 갈래를 고른다.
   ↑↓ 고르기, Enter 열기, Tab 갈래 바꾸기, Esc 닫기. 본문까지는 「모든 결과 보기」(/search). */
(function(){
  const TABS=[['all','전체'],['chats','대화'],['projects','프로젝트'],['docs','문서'],['hwp','한글 문서']];
  const ICON={chats:'solar:chat-round-line-linear',projects:'solar:folder-linear',docs:'solar:document-linear',hwp:'solar:document-text-linear'};
  const NAME={chats:'대화',projects:'프로젝트',docs:'문서',hwp:'한글 문서'};
  const RECENT='auraQuickRecent';
  const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e;};
  const ic=n=>{const i=document.createElement('iconify-icon');i.setAttribute('icon',n);i.setAttribute('aria-hidden','true');return i;};
  const href=(k,it)=>k==='chats'?'/chat/'+it.id:k==='projects'?'/project/'+it.id:k==='docs'?'/doc/'+it.id:'/webhwpctrl/aura-edit?id='+it.id;
  const recent=()=>{try{return JSON.parse(localStorage.getItem(RECENT)||'[]');}catch(_){return [];}};
  const remember=x=>{try{const r=recent().filter(y=>y.url!==x.url);r.unshift(x);localStorage.setItem(RECENT,JSON.stringify(r.slice(0,8)));}catch(_){}};
  let d=null;
  function open(){
    if(d){d.querySelector('input').focus();return;}
    d=el('dialog','qs');d.setAttribute('aria-label','빠른 찾기');
    d.innerHTML='<div class="qs-top"><iconify-icon icon="solar:magnifer-linear" aria-hidden="true"></iconify-icon><input type="search" placeholder="대화·프로젝트·문서 찾기" aria-label="찾을 말" autocomplete="off"></div><div class="qs-tabs" role="tablist"></div><div class="qs-list" role="listbox"></div><div class="qs-foot"><a class="qs-all" href="/search">본문까지 모든 결과 보기</a></div>';
    document.body.append(d);d.showModal();
    const input=d.querySelector('input'),tabs=d.querySelector('.qs-tabs'),list=d.querySelector('.qs-list'),all=d.querySelector('.qs-all');
    let tab='all',data=null,cur=-1,seq=0,timer=0;
    TABS.forEach(([k,l])=>{const b=el('button','qs-tab',l);b.type='button';b.dataset.k=k;b.setAttribute('role','tab');b.onclick=()=>{tab=k;paint();input.focus();};tabs.append(b);});
    const items=()=>[...list.querySelectorAll('.qs-row')];
    const mark=i=>{const r=items();cur=Math.max(-1,Math.min(r.length-1,i));r.forEach((x,j)=>x.classList.toggle('on',j===cur));if(r[cur])r[cur].scrollIntoView({block:'nearest'});};
    function row(k,it,title,meta){
      const a=el('a','qs-row');a.href=href(k,it);a.setAttribute('role','option');
      a.append(ic(ICON[k]),el('span','qs-name',title),el('span','qs-meta',meta||''));
      a.onclick=()=>remember({k,id:it.id,title,meta,url:a.href});
      a.onmousemove=()=>mark(items().indexOf(a));
      list.append(a);
    }
    function paint(){
      tabs.querySelectorAll('.qs-tab').forEach(b=>{const k=b.dataset.k,n=data&&k!=='all'?(data[k]||[]).length:0;b.classList.toggle('on',k===tab);b.setAttribute('aria-selected',k===tab);
        b.textContent=TABS.find(t=>t[0]===k)[1]+(n?' '+n:'');});
      list.replaceChildren();
      const term=input.value.trim();all.href='/search'+(term?'?q='+encodeURIComponent(term):'');
      if(!data){list.append(el('p','qs-empty',term?'찾는 중…':'불러오는 중…'));return;}
      if(!term&&tab==='all'){const r=recent().slice(0,4);if(r.length){list.append(el('p','qs-sect','최근 연 것'));r.forEach(x=>row(x.k,{id:x.id},x.title,x.meta));}}
      const ks=tab==='all'?['chats','projects','docs','hwp']:[tab];let n=0;
      ks.forEach(k=>{const xs=data[k]||[];if(!xs.length)return;if(tab==='all')list.append(el('p','qs-sect',(term?'':'최근 ')+NAME[k]));
        xs.forEach(it=>{n++;row(k,it,k==='projects'?it.name:k==='chats'?it.title:String(it.name).replace(/\.hwpx?$/i,''),k==='chats'?(it.project||it.at):k==='hwp'?it.where:it.at);});});
      if(!n)list.append(el('p','qs-empty',term?'이름·제목에서 찾지 못했습니다. 본문까지 찾으려면 Enter':'아직 없습니다'));
      mark(0);
    }
    async function load(){const term=input.value.trim(),my=++seq;
      try{const r=await fetch('/api/quick-search?q='+encodeURIComponent(term),{headers:{Accept:'application/json'}});const j=await r.json();if(my!==seq)return;data=j;}catch(_){if(my!==seq)return;data={chats:[],projects:[],docs:[],hwp:[]};}
      paint();}
    input.oninput=()=>{clearTimeout(timer);timer=setTimeout(load,120);};
    input.onkeydown=e=>{
      if(e.key==='ArrowDown'){e.preventDefault();mark(cur+1);}
      else if(e.key==='ArrowUp'){e.preventDefault();mark(cur-1);}
      else if(e.key==='Tab'){e.preventDefault();const i=TABS.findIndex(t=>t[0]===tab);tab=TABS[(i+(e.shiftKey?TABS.length-1:1))%TABS.length][0];paint();}
      else if(e.key==='Enter'){e.preventDefault();const r=items()[cur];if(r){r.click();location.href=r.href;}else if(input.value.trim())location.href=all.href;}
    };
    d.addEventListener('click',e=>{if(e.target===d)d.close();});
    d.addEventListener('close',()=>{d.remove();d=null;});
    paint();load();input.focus();
  }
  document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&!e.altKey&&e.key.toLowerCase()==='k'){e.preventDefault();open();}});
  document.addEventListener('click',e=>{const t=e.target.closest('[data-quick-search]');if(t){e.preventDefault();open();}});
  window.AuraQuickSearch={open};
})();
