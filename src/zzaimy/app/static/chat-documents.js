(() => {
 const workspace=document.getElementById('chatWorkspace'), opener=document.getElementById('chatDocumentOpen');
 if(!workspace||!opener)return;
 const sid=workspace.dataset.session, main=workspace.parentElement;
 let linked=null, panel=null;
 const divider=document.createElement('div');
 divider.className='chat-doc-divider';divider.tabIndex=0;
 divider.setAttribute('role','separator');divider.setAttribute('aria-orientation','vertical');
 divider.setAttribute('aria-label','채팅과 문서 너비 조절');
 divider.setAttribute('aria-controls','chatWorkspace chatDocumentPanel');
 divider.title='드래그로 너비 조절 · 두 번 클릭하면 반반';
 let ratio=Number(sessionStorage.getItem('chatDocRatio:'+sid))||50;
 function setRatio(value){
   const available=Math.max(main.clientWidth-8,1), minimum=Math.min(45,Math.max(20,260/available*100));
   ratio=Math.max(minimum,Math.min(100-minimum,value));
   main.style.setProperty('--chat-width',ratio+'%');
   divider.setAttribute('aria-valuemin',String(Math.ceil(minimum)));
   divider.setAttribute('aria-valuemax',String(Math.floor(100-minimum)));
   divider.setAttribute('aria-valuenow',String(Math.round(ratio)));
   divider.setAttribute('aria-valuetext','채팅 '+Math.round(ratio)+'%, 문서 '+Math.round(100-ratio)+'%');
   sessionStorage.setItem('chatDocRatio:'+sid,String(ratio));
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
 divider.addEventListener('dblclick',()=>setRatio(50));
 divider.addEventListener('keydown',event=>{
   if(!['ArrowLeft','ArrowRight','Home','End','Enter'].includes(event.key))return;
   event.preventDefault();setRatio(event.key==='Enter'?50:event.key==='Home'?0:event.key==='End'?100:ratio+(event.key==='ArrowLeft'?-2:2));
 });
 new ResizeObserver(()=>{if(main.classList.contains('chat-doc-open'))setRatio(ratio);}).observe(main);
 function visibility(visible){
   if(!panel)return;
   panel.hidden=!visible;main.classList.toggle('chat-doc-open',visible);
   if(!visible)stopResize();
   opener.setAttribute('aria-pressed',String(visible));
   opener.title=visible?'문서 패널 접기':'문서 패널 열기';
   opener.setAttribute('aria-label',opener.title);
   sessionStorage.setItem('chatDocHidden:'+sid,visible?'0':'1');
 }
 async function api(url,options={}){const response=await fetch(url,{cache:'no-store',...options});if(!response.ok||response.redirected){let message='요청을 처리하지 못했습니다.';try{const data=await response.json();if(typeof data.detail==='string')message=data.detail;}catch(_){}throw new Error(message);}return response.json();}
 function dialog(title){const d=document.createElement('dialog');d.className='chat-doc-dialog';d.setAttribute('aria-label',title);d.innerHTML='<h2></h2><form><div data-fields></div><p role="status"></p><footer><button type="button" class="secondary" data-cancel>취소</button><button type="submit" data-submit>연결</button></footer></form>';d.querySelector('h2').textContent=title;d.querySelector('[data-fields]').style.display='grid';d.querySelector('[data-fields]').style.gap='16px';d.querySelector('[data-cancel]').onclick=()=>d.close();d.addEventListener('close',()=>{d.remove();opener.focus();});document.body.append(d);d.showModal();return d;}
 async function connect(){const d=dialog('Google Docs 연결'), f=d.querySelector('form'), fields=d.querySelector('[data-fields]'), status=d.querySelector('[role=status]'), submit=d.querySelector('[data-submit]');submit.disabled=true;status.textContent='연결 계정 확인 중…';
 try{const data=await api('/api/chat-documents/accounts');if(!d.isConnected)return;
 fields.innerHTML='<label>Google 계정<select name="account" required></select></label><label>문서 주소<input name="doc" required placeholder="Google Docs 주소 또는 문서 ID"></label>';
 data.accounts.filter(a=>a.docs_ok).forEach(a=>{const o=document.createElement('option');o.value=a.email;o.textContent=a.email;fields.querySelector('select').append(o);});
 if(!fields.querySelector('option')){fields.replaceChildren();status.textContent='문서 편집 권한이 있는 연결 계정이 없습니다.';const a=document.createElement('a');a.href=opener.dataset.connections;a.textContent='계정 연결 설정';fields.append(a);submit.hidden=true;return;}
 status.textContent='선택한 문서가 이 대화에 연결됩니다.';submit.disabled=false;
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
 function show(){if(panel){panel.hidden=false;main.classList.add('chat-doc-open');return;}panel=document.createElement('section');panel.className='chat-doc-editor';panel.setAttribute('aria-label','연결된 Google Docs');panel.innerHTML='<header><strong></strong><label>문서 폭<input type="range" min="40" max="70" value="60" aria-label="문서 영역 너비"></label><a target="_blank" rel="noopener">새 창 ↗</a><button type="button" class="secondary" data-hide>닫기</button></header><iframe title="Google Docs 편집기"></iframe><div class="chat-doc-note">편집기가 열리지 않으면 새 창에서 편집하세요. <button type="button" class="act-btn" data-unlink>문서 연결 해제</button></div>';
 panel.querySelector('strong').textContent=linked.title;panel.querySelector('iframe').src=linked.embed_url;panel.querySelector('a').href=linked.embed_url;panel.querySelector('input').oninput=e=>main.style.setProperty('--document-width',e.target.value+'%');panel.querySelector('[data-hide]').onclick=()=>{panel.hidden=true;main.classList.remove('chat-doc-open');sessionStorage.setItem('chatDocHidden:'+sid,'1');};const insBtn=panel.querySelector('[data-insert]');if(insBtn)insBtn.onclick=insert;
 panel.querySelector('[data-unlink]').onclick=()=>{const d=dialog('문서 연결 해제');d.querySelector('[role=status]').textContent='원본 문서와 대화는 유지됩니다. 이 대화에서 문서 연결을 해제할까요?';d.querySelector('[data-submit]').textContent='연결 해제';d.querySelector('form').onsubmit=async e=>{e.preventDefault();d.querySelector('[data-submit]').disabled=true;try{await api('/api/chat-documents/'+sid,{method:'DELETE'});visibility(false);panel.remove();panel=null;linked=null;opener.title='문서 열기';opener.setAttribute('aria-label',opener.title);opener.removeAttribute('aria-controls');d.close();}catch(error){d.querySelector('[role=status]').textContent=error.message;d.querySelector('[data-submit]').disabled=false;}};};main.prepend(panel);main.classList.add('chat-doc-open');}
 const createPanel=show;
 show=()=>{
   createPanel();
   if(!panel.querySelector('[data-folder]')){
     const folder=document.createElement('a');folder.dataset.folder='';folder.textContent='폴더 열기 ↗';
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
   panel.querySelector('[data-hide]').onclick=()=>{visibility(false);opener.focus();};
   visibility(true);
 };
 opener.setAttribute('aria-pressed','false');
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
 const initialized=sid?api('/api/chat-documents/'+sid).then(data=>{if(data.connected){linked=data;opener.title='문서 패널 열기';opener.setAttribute('aria-label',opener.title);if(sessionStorage.getItem('chatDocHidden:'+sid)!=='1')show();}}).catch(error=>{loadError=error.message;}):Promise.resolve();
 opener.onclick=async()=>{await initialized;if(panel&&!panel.hidden)visibility(false);else if(linked)show();else showEmpty();};
})();
