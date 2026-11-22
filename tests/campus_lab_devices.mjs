import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
import test from "node:test";
const script = readFileSync(new URL("../dean/static/campus-lab.js",import.meta.url),"utf8");

function section(selector, controls, extra = {}) {
  const region = {querySelector: key => controls[key], ...extra};
  return {querySelector: key => key === selector ? region : null};
}

test("Учитывает погрешность и прекращает наблюдение за геозоной", () => {
  let update, cleared;
  const controls = Object.fromEntries(["start","stop","share","result"].map(n => ["[data-geo-"+n+"]", {}]));
  const share = controls["[data-geo-share]"];
  share.form = {elements: {state:{}}};
  const navigator = {geolocation: {
    watchPosition(fn) {update = fn; return 17;},
    clearWatch(id) {cleared = id;}
  }};
  vm.runInNewContext(script,{document:section("[data-lab-geo]",controls),navigator,window:{addEventListener(){}}});
  controls["[data-geo-start]"].onclick();
  for (const [latitude,accuracy,expected] of [[55.672082,10,"near"],[55.677,70,"unknown"],[55.690,20,"far"]]) {
    update({coords:{latitude,longitude:37.488170,accuracy}});
    assert.equal(share.form.elements.state.value,expected);
  }
  controls["[data-geo-stop]"].onclick();
  assert.equal(cleared,17);
  assert.equal(share.disabled,true);
});

test("У NFC-кассы принимается только нужная NDEF-метка, затем требуется подтверждение", async () => {
  let reader;
  class NDEFReader {
    constructor(){reader=this;}
    async scan() {}
  }
  const controls = Object.fromEntries(["confirm","status","sim","read"].map(n=>["[data-nfc-"+n+"]", {}]));
  const confirm = controls["[data-nfc-confirm]"];
  confirm.form={elements:{terminal:{},mode:{}}};
  const document=section("[data-nfc-pay]",controls,{dataset:{tag:"CAMPUS-1"}});
  vm.runInNewContext(script,{document,window:{addEventListener(){},NDEFReader},NDEFReader,AbortController,TextDecoder});
  await controls["[data-nfc-read]"].onclick();
  const tap=text=>reader.onreading({message:{records:[{recordType:"text",data:new TextEncoder().encode(text)}]}});
  tap("OTHER");
  assert.equal(confirm.disabled,true);
  tap("CAMPUS-1");
  assert.equal(confirm.disabled,false);
  assert.equal(confirm.form.elements.mode.value,"nfc");
  controls["[data-nfc-sim]"].onclick();
  assert.equal(confirm.form.elements.mode.value,"demo");
});

test("Метка кассы содержит ссылку и записывается без перезаписи", async () => {
  let written;
  class NDEFReader {
    async write(message, options) {
      written = JSON.parse(JSON.stringify({message, options}));
    }
  }
  const url = "https://example.test/campus-pay/terminal";
  const writer = {dataset:{terminalUrl:url, writeTerminal:"CAMPUS-1"}}, result = {};
  const controls = {"[data-write-terminal]":writer, "[data-terminal-result]":result};
  const document = {querySelector: key => controls[key] ?? null};
  vm.runInNewContext(script, {document, window:{NDEFReader}, NDEFReader});
  await writer.onclick();
  assert.deepEqual(written, {message:{records:[
    {recordType:"url", data:url}, {recordType:"text", data:"CAMPUS-1"},
  ]}, options:{overwrite:false}});
  assert.equal(writer.disabled, false);
});

test("Камера учитывает один проход в каждом направлении и игнорирует изменение освещения", async () => {
  const {crossingDetector}=await import("../dean/static/door-counter.js");
  const read=crossingDetector(64,36);
  function frame(x,light=20){
    const bytes=new Uint8ClampedArray(64*36*4).fill(light);
    if (x!==null) for(let y=8;y<29;y++) for(let k=x;k<x+7;k++) {
      const i=(y*64+k)*4;bytes[i]=bytes[i+1]=bytes[i+2]=205;
    }
    return read(bytes);
  }
  assert.deepEqual([null,5,6,21,29,36,46,48,50,50,null,null,null,50,49,37,30,22,6,5,null,null,null]
    .map(x=>frame(x)).filter(Boolean),["enter","exit"]);
  assert.equal(frame(null,225),undefined);
  assert.deepEqual([null,null,null].map(x=>frame(x,225)).filter(Boolean),[]);
});

test("Позднее геособытие после остановки не разрешает публикацию",()=>{
  let update;
  const controls=Object.fromEntries(["start","stop","share","result"].map(n=>["[data-geo-"+n+"]",{}]));
  controls["[data-geo-share]"].form={elements:{state:{}}};
  const navigator={geolocation:{watchPosition(fn){update=fn;return 3;},clearWatch(){}}};
  vm.runInNewContext(script,{document:section("[data-lab-geo]",controls),navigator,window:{addEventListener(){}}});
  controls["[data-geo-start]"].onclick(); controls["[data-geo-stop]"].onclick();
  update({coords:{latitude:55.672082,longitude:37.488170,accuracy:3}});
  assert.equal(controls["[data-geo-share]"].disabled,true);
});

test("После сетевой ошибки проигрыватель возобновляет тот же фрагмент", async()=>{
  let fail=false;
  const played=[];
  const video={async play(){if(fail){fail=false;throw Error();}played.push(this.src);}};
  const start={},status={};
  const document=section("[data-lecture-view]",{"video":video,"[data-view-start]":start,"[data-view-status]":status},{dataset:{lectureView:"/lectures/example"}});
  const fetch=async()=>({ok:true,async json(){return {count:2,live:false};}});
  vm.runInNewContext(script,{document,window:{addEventListener(){}},fetch,setTimeout,clearTimeout});
  start.onclick(); await new Promise(setImmediate);
  fail=true; await video.onended(); assert.equal(start.disabled,false);
  start.onclick();await new Promise(setImmediate);
  assert.deepEqual(played,["/lectures/example/segments/0","/lectures/example/segments/1"]);
  await video.onended();assert.equal(status.textContent,"Конец записи.");
});
