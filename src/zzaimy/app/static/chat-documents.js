(() => {
 const workspace=document.getElementById('chatWorkspace'), opener=document.getElementById('chatDocumentOpen');
 if(!workspace||!opener)return;
 const sid=workspace.dataset.session, main=workspace.parentElement;
 let linked=null, panel=null;
 let fitObserver=null;
 function clearPanel(){fitObserver?.disconnect();fitObserver=null;panel?.remove();panel=null;}
 function fitEditor(){
   const frame=panel.querySelector('iframe');if(!frame||frame.parentElement.classList.contains('chat-doc-viewport'))return;
   const viewport=document.createElement('div');viewport.className='chat-doc-viewport';
   frame.before(viewport);viewport.append(frame);
   const toggle=document.createElement('button');toggle.type='button';toggle.className='secondary';toggle.dataset.fit='';toggle.textContent='너비 맞춤';toggle.setAttribute('aria-pressed','true');
   panel.querySelector('header').insertBefore(toggle,panel.querySelector('[data-hide]'));
   let fit=true;
   const resize=()=>{
     const width=viewport.clientWidth,height=viewport.clientHeight;if(!width||!height)return;
     const scale=fit?Math.min(1,width/1100):1;
     frame.style.cssText=`position:absolute;left:0;top:0;width:${width/scale}px;height:${height/scale}px;transform:scale(${scale});transform-origin:top left;max-width:none;`;
   };
   toggle.onclick=()=>{fit=!fit;toggle.setAttribute('aria-pressed',String(fit));toggle.textContent=fit?'너비 맞춤':'원래 크기';resize();};
   fitObserver=new ResizeObserver(resize);fitObserver.observe(viewport);
 }
 const divider=document.createElement('div');
 divider.className='chat-doc-divider';divider.tabIndex=0;
 divider.setAttribute('role','separator');divider.setAttribute('aria-orientation','vertical');
 divider.setAttribute('aria-label','채팅과 문서 너비 조절');
 divider.setAttribute('aria-controls','chatWorkspace chatDocumentPanel');
 divider.title='드래그로 너비 조절 · 두 번 클릭하면 반반';
 let ratio=Number(sessionStorage.getItem('chatDocRatio:'+sid))||50;
 function setRatio(value,persist=true){
   const available=Math.max(main.clientWidth-8,1), minimum=Math.min(45,Math.max(20,260/available*100));
   ratio=Math.max(minimum,Math.min(100-minimum,value));
   main.style.setProperty('--chat-width',ratio+'%');
   divider.setAttribute('aria-valuemin',String(Math.ceil(minimum)));
   divider.setAttribute('aria-valuemax',String(Math.floor(100-minimum)));
   divider.setAttribute('aria-valuenow',String(Math.round(ratio)));
   divider.setAttribute('aria-valuetext','채팅 '+Math.round(ratio)+'%, 문서 '+Math.round(100-ratio)+'%');
   if(persist)sessionStorage.setItem('chatDocRatio:'+sid,String(ratio));
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
 new ResizeObserver(()=>{if(main.classList.contains('chat-doc-open'))setRatio(ratio,false);}).observe(main);
 function reveal(){
   // 목표 폭으로 바로 표시한다. 채팅을 전체 폭으로 되돌리는 중간 프레임은 없다.
   setRatio(ratio,false);
   panel.hidden=false;main.classList.add('chat-doc-open');
 }
 function conceal(){
   if(!panel||panel.hidden)return;
   panel.hidden=true;main.classList.remove('chat-doc-open');
 }
 function visibility(visible){
   if(!panel)return;
   if(visible){
     const close=panel.querySelector('header [data-hide],header [data-close]');
     if(close){close.textContent='×';close.title='문서 패널 닫기';close.setAttribute('aria-label','문서 패널 닫기');close.classList.add('doc-icon-button');}
     const back=panel.querySelector('header [data-list]');
     if(back){back.textContent='← 문서함';back.title='문서함으로 돌아가기';back.setAttribute('aria-label',back.title);back.classList.remove('doc-icon-button');}
   }
   if(visible)reveal();else conceal();
   if(!visible)stopResize();
   opener.setAttribute('aria-pressed',String(visible));
   opener.title=visible?'문서 패널 접기':'문서 패널 열기';
   opener.setAttribute('aria-label',opener.title);
   sessionStorage.setItem('chatDocHidden:'+sid,visible?'0':'1');
 }
 async function api(url,options={}){const response=await fetch(url,{cache:'no-store',...options});if(!response.ok||response.redirected){let message='요청을 처리하지 못했습니다.';try{const data=await response.json();if(typeof data.detail==='string')message=data.detail;}catch(_){}throw new Error(message);}return response.json();}
 function dialog(title){const d=document.createElement('dialog');d.className='chat-doc-dialog';d.setAttribute('aria-label',title);d.innerHTML='<h2></h2><form><div data-fields></div><p role="status"></p><footer><button type="button" class="secondary" data-cancel>취소</button><button type="submit" data-submit>연결</button></footer></form>';d.querySelector('h2').textContent=title;d.querySelector('[data-fields]').style.display='grid';d.querySelector('[data-fields]').style.gap='16px';d.querySelector('[data-cancel]').onclick=()=>d.close();d.addEventListener('close',()=>{d.remove();opener.focus();});document.body.append(d);d.showModal();return d;}
 async function connect(){const d=dialog('다른 폴더에서 문서 가져오기'), f=d.querySelector('form'), fields=d.querySelector('[data-fields]'), status=d.querySelector('[role=status]'), submit=d.querySelector('[data-submit]');submit.disabled=true;submit.textContent='선택한 문서 가져오기';status.textContent='연결 계정 확인 중…';
 try{const data=await api('/api/chat-documents/accounts');if(!d.isConnected)return;
 fields.innerHTML='<label>Google 계정<select name="account" required></select></label><input type="hidden" name="doc"><div class="drive-picker-path"></div><div class="drive-picker-list"></div><p class="muted">Google Docs 문서를 선택하면 이 대화에서 작업할 수 있습니다. 다른 형식은 문서 추가를 이용해 주세요. 원본은 이동하거나 삭제하지 않습니다.</p>';
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
       if(!folder&&!doc)b.title='현재 선택 가능 형식: Google Docs';
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
 function show(){if(panel&&panel.classList.contains('chat-doc-editor')){visibility(true);return;}if(panel)clearPanel();panel=document.createElement('section');panel.className='chat-doc-editor';panel.setAttribute('aria-label','연결된 Google Docs');panel.innerHTML='<header><strong></strong><label class="chat-doc-toolbar"><input type="checkbox" data-toolbar> 서식 도구</label><a target="_blank" rel="noopener">새 창 ↗</a><button type="button" class="secondary" data-hide>닫기</button></header><iframe title="Google Docs 편집기"></iframe><div class="chat-doc-note">편집기가 열리지 않으면 새 창에서 편집하세요. <button type="button" class="act-btn" data-unlink>문서 연결 해제</button></div>';
 panel.querySelector('strong').textContent=linked.title;panel.querySelector('iframe').src=linked.embed_url;panel.querySelector('a').href=linked.embed_url_toolbar||linked.embed_url;panel.querySelector('[data-toolbar]').onchange=e=>{panel.querySelector('iframe').src=e.target.checked?(linked.embed_url_toolbar||linked.embed_url):linked.embed_url;};panel.querySelector('[data-hide]').onclick=()=>{visibility(false);opener.focus();};const insBtn=panel.querySelector('[data-insert]');if(insBtn)insBtn.onclick=insert;
 panel.querySelector('[data-unlink]').onclick=()=>{const d=dialog('문서 연결 해제');d.querySelector('[role=status]').textContent='원본 문서와 대화는 유지됩니다. 이 대화에서 문서 연결을 해제할까요?';d.querySelector('[data-submit]').textContent='연결 해제';d.querySelector('form').onsubmit=async e=>{e.preventDefault();d.querySelector('[data-submit]').disabled=true;try{await api('/api/chat-documents/'+sid,{method:'DELETE'});visibility(false);panel.remove();panel=null;linked=null;opener.title='문서 열기';opener.setAttribute('aria-label',opener.title);opener.removeAttribute('aria-controls');d.close();}catch(error){d.querySelector('[role=status]').textContent=error.message;d.querySelector('[data-submit]').disabled=false;}};};main.prepend(panel);visibility(true);}
 // 플랫폼 문서(첨부·접수·기준)의 구글 열람본을 같은 자리(iframe)에 연다 — 연결 문서와 달리 에이전트 편집 대상은 아니다
 function showViewer(v){
   clearPanel();panel=document.createElement('section');panel.className='chat-doc-editor chat-doc-viewer';panel.setAttribute('aria-label','문서 열람');
   panel.innerHTML='<header><button type="button" class="secondary" data-list>목록</button><strong></strong><a target="_blank" rel="noopener">새 창 ↗</a><button type="button" class="secondary" data-hide>닫기</button></header><iframe title="문서 열람"></iframe>';
   panel.querySelector('strong').textContent=v.title;panel.querySelector('iframe').src=v.embed_url;panel.querySelector('a[target]').href=v.url;
   const state=document.createElement('span');state.className='chat-doc-state';
   state.textContent=[v.original_format?originalLabel(v.original_format):'문서',v.is_doc?'변환본 보기':'미리보기','읽기 전용'].join(' · ');
   panel.querySelector('header').after(state);
   panel.querySelector('[data-list]').onclick=showFiles;panel.querySelector('[data-hide]').onclick=()=>{visibility(false);opener.focus();};
   // 독스 문서면 "이 문서로 작업" — 복제본을 만들어 이 대화의 작업 문서로 잇는다(원본 서식은 그대로)
   if(v.is_doc&&sid){const work=document.createElement('button');work.type='button';work.className='secondary';work.textContent='이 문서로 작업';work.title='복제본을 만들어 이 대화에 연결합니다';
     work.onclick=async()=>{work.disabled=true;work.textContent='복제본 만드는 중…';try{await api('/api/chat/'+sid+'/work-on/'+v.doc_id,{method:'POST'});const d=await api('/api/chat-documents/'+sid);linked=d;clearPanel();setRatio(50);show();location.reload();}catch(e){work.textContent=e.message;}};
     panel.querySelector('header').insertBefore(work,panel.querySelector('[data-hide]'));}
   main.append(panel);if(!divider.isConnected)main.append(divider);setRatio(50,false);visibility(true);fitEditor();
   const fit=panel.querySelector('[data-fit]');if(fit&&!/document\//.test(v.embed_url))fit.click();   // 시트·슬라이드·PDF 는 원래 크기
   panel.querySelectorAll('header a,header button').forEach(el=>el.classList.add('chat-doc-mini'));
 }
 const createPanel=show;
 function arrangeHeader(){
   const header=panel.querySelector('header');if(header.classList.contains('doc-header-refined'))return;
   header.classList.add('doc-header-refined');
   const titleRow=document.createElement('div');titleRow.className='doc-title-row';
   const tools=document.createElement('div');tools.className='doc-view-tools';tools.setAttribute('aria-label','문서 보기 설정');
   const back=header.querySelector('[data-list]'),title=header.querySelector('strong'),close=header.querySelector('[data-hide]');
   back.textContent='‹';back.title='파일 목록으로';back.setAttribute('aria-label','파일 목록으로');back.classList.add('doc-icon-button');
   title.title=title.textContent;
   close.textContent='×';close.title='문서 패널 닫기';close.setAttribute('aria-label','문서 패널 닫기');close.classList.add('doc-icon-button');
   titleRow.append(back,title,close);
   const more=document.createElement('details');more.className='doc-more';
   const summary=document.createElement('summary');summary.textContent='•••';summary.title='문서 더보기';summary.setAttribute('aria-label','문서 더보기');
   const menu=document.createElement('div');menu.className='doc-more-actions';
   header.querySelectorAll('a').forEach(a=>menu.append(a));
   const unlink=panel.querySelector('[data-unlink]');menu.append(unlink);
   more.append(summary,menu);
   more.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();more.open=false;summary.focus();}});
   menu.addEventListener('click',e=>{if(e.target.closest('a,button'))more.open=false;});
   tools.append(header.querySelector('.chat-doc-toolbar'),header.querySelector('[data-fit]'),more);
   header.append(titleRow,tools);
   panel.querySelector('.chat-doc-note')?.remove();
 }
 document.addEventListener('pointerdown',e=>{const more=panel?.querySelector('.doc-more[open]');if(more&&!more.contains(e.target))more.open=false;});
 show=()=>{
   if(panel&&!panel.querySelector('iframe'))clearPanel();
   createPanel();
   fitEditor();
   if(!panel.querySelector('[data-list]')){
     const back=document.createElement('button');back.type='button';back.className='secondary';back.dataset.list='';back.textContent='목록';back.onclick=showFiles;
     panel.querySelector('header').prepend(back);
   }
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
   arrangeHeader();
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
 function originalLabel(format){const ext=(format||'').replace(/^\./,'').toLowerCase();return ['hwp','hwpx'].includes(ext)?'한글 ('+ext.toUpperCase()+')':ext?ext.toUpperCase():'문서';}
 function fileCard(button,name,subtitle,imageUrl,originalFormat){
   button.classList.add('chat-file-card');button.title=name;button.replaceChildren();
   const visual=document.createElement('span');visual.className='chat-file-visual';
   const format=originalLabel(originalFormat||name.match(/\.([a-z0-9]+)$/i)?.[1]);visual.textContent=format;
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
 async function showFiles(){
   clearPanel();showEmpty();setRatio(70,false);
   panel.classList.add('chat-file-library');panel.querySelector('header strong').textContent='문서함';
   const current=panel,body=panel.querySelector('.chat-doc-empty');body.className='chat-doc-files';body.replaceChildren();
   const status=document.createElement('p');status.setAttribute('role','status');status.textContent='파일을 불러오는 중…';body.append(status);
   const workHeading=document.createElement('h3');workHeading.textContent='작업 문서';
   const workDocs=document.createElement('div');workDocs.className='chat-file-list chat-work-files';
   const workNote=document.createElement('p');workNote.className='chat-file-section-note';workNote.textContent='Google Drive 문서를 열어 채팅과 함께 작업합니다.';
   const docsHeading=document.createElement('h3');docsHeading.textContent='참고·첨부 문서';
   const docs=document.createElement('div');docs.className='chat-file-list';
   const referenceNote=document.createElement('p');referenceNote.className='chat-file-section-note';referenceNote.textContent='플랫폼의 접수·기준·첨부 문서와 참고 파일입니다.';
   const imagesHeading=document.createElement('h3');imagesHeading.textContent='콘텐츠';
   const images=document.createElement('div');images.className='chat-image-grid';
   body.append(workHeading,workNote,workDocs,docsHeading,referenceNote,docs,imagesHeading,images);
   const imageFile=name=>/\.(png|jpe?g|gif|webp|bmp)$/i.test(name);
   try{
     const data=sid?await api('/api/chat-documents/'+sid+'/files').catch(error=>({files:[],error:error.message})):{files:[]};if(panel!==current)return;
     status.textContent=data.files.length?'':'연결된 폴더에 표시할 파일이 없습니다.';
     if(data.folder_url){const folder=document.createElement('a');folder.href=data.folder_url;folder.target='_blank';folder.rel='noopener';folder.textContent='폴더 열기 ↗';panel.querySelector('header').insertBefore(folder,panel.querySelector('[data-close]'));}
     for(const file of data.files){
       const button=document.createElement('button');button.type='button';button.className='chat-doc-file';
       const isImage=file.mime_type.startsWith('image/')||imageFile(file.name);
       const editable=file.mime_type==='application/vnd.google-apps.document',active=editable&&linked?.doc===file.id;
       fileCard(button,file.name,isImage?'이미지 · Google Drive':editable?(active?'현재 작업 중 · Google Docs':'Google Docs · 작업 문서'):'Google Drive · 참고 파일',null,editable?'Google Docs':null);
       if(active){button.classList.add('chat-file-current');button.setAttribute('aria-current','true');}
       (isImage?images:editable?workDocs:docs).append(button);
       button.onclick=async()=>{
         if(file.mime_type!=='application/vnd.google-apps.document'){
           const path=file.mime_type==='application/vnd.google-apps.folder'?'drive/folders/'+file.id:'file/d/'+file.id+'/view';
           window.open('https://drive.google.com/'+path,'_blank','noopener');return;
         }
         // 이미 연결된 작업본은 다시 연결 요청을 보내지 않고 기존 정보로 연다.
         if(linked?.doc===file.id&&linked?.account===data.account){clearPanel();setRatio(50,false);show();return;}
         button.disabled=true;status.textContent='문서를 여는 중…';
         try{const form=new FormData();form.set('session_id',sid);form.set('doc',file.id);form.set('account',data.account);
           await api('/api/chat-documents/connect',{method:'POST',body:form});const document=await api('/api/chat-documents/'+sid);
           if(panel!==current)return;linked=document;clearPanel();setRatio(50);show();
         }catch(error){status.textContent=error.message;button.disabled=false;}
       };
     }
     // 플랫폼 문서 — 이 대화의 첨부, 프로젝트의 기준·접수 문서. 클릭하면 구글 열람본(없으면 만들어서)으로 연다.
     if(sid){try{const mine=await api('/api/chat/'+sid+'/documents');if(panel!==current)return;
       if(mine.documents.length){status.textContent='';
         for(const d of mine.documents){const a=document.createElement('button');a.type='button';a.className='chat-doc-file';a.title=(d.kind||'')+(d.status?' · '+d.status:'');
           const isImage=imageFile(d.name),imageUrl='/doc/'+encodeURIComponent(d.id)+'/original';
           fileCard(a,d.name,[d.group,d.kind,d.status,'읽기 전용'].filter(Boolean).join(' · '),isImage?imageUrl:null,d.original_format);(isImage?images:docs).append(a);
           a.onclick=async()=>{a.disabled=true;status.textContent='구글 열람본을 여는 중…';
             if(isImage){a.disabled=false;status.textContent='';previewImage(d.name,imageUrl,a);return;}
             try{const g=await api('/api/doc/'+d.id+'/google');if(panel!==current)return;const preview='https://drive.google.com/file/d/'+encodeURIComponent(g.id)+'/preview';showViewer({title:d.name,embed_url:preview,url:preview,original_format:d.original_format,page:d.page,doc_id:d.id,is_doc:g.mime==='application/vnd.google-apps.document'});}
             catch(error){status.textContent=error.message;a.disabled=false;}};}}
     }catch(error){status.textContent='프로젝트 문서를 불러오지 못했습니다. 문서함을 다시 열어 주세요.';}}
     if(data.error)status.textContent='Drive 목록: '+data.error;
     else if(workDocs.children.length||docs.children.length||images.children.length)status.textContent='';
     workHeading.textContent='작업 문서 '+workDocs.children.length;
     docsHeading.textContent='참고·첨부 문서 '+docs.children.length;
     if(!workDocs.children.length){const empty=document.createElement('p');empty.className='chat-file-empty';empty.textContent=data.error?'작업 문서 목록을 불러오지 못했습니다.':'아직 작업 문서가 없습니다.';workDocs.append(empty);}
     if(!docs.children.length){const empty=document.createElement('p');empty.className='chat-file-empty';empty.textContent='표시할 참고·첨부 문서가 없습니다.';docs.append(empty);}
     imagesHeading.textContent='콘텐츠 '+images.children.length;
     if(!images.children.length){const empty=document.createElement('p');empty.className='chat-file-empty';empty.textContent='첨부된 이미지가 없습니다.';images.append(empty);}
     const connectButton=document.createElement('button');connectButton.type='button';connectButton.className='secondary';connectButton.textContent='다른 폴더에서 가져오기';connectButton.onclick=connect;workDocs.after(connectButton);
   }catch(error){status.textContent=error.message;const retry=document.createElement('button');retry.textContent='다시 시도';retry.onclick=showFiles;body.append(retry);}
 }
 const initialized=sid?api('/api/chat-documents/'+sid).then(data=>{if(data.connected)linked=data;}).catch(error=>{loadError=error.message;}):Promise.resolve();
 // 새로 생성된 문서만 자동으로 연다. 기존 문서는 문서 버튼에서 목록을 먼저 표시한다.
 let watch=null;
 async function checkCreatedDocument(){
   if(linked||!workspace.isConnected)return;
   try{
     const data=await api('/api/chat-documents/'+sid);
     // 요청 중 사용자가 목록에서 다른 문서를 선택했다면 그 선택을 보존한다.
     if(!linked&&data.connected){linked=data;setRatio(50);show();}
   }catch(_){}
   if(!linked&&workspace.isConnected)watch=setTimeout(checkCreatedDocument,6000);
 }
 initialized.then(()=>{if(sid&&!linked)watch=setTimeout(checkCreatedDocument,6000);});
 window.addEventListener('pagehide',()=>clearTimeout(watch));
 opener.onclick=async()=>{await initialized;if(panel&&!panel.hidden)visibility(false);else showFiles();};
})();
