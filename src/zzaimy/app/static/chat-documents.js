(() => {
 const workspace=document.getElementById('chatWorkspace'), opener=document.getElementById('chatDocumentOpen');
 if(!workspace||!opener)return;
 let sid=workspace.dataset.session;
 const main=workspace.parentElement;
 let linked=null, panel=null;
 workspace.addEventListener('click',async event=>{
   const anchor=event.target.closest('a[data-document-link]');
   if(!anchor||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
   const url=new URL(anchor.href);
   if(url.protocol!=='https:'||url.username||url.password||url.port)return;
   const isDoc=url.hostname==='docs.google.com';
   if(!isDoc&&url.hostname!=='drive.google.com')return;
   const match=url.pathname.match(isDoc? /^\/document\/d\/([A-Za-z0-9_-]+)(?:\/|$)/ : /^\/file\/d\/([A-Za-z0-9_-]+)(?:\/|$)/);
   if(!match)return;
   event.preventDefault();
   // 답 속 [문서 n] 이 이 대화의 작업 문서면 읽기 전용 미리보기가 아니라 편집기로 연다.
   // 연결 정보가 아직 오는 중일 수 있어 기다린 뒤 판단한다.
   const session=sid;
   if(isDoc){await initialized.catch(()=>{});if(session!==sid)return;}
   if(isDoc&&linked?.doc===match[1]){
     const heading=new URLSearchParams(url.hash.slice(1)).get('heading');
     const section=heading&&(linked.sections||[]).find(s=>s.heading_id===heading);
     if(section)jumpToSection(section);else show();
     return;
   }
   showViewer({title:anchor.textContent+' 미리보기',url:url.href,
     embed_url:url.origin+(isDoc?'/document/d/':'/file/d/')+match[1]+'/preview',is_doc:false});
 });
 // 편집기 iframe 을 다시 불러온다. 교차 출처 iframe 은 # 만 바꾸면 이동하지 않을 때가 있어(구글 쪽 주소가
 // 이미 바뀌어 있으면 조각 이동으로 끝난다) 매번 다른 zzjump 값을 붙여 새로 연다. 독스 주소 꼴은
 // …/edit?tab=<탭>#heading=<제목 id> 이다. 탭은 질의에, 제목은 # 에 넣어야 독스가 알아듣는다.
 function navigateEditor(section){
   const frame=panel?.querySelector('iframe');if(!frame||!linked)return;
   const url=new URL(frame.src||linked.embed_url,location.href);
   url.searchParams.set('zzjump',String(Date.now()));
   if(section){
     if(section.tab_id)url.searchParams.set('tab',section.tab_id);else url.searchParams.delete('tab');
     url.hash='heading='+section.heading_id;
   }
   loadFrame(frame,url.href,section?`「${section.heading}」 위치로 이동하는 중…`:'문서를 새로 불러오는 중…',{quick:Boolean(section)});
 }
 // iframe 을 (다시) 불러오는 동안 안내를 띄운다. 구글 편집기는 교차 출처라 다 그렸는지 알 수 없고, 첫 load(약 3초) 뒤에도
 // 편집기 스크립트가 글을 그리느라 10초 안팎 비어 있다. 그래서 첫 load 로 걷지 않고 다음 중 먼저 오는 때에 걷는다:
 //   ① 두 번째 load(구글이 편집기 문서를 한 번 더 불러오는 경우) + 0.6초, ② 첫 load 로부터 10초,
 //   ③ 시작으로부터 30초(어떤 경우에도 안내가 남아 있지 않게), ④ 담당자가 안내를 누를 때.
 // 걷을 때는 흐리게 사라진다. 불러오기마다 번호를 매겨 앞선 불러오기의 load·타이머가 새 안내를 건드리지 않게 한다.
 // 시간 값은 시험에서만 window.chatDocLoadTiming 으로 줄인다.
 // 안내는 편집기를 가리지 않는 작은 알림이다(위에 떠 있고 아래 문서는 그대로 보이고 스크롤된다). 위치 이동(quick)도 편집기가
 // 그 위치를 그리기까지 첫 load 뒤 5~7초 비어 있어(실측) 그동안 이동 문구를 두고(jump) 걷는다.
 const LOAD_TIMING=Object.assign({settle:10000,cap:30000,slow:15000,jump:6000},window.chatDocLoadTiming||{});
 let loadSeq=0;
 function loadFrame(frame,url,text,{quick=false}={}){
   const viewport=frame.parentElement;if(!viewport)return;
   let note=[...viewport.children].find(el=>el.classList.contains('chat-doc-loading'));
   if(!note){
     note=document.createElement('div');note.className='chat-doc-loading';note.setAttribute('role','status');
     note.title='눌러서 안내 닫기';
     const dot=document.createElement('span');dot.className='chat-doc-busy-dot';dot.setAttribute('aria-hidden','true');
     note.append(dot,document.createElement('span'));viewport.append(note);
     note.addEventListener('click',()=>note.finish?.());
   }
   note.cleanup?.();
   const seq=String(++loadSeq);frame.dataset.loadSeq=seq;
   note.lastElementChild.textContent=text;note.hidden=false;note.classList.remove('chat-doc-leaving');
   viewport.setAttribute('aria-busy','true');
   let loads=0;const timers=[];
   const later=(fn,ms)=>timers.push(setTimeout(fn,ms));
   const current=()=>frame.dataset.loadSeq===seq;
   const cleanup=()=>{timers.forEach(clearTimeout);frame.removeEventListener('load',onLoad);if(note.cleanup===cleanup){note.cleanup=null;note.finish=null;}};
   const finish=()=>{
     cleanup();if(!current())return;
     viewport.removeAttribute('aria-busy');note.classList.add('chat-doc-leaving');
     setTimeout(()=>{if(current()&&note.classList.contains('chat-doc-leaving'))note.hidden=true;},300);
   };
   function onLoad(){
     if(!current()){cleanup();return;}
     loads+=1;
     if(loads>=2){later(finish,600);return;}
     if(quick){later(finish,LOAD_TIMING.jump);return;}
     note.lastElementChild.textContent='편집기를 그리는 중…';
     later(finish,LOAD_TIMING.settle);
   }
   note.cleanup=cleanup;note.finish=finish;
   later(()=>{if(current()&&!note.hidden)note.lastElementChild.textContent=text+' 구글 문서가 크면 1분 가까이 걸립니다.';},LOAD_TIMING.slow);
   later(finish,LOAD_TIMING.cap);
   frame.addEventListener('load',onLoad);
   frame.src=url;
 }
 function isEditor(){return Boolean(panel&&panel.querySelector('[data-toolbar]')&&!panel.classList.contains('chat-doc-viewer'));}
 function jumpToSection(section){
   if(!section?.heading_id||!linked)return;
   show();visibility(true);
   navigateEditor(section);
   const chapter=panel.querySelector('[data-section-chapter]');if(chapter?._show)chapter._show(section.index);
   const select=panel.querySelector('[data-section-jump]');if(select)select.value=[...select.options].some(o=>o.value===String(section.index))?String(section.index):'';
 }
 function addLocationButtons(){
   if(!linked)return;
   const normalize=value=>value.replace(/\s+/g,' ').trim();
   workspace.querySelectorAll('[data-section-location]').forEach(button=>{if(button.dataset.locationDoc!==String(linked.doc||''))button.remove();});
   workspace.querySelectorAll('.chat-locations').forEach(row=>{if(!row.querySelector('[data-section-location]'))row.remove();});
   workspace.querySelectorAll('.turn.assistant .msg.assistant').forEach(message=>{
     const text=normalize(message.textContent);
     (linked.sections||[]).filter(s=>s.heading_id&&s.heading.length>=6&&text.includes(normalize(s.heading))).slice(0,3).forEach(s=>{
       if(message.querySelector(`[data-section-location="${s.index}"]`))return;
       const button=document.createElement('button');button.type='button';button.className='secondary chat-location-button';button.dataset.sectionLocation=s.index;
       const documentId=String(linked.doc||'');button.dataset.locationDoc=documentId;
       button.textContent=s.heading;button.title='문서에서 「'+s.heading+'」 위치 보기';button.onclick=()=>{if(String(linked?.doc||'')===documentId)jumpToSection(s);};
       let row=message.querySelector(':scope > .chat-locations');
       if(!row){row=document.createElement('div');row.className='chat-locations';const label=document.createElement('span');label.textContent='문서에서 보기';row.append(label);message.append(row);}
       row.append(button);
     });
   });
 }
 let locationRefresh;
 new MutationObserver(()=>{clearTimeout(locationRefresh);locationRefresh=setTimeout(addLocationButtons,100);}).observe(workspace,{childList:true,subtree:true});
 let fitObserver=null;
 let cancelFormatTransition=null;
 function clearPanel(){main.classList.remove('chat-doc-full');docCursorOff();finishClose();cancelFormatTransition?.();fitObserver?.disconnect();fitObserver=null;panel?.remove();panel=null;}
 function switchFormatting(event){
   cancelFormatTransition?.();
   const control=event.target, frame=panel.querySelector('iframe');
   const next=new URL(control.checked?(linked.embed_url_toolbar||linked.embed_url):linked.embed_url,location.href);
   // Preserve an explicit navigation anchor. Cross-origin editor scroll is not readable.
   const previous=new URL(frame.src);
   if(previous.hash)next.hash=previous.hash;
   if(previous.searchParams.get('tab'))next.searchParams.set('tab',previous.searchParams.get('tab'));
   if(frame.src===next.href)return;
   control.disabled=true;
   let timer;
   const done=()=>{clearTimeout(timer);frame.removeEventListener('load',done);control.disabled=false;cancelFormatTransition=null;};
   cancelFormatTransition=done;
   frame.addEventListener('load',done);
   timer=setTimeout(()=>{control.disabled=false;},15000);
   loadFrame(frame,next.href,control.checked?'구글 서식 도구를 여는 중…':'편집 화면으로 돌아가는 중…');
 }
 function fitEditor(autoFit=true){
   const frame=panel.querySelector('iframe');if(!frame||frame.parentElement.classList.contains('chat-doc-viewport'))return;
   const viewport=document.createElement('div');viewport.className='chat-doc-viewport';
   frame.before(viewport);viewport.append(frame);
   const toolbar=panel.querySelector('[data-toolbar]');
   const resize=()=>{
     const width=viewport.clientWidth,height=viewport.clientHeight;if(!width||!height)return;
     const nativeTools=Boolean(toolbar?.checked);
     // Full Docs chrome includes its document-tabs sidebar. Keep it in the
     // fitted viewport instead of forcing 100% when formatting is enabled.
     const scale=autoFit?Math.min(1,width/(nativeTools?1440:1100)):1;
     frame.style.cssText=`position:absolute;left:0;top:0;width:${width/scale}px;height:${height/scale}px;transform:scale(${scale});transform-origin:top left;max-width:none;`;
   };
   toolbar?.addEventListener('change',resize);
   fitObserver=new ResizeObserver(resize);fitObserver.observe(viewport);
 }
 const divider=document.createElement('div');
 divider.className='chat-doc-divider';divider.tabIndex=0;
 divider.setAttribute('role','separator');divider.setAttribute('aria-orientation','vertical');
 divider.setAttribute('aria-label','채팅과 문서 너비 조절');
 divider.setAttribute('aria-controls','chatWorkspace chatDocumentPanel');
 divider.title='드래그로 너비 조절 · 두 번 클릭하면 반반';
 // 폭은 보기마다 따로 — 문서를 열어 볼 때(편집기·열람)는 넓게(기본 반반), 문서 목록만 볼 때는 좁게(기본 대화 70% · 패널 30%).
 // 끌어 둔 폭도 보기마다 따로 기억한다.
 const LIST_DEFAULT=70, DOC_DEFAULT=42;     // 문서를 볼 때는 문서 칸을 조금 넓게(대화 42% · 문서 58%)
 let ratio=Number(sessionStorage.getItem('chatDocRatio:'+sid))||DOC_DEFAULT;
 let listRatio=Number(sessionStorage.getItem('chatDocListRatio'))||LIST_DEFAULT;
 const listMode=()=>Boolean(panel&&panel.classList.contains('chat-file-library'));
 const currentRatio=()=>listMode()?listRatio:ratio;
 function setRatio(value,persist=true){
   const available=Math.max(main.clientWidth-8,1), minimum=Math.min(45,Math.max(20,260/available*100));
   const next=Math.max(minimum,Math.min(100-minimum,value));
   if(listMode()){listRatio=next;if(persist)sessionStorage.setItem('chatDocListRatio',String(next));}
   else{ratio=next;if(persist)sessionStorage.setItem('chatDocRatio:'+sid,String(next));}
   const shown=next;
   main.style.setProperty('--chat-width',shown+'%');
   divider.setAttribute('aria-valuemin',String(Math.ceil(minimum)));
   divider.setAttribute('aria-valuemax',String(Math.floor(100-minimum)));
   divider.setAttribute('aria-valuenow',String(Math.round(shown)));
   divider.setAttribute('aria-valuetext','채팅 '+Math.round(shown)+'%, 문서 '+Math.round(100-shown)+'%');
 }
 divider.addEventListener('pointerdown',event=>{
   if(event.button!==0)return;
   event.preventDefault();divider.focus();divider.setPointerCapture(event.pointerId);
   main.classList.add('chat-doc-resizing');
 });
 divider.addEventListener('pointermove',event=>{
   if(!divider.hasPointerCapture(event.pointerId))return;
   const bounds=main.getBoundingClientRect();setRatio((event.clientX-bounds.left-4)/(bounds.width-8)*100);
 });
 const stopResize=()=>main.classList.remove('chat-doc-resizing');
 ['pointerup','pointercancel','lostpointercapture'].forEach(name=>divider.addEventListener(name,stopResize));
 divider.addEventListener('dblclick',()=>setRatio(listMode()?LIST_DEFAULT:DOC_DEFAULT));
 divider.addEventListener('keydown',event=>{
   if(!['ArrowLeft','ArrowRight','Home','End','Enter'].includes(event.key))return;
   event.preventDefault();setRatio(event.key==='Enter'?(listMode()?LIST_DEFAULT:DOC_DEFAULT):event.key==='Home'?0:event.key==='End'?100:currentRatio()+(event.key==='ArrowLeft'?-2:2));
 });
 new ResizeObserver(()=>{if(main.classList.contains('chat-doc-open'))setRatio(currentRatio(),false);}).observe(main);
 // 여닫기는 CSS 의 대화 칸 폭 전환으로 부드럽게 — 닫을 때는 전환이 끝난 뒤에 패널을 숨긴다.
 let closing=0;
 function motionMs(){
   const d=getComputedStyle(workspace).transitionDuration.split(',').map(v=>parseFloat(v)*(v.trim().endsWith('ms')?1:1000)||0);
   return Math.max(0,...d);
 }
 function finishClose(){
   if(!closing)return;
   clearTimeout(closing);closing=0;
   main.classList.remove('chat-doc-open','chat-doc-closing');
   if(panel)panel.hidden=true;
 }
 function reveal(){
   if(closing){clearTimeout(closing);closing=0;}
   main.classList.remove('chat-doc-closing');
   setRatio(currentRatio(),false);
   panel.hidden=false;main.classList.add('chat-doc-open');
 }
 function conceal(){
   if(!panel||panel.hidden||closing)return;
   const ms=motionMs();
   main.classList.add('chat-doc-closing');
   closing=setTimeout(finishClose,ms+40);
   if(!ms)finishClose();
 }
 function visibility(visible){
   if(!panel)return;
   if(visible){
     const close=panel.querySelector('header [data-hide],header [data-close]');
     if(close){close.textContent='×';close.title='문서 패널 닫기';close.setAttribute('aria-label','문서 패널 닫기');close.classList.add('doc-icon-button');}
   }
   if(visible)reveal();else{toggleFull(false);conceal();}
   if(!visible)stopResize();
   opener.setAttribute('aria-expanded',String(visible));opener.hidden=visible;
   opener.title='문서 패널 열기';opener.setAttribute('aria-label',opener.title);
   sessionStorage.setItem('chatDocHidden:'+sid,visible?'0':'1');
 }
 async function api(url,options={}){const response=await fetch(url,{cache:'no-store',...options});if(!response.ok||response.redirected){let message='요청을 처리하지 못했습니다.';try{const data=await response.json();if(typeof data.detail==='string')message=data.detail;}catch(_){}const err=new Error(message);err.status=response.status;throw err;}return response.json();}
 function dialog(title){const d=document.createElement('dialog');d.className='chat-doc-dialog';d.setAttribute('aria-label',title);d.innerHTML='<h2></h2><form><div data-fields></div><p role="status"></p><footer><button type="button" class="secondary" data-cancel>취소</button><button type="submit" data-submit>연결</button></footer></form>';d.querySelector('h2').textContent=title;d.querySelector('[data-fields]').style.display='grid';d.querySelector('[data-fields]').style.gap='16px';d.querySelector('[data-cancel]').onclick=()=>d.close();d.addEventListener('close',()=>{d.remove();opener.focus();});document.body.append(d);d.showModal();return d;}
 async function connect(){const d=dialog('드라이브에서 문서 가져오기'), f=d.querySelector('form'), fields=d.querySelector('[data-fields]'), status=d.querySelector('[role=status]'), submit=d.querySelector('[data-submit]');submit.disabled=true;submit.textContent='선택한 문서 가져오기';status.textContent='연결 계정 확인 중…';
 try{const data=await api('/api/chat-documents/accounts');if(!d.isConnected)return;
 fields.innerHTML='<label>구글 계정<select name="account" required></select></label><input type="hidden" name="doc"><div class="drive-picker-path"></div><div class="drive-picker-list"></div><p class="muted">구글 문서를 선택하면 이 대화에서 작업할 수 있습니다. 다른 형식은 문서 추가를 이용해 주세요. 원본은 이동하거나 삭제하지 않습니다.</p>';
 data.accounts.filter(a=>a.docs_ok).forEach(a=>{const o=document.createElement('option');o.value=a.email;o.textContent=a.email;fields.querySelector('select').append(o);});
 if(!fields.querySelector('option')){fields.replaceChildren();status.textContent='문서 편집 권한이 있는 연결 계정이 없습니다.';const a=document.createElement('a');a.href=opener.dataset.connections;a.textContent='계정 연결 설정';fields.append(a);submit.hidden=true;return;}
 const account=f.elements.account,list=fields.querySelector('.drive-picker-list'),path=fields.querySelector('.drive-picker-path');
 f.addEventListener('submit',e=>{if(!f.elements.doc.value){e.preventDefault();e.stopImmediatePropagation();}},true);
 let trail=[{id:'root',name:'내 드라이브'}],generation=0;
 async function load(page=''){
   const version=++generation;submit.disabled=true;f.elements.doc.value='';status.textContent='폴더를 불러오는 중…';
   if(!page)list.replaceChildren();path.replaceChildren();
   trail.forEach((part,index)=>{const b=document.createElement('button');b.type='button';b.className='secondary';b.textContent=part.name;b.onclick=()=>{trail=trail.slice(0,index+1);load();};path.append(b);});
   try{const result=await api('/api/chat-documents/browse?'+new URLSearchParams({account:account.value,folder:trail.at(-1).id,page}));if(!d.isConnected||version!==generation)return;
     status.textContent=result.files.length?'문서를 선택하세요.':'이 폴더에 파일이 없습니다.';
     result.files.forEach(file=>{const folder=file.mimeType==='application/vnd.google-apps.folder',doc=file.mimeType==='application/vnd.google-apps.document';
       const b=document.createElement('button');b.type='button';b.className='drive-picker-item';b.textContent=(folder?'폴더 · ':'')+file.name;b.disabled=!folder&&!doc;
       if(!folder&&!doc)b.title='고를 수 있는 형식: 구글 문서';
       b.onclick=()=>{if(folder){trail.push({id:file.id,name:file.name});load();return;}list.querySelectorAll('[aria-pressed]').forEach(el=>el.setAttribute('aria-pressed','false'));b.setAttribute('aria-pressed','true');f.elements.doc.value=file.id;submit.disabled=false;status.textContent='선택: '+file.name;};if(doc)b.setAttribute('aria-pressed','false');list.append(b);
     });
     if(result.next){const more=document.createElement('button');more.type='button';more.textContent='더 보기';more.onclick=()=>{more.remove();load(result.next);};list.append(more);}
   }catch(error){if(version!==generation||!d.isConnected)return;status.textContent=error.message;const retry=document.createElement('button');retry.type='button';retry.textContent='다시 시도';retry.onclick=()=>{retry.remove();load(page);};list.append(retry);}
 }
 account.onchange=()=>{trail=[{id:'root',name:'내 드라이브'}];load();};load();
 f.onsubmit=async e=>{e.preventDefault();submit.disabled=true;status.textContent='문서 확인 중…';const body=new FormData(f);if(sid)body.set('session_id',sid);const project=document.querySelector('#chatForm [name=project_id]')?.value;if(project)body.set('project_id',project);try{const result=await api('/api/chat-documents/connect',{method:'POST',body});sessionStorage.removeItem('chatDocHidden:'+result.session_id);location.assign('/chat/'+result.session_id);}catch(error){status.textContent=error.message;submit.disabled=false;}};
 }catch(error){status.textContent=error.message;}}
 async function insert(){const d=dialog('문서에 내용 삽입'),f=d.querySelector('form'),fields=d.querySelector('[data-fields]'),status=d.querySelector('[role=status]'),submit=d.querySelector('[data-submit]');
 fields.innerHTML='<label>삽입 위치<select name="section" required></select></label><label>삽입할 내용<textarea name="text" maxlength="50000" required></textarea></label><p data-confirmation hidden></p>';linked.sections.forEach(s=>{const o=document.createElement('option');o.value=s.index;o.textContent=s.heading;fields.querySelector('select').append(o);});submit.textContent='내용 확인';submit.disabled=!linked.sections.length;
 f.onsubmit=e=>e.preventDefault();submit.disabled=true;
 if(!linked.sections.length)status.textContent='삽입 가능한 위치가 없습니다.';
 try{const data=await api('/chat/'+sid+'/messages');const answer=[...data.messages].reverse().find(m=>m.role==='assistant');if(d.isConnected&&answer&&!f.elements.text.value)f.elements.text.value=answer.content;}catch(_){}
 if(!d.isConnected)return;submit.disabled=!linked.sections.length;f.dataset.ready='true';
 let confirmed=false;f.addEventListener('input',()=>{confirmed=false;fields.querySelector('[data-confirmation]').hidden=true;submit.textContent='내용 확인';});
 f.onsubmit=async e=>{e.preventDefault();if(!confirmed){confirmed=true;const p=fields.querySelector('[data-confirmation]');p.textContent='「'+f.elements.section.selectedOptions[0].textContent+'」 아래에 '+f.elements.text.value.length+'자를 삽입합니다.';p.hidden=false;submit.textContent='삽입 확정';return;}submit.disabled=true;status.textContent='삽입 중…';const body=new FormData(f);body.set('confirmed','true');try{await api('/api/chat-documents/'+sid+'/insert',{method:'POST',body});status.textContent='삽입했습니다. 문서에서 확인하세요.';submit.hidden=true;f.querySelector('[data-cancel]').textContent='닫기';f.querySelectorAll('input,select,textarea').forEach(el=>el.disabled=true);}catch(error){status.textContent=error.message;submit.disabled=false;confirmed=false;submit.textContent='내용 확인';}};}
 function show(){if(panel&&panel.classList.contains('chat-doc-editor')){visibility(true);return;}if(panel)clearPanel();panel=document.createElement('section');panel.className='chat-doc-editor';panel.setAttribute('aria-label','연결된 구글 문서');panel.innerHTML='<header><strong></strong><div class="chat-doc-switch" role="group" aria-label="편집기"><button type="button" data-ed="gdocs" aria-pressed="true">구글 문서</button><button type="button" data-ed="webhwp" aria-pressed="false">한글 기안기</button></div><label class="chat-doc-toolbar"><input type="checkbox" data-toolbar> 구글 서식 도구 보기</label><a class="mini-btn" target="_blank" rel="noopener">새 창에서 열기</a><button type="button" class="secondary" data-hide>닫기</button></header><iframe title="구글 문서 편집기"></iframe><div class="chat-doc-hwp" data-hwp hidden></div><div class="chat-doc-note">편집기가 열리지 않으면 새 창에서 편집하세요. <button type="button" class="act-btn" data-unlink>문서 연결 해제</button></div>';
 panel.querySelector('strong').textContent=linked.title;panel.querySelector('iframe').dataset.pendingSrc=linked.embed_url;panel.querySelector('a').href=linked.embed_url_toolbar||linked.embed_url;panel.querySelector('[data-toolbar]').onchange=switchFormatting;panel.querySelector('[data-hide]').onclick=()=>{visibility(false);opener.focus();};const insBtn=panel.querySelector('[data-insert]');if(insBtn)insBtn.onclick=insert;
 panel.querySelector('[data-unlink]').onclick=()=>{const d=dialog('문서 연결 해제');d.querySelector('[role=status]').textContent='원본 문서와 대화는 유지됩니다. 이 대화에서 문서 연결을 해제할까요?';d.querySelector('[data-submit]').textContent='연결 해제';d.querySelector('form').onsubmit=async e=>{e.preventDefault();d.querySelector('[data-submit]').disabled=true;try{await api('/api/chat-documents/'+sid,{method:'DELETE'});visibility(false);panel.remove();panel=null;linked=null;currentDoc=null;opener.hidden=false;opener.removeAttribute('aria-controls');d.close();}catch(error){d.querySelector('[role=status]').textContent=error.message;d.querySelector('[data-submit]').disabled=false;}};};main.prepend(panel);visibility(true);initEditorSwitch();}
 // 문서 창 편집기 고르기 — 구글 문서(작업본) 또는 한글 기안기(작업본을 원본 서식 hwpx 에 채운 한글 완성본, 부서 한글 문서함).
 // 고른 것은 계정 설정(editor)으로 남아 다음에도 그쪽으로 열고, 에이전트의 「한글로 내보내기」 도 그 자리로 간다
 async function initEditorSwitch(){
   const sw=panel?.querySelector('.chat-doc-switch');if(!sw)return;
   let st=null;try{st=await api('/api/chat/'+sid+'/hwp');}catch(_){sw.hidden=true;return;}
   if(!st.ok){sw.querySelector('[data-ed=webhwp]').disabled=true;sw.querySelector('[data-ed=webhwp]').title='웹 한글 기안기 서버에 연결되지 않았습니다';}
   sw.querySelectorAll('button').forEach(b=>b.onclick=()=>pickEditor(b.dataset.ed));
   if(st.editor==='webhwp'&&st.ok)pickEditor('webhwp',st);
 }
 let hwpPoll=null;
 async function pickEditor(kind,st){
   if(!panel)return;
   const sw=panel.querySelector('.chat-doc-switch'), frame=panel.querySelector('iframe'), box=panel.querySelector('[data-hwp]');
   sw.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.ed===kind)));
   editorKind=kind;panel.classList.toggle('is-hwp',kind==='webhwp');      // 한글 쪽에서는 구글 문서 목차 이동·서식 도구를 감춘다
   panel.querySelector('.chat-doc-toolbar').hidden=kind!=='gdocs';
   clearInterval(hwpPoll);hwpPoll=null;
   const body=new URLSearchParams({editor:kind,back:'/'});fetch('/hwp/editor',{method:'POST',body,redirect:'manual'}).catch(()=>{});
   if(kind==='gdocs'){box.hidden=true;frame.hidden=false;if(frame.dataset.gsrc){frame.src=frame.dataset.gsrc;delete frame.dataset.gsrc;}panel.querySelector('header a').href=linked.embed_url_toolbar||linked.embed_url;return;}
   st=st||await api('/api/chat/'+sid+'/hwp').catch(()=>null);
   if(st&&st.file){
     box.hidden=true;frame.hidden=false;
     if(!frame.dataset.gsrc)frame.dataset.gsrc=frame.src||linked.embed_url;
     frame.title='한글 기안기';frame.src=st.file.url;panel.querySelector('header a').href=st.file.url;
     return;
   }
   frame.hidden=true;box.hidden=false;
   box.innerHTML='<div class="chat-doc-hwp-card"><iconify-icon icon="solar:document-add-linear" aria-hidden="true"></iconify-icon><strong>이 대화에 한글 문서가 없습니다</strong>'
     +'<p>부서 폴더나 구글 드라이브의 한글 파일을 가져와 바로 편집하거나, 지금 구글 작업본을 원본 서식(한글)에 채워 한글 문서로 만들 수 있습니다.</p>'
     +'<div class="chat-doc-hwp-actions"><button type="button" data-hwp-pick>파일 가져오기</button><button type="button" class="secondary" data-hwp-make>구글 작업본을 한글로</button></div><p role="status" data-hwp-msg></p></div>';
   box.querySelector('[data-hwp-pick]').onclick=openFinder;
   const btn=box.querySelector('[data-hwp-make]'), msg=box.querySelector('[data-hwp-msg]');
   const wait=()=>{msg.textContent='만드는 중… 끝나면 대화에도 결과가 남습니다';btn.disabled=true;hwpPoll=setInterval(async()=>{const s2=await api('/api/chat/'+sid+'/hwp').catch(()=>null);if(s2&&s2.file){clearInterval(hwpPoll);hwpPoll=null;pickEditor('webhwp',s2);}else if(s2&&!s2.busy){clearInterval(hwpPoll);hwpPoll=null;btn.disabled=false;msg.textContent='만들지 못했습니다 — 대화의 안내를 확인해 주세요';}},3000);};
   if(st&&st.busy)wait();
   btn.onclick=async()=>{try{await api('/api/chat/'+sid+'/hwp/export',{method:'POST'});wait();}catch(e){msg.textContent=e.message;}};
 }
 // 플랫폼 문서(첨부·접수·기준)의 구글 열람본을 같은 자리(iframe)에 연다 — 연결 문서와 달리 에이전트 편집 대상은 아니다
 function showViewer(v){
   clearPanel();panel=document.createElement('section');panel.className='chat-doc-editor chat-doc-viewer';panel.setAttribute('aria-label','문서 열람');
   panel.innerHTML='<header><button type="button" class="secondary" data-list>목록</button><strong></strong><a class="mini-btn" target="_blank" rel="noopener">새 창에서 열기</a><button type="button" class="secondary" data-hide>닫기</button></header><iframe title="문서 열람"></iframe>';
   panel.querySelector('strong').textContent=v.title;panel.querySelector('a[target]').href=v.url;
   const state=document.createElement('span');state.className='chat-doc-state';
   state.textContent=[v.original_format?originalLabel(v.original_format):'문서',v.is_doc?'변환본 보기':'미리보기','읽기 전용'].join(' · ');
   panel.querySelector('header').after(state);
   panel.querySelector('[data-list]').onclick=showFiles;panel.querySelector('[data-hide]').onclick=()=>{visibility(false);opener.focus();};
   // 독스 문서면 "이 문서로 작업" — 복제본을 만들어 이 대화의 작업 문서로 잇는다(원본 서식은 그대로)
   if(v.is_doc&&sid){const work=document.createElement('button');work.type='button';work.className='secondary';work.textContent='이 문서로 작업';work.title='복제본을 만들어 이 대화에 연결합니다';
     let ready=null;
     work.onclick=async()=>{
       if(ready){linked=ready;clearPanel();show();return;}
       const current=panel, session=sid;
       work.disabled=true;work.textContent='복제본 만드는 중…';
       try{
         await api('/api/chat/'+session+'/work-on/'+v.doc_id,{method:'POST'});
         const d=await api('/api/chat-documents/'+session);
         if(session!==sid)return;
         linked=d;ready=d;currentDoc=d.connected?d.doc:null;
         // 요청 중 사용자가 다른 문서를 열거나 패널을 접었다면 그 선택을 유지한다.
         if(panel===current&&!panel.hidden){clearPanel();show();}
       }catch(e){work.textContent=e.message;}
       finally{work.disabled=false;if(work.isConnected&&ready)work.textContent='작업 문서 열기';}
     };
     panel.querySelector('header').insertBefore(work,panel.querySelector('[data-hide]'));}
   main.append(panel);if(!divider.isConnected)main.append(divider);setRatio(ratio,false);visibility(true);fitEditor(/document\//.test(v.embed_url));
   loadFrame(panel.querySelector('iframe'),v.embed_url,'문서를 불러오는 중…');
   panel.querySelectorAll('header a,header button').forEach(el=>el.classList.add('chat-doc-mini'));
 }
 const createPanel=show;
 function arrangeHeader(){
   const header=panel.querySelector('header');if(header.classList.contains('doc-header-refined'))return;
   header.classList.add('doc-header-refined');
   const titleRow=document.createElement('div');titleRow.className='doc-title-row';
   const tools=document.createElement('div');tools.className='doc-view-tools';tools.setAttribute('aria-label','문서 보기 설정');
   const back=header.querySelector('[data-list]'),title=header.querySelector('strong'),close=header.querySelector('[data-hide]');
   back.textContent='← 문서 목록';back.title='문서 목록으로 돌아가기';back.setAttribute('aria-label',back.title);back.classList.remove('doc-icon-button');
   title.title=title.textContent;
   close.textContent='×';close.title='문서 패널 닫기';close.setAttribute('aria-label','문서 패널 닫기');close.classList.add('doc-icon-button');
   titleRow.append(back,title,close);
   const more=document.createElement('details');more.className='doc-more';
   const summary=document.createElement('summary');summary.textContent='•••';summary.title='문서 더보기';summary.setAttribute('aria-label','문서 더보기');
   const menu=document.createElement('div');menu.className='doc-more-actions';
   header.querySelectorAll('a').forEach(a=>menu.append(a));
   // 열람 패널처럼 연결 해제 단추가 없는 머리줄도 있어 있는 것만 옮긴다.
   const unlink=panel.querySelector('[data-unlink]');if(unlink)menu.append(unlink);
   more.append(summary,menu);
   more.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();more.open=false;summary.focus();}});
   menu.addEventListener('click',e=>{if(e.target.closest('a,button'))more.open=false;});
   const formatting=header.querySelector('.chat-doc-toolbar');if(formatting)tools.append(formatting);
   const full=document.createElement('button');full.type='button';full.className='secondary doc-full-btn';full.dataset.full='';full.textContent='전체 보기';full.title='전체 보기';full.setAttribute('aria-pressed','false');full.onclick=()=>toggleFull();
   titleRow.insertBefore(full,close);
   const edSwitch=header.querySelector('.chat-doc-switch');if(edSwitch){edSwitch.classList.add('doc-ed-switch');titleRow.insertBefore(edSwitch,close);}   // 편집기 고르기는 제목 줄 오른쪽(닫기 앞)
   tools.append(more);
   buildSectionJump(tools);
   addLocationButtons();
   header.append(titleRow,tools);
   panel.querySelector('.chat-doc-note')?.remove();
 }
 // 문서 위치 이동 — 제목 id 가 있는 절만 고른다(id 가 없으면 이동할 곳이 없다). 답이 끝나 절이 바뀌면 다시 만든다.
 // 절이 많고 단계가 둘 이상이면 두 칸으로 나눈다: 앞 칸은 맨 윗단계(장), 뒤 칸([data-section-jump])은 선택한 장 안의 절.
 // 장을 고르면 그 장 머리로 가고 뒤 칸이 그 장의 절로 바뀐다. 절이 적거나 단계가 하나면 한 칸에 모두 둔다(단계는 들여 쓴다).
 const FLAT_LIMIT=12;
 function sectionTree(destinations){
   const level=s=>Number(s.level)||1;
   const top=Math.min(...destinations.map(level));
   const chapters=[];
   destinations.forEach(s=>{if(level(s)<=top||!chapters.length)chapters.push({head:s,items:[]});else chapters.at(-1).items.push(s);});
   return {chapters,top,level};
 }
 function fillOptions(select,prompt,items,depth){
   select.replaceChildren();
   const first=document.createElement('option');first.value='';first.textContent=prompt;select.append(first);
   items.forEach(s=>{const o=document.createElement('option');o.value=s.index;o.textContent='   '.repeat(Math.max(0,depth(s)))+s.heading;select.append(o);});
 }
 function buildSectionJump(tools){
   tools=tools||panel?.querySelector('.doc-view-tools');if(!tools||!linked)return;
   tools.querySelector('[data-section-jump]')?.remove();tools.querySelector('[data-section-chapter]')?.remove();
   const destinations=(linked.sections||[]).filter(s=>s.heading_id&&s.heading);
   if(!destinations.length)return;
   const {chapters,top,level}=sectionTree(destinations);
   const select=document.createElement('select');select.dataset.sectionJump='';select.setAttribute('aria-label','목차에서 선택한 제목 위치로 이동');
   const go=value=>{const section=destinations.find(s=>String(s.index)===value);if(section)jumpToSection(section);};
   select.onchange=()=>go(select.value);
   const nested=destinations.length>FLAT_LIMIT&&chapters.some(c=>c.items.length);
   if(!nested){fillOptions(select,'목차로 이동',destinations,s=>level(s)-top);tools.prepend(select);return;}
   const chapter=document.createElement('select');chapter.dataset.sectionChapter='';chapter.setAttribute('aria-label','장 선택');
   fillOptions(chapter,'장 선택',chapters.map(c=>c.head),()=>0);
   const showChapter=c=>{
     const items=c?c.items:[];
     fillOptions(select,c?(items.length?`절 선택 · ${items.length}개`:'이 장에는 절이 없습니다'):'장을 먼저 선택하세요',items,s=>level(s)-top-1);
     select.disabled=!items.length;
   };
   chapter.onchange=()=>{const c=chapters.find(c=>String(c.head.index)===chapter.value);showChapter(c);if(c)go(chapter.value);};
   chapter._show=index=>{const c=chapters.find(c=>c.head.index===index||c.items.some(s=>s.index===index));if(!c)return;chapter.value=String(c.head.index);showChapter(c);};
   showChapter(null);
   tools.prepend(chapter,select);
 }
 document.addEventListener('pointerdown',e=>{const more=panel?.querySelector('.doc-more[open]');if(more&&!more.contains(e.target))more.open=false;});
 show=()=>{
   // 문서함(iframe 없음)·열람 패널(미리보기 iframe)은 편집기가 아니므로 비우고 새로 만든다. 그대로 두면
   // 미리보기 위에 편집기 머리줄이 덧붙고 위치 이동이 미리보기 iframe 을 건드린다.
   if(panel&&!isEditor())clearPanel();
   createPanel();
   fitEditor();
   const frame=panel.querySelector('iframe');
   if(frame?.dataset.pendingSrc){const first=frame.dataset.pendingSrc;delete frame.dataset.pendingSrc;loadFrame(frame,first,'문서를 불러오는 중…');}
   if(!panel.querySelector('[data-list]')){
     const back=document.createElement('button');back.type='button';back.className='secondary';back.dataset.list='';back.textContent='목록';back.onclick=showFiles;
     panel.querySelector('header').prepend(back);
   }
   if(!panel.dataset.folderAsked){
     panel.dataset.folderAsked='1';
     const folder=document.createElement('a');folder.dataset.folder='';folder.textContent='드라이브 폴더 열기';
     folder.target='_blank';folder.rel='noopener';folder.hidden=true;
     panel.querySelector('header').insertBefore(folder,panel.querySelector('header a'));
     api('/api/chat-documents/'+sid+'/folder').then(data=>{
       if(data.url&&/^https:\/\/drive\.google\.com\/drive\/folders\/[A-Za-z0-9_-]+$/.test(data.url)){
         folder.href=data.url;folder.hidden=false;
       }
     }).catch(()=>{folder.remove();});
   }
   if(!divider.isConnected)main.append(divider);
   setRatio(ratio);
   panel.id='chatDocumentPanel';opener.setAttribute('aria-controls',panel.id);
   const width=panel.querySelector('input[type=range]');
   if(width){width.closest('label').remove();main.style.setProperty('--document-width','50%');}
   arrangeHeader();
   panel.querySelector('[data-hide]').onclick=()=>{visibility(false);opener.focus();};
   visibility(true);
   paintBusy();
 };
 opener.setAttribute('aria-expanded','false');
 let loadError='';
 function showEmpty(){
   if(!panel){
     panel=document.createElement('section');panel.className='chat-doc-editor';panel.id='chatDocumentPanel';
     panel.setAttribute('aria-label','대화 문서');
     panel.innerHTML='<header><strong>문서</strong><button type="button" class="secondary" data-close>닫기</button></header><div class="chat-doc-empty"><p role="status"></p><button type="button" class="secondary" data-connect>기존 문서 연결</button></div>';
     panel.querySelector('[role=status]').textContent=loadError||'이 대화에 연결된 문서가 없습니다.';
     panel.querySelector('[data-close]').onclick=()=>{visibility(false);opener.focus();};
     panel.querySelector('[data-connect]').onclick=connect;
     main.append(panel);if(!divider.isConnected)main.append(divider);
     opener.setAttribute('aria-controls',panel.id);setRatio(ratio);
   }
   visibility(true);
 }
 function originalLabel(format){const ext=(format||'').replace(/^\./,'').toLowerCase();return ['hwp','hwpx'].includes(ext)?'한글 ('+ext.toUpperCase()+')':ext?ext.toUpperCase():'파일';}
 function fileCard(button,name,subtitle,imageUrl,originalFormat){
   button.classList.add('chat-file-card');if(!button.title)button.title=name;button.replaceChildren();
   const visual=document.createElement('span');visual.className='chat-file-visual';
   const format=originalLabel(originalFormat||name.match(/\.([a-z0-9]+)$/i)?.[1]);visual.textContent=format.replace('한글 (','').replace(')','');
   if(originalFormat==='folder'){visual.textContent='';const icon=document.createElement('iconify-icon');icon.setAttribute('icon','solar:folder-bold');icon.setAttribute('aria-hidden','true');icon.style.color='#C9A227';visual.append(icon);}
   if(originalFormat==='Google Docs'){
     visual.textContent='';const icon=document.createElement('iconify-icon');icon.setAttribute('icon','solar:document-text-linear');icon.setAttribute('aria-hidden','true');visual.append(icon);
   }
   if(imageUrl){const img=document.createElement('img');img.src=imageUrl;img.alt='';img.loading='lazy';img.onerror=()=>img.remove();visual.append(img);}
   const copy=document.createElement('span');copy.className='chat-file-copy';
   const title=document.createElement('strong');title.textContent=name;
   const detail=document.createElement('small');detail.textContent=subtitle||format;
   copy.append(title,detail);button.append(visual,copy);
 }
 function previewImage(name,url,trigger){
   const modal=document.createElement('dialog');modal.className='chat-image-preview';modal.setAttribute('aria-label',name);
   const close=document.createElement('button');close.type='button';close.textContent='닫기';close.onclick=()=>modal.close();
   const img=document.createElement('img');img.src=url;img.alt=name;
   const caption=document.createElement('p');caption.textContent=name;
   img.onerror=()=>{img.hidden=true;caption.textContent='이미지를 불러오지 못했습니다. 문서 화면에서 확인해 주세요.';};
   modal.append(close,img,caption);document.body.append(modal);
   modal.addEventListener('click',e=>{if(e.target===modal)modal.close();});
   modal.addEventListener('close',()=>{modal.remove();if(trigger.isConnected)trigger.focus();});modal.showModal();
 }
 // 「현재 작업 중」은 이 대화의 연결 정보(/api/chat-documents/{sid} 의 doc)와 같은 문서에만 붙인다.
 // 같은 프로젝트 폴더에는 다른 대화의 작업본도 있어서, 화면에 남은 linked(옛 연결)로 판단하면 다른 대화의
 // 문서가 표시될 수 있다. 그래서 문서함을 열 때마다 연결 정보를 새로 읽는다.
 let currentDoc=null;
 function markCurrentFile(){
   const list=panel?.querySelector('.chat-work-files');if(!list)return;
   list.querySelectorAll('[data-drive-doc]').forEach(button=>{
     const active=Boolean(currentDoc)&&button.dataset.driveDoc===currentDoc;
     button.classList.toggle('chat-file-current',active);
     if(active)button.setAttribute('aria-current','true');else button.removeAttribute('aria-current');
     button.querySelector('small').textContent=active?'현재 작업 중 · 구글 문서':'구글 문서 · 작업 문서';
   });
   const current=list.querySelector('[aria-current="true"]');if(current)list.prepend(current);
 }
 // 문서함 — 작업 문서(이 대화와 함께 편집하는 Google Docs) 아래에 프로젝트 화면과 같은 세 갈래:
 // 첨부 문서 · 참고 문서(에이전트가 불러온 문서 포함) · 기준 문서. 답이 끝날 때마다 목록을 다시 읽는다.
 // 설명·빈 상태 문구는 프로젝트 대화인지에 따라 고른다(프로젝트 없는 대화에 '프로젝트에 올린'을 말하지 않게)
 const DOC_GROUPS=[['attached','첨부 문서'],['reference','참고 문서'],['rules','기준 문서']];
 const DOC_NOTES={project:{attached:'프로젝트에 올리거나 대화에 첨부한 문서',reference:'라이브러리에서 불러온 문서 · 에이전트가 근거로 쓴 문서',rules:'프로젝트에 연결된 공고·지침·기준'},
   chat:{attached:'이 대화에 첨부한 문서',reference:'라이브러리에서 불러온 문서 · 에이전트가 근거로 쓴 문서',rules:'이 대화에 첨부한 공고·지침·기준'}};
 const DOC_EMPTY={project:{attached:'아직 올리거나 첨부한 문서가 없습니다.',reference:'에이전트가 근거로 불러온 문서가 여기에 쌓입니다.',rules:'연결된 공고·지침이 없습니다.'},
   chat:{attached:'아직 첨부한 문서가 없습니다.',reference:'에이전트가 근거로 불러온 문서가 여기에 쌓입니다.',rules:'첨부한 공고·지침이 없습니다.'}};
 const inProject=()=>Boolean(document.querySelector('#chatForm [name=project_id]'));
 let refreshLibrary=null;
 // 문서 패널 맨 위 편집기 고르기(한글 문서 · 구글 문서)와 「작업 문서」 의 한글 파일 — 이 대화로 가져온 파일·양식·완성본만 선다.
 // 파일을 찾아오는 일(부서 폴더·구글 드라이브)은 머리줄 「가져오기」 의 파인더 창(openFinder)에서 한다.
 const elx=(t,c,txt)=>{const e=document.createElement(t);if(c)e.className=c;if(txt!=null)e.textContent=txt;return e;};
 let editorKind='gdocs';
 function hwpWork(body,current,workDocs,status){
   // 편집기 고르기 — 머리줄 제목 옆의 작은 원형 토글(한글 · 구글)
   const sw=elx('div','chat-ed-pill');sw.setAttribute('role','group');sw.setAttribute('aria-label','문서 편집기');
   const bH=elx('button',null,'한글'),bG=elx('button',null,'구글');bH.type=bG.type='button';bH.dataset.ed='webhwp';bG.dataset.ed='gdocs';
   bH.title='한글 문서(웹 한글 기안기)로 작업';bG.title='구글 문서로 작업';
   sw.append(bH,bG);const head=current.querySelector('header');head.insertBefore(sw,head.querySelector('strong').nextSibling);
   const mark=ed=>{editorKind=ed;[bH,bG].forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.ed===ed)));};
   const paint=st=>{
     workDocs.querySelectorAll('[data-hwp-file]').forEach(x=>x.remove());
     (st.files||[]).slice().reverse().forEach(f=>{
       const row=elx('div','chat-hwp-work');row.dataset.hwpFile=f.id;
       const a=elx('button','chat-doc-file'+(f.current?' chat-file-current':''));a.type='button';
       fileCard(a,f.name.replace(/\.hwpx?$/i,''),[f.current?'현재 작업 중':'',f.where,f.saved?('저장 '+f.saved+(f.saved_by?' · '+f.saved_by:'')):''].filter(Boolean).join(' · '),null,'hwpx');
       if(f.current)a.setAttribute('aria-current','true');
       a.onclick=async()=>{try{const r=await api('/api/chat/'+sid+'/hwp/current',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:f.id})});showHwp(r.file);}catch(e){status.textContent=e.message;}};
       const x=elx('button','chat-hwp-detach','×');x.type='button';x.title='작업 문서 목록에서 빼기(파일은 부서 폴더에 그대로)';x.setAttribute('aria-label',x.title);
       x.onclick=async()=>{try{await api('/api/chat/'+sid+'/hwp/files/'+f.id,{method:'DELETE'});row.remove();}catch(e){status.textContent=e.message;}};
       row.append(a,x);workDocs.prepend(row);
     });
     workDocs.dispatchEvent(new Event('hwp-painted'));
   };
   api('/api/chat/'+sid+'/hwp').then(st=>{if(panel!==current)return;mark(st.editor||'gdocs');if(!st.ok){bH.title='웹 한글 기안기 서버 연결 안 됨';}paint(st);}).catch(()=>{});
   [bH,bG].forEach(b=>b.onclick=()=>{mark(b.dataset.ed);fetch('/hwp/editor',{method:'POST',body:new URLSearchParams({editor:b.dataset.ed,back:'/'}),redirect:'manual'}).catch(()=>{});});
 }
 // 파일 가져오기 — 맥 파인더처럼 왼쪽에 위치(부서 폴더 · 구글 드라이브 · 공유 드라이브), 오른쪽에 그 폴더의 폴더·한글 파일.
 // 파일을 고르고 「열기」(또는 두 번 누르기)면 이 대화의 작업 문서로 이어 기안기로 연다. 드라이브 파일은 내 부서 폴더 「작업」 으로 받아서,
 // 잠긴 공통 양식은 내 부서 폴더로 복사해서 연다(원본 양식은 그대로).
 let finderFolderUrl='';
 async function openFinder(){
   const d=elx('dialog','hwp-finder');d.setAttribute('aria-label','파일 가져오기');
   d.innerHTML='<header><h2>파일 가져오기</h2><form class="hf-search" role="search"><input type="search" name="q" placeholder="구글 드라이브에서 한글 파일 검색" aria-label="구글 드라이브에서 한글 파일 검색"></form><button type="button" class="hf-x" aria-label="닫기">×</button></header>'
     +'<div class="hf-body"><nav class="hf-side" aria-label="위치"></nav><section class="hf-main"><div class="hf-bar"><button type="button" class="hf-back" aria-label="뒤로" disabled>‹</button><div class="hf-path"></div></div><div class="hf-list" role="listbox" aria-label="폴더와 파일"></div></section></div>'
     +'<footer><span class="hf-sel">파일을 고르세요</span><span class="hf-msg" role="status"></span><button type="button" class="secondary" data-cancel>취소</button><button type="button" data-open disabled>열기</button></footer>';
   document.body.append(d);d.showModal();
   const side=d.querySelector('.hf-side'),list=d.querySelector('.hf-list'),path=d.querySelector('.hf-path'),back=d.querySelector('.hf-back');
   const sel=d.querySelector('.hf-sel'),msg=d.querySelector('.hf-msg'),openBtn=d.querySelector('[data-open]'),q=d.querySelector('input');
   const close=()=>d.close();d.querySelector('.hf-x').onclick=close;d.querySelector('[data-cancel]').onclick=close;
   d.addEventListener('close',()=>d.remove());d.addEventListener('click',e=>{if(e.target===d)close();});
   let loc=null,hist=[],picked=null,drives=[];
   const sideBtn=(label,icon,go,key)=>{const b=elx('button','hf-place');b.type='button';b.dataset.key=key;const i=document.createElement('iconify-icon');i.setAttribute('icon',icon);i.setAttribute('aria-hidden','true');b.append(i,elx('span',null,label));b.onclick=()=>{hist=[];go();};return b;};
   const markSide=key=>side.querySelectorAll('.hf-place').forEach(b=>b.classList.toggle('on',b.dataset.key===key));
   const pick=(row,item)=>{list.querySelectorAll('.hf-row.on').forEach(r=>r.classList.remove('on'));row.classList.add('on');picked=item;sel.textContent=item.name;openBtn.disabled=false;};
   const row=(kind,name,meta,onOpen,item)=>{const r=elx('button','hf-row');r.type='button';r.setAttribute('role','option');r.dataset.kind=kind;
     const i=document.createElement('iconify-icon');i.setAttribute('icon',kind==='folder'?'solar:folder-bold':'solar:document-text-bold');i.setAttribute('aria-hidden','true');
     const nm=elx('span','hf-name',name),mt=elx('span','hf-meta',meta||'');r.append(i,nm,mt);
     if(kind==='folder'){r.onclick=onOpen;}else{r.onclick=()=>pick(r,item);r.ondblclick=()=>{pick(r,item);openPicked();};}
     list.append(r);};
   const crumbs=parts=>{path.replaceChildren();parts.forEach((p,i)=>{if(i)path.append(elx('span','hf-sep','›'));const b=elx('button',null,p.name);b.type='button';b.disabled=!p.go||i===parts.length-1;if(p.go)b.onclick=()=>{hist.push(loc);p.go();};path.append(b);});};
   const busy=t=>{list.replaceChildren(elx('p','hf-empty',t));picked=null;openBtn.disabled=true;sel.textContent='파일을 고르세요';};
   async function goDept(dept,folder){
     loc={src:'dept',dept,folder};back.disabled=!hist.length;markSide('dept:'+dept);busy('불러오는 중…');if(dept)crumbs([{name:dept}]);
     let j;try{j=await api('/api/hwp/browse?dept='+encodeURIComponent(dept||'')+'&folder='+encodeURIComponent(folder||''));}catch(e){busy(e.message);return;}
     if(!d.open)return;const here=j.here;if(!here){busy('쓸 수 있는 부서 폴더가 없습니다');return;}
     if(!dept){loc.dept=here.dept;markSide('dept:'+here.dept);}
     crumbs([{name:here.dept,go:()=>goDept(here.dept,'')},...(here.crumbs||[]).map(c=>({name:c.name,go:()=>goDept(here.dept,c.path)}))]);
     list.replaceChildren();
     here.folders.forEach(f=>row('folder',f.name,'문서 '+f.count,()=>{hist.push(loc);goDept(here.dept,f.path);}));
     here.files.forEach(f=>row('file',f.name.replace(/\.hwpx?$/i,''),[f.saved,f.saved_by||f.owner].filter(Boolean).join(' · '),null,{id:f.id,name:f.name}));
     if(!here.folders.length&&!here.files.length)busy('이 폴더는 비어 있습니다');
     return j;
   }
   async function goDrive(drive,folder,trail){
     loc={src:'drive',drive,folder,trail};back.disabled=!hist.length;markSide('drive:'+(drive||'my'));busy('불러오는 중…');
     const root=drive?(drives.find(x=>x.id===drive)||{name:'공유 드라이브'}).name:'내 드라이브';
     crumbs([{name:root,go:()=>goDrive(drive,'',[])},...trail.map((t,i)=>({name:t.name,go:()=>goDrive(drive,t.id,trail.slice(0,i+1))}))]);
     let j;try{j=await api('/api/hwp/drive/browse?drive='+encodeURIComponent(drive||'')+'&folder='+encodeURIComponent(folder||''));}
     catch(e){busy(e.status===409?'학교 구글 계정을 연결하면 드라이브 파일을 고를 수 있습니다 — 설정 › 내 학교 계정 연결':e.message);return;}
     if(!d.open)return;
     list.replaceChildren();
     j.folders.forEach(f=>row('folder',f.name,'폴더',()=>{hist.push(loc);goDrive(drive,f.id,[...trail,f]);}));
     j.files.forEach(f=>row('file',f.name.replace(/\.hwpx?$/i,''),'드라이브 · '+(f.modifiedTime||'').slice(0,10),null,{drive_id:f.id,name:f.name}));
     if(!j.folders.length&&!j.files.length)busy('이 폴더에 한글 파일이 없습니다');
   }
   async function search(text){
     loc={src:'search',text};back.disabled=!hist.length;markSide('');busy('찾는 중…');crumbs([{name:'「'+text+'」 검색 결과'}]);
     let j;try{j=await api('/api/hwp/drive/browse?q='+encodeURIComponent(text));}catch(e){busy(e.status===409?'학교 구글 계정을 연결하면 드라이브에서 찾을 수 있습니다':e.message);return;}
     list.replaceChildren();j.files.forEach(f=>row('file',f.name.replace(/\.hwpx?$/i,''),'드라이브 · '+(f.modifiedTime||'').slice(0,10),null,{drive_id:f.id,name:f.name}));
     if(!j.files.length)busy('맞는 한글 파일이 없습니다');
   }
   const goLoc=l=>l.src==='dept'?goDept(l.dept,l.folder):l.src==='drive'?goDrive(l.drive,l.folder,l.trail):search(l.text);
   back.onclick=()=>{const l=hist.pop();if(l)goLoc(l);};
   d.querySelector('.hf-search').onsubmit=e=>{e.preventDefault();const t=q.value.trim();if(t){hist.push(loc);search(t);}};
   async function openPicked(){
     if(!picked)return;openBtn.disabled=true;msg.textContent=picked.drive_id?'드라이브에서 받아 여는 중…':'여는 중…';
     const payload=picked.drive_id?{drive_id:picked.drive_id}:{id:picked.id};
     try{const r=await api('/api/chat/'+sid+'/hwp/bind',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});close();showHwp(r.file);}
     catch(e){msg.textContent=e.message;openBtn.disabled=false;}
   }
   openBtn.onclick=openPicked;
   // 왼쪽 목록 — 부서 폴더(쓸 수 있는 부서), 구글 드라이브(내 드라이브·공유 드라이브), 이 프로젝트의 드라이브 폴더(새 창)
   side.append(elx('p','hf-group','부서 폴더'));
   const first=await goDept('','');
   ((first&&first.depts)||[]).forEach(x=>side.append(sideBtn(x.dept,'solar:folder-with-files-linear',()=>goDept(x.dept,''),'dept:'+x.dept)));
   if(first&&first.here)markSide('dept:'+first.here.dept);
   side.append(elx('p','hf-group','구글 드라이브'));
   side.append(sideBtn('내 드라이브','solar:cloud-linear',()=>goDrive('','',[]),'drive:my'));
   api('/api/hwp/drive/browse').then(j=>{drives=j.drives||[];const anchor=side.querySelector('[data-key="drive:my"]');
     drives.slice().reverse().forEach(x=>anchor.after(sideBtn(x.name,'solar:users-group-rounded-linear',()=>goDrive(x.id,'',[]),'drive:'+x.id)));}).catch(()=>{});
   if(finderFolderUrl){const a=elx('a','hf-place hf-link','프로젝트 드라이브 폴더 ↗');a.href=finderFolderUrl;a.target='_blank';a.rel='noopener';side.append(a);}
 }
 async function showFiles(){
   // 문서 목록은 좁게 연다(목록 폭, 기본 패널 30%) — 문서를 열면 편집기·열람 폭으로 넓어진다
   clearPanel();showEmpty();
   panel.classList.add('chat-file-library');setRatio(listRatio,false);panel.querySelector('header strong').textContent='문서 패널';
   const refresh=document.createElement('button');refresh.type='button';refresh.className='secondary doc-icon-button';refresh.innerHTML='<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M20 7v5h-5M20 12a8 8 0 1 0-2.3 5.7"/></svg>';refresh.title='목록 새로고침';refresh.setAttribute('aria-label',refresh.title);refresh.onclick=showFiles;
   panel.querySelector('header').insertBefore(refresh,panel.querySelector('[data-close]'));

   const current=panel,body=panel.querySelector('.chat-doc-empty');body.className='chat-doc-files';body.replaceChildren();
   const status=document.createElement('p');status.setAttribute('role','status');status.textContent='파일을 불러오는 중…';body.append(status);
   const countHeading=(heading,label,count)=>{heading.textContent=label+' ';const badge=document.createElement('span');badge.className='chat-file-count';badge.textContent=count;heading.append(badge);};
   const workHeading=document.createElement('h3');workHeading.textContent='작업 문서';
   const workDocs=document.createElement('div');workDocs.className='chat-file-list chat-work-files';
   const workNote=document.createElement('p');workNote.className='chat-file-section-note';workNote.textContent='채팅과 함께 편집하는 문서';
   body.append(workHeading,workNote,workDocs);
   hwpWork(body,current,workDocs,status);
   const groups={};
   let scope=inProject()?'project':'chat';
   for(const [key,label] of DOC_GROUPS){
     const heading=document.createElement('h3');heading.textContent=label;
     const help=document.createElement('p');help.className='chat-file-section-note';help.textContent=DOC_NOTES[scope][key];
     const list=document.createElement('div');list.className='chat-file-list';list.dataset.docGroup=key;
     groups[key]={heading,list,label,help};body.append(heading,help,list);
   }
   const imagesHeading=document.createElement('h3');imagesHeading.textContent='콘텐츠';
   const images=document.createElement('div');images.className='chat-image-grid';
   body.append(imagesHeading,images);
   const imageFile=name=>/\.(png|jpe?g|gif|webp|bmp)$/i.test(name);
   function finishGroups(){
     for(const [key] of DOC_GROUPS){const g=groups[key];
       g.list.querySelectorAll('.chat-file-empty').forEach(x=>x.remove());
       const n=g.list.querySelectorAll('.chat-doc-file').length;countHeading(g.heading,g.label,n);
       g.help.textContent=DOC_NOTES[scope][key];
       if(!n){const empty=document.createElement('p');empty.className='chat-file-empty';empty.textContent=DOC_EMPTY[scope][key];g.list.append(empty);}}
     imagesHeading.textContent='콘텐츠 '+images.children.length;
     imagesHeading.hidden=images.hidden=!images.children.length;
   }
   // 플랫폼 문서 — 세 갈래. 클릭하면 구글 열람본(없으면 만들어서)으로 연다. 그림 문서도 제 갈래에 둔다.
   async function loadPlatform(){
     if(!sid)return;
     const mine=await api('/api/chat/'+sid+'/documents');if(panel!==current)return;
     if('project_id' in mine)scope=mine.project_id?'project':'chat';
     for(const [key] of DOC_GROUPS)groups[key].list.querySelectorAll('[data-platform-doc]').forEach(x=>x.remove());
     const byGroup=mine.groups?Object.fromEntries(mine.groups.map(g=>[g.key,g.documents])):{attached:mine.documents||[]};
     for(const [key] of DOC_GROUPS)for(const d of byGroup[key]||[]){
       const a=document.createElement('button');a.type='button';a.className='chat-doc-file';a.dataset.platformDoc=d.id;
       const via=(d.via||'').startsWith('에이전트')?'에이전트 참고':d.via;
       const unfinished=d.state&&d.state!=='reviewed'?d.status:'';
       const detail=[via,d.kind,unfinished].filter(Boolean).join(' · ');a.title=[d.name,d.via,d.kind,d.status].filter(Boolean).join(' · ');
       const isImage=imageFile('.'+(d.original_format||'')),imageUrl='/doc/'+encodeURIComponent(d.id)+'/original';
       fileCard(a,d.name,detail,isImage?imageUrl:null,d.original_format);
       if((d.via||'').startsWith('에이전트'))a.classList.add('chat-file-agent');
       // Drive 참고 파일보다 플랫폼 문서를 앞에 둔다
       groups[key].list.insertBefore(a,groups[key].list.querySelector('[data-drive-ref]'));
       a.onclick=async()=>{a.disabled=true;status.textContent='구글 열람본을 여는 중…';
         if(isImage){a.disabled=false;status.textContent='';previewImage(d.name,imageUrl,a);return;}
         try{const g=await api('/api/doc/'+d.id+'/google');if(panel!==current)return;const preview='https://drive.google.com/file/d/'+encodeURIComponent(g.id)+'/preview';showViewer({title:d.name,embed_url:preview,url:preview,original_format:d.original_format,page:d.page,doc_id:d.id,is_doc:g.mime==='application/vnd.google-apps.document'});}
         catch(error){status.textContent=error.message;a.disabled=false;}};
     }
     finishGroups();
   }
   refreshLibrary=async()=>{if(panel!==current||!panel.isConnected)return;try{await loadPlatform();}catch(_){}};
   // Start independent requests together; a slow Drive request must not add
   // another full round trip before requesting the platform documents.
   const platformRequest=loadPlatform().then(()=>null,error=>error);
   const session=sid;
   if(session)api('/api/chat-documents/'+session).then(fresh=>{
     if(session!==sid)return;
     currentDoc=fresh.connected?fresh.doc:null;
     // 이미 연결을 알던 경우만 새 값으로 바꾼다 — 새로 생긴 작업본은 checkCreatedDocument 가 열어 준다
     if(fresh.connected&&linked)linked=fresh;
     markCurrentFile();
   }).catch(()=>{});
   try{
     const data=sid?await api('/api/chat-documents/'+sid+'/files').catch(error=>({files:[],error:error.message})):{files:[]};if(panel!==current)return;
     status.textContent='';
     if(data.folder_url)finderFolderUrl=data.folder_url;      // 드라이브 폴더는 가져오기 창 왼쪽 목록에서 연다
     for(const file of data.files){
       if(file.mime_type==='application/vnd.google-apps.folder')continue;   // 폴더는 문서가 아니다(서버도 거른다)
       const button=document.createElement('button');button.type='button';button.className='chat-doc-file';
       const isImage=file.mime_type.startsWith('image/')||imageFile(file.name);
       const editable=file.mime_type==='application/vnd.google-apps.document';
       fileCard(button,file.name,isImage?'이미지 · 구글 드라이브':editable?'구글 문서 · 작업 문서':'구글 드라이브 · 참고 파일',null,editable?'Google Docs':null);
       button.dataset.driveDoc=file.id;
       if(!isImage&&!editable)button.dataset.driveRef='';
       (isImage?images:editable?workDocs:groups.reference.list).append(button);
       button.onclick=async()=>{
         if(file.mime_type!=='application/vnd.google-apps.document'){
           const path=file.mime_type==='application/vnd.google-apps.folder'?'drive/folders/'+file.id:'file/d/'+file.id+'/view';
           window.open('https://drive.google.com/'+path,'_blank','noopener');return;
         }
         // 이미 연결된 작업본은 다시 연결 요청을 보내지 않고 기존 정보로 연다.
         if(linked?.doc===file.id&&linked?.account===data.account){clearPanel();show();return;}
         button.disabled=true;status.textContent='문서를 여는 중…';
         try{const form=new FormData();form.set('session_id',sid);form.set('doc',file.id);form.set('account',data.account);
           await api('/api/chat-documents/connect',{method:'POST',body:form});const document=await api('/api/chat-documents/'+sid);
           if(panel!==current)return;linked=document;currentDoc=document.connected?document.doc:null;clearPanel();show();
         }catch(error){status.textContent=error.message;button.disabled=false;}
       };
     }
     const platformError=await platformRequest;if(panel!==current)return;
     if(platformError)status.textContent='프로젝트 문서를 불러오지 못했습니다. 새로고침을 눌러 주세요.';
     if(data.error)status.textContent='Drive 목록: '+data.error;
     finishGroups();
     const connectButton=document.createElement('button');connectButton.type='button';connectButton.className='secondary';connectButton.textContent='파일 가져오기';
     connectButton.title='부서 폴더·구글 드라이브에서 문서를 골라 이 대화의 작업 문서로';
     connectButton.onclick=()=>editorKind==='webhwp'?openFinder():connect();workDocs.after(connectButton);
     connectButton.classList.add('chat-file-import');
     const tidy=()=>{workDocs.querySelectorAll(':scope>.chat-file-empty').forEach(x=>x.remove());countHeading(workHeading,'작업 문서',workDocs.querySelectorAll('.chat-doc-file').length);
       if(!workDocs.querySelector('.chat-doc-file'))workDocs.append(elx('p','chat-file-empty','아직 작업 문서가 없습니다 — 「파일 가져오기」로 부서 폴더나 드라이브의 문서를 고르거나, 대화에서 문서를 쓰자고 하면 AURA 가 양식으로 만듭니다.'));
       workNote.hidden=!workDocs.querySelector('.chat-doc-file');};
     workDocs.addEventListener('hwp-painted',tidy);tidy();
     markCurrentFile();
   }catch(error){status.textContent=error.message;const retry=document.createElement('button');retry.textContent='다시 시도';retry.onclick=showFiles;body.append(retry);}
 }
 let initialized=Promise.resolve();
 // 새로 생성된 문서만 자동으로 연다. 기존 문서는 문서 버튼에서 목록을 먼저 표시한다.
 let watch=null;
 // 한글 기안기 작업본이 이어진 대화 — 문서 창에 기안기를 연다(에이전트가 hwp_start 로 만들면 몇 초 안에 뜬다)
 let hwpFile=null;
 function showHwp(file){
   hwpFile=file;
   if(panel&&panel.classList.contains('chat-doc-hwp-panel')&&panel.dataset.fid===String(file.id)){visibility(true);return;}
   if(panel)clearPanel();
   panel=document.createElement('section');panel.className='chat-doc-editor chat-doc-hwp-panel';panel.dataset.fid=String(file.id);
   panel.setAttribute('aria-label','한글 기안기 작업본');
   // 제목·저장 상태·새 창·내려받기는 기안기 화면 머리줄(embed)에 있다 — 바깥 머리줄은 목록으로 돌아가기·닫기만(제목이 두 번 서지 않게)
   // 목록·닫기는 기안기 화면 머리줄 안에(postMessage) — 바깥 머리줄 없이 한 줄
   panel.innerHTML='<header class="hwp-panel-head" hidden><button type="button" data-list></button><a hidden></a><button type="button" data-hide></button></header><iframe title="한글 기안기"></iframe>';
   panel.querySelector('[data-list]').onclick=showFiles;
   panel.querySelector('a').href=file.url;
   panel.querySelector('iframe').src=file.url+'&embed=1';
   panel.querySelector('[data-hide]').onclick=()=>{visibility(false);opener.focus();};
   main.prepend(panel);visibility(true);
 }
 // 전체 보기 — 문서 칸을 대화 칸 자리까지 넓힌다(다시 누르거나 Esc 로 나란히 보기). 한글 기안기·구글 문서 같이 쓴다
 function toggleFull(on){
   const next=on===undefined?!main.classList.contains('chat-doc-full'):Boolean(on);
   main.classList.toggle('chat-doc-full',next);
   panel?.querySelectorAll('[data-full]').forEach(b=>{b.setAttribute('aria-pressed',String(next));b.title=next?'나란히 보기':'전체 보기';b.setAttribute('aria-label',b.title);b.textContent=next?'나란히':'전체 보기';});
   try{panel?.querySelector('iframe')?.contentWindow?.postMessage({aura:'hwp-full',on:next},location.origin);}catch(_){}
 }
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&main.classList.contains('chat-doc-full')&&!document.querySelector('dialog[open]'))toggleFull(false);});
 // 기안기 화면(embed) 머리줄의 「목록」·「닫기」
 window.addEventListener('message',e=>{
   if(e.origin!==location.origin||!e.data||e.data.aura!=='hwp'||!panel?.classList.contains('chat-doc-hwp-panel'))return;
   if(e.source!==panel.querySelector('iframe')?.contentWindow)return;
   if(e.data.act==='full'){toggleFull();return;}
   if(e.data.act==='list')showFiles();
   else if(e.data.act==='close'){visibility(false);opener.focus();}
 });
 async function checkHwp(){
   const session=sid;
   if(!session||linked)return false;
   try{const st=await api('/api/chat/'+session+'/hwp');if(session===sid&&st.file&&(!hwpFile||hwpFile.id!==st.file.id)){showHwp(st.file);return true;}return Boolean(st.file);}
   catch(e){return false;}
 }
 async function checkCreatedDocument(){
   const session=sid;
   if(!session||linked||!workspace.isConnected)return;
   if(await checkHwp()){if(session===sid)watch=setTimeout(checkCreatedDocument,6000);return;}
   try{
     const data=await api('/api/chat-documents/'+session);
     // 요청 중 사용자가 목록에서 다른 문서를 선택했다면 그 선택을 보존한다.
     if(session!==sid)return;
     if(!linked&&data.connected){linked=data;currentDoc=data.doc;show();}
   }catch(e){if(e.status===404||e.status===403)return;}   // 지워졌거나 볼 수 없는 대화 — 더 묻지 않는다
   if(session===sid&&!linked&&workspace.isConnected)watch=setTimeout(checkCreatedDocument,6000);
 }
 function initializeSession(){
   const session=sid;
   clearTimeout(watch);
   initialized=session?api('/api/chat-documents/'+session).then(data=>{
     if(session===sid){currentDoc=data.connected?data.doc:null;if(data.connected)linked=data;markCurrentFile();}
   }).catch(error=>{if(session===sid)loadError=error.message;}):Promise.resolve();
   hwpFile=null;
   initialized.then(()=>{if(session===sid&&sid&&!linked){checkHwp();watch=setTimeout(checkCreatedDocument,6000);}});
 }
 // 에이전트 작업 중 표시 — 대화 쪽 진행 카드(chat-workspace.js 의 폴링)의 현재 단계를 편집기 위 한 줄로
 // 비춘다. 단계 글은 'chat-progress' 사건으로, 대기 시작·끝은 'chat-session-updated' 의 waiting 으로 받는다.
 let agentBusy=workspace.dataset.waiting==='true', busyLabel='', busyWriting=null;
 // 문서 위 AURA 커서 — 에이전트가 문서에 쓰는 동안 편집기 위에 이름표 달린 커서가 움직인다(함께 편집하는 사람 커서처럼).
 // 구글 편집기는 교차 출처라 쓰는 줄의 정확한 자리는 알 수 없다 — 편집기 본문 쪽에서 천천히 움직이며 지금 쓰는 절 이름을 보인다
 let docCursor=null,docCursorTimer=0;
 function docCursorOff(){
   clearInterval(docCursorTimer);docCursorTimer=0;
   if(docCursor){const c=docCursor;docCursor=null;c.classList.add('leaving');setTimeout(()=>c.remove(),450);}
 }
 function docCursorOn(label){
   const frame=panel?.querySelector('iframe');
   if(!frame||panel.hidden){docCursorOff();return;}
   const still=window.matchMedia('(prefers-reduced-motion: reduce)').matches;   // 움직임 줄이기 설정이면 한 자리에 둔다
   if(!docCursor){
     docCursor=document.createElement('div');docCursor.className='aura-cursor doc-cursor';docCursor.setAttribute('aria-hidden','true');
     docCursor.innerHTML='<span class="aura-cursor-arrow"></span><span class="aura-cursor-tag"></span>';
     document.body.append(docCursor);
   }
   docCursor.querySelector('.aura-cursor-tag').textContent=label;
   const step=()=>{
     const f=panel?.querySelector('iframe');if(!f||!docCursor){docCursorOff();return;}
     const r=f.getBoundingClientRect();if(r.width<80||r.height<80){docCursor.style.opacity='0';return;}
     docCursor.style.opacity='';
     docCursor.style.left=Math.round(r.left+r.width*(0.18+Math.random()*0.45))+'px';
     docCursor.style.top=Math.round(r.top+r.height*(0.22+Math.random()*0.4))+'px';
   };
   if(!docCursorTimer){step();docCursorTimer=still?-1:setInterval(step,1800);}
 }
 function paintBusy(){
   if(!isEditor())return;
   let strip=panel.querySelector('.chat-doc-busy');
   if(!agentBusy){strip?.remove();docCursorOff();return;}
   if(!strip){
     strip=document.createElement('div');strip.className='chat-doc-busy';strip.setAttribute('role','status');strip.setAttribute('aria-live','polite');
     const dot=document.createElement('span');dot.className='chat-doc-busy-dot';dot.setAttribute('aria-hidden','true');
     strip.append(dot,document.createElement('span'));panel.querySelector('header').after(strip);
   }
   const label=busyLabel||document.querySelector('#chatProgress [data-progress-label]')?.textContent.trim()||'요청 처리 중';
   // 작성 카드 — 문서에 쓰는 중이면 AURA 커서와 「지금 쓰는 절 · 쓴 절 수」
   const writing=busyWriting&&busyWriting.state==='writing';
   strip.classList.toggle('is-writing',writing);
   const done=(busyWriting?.done||[]).length;
   if(writing)docCursorOn('AURA · '+(busyWriting.section?`「${busyWriting.section}」 작성 중`:'문서 작성 중'));else docCursorOff();
   strip.lastElementChild.textContent=writing&&busyWriting.section
     ?`AURA가 「${busyWriting.section}」 작성 중`+(done?` · 이번 요청에서 ${done}개 씀`:'')
     :label+(done?` · 이번 요청에서 ${done}개 씀`:'');
 }
 workspace.addEventListener('chat-progress',event=>{
   const label=event.detail?.label;
   if(typeof label!=='string'||!label.trim())return;
   busyWriting=event.detail?.writing||null;
   agentBusy=true;busyLabel=label.trim();paintBusy();
 });
 // 답이 막 끝난 대화에서 에이전트가 쓴 절 — 답 머리의 [제목] 표시를 먼저, 없으면 답에 나온 절 제목 중
 // 가장 뒤에 나온 것(「문서에서 보기」 단추와 같은 기준). 사례별 규칙 없이 문서의 절 목록과만 맞춘다.
 function writtenSection(){
   const messages=workspace.querySelectorAll('.turn.assistant .msg.assistant');
   const message=messages[messages.length-1];if(!message||!linked)return null;
   const normalize=value=>String(value||'').replace(/\s+/g,' ').trim();
   const sections=(linked.sections||[]).filter(s=>s.heading_id&&s.heading);
   const text=message.innerText||message.textContent||'';
   let found=null;
   for(const mark of text.matchAll(/\[([^\[\]\n]{2,80})\]/g)){
     const label=normalize(mark[1]);
     const hit=sections.find(s=>{const heading=normalize(s.heading);return heading===label||(label.length>=6&&heading.startsWith(label));});
     if(hit)found=hit;
   }
   if(found)return found;
   const flat=normalize(text);let best=null,at=-1;
   sections.filter(s=>normalize(s.heading).length>=6).forEach(s=>{const i=flat.lastIndexOf(normalize(s.heading));if(i>at){at=i;best=s;}});
   return best;
 }
 // 답이 도착하면 연결 정보를 다시 읽고(절·연결 문서가 바뀌었을 수 있다) 편집기를 새로 불러온다.
 // 쓴 절을 알면 그 자리로 옮긴다.
 async function afterAnswer(){
   const session=sid;
   let fresh=null;
   try{fresh=await api('/api/chat-documents/'+session);}catch(_){}
   if(session!==sid)return;
   if(fresh){
     const before=linked?.doc;
     currentDoc=fresh.connected?fresh.doc:null;
     if(fresh.connected){
       linked=fresh;
       // 새로 생긴 작업본은 checkCreatedDocument 와 같이 바로 연다
       if(!before){show();}
       else if(before!==fresh.doc&&isEditor()){const open=!panel.hidden;clearPanel();show();if(!open)visibility(false);}
       else buildSectionJump();
     }
   }
   if(!isEditor()||panel.hidden)return;
   offerJump(writtenSection());
 }
 // 답이 끝난 뒤 편집기 위 한 줄 — 쓴 절 이름과 「그 위치로 이동」. 구글 편집기는 고친 내용을 바로 받으므로
 // 답마다 다시 불러오지 않고(20~40초 빈 화면), 누를 때만 그 위치로 다시 연다.
 function offerJump(section){
   panel?.querySelector('.chat-doc-done')?.remove();
   if(!section||!isEditor())return;
   const strip=document.createElement('div');strip.className='chat-doc-done';strip.setAttribute('role','status');
   const text=document.createElement('span');text.textContent=`「${section.heading}」에 썼습니다. 편집기에 바로 반영됩니다.`;
   const go=document.createElement('button');go.type='button';go.className='secondary';go.textContent='그 위치로 이동';
   go.onclick=()=>{strip.remove();jumpToSection(section);};
   const close=document.createElement('button');close.type='button';close.className='doc-icon-button';close.textContent='×';close.title='알림 닫기';close.setAttribute('aria-label','알림 닫기');close.onclick=()=>strip.remove();
   strip.append(text,go,close);panel.querySelector('header').after(strip);
 }
 workspace.addEventListener('chat-session-updated',event=>{
   const next=workspace.dataset.session;
   // 같은 대화의 답변 갱신은 iframe·분할 비율을 그대로 둔다. 문서함이 열려 있으면 목록만 다시 읽는다
   // (새 첨부, 에이전트가 근거로 불러온 문서가 제 갈래에 바로 선다).
   if(next===sid){
     const waiting=Boolean(event.detail?.waiting), wasBusy=agentBusy;
     agentBusy=waiting;if(waiting){busyLabel='';busyWriting=null;panel?.querySelector('.chat-doc-done')?.remove();}
     paintBusy();
     if(!waiting&&panel?.classList.contains('chat-file-library'))refreshLibrary?.();
     if(!waiting&&wasBusy)afterAnswer();
     return;
   }
   agentBusy=Boolean(event.detail?.waiting);busyLabel='';
   sid=next;linked=null;currentDoc=null;loadError='';
   visibility(false);clearPanel();
   ratio=Number(sessionStorage.getItem('chatDocRatio:'+sid))||DOC_DEFAULT;
   initializeSession();
 });
 initializeSession();
 window.addEventListener('pagehide',()=>clearTimeout(watch));
 opener.onclick=()=>{
   if(panel&&!panel.hidden&&!closing)return;
   if(panel)visibility(true);
   else if(hwpFile)showHwp(hwpFile);
   else showFiles();
 };
})();
