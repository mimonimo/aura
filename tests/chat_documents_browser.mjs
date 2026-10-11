import {spawn} from 'node:child_process';
import {readFile,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const root=process.cwd()+'/src/zzaimy/app/static/';
const chrome=spawn(process.env.CHROME_BIN || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',['--headless','--disable-gpu','--no-first-run','--remote-debugging-port=0','--user-data-dir=/private/tmp/aura-fit-browser','about:blank'],{stdio:['ignore','ignore','pipe']});
let ws;
try {
 const url=await new Promise((resolve,reject)=>{let out='';chrome.stderr.on('data',b=>{out+=b;const m=out.match(/DevTools listening on (ws:\/\/\S+)/);if(m)resolve(m[1]);});setTimeout(()=>reject(Error('startup')),15000).unref();});
 ws=new WebSocket(url);await new Promise(r=>ws.addEventListener('open',r,{once:true}));
 let id=0,session;const pending=new Map();
 ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result);}};
 const send=(method,params={})=>new Promise((resolve,reject)=>{const i=++id;pending.set(i,{resolve,reject});ws.send(JSON.stringify({id:i,method,params,...(session?{sessionId:session}:{})}));});
 const t=await send('Target.createTarget',{url:'about:blank'});session=(await send('Target.attachToTarget',{targetId:t.targetId,flatten:true})).sessionId;
 await send('Emulation.setDeviceMetricsOverride',{width:1600,height:1000,deviceScaleFactor:1,mobile:false});
 await send('Page.enable');await send('Page.navigate',{url:process.env.UI_TEST_URL || 'http://localhost:8894/chat'});await new Promise(r=>setTimeout(r,800));
 const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
 const css=(await readFile(root+'chat-documents.css','utf8'))+(await readFile(root+'platform-spaces.css','utf8'));const js=await readFile(root+'chat-documents.js','utf8');
 await evaluate(`document.head.innerHTML='';document.body.innerHTML='<main style="display:flex;height:900px;width:1500px"><section id="chatWorkspace" data-session="1"><button id="chatDocumentOpen">문서</button></section></main>';const style=document.createElement('style');style.textContent=${JSON.stringify(css)};document.head.append(style);window.fetch=async(url)=>({ok:true,redirected:false,json:async()=>url.endsWith('/files')?{account:'test@example.test',folder_url:'https://drive.google.com/drive/folders/test',files:[{id:'docA',name:'시험 문서',mime_type:'application/vnd.google-apps.document'}]}:url.endsWith('/folder')?{url:null}:{connected:true,title:'시험 문서',embed_url:'about:blank',sections:[]}});`);
 await evaluate(`const fixtureFetch=window.fetch;window.fetch=async(...args)=>{const response=await fixtureFetch(...args);const json=response.json;response.json=async()=>{const data=await json();if(data.connected){data.doc='docA';data.sections=[{index:1,heading:'1. 추진 배경',heading_id:'h.test',tab_id:'t.test'}];}return data;};return response;};`);
 await evaluate(js);await evaluate(`document.getElementById('chatDocumentOpen').click()`);await new Promise(r=>setTimeout(r,150));
 await evaluate(`const output=document.createElement('a');output.dataset.documentLink='true';output.href='https://docs.google.com/document/d/test_output/edit';output.textContent='[문서 1]';workspaceLink=document.getElementById('chatWorkspace');workspaceLink.append(output);output.click();`);
 assert.equal(await evaluate(`document.querySelector('.chat-doc-viewer iframe').src`),'https://docs.google.com/document/d/test_output/preview');
 assert.equal(await evaluate(`Array.from(document.querySelectorAll('.chat-doc-viewer button')).some(b=>b.textContent==='이 문서로 작업')`),false);
 await evaluate(`document.querySelector('.chat-doc-viewer [data-list]').click()`);await new Promise(r=>setTimeout(r,150));
 assert.equal(await evaluate(`document.querySelectorAll('.chat-doc-file').length`),1);
 assert.equal(await evaluate(`document.querySelectorAll('iframe').length`),0);
 await new Promise(r=>setTimeout(r,400));
 assert.equal(await evaluate(`document.querySelector('main').style.getPropertyValue('--chat-width')`),'50%');
 await evaluate(`document.querySelector('.chat-doc-file').click()`);await new Promise(r=>setTimeout(r,400));
 const wide=await evaluate(`(()=>{const f=document.querySelector('iframe');return {width:f.getBoundingClientRect().width,scale:new DOMMatrix(getComputedStyle(f).transform).a,viewport:f.parentElement.clientWidth}})()`);
 assert.equal(await evaluate(`document.querySelector('main').style.getPropertyValue('--chat-width')`),'50%');
 assert.ok(Math.abs(wide.width-wide.viewport)<2);assert.ok(wide.scale<1);
 await evaluate(`const section=document.querySelector('[data-section-jump]');section.value='1';section.dispatchEvent(new Event('change'));`);
 assert.ok(await evaluate(`document.querySelector('iframe').src.includes('heading=h.test')`));
 // 독스 주소 꼴: 탭은 질의, 제목은 # — 매번 다른 zzjump 로 새로 불러온다
 const jumped=await evaluate(`(()=>{const u=new URL(document.querySelector('iframe').src);return {tab:u.searchParams.get('tab'),hash:u.hash,jump:u.searchParams.get('zzjump')}})()`);
 assert.equal(jumped.tab,'t.test');assert.equal(jumped.hash,'#heading=h.test');assert.ok(jumped.jump);
 await evaluate(`const turn=document.createElement('div');turn.className='turn assistant';turn.innerHTML='<div class="msg assistant">1. 추진 배경 내용을 확인했습니다.</div>';document.getElementById('chatWorkspace').append(turn);`);await new Promise(r=>setTimeout(r,250));
 assert.equal(await evaluate(`document.querySelectorAll('[data-section-location]').length`),1);
 await evaluate(`const tb=document.querySelector('[data-toolbar]');tb.checked=true;tb.dispatchEvent(new Event('change'));`);await new Promise(r=>setTimeout(r,150));
 const formatted=await evaluate(`(()=>{const f=document.querySelector('iframe');return {scale:new DOMMatrix(getComputedStyle(f).transform).a,width:f.getBoundingClientRect().width,viewport:f.parentElement.clientWidth}})()`);
 assert.ok(formatted.scale<1&&formatted.scale<=wide.scale);assert.ok(Math.abs(formatted.width-formatted.viewport)<2);
 assert.equal(await evaluate(`document.querySelector('[data-fit]')`),null);
 await evaluate(`const tb2=document.querySelector('[data-toolbar]');tb2.checked=false;tb2.dispatchEvent(new Event('change'));`);await new Promise(r=>setTimeout(r,150));
 // 답 속 [문서 n] 이 이 대화의 작업 문서면 미리보기 대신 편집기를 연다
 await evaluate(`const own=document.createElement('a');own.dataset.documentLink='true';own.href='https://docs.google.com/document/d/docA/edit';own.textContent='[문서 2]';document.getElementById('chatWorkspace').append(own);own.click();`);await new Promise(r=>setTimeout(r,150));
 assert.equal(await evaluate(`Boolean(document.querySelector('.chat-doc-viewer'))`),false);
 assert.equal(await evaluate(`Boolean(document.querySelector('.chat-doc-editor [data-toolbar]'))`),true);
 // 열람 패널에서 「문서에서 보기」를 누르면 편집기로 바뀌고 머리줄에 null 이 붙지 않는다
 await evaluate(`const other=document.createElement('a');other.dataset.documentLink='true';other.href='https://docs.google.com/document/d/test_other/edit';other.textContent='[문서 3]';document.getElementById('chatWorkspace').append(other);other.click();`);await new Promise(r=>setTimeout(r,150));
 assert.equal(await evaluate(`Boolean(document.querySelector('.chat-doc-viewer'))`),true);
 await evaluate(`document.querySelector('[data-section-location]').click()`);await new Promise(r=>setTimeout(r,150));
 assert.equal(await evaluate(`Boolean(document.querySelector('.chat-doc-viewer'))`),false);
 assert.equal(await evaluate(`/null/.test(document.querySelector('.chat-doc-editor').textContent)`),false);
 assert.ok(await evaluate(`new URL(document.querySelector('.chat-doc-editor iframe').src).hash==='#heading=h.test'`));
 // 에이전트 작업 중에는 편집기 위에 현재 단계가 뜨고, 답이 오면 줄이 사라진다. 편집기는 다시 불러오지 않고
 // (구글 편집기는 고친 내용을 바로 받는다) 쓴 절 알림의 「그 위치로 이동」을 누를 때만 그 자리로 연다
 await evaluate(`document.getElementById('chatWorkspace').dispatchEvent(new CustomEvent('chat-session-updated',{detail:{session:'1',waiting:true}}));document.getElementById('chatWorkspace').dispatchEvent(new CustomEvent('chat-progress',{detail:{label:'「1. 추진 배경」 필요한 자료 찾는 중'}}));`);
 assert.equal(await evaluate(`document.querySelector('.chat-doc-busy').textContent`),'「1. 추진 배경」 필요한 자료 찾는 중');
 await evaluate(`window.beforeAnswer=document.querySelector('.chat-doc-editor iframe').src;const t=document.createElement('div');t.className='turn assistant';t.innerHTML='<div class="msg assistant">[1. 추진 배경]<br>새로 쓴 내용</div>';document.getElementById('chatWorkspace').append(t);document.getElementById('chatWorkspace').dispatchEvent(new CustomEvent('chat-session-updated',{detail:{session:'1',waiting:false}}));`);await new Promise(r=>setTimeout(r,200));
 assert.equal(await evaluate(`document.querySelector('.chat-doc-busy')`),null);
 assert.equal(await evaluate(`document.querySelector('.chat-doc-editor iframe').src===beforeAnswer`),true);
 assert.equal(await evaluate(`document.querySelector('.chat-doc-done span').textContent`),'「1. 추진 배경」에 썼습니다. 편집기에 바로 반영됩니다.');
 // 위치 이동 — 누르는 즉시 덮개 안내가 뜨고, load 가 오면 걷힌다. 앞선 이동의 load 는 새 안내를 지우지 않는다
 const jumpNote=await evaluate(`(()=>{document.querySelector('.chat-doc-done button.secondary').click();const n=document.querySelector('.chat-doc-viewport .chat-doc-loading');return {shown:Boolean(n&&!n.hidden),text:n?.textContent,busy:document.querySelector('.chat-doc-viewport').getAttribute('aria-busy')}})()`);
 assert.deepEqual(jumpNote,{shown:true,text:'「1. 추진 배경」 위치로 이동하는 중…',busy:'true'});
 assert.equal(await evaluate(`Boolean(document.querySelector('.chat-doc-done'))`),false);
 // 첫 load 로는 걷지 않는다(구글 편집기는 그 뒤에도 그리는 중) — 두 번째 load 가 오면 걷힌다
 assert.deepEqual(await evaluate(`(()=>{const n=document.querySelector('.chat-doc-viewport .chat-doc-loading');return {shown:!n.hidden,text:n.textContent}})()`),{shown:true,text:'편집기를 그리는 중…'});
 await evaluate(`document.querySelector('.chat-doc-editor iframe').dispatchEvent(new Event('load'))`);await new Promise(r=>setTimeout(r,1100));
 assert.equal(await evaluate(`document.querySelector('.chat-doc-viewport .chat-doc-loading').hidden`),true);
 assert.ok(await evaluate(`(()=>{const s=document.querySelector('.chat-doc-editor iframe').src;return s!==beforeAnswer&&new URL(s).hash==='#heading=h.test'})()`));
 // 앞선 이동이 끝나기 전에 다시 옮기면 안내는 나중 것 — 앞 이동의 load 처리기는 나중 안내를 지우지 않고 빠진다
 const twice=await evaluate(`(()=>{const sel=document.querySelector('[data-section-jump]');sel.value='1';sel.dispatchEvent(new Event('change'));const f=document.querySelector('.chat-doc-editor iframe');const first=f.dataset.loadSeq;sel.dispatchEvent(new Event('change'));const n=document.querySelector('.chat-doc-viewport .chat-doc-loading');return {moved:f.dataset.loadSeq!==first,shown:!n.hidden}})()`);
 assert.deepEqual(twice,{moved:true,shown:true});
 // 첫 load 로는 걷지 않는다(구글 편집기는 그 뒤에도 그리는 중) — 두 번째 load 가 오면 걷힌다
 assert.deepEqual(await evaluate(`(()=>{const n=document.querySelector('.chat-doc-viewport .chat-doc-loading');return {shown:!n.hidden,text:n.textContent}})()`),{shown:true,text:'편집기를 그리는 중…'});
 await evaluate(`document.querySelector('.chat-doc-editor iframe').dispatchEvent(new Event('load'))`);await new Promise(r=>setTimeout(r,1100));
 assert.equal(await evaluate(`document.querySelector('.chat-doc-viewport .chat-doc-loading').hidden`),true);
 // 다시 보내면(대기) 쓴 절 알림은 걷힌다
 await evaluate(`document.getElementById('chatWorkspace').dispatchEvent(new CustomEvent('chat-session-updated',{detail:{session:'1',waiting:false}}))`);
 await evaluate(`const d=document.querySelector('.chat-doc-divider');for(let i=0;i<14;i++)d.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));`);await new Promise(r=>setTimeout(r,400));
 const narrow=await evaluate(`(()=>{const f=document.querySelector('iframe');f.contentDocument.body.innerHTML='<input id="test" style="margin:20px">';const r=f.contentDocument.querySelector('input').getBoundingClientRect(),b=f.getBoundingClientRect(),s=new DOMMatrix(getComputedStyle(f).transform).a;return {scale:s,width:b.width,viewport:f.parentElement.clientWidth,x:b.left+(r.x+5)*s,y:b.top+(r.y+5)*s}})()`);
 assert.ok(narrow.scale<wide.scale);assert.ok(Math.abs(narrow.width-narrow.viewport)<2);
 await evaluate(`document.querySelector('.doc-more summary').click()`);
 const menu=await evaluate(`(()=>{const m=document.querySelector('.doc-more-actions').getBoundingClientRect(),p=document.querySelector('.chat-doc-editor').getBoundingClientRect();return {left:m.left,right:m.right,pLeft:p.left,pRight:p.right}})()`);
 assert.ok(menu.left>=menu.pLeft&&menu.right<=menu.pRight);
 await evaluate(`document.querySelector('.doc-more summary').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`);
 assert.equal(await evaluate(`document.querySelector('.doc-more').open`),false);
 await send('Input.dispatchMouseEvent',{type:'mousePressed',x:narrow.x,y:narrow.y,button:'left',clickCount:1});await send('Input.dispatchMouseEvent',{type:'mouseReleased',x:narrow.x,y:narrow.y,button:'left',clickCount:1});await send('Input.insertText',{text:'fit typing'});
 assert.equal(await evaluate(`document.querySelector('iframe').contentDocument.querySelector('input').value`),'fit typing');
 // 편집기 → 문서함으로 가도 사용자가 끌어 둔 폭을 그대로 쓴다(보기마다 폭이 바뀌면 입력창이 밀린다)
 const editorWidth=await evaluate(`document.querySelector('main').style.getPropertyValue('--chat-width')`);
 assert.notEqual(editorWidth,'50%');
 await evaluate(`document.querySelector('[data-list]').click()`);await new Promise(r=>setTimeout(r,150));assert.equal(await evaluate(`document.querySelectorAll('.chat-doc-file').length`),1);
 await new Promise(r=>setTimeout(r,400));
 assert.equal(await evaluate(`document.querySelector('main').style.getPropertyValue('--chat-width')`),editorWidth);
 assert.equal(await evaluate(`document.getElementById('chatDocumentOpen').hidden`),true);
 await evaluate(`document.querySelector('.chat-doc-editor :is([data-hide],[data-close])').click()`);await new Promise(r=>setTimeout(r,400));assert.equal(await evaluate(`document.querySelector('.chat-doc-editor').hidden`),true);
 assert.equal(await evaluate(`document.getElementById('chatDocumentOpen').hidden`),false);
 await evaluate(`document.querySelector('main').innerHTML='<section id="chatWorkspace" data-session="2"><button id="chatDocumentOpen">문서</button></section>';let reads=0;const originalFetch=window.fetch;window.fetch=async(url,options)=>url==='/api/chat-documents/2'?{ok:true,redirected:false,json:async()=>++reads===1?{connected:false}:{connected:true,title:'자동 생성',embed_url:'about:blank',sections:[]}}:originalFetch(url,options);const originalTimeout=window.setTimeout;window.setTimeout=(fn,delay,...args)=>{if(delay===6000){window.creationPoll=fn;return 999;}return originalTimeout(fn,delay,...args);};`);
 await evaluate(js);await evaluate(`document.getElementById('chatDocumentOpen').click()`);await new Promise(r=>setTimeout(r,150));
 assert.equal(await evaluate(`document.querySelectorAll('.chat-doc-file').length`),1);
 await evaluate(`window.creationPoll()`);await new Promise(r=>setTimeout(r,150));
 assert.equal(await evaluate(`document.querySelectorAll('iframe').length`),1);
 assert.equal(await evaluate(`document.querySelector('.doc-title-row strong').textContent`),'자동 생성');
 await evaluate(`const previousFetch=window.fetch;window.fetch=async(url,options)=>url.endsWith('/documents')?{ok:true,redirected:false,json:async()=>({project:'시험',documents:[{id:9,name:'이미지',original_format:'png',group:'첨부',status:'완료'}]})}:previousFetch(url,options);document.querySelector('[data-list]').click();`);await new Promise(r=>setTimeout(r,400));
 assert.equal(await evaluate(`document.querySelectorAll('[data-doc-group=attached] [data-platform-doc]').length`),1);
 await evaluate(`document.querySelector('[data-doc-group=attached] [data-platform-doc]').click()`);
 assert.equal(await evaluate(`document.querySelector('.chat-image-preview').open`),true);
 await evaluate(`document.querySelector('.chat-image-preview').close()`);await new Promise(r=>setTimeout(r,150));
 assert.equal(await evaluate(`document.activeElement===document.querySelector('[data-doc-group=attached] [data-platform-doc]')`),true);
 await evaluate(`const pickerFetch=window.fetch;window.fetch=async(url,options)=>url.endsWith('/accounts')?{ok:true,json:async()=>({accounts:[{email:'test@example.test',docs_ok:true}]})}:url.includes('/browse?')?{ok:true,json:async()=>({files:url.includes('folder=root')?[{id:'folderA',name:'사업 자료',mimeType:'application/vnd.google-apps.folder'}]:[{id:'docB',name:'선택 문서',mimeType:'application/vnd.google-apps.document'}],next:''})}:pickerFetch(url,options);Array.from(document.querySelectorAll('.chat-doc-files>button')).find(b=>b.textContent==='드라이브에서 가져오기').click();`);
 await new Promise(r=>setTimeout(r,200));
 assert.equal(await evaluate(`document.querySelectorAll('.chat-doc-dialog input[name=doc][type=hidden]').length`),1);
 await evaluate(`document.querySelector('.drive-picker-item').click()`);await new Promise(r=>setTimeout(r,150));
 await evaluate(`document.querySelector('.drive-picker-item').click()`);
 assert.equal(await evaluate(`document.querySelector('.chat-doc-dialog [name=doc]').value`),'docB');
 assert.equal(await evaluate(`document.querySelector('.chat-doc-dialog [data-submit]').disabled`),false);
 await evaluate(`document.querySelector('.chat-doc-dialog').close()`);
 await evaluate(`document.querySelector('.chat-work-files .chat-doc-file').click()`);await new Promise(r=>setTimeout(r,300));
 await evaluate(`window.savedPanel=document.querySelector('.chat-doc-editor');window.savedFrame=savedPanel.querySelector('iframe');window.savedRatio=document.querySelector('main').style.getPropertyValue('--chat-width');document.querySelector('#chatWorkspace').insertAdjacentHTML('beforeend','<div class="chat-heading"></div><div id="chatScroll"><form class="chat-suggestion"><input name="question" value="지금까지 쓴 내용 검토"><button type="submit">검토</button></form></div><form id="chatForm" action="/chat/send"><textarea id="chatInput" name="question"></textarea><input type="file" id="chatFile"><button id="sendBtn"><iconify-icon></iconify-icon></button></form><div id="chatSendState"></div><div id="dockHint"></div><button id="chatLatest"></button><div id="chatEditNote"></div><button id="chatEditCancel"></button><button id="attachButton"></button><div id="chatTools" popover></div>');window.sendCalls=0;window.fetch=async(url,options)=>{sendCalls++;return {ok:true,url:location.origin+'/chat/2',text:async()=>'<section id="chatWorkspace" data-session="2" data-waiting="false"><div class="chat-heading"></div><div id="chatScroll"><div class="turn assistant">검토 완료</div></div></section>'};};`);
 await evaluate(await readFile(root+'chat-workspace.js','utf8'));
 await evaluate(`document.getElementById('chatInput').value='작성 중';document.querySelector('.chat-suggestion').requestSubmit()`);
 assert.equal(await evaluate('sendCalls'),0);
 await evaluate(`document.getElementById('chatInput').value='';document.querySelector('.chat-suggestion').requestSubmit()`);await new Promise(r=>setTimeout(r,200));
 assert.equal(await evaluate('sendCalls'),1);
 assert.equal(await evaluate(`savedPanel===document.querySelector('.chat-doc-editor')&&!savedPanel.hidden&&savedFrame===savedPanel.querySelector('iframe')&&savedRatio===document.querySelector('main').style.getPropertyValue('--chat-width')`),true);
 assert.equal(await evaluate(`document.getElementById('chatScroll').textContent.includes('검토 완료')`),true);
 await evaluate(`window.sessionRequests=[];window.fetch=async(url)=>{sessionRequests.push(url);return {ok:true,json:async()=>({connected:false})};};document.getElementById('chatWorkspace').dataset.session='9';document.getElementById('chatWorkspace').dispatchEvent(new CustomEvent('chat-session-updated'));`);
 await new Promise(r=>setTimeout(r,150));
 assert.equal(await evaluate(`sessionRequests.includes('/api/chat-documents/9')`),true);
 assert.equal(await evaluate(`document.querySelectorAll('.chat-doc-editor').length`),0);
 // A pending connection lookup must not delay opening the library. Both list
 // endpoints start immediately, and closing/reopening preserves the loaded DOM.
 await evaluate(`window.listCalls=[];window.fetch=async(url)=>{listCalls.push(url);if(url==='/api/chat-documents/10')return new Promise(resolve=>{window.finishLookup=()=>resolve({ok:true,json:async()=>({connected:true,doc:'current',account:'test'})});});if(url.endsWith('/files'))return {ok:true,json:async()=>({account:'test',files:[{id:'old',name:'이전 문서',mime_type:'application/vnd.google-apps.document'},{id:'current',name:'현재 문서',mime_type:'application/vnd.google-apps.document'}]})};return {ok:true,json:async()=>({documents:[]})};};document.getElementById('chatWorkspace').dataset.session='10';document.getElementById('chatWorkspace').dispatchEvent(new CustomEvent('chat-session-updated'));document.getElementById('chatDocumentOpen').click();`);
 assert.equal(await evaluate(`Boolean(document.querySelector('.chat-file-library:not([hidden])'))`),true);
 assert.equal(await evaluate(`listCalls.includes('/api/chat-documents/10/files')&&listCalls.includes('/api/chat/10/documents')`),true);
 await evaluate('finishLookup()');await new Promise(r=>setTimeout(r,100));
 assert.equal(await evaluate(`document.querySelector('.chat-work-files button').dataset.driveDoc`),'current');
 assert.equal(await evaluate(`Array.from(document.querySelectorAll('.chat-work-files [aria-current=true]')).map(b=>b.dataset.driveDoc).join()`),'current');
 await evaluate(`window.library=document.querySelector('.chat-file-library');window.requestCount=listCalls.length;document.getElementById('chatDocumentOpen').click();document.getElementById('chatDocumentOpen').click();`);
 assert.equal(await evaluate(`library===document.querySelector('.chat-file-library')&&!library.hidden&&listCalls.length===requestCount`),true);
 await evaluate(`window.fetch=async(url)=>({ok:true,json:async()=>url.endsWith('/files')?{files:[]}:url.endsWith('/documents')?{documents:[]}:{connected:false}});document.querySelector('.chat-file-library .doc-icon-button').click();`);
 await new Promise(r=>setTimeout(r,150));
 assert.equal(await evaluate(`document.querySelectorAll('.chat-file-count').length`),4); // 작업 문서 + 세 갈래(첨부·참고·규정)
 assert.equal(await evaluate(`document.querySelector('.chat-file-library [role=status]').textContent`),'');
 assert.equal(await evaluate(`document.querySelector('.chat-file-library .doc-icon-button').getBoundingClientRect().width`),32);
 assert.equal(await evaluate(`getComputedStyle(document.querySelector('.chat-file-library .chat-doc-files')).flexGrow`),'1');
 const template=await readFile(process.cwd()+'/src/zzaimy/app/templates/chat_workspace.html','utf8');
 const row=template.split('<div class="dock-row">')[1].split('<div class="dock-hint"')[0];
 const modeScript=template.split('  var webBtn =')[1].split('  /* ---- 답변 복사')[0];
 const dockCss=await readFile(root+'chat-workspace.css','utf8');
 // Match production order: base, page inline, workspace, then platform overrides.
 const base=await readFile(process.cwd()+'/src/zzaimy/app/templates/base.html','utf8');
 const baseCss=[...base.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map(m=>m[1]).join('\n');
 const productionCss=baseCss+(await readFile(root+'chat-documents.css','utf8'))+template.split('<style>')[1].split('</style>')[0]+dockCss+(await readFile(root+'platform-spaces.css','utf8'));
 await evaluate(`document.body.innerHTML='<form id="chatForm" class="chat-dock" style="width:780px;margin:60px auto"><div class="dock-row">'+${JSON.stringify(row)}+'</form>';document.head.querySelector('style').textContent=${JSON.stringify(productionCss)};`);
 await evaluate('var webBtn ='+modeScript);
 for(const [value,web] of [['1','1'],['model','model'],['','']]) {
  await evaluate(`answerMode.value=${JSON.stringify(value)};answerMode.dispatchEvent(new Event('change'));`);
  assert.equal(await evaluate(`webField.value`),web);
 }
 assert.equal(await evaluate(`document.querySelectorAll('#answerMode option').length`),3);
 assert.equal(await evaluate(`document.getElementById('webSearchToggle')`),null);
 for(const width of [280,360,520,780]) {
  for(const web of [false,true]) {
   await evaluate(`document.getElementById('chatForm').style.width='${width}px';setMode(${JSON.stringify(web?'1':'model')});`);
   const layout=await evaluate(`(()=>{const r=id=>{const b=document.getElementById(id).getBoundingClientRect();return {left:b.left,right:b.right,top:b.top,bottom:b.bottom}};return {input:r('chatInput'),mode:r('answerMode'),send:r('sendBtn'),overflow:chatForm.scrollWidth>chatForm.clientWidth}})()`);
   assert.equal(layout.overflow,false,JSON.stringify({width,web,layout}));
   assert.ok(layout.mode.right<=layout.send.left,JSON.stringify({width,web,layout}));
   assert.ok(Math.abs(layout.mode.top-layout.send.top)<2);
   if(width>520) {
    assert.ok(layout.input.right<=layout.mode.left);
   } else {
    assert.ok(layout.input.bottom<=layout.mode.top);
   }
  }
 }
 // 처음 여는 편집기도 iframe 이 load 될 때까지 「문서를 불러오는 중…」 덮개를 둔다(느린 응답을 1.5초 붙잡아 본다)
 ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.method==='Fetch.requestPaused')setTimeout(()=>send('Fetch.continueRequest',{requestId:m.params.requestId}).catch(()=>{}),1500);});
 await send('Fetch.enable',{patterns:[{urlPattern:'*slow-doc*'}]});
 await evaluate(`document.body.innerHTML='<main style="display:flex;height:900px;width:1500px"><section id="chatWorkspace" data-session="5"><button id="chatDocumentOpen">문서</button></section></main>';window.fetch=async(url)=>({ok:true,redirected:false,json:async()=>url.endsWith('/folder')?{url:null}:{connected:true,doc:'slowDoc',title:'느린 문서',embed_url:location.origin+'/slow-doc',sections:[]}})`);
 await evaluate(`window.chatDocLoadTiming={settle:99999,cap:3000,slow:99999}`);
 await evaluate(js);await new Promise(r=>setTimeout(r,150));
 await evaluate(`const a=document.createElement('a');a.dataset.documentLink='true';a.href='https://docs.google.com/document/d/slowDoc/edit';a.textContent='[문서 1]';document.getElementById('chatWorkspace').append(a);a.click();`);
 await new Promise(r=>setTimeout(r,700));
 assert.deepEqual(await evaluate(`(()=>{const n=document.querySelector('.chat-doc-viewport .chat-doc-loading');return {shown:Boolean(n&&!n.hidden),text:n?.textContent}})()`),{shown:true,text:'문서를 불러오는 중…'});
 // 느린 응답이 끝나 첫 load 가 와도 안내는 남고(편집기를 그리는 중), 상한(시험에서는 3초)이 지나면 반드시 걷힌다
 await new Promise(r=>setTimeout(r,1500));
 assert.deepEqual(await evaluate(`(()=>{const n=document.querySelector('.chat-doc-viewport .chat-doc-loading');return {shown:!n.hidden,text:n.textContent}})()`),{shown:true,text:'편집기를 그리는 중…'});
 await new Promise(r=>setTimeout(r,1300));
 assert.equal(await evaluate(`document.querySelector('.chat-doc-viewport .chat-doc-loading').hidden`),true);
 await send('Fetch.disable');
 if(process.env.UI_SCREENSHOT){const shot=await send('Page.captureScreenshot',{format:'png'});await writeFile(process.env.UI_SCREENSHOT,Buffer.from(shot.data,'base64'));}
 console.log(JSON.stringify({pass:true,wide,narrow,autoCreationFromList:true,imageModal:true,folderPicker:true,suggestionPreservesEditor:true,newSessionBinding:true,emptyLibrary:true,keepEditorAfterAnswer:true,loadingCover:true,syntheticIframeOnly:true}));
} finally {ws?.close();chrome.kill();}
