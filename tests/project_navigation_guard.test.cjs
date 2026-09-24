const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
test('project navigation warns only for unsaved user fields', async () => {
  const handlers = {}, form = {}, otherForm = {};
  const hidden = {type:'hidden', value:'추천 질문'};
  const question = {type:'text', value:'', isConnected:true, form};
  const note = {type:'textarea', value:'저장된 지침', isConnected:true, form:otherForm};
  const fields = [question, note];
  const root = {
    querySelectorAll: selector => selector === '[data-project-tab]' ? [] :
      selector.includes(':not([type=hidden])') ? fields : [hidden, ...fields],
    addEventListener: (type, fn) => {handlers[type] = fn;}
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/zzaimy/app/static/project-workspace.js'),'utf8'), {
    document:{getElementById:()=>root}, location:{pathname:'/project/1'},
    sessionStorage:{getItem:()=>null}, queueMicrotask,
    window:{addEventListener:(type,fn)=>{handlers[type]=fn;}}
  });
  const warns = () => {let result=false;handlers.beforeunload({preventDefault(){result=true;}});return result;};
  assert.equal(warns(),false);
  question.value='새 질문';assert.equal(warns(),true);
  question.value='';assert.equal(warns(),false);
  note.value='새 지침';assert.equal(warns(),true);
  handlers.submit({target:form,defaultPrevented:false});assert.equal(warns(),true);
  handlers.submit({target:otherForm,defaultPrevented:false});assert.equal(warns(),false);
  handlers.pageshow();assert.equal(warns(),true);
  const event={target:otherForm,defaultPrevented:false};handlers.submit(event);
  event.defaultPrevented=true;await Promise.resolve();assert.equal(warns(),true);
  note.value='저장된 지침';assert.equal(warns(),false);
});
