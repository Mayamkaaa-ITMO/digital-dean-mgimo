import fs from "node:fs";
import vm from "node:vm";
import test from "node:test";
import assert from "node:assert/strict";

test("Зона полевого замера учитывает погрешность, границу, отказ и удалённость", () => {
  const script=fs.readFileSync(new URL("../dean/static/field-camera.js",import.meta.url),"utf8");
  let click,coords;
  const output={};
  const zone={dataset:{zoneLat:"59",zoneLon:"30",radius:"100"},querySelector(s){
    return s==="[data-zone]"?{addEventListener(_,handler){click=handler;}}:output;
  }};
  const report={dataset:{fieldSession:"/coworking/id"},querySelector(s){
    return s==="[data-zone-lat]"?zone:s==="[data-condition]"?{}:null;
  }};
  vm.runInNewContext(script,{document:{querySelector:s=>s==="[data-field-session]"?report:null},
    navigator:{geolocation:{getCurrentPosition(ok,fail){if(coords)ok({coords});else fail();}}},
    fetch:()=>new Promise(()=>{}),AbortSignal,setInterval(){}});
  const check=(lat,accuracy,expect)=>{
    coords={latitude:lat,longitude:30,accuracy};click();assert.match(output.textContent,expect);
  };
  check(59,8,/В зоне/);check(59.01,10,/За пределами/);check(59.00085,20,/границу/);
  coords=null;click();assert.match(output.textContent,/недоступно/);
});
