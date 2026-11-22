import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFileSync } from "node:fs";

test("Вишка следит за курсором и сохраняет закрытую позу во время входа", () => {
  const handlers = {}, attrs = new Set();
  const pupils = {
    transform: "",
    setAttribute(_,value){ this.transform = value; },
    removeAttribute(){ this.transform = ""; }
  };
  const eyes = {getBoundingClientRect:() => ({left:40,top:40,width:60,height:40})};
  const mascot = {
    querySelector:s => s.includes("pupils") ? pupils : eyes,
    hasAttribute:key=>attrs.has(key),
    toggleAttribute(key, on){ return on ? attrs.add(key) : attrs.delete(key); }
  };
  const password = {type:"password",closest:() => true};
  const login = {closest:() => false};
  const document = {
    activeElement:login,
    querySelector:s=>s==="[data-vishka]" ? mascot : {
      querySelector:()=>password, addEventListener:(name,fn)=>handlers[name]=fn
    },
    documentElement:{addEventListener(){}}
  };
  let mutate;
  vm.runInNewContext(readFileSync(new URL("../dean/static/login-mascot.js",import.meta.url),"utf8"),{
    document,queueMicrotask:fn=>fn(),requestAnimationFrame:fn=>fn(),cancelAnimationFrame(){},
    MutationObserver:class {constructor(cb){mutate=cb;} observe(){}},
    window:{matchMedia:()=>({matches:false,addEventListener(){}}),addEventListener:(name,fn)=>handlers[name]=fn}
  });
  const turned = () => attrs.has("data-private-pose");
  handlers.pointermove({clientX:100,clientY:50,pointerType:"mouse"});
  assert.match(pupils.transform,/translate\(3\.53 -1\.36\)/);
  document.activeElement = password;
  handlers.focusin();
  assert.equal(turned(),true);
  assert.equal(pupils.transform,"");
  password.type="text";
  document.activeElement=login;
  handlers.focusout();
  assert.equal(turned(),true);
  password.type="password";
  mutate();
  assert.equal(turned(),false);
  document.activeElement={closest:selector=>selector.includes(".dean-provider")};
  handlers.focusin();
  assert.equal(turned(),true);
  handlers.submit();
  document.activeElement=login;
  handlers.focusout();
  assert.equal(turned(),true);
  handlers.pageshow();
  assert.equal(turned(),false);
});
