(() => {
  "use strict";
  const setup = document.querySelector("[data-field-setup]");
  const report = document.querySelector("[data-field-session]");
  if (!setup && !report) return;
  const live = report?.querySelector("[data-field-live]");
  const host = setup || live;
  const path = report?.dataset.fieldSession;
  const csrf = live?.dataset.csrf;
  let active = null, epoch = 0, camera = "", boxes = [], seq = 0, busy = false, feedStarted = 0, lastFresh = 0;
  const time = value => new Date(value).toLocaleTimeString("ru-RU",{timeZone:"Europe/Moscow",hour12:false});
  const uid = () => crypto.randomUUID().replaceAll("-","");
  async function post(endpoint, body) {
    const response = await fetch(path + "/" + endpoint, {method:"POST",redirect:"error",cache:"no-store",
      body:new URLSearchParams({csrf,...body}), signal:AbortSignal.timeout(47000)});
    if (!response.ok) {
      const messages = {403:"Войди как сотрудник и проверь, что сервер принимает кадры по HTTPS.",409:"Сеанс камеры изменился. Открой замер заново.",
        503:"Детектор недоступен. Проверь установку модели на сервере."};
      throw new Error(messages[response.status] || "Сервер вернул ошибку " + response.status + ".");
    }
    return response.json();
  }
  async function refresh() {
    if (!report) return;
    try {
      const res=await fetch(path+"/status",{cache:"no-store",signal:AbortSignal.timeout(5000)});
      if (!res.ok) throw Error();
      const s=await res.json();
      for (const el of report.querySelectorAll("[data-number]")) el.textContent=s[el.dataset.number];
      report.querySelector("[data-fill]").value=s.occupancy;
      report.querySelector("[data-condition]").textContent=s.condition;
      report.querySelector("[data-last]").textContent=s.last ? "Последний сигнал · "+time(s.last)+" МСК" : "";
      report.querySelector("[data-uncertain]").hidden=!s.check;
      const heat=s.heat.map(bin=>{
        const el=document.createElement("li"),share=bin.high/s.capacity;
        el.className=share>=.75?"field-hot":share>=.4?"field-warm":"field-cool";
        el.textContent=time(bin.minute).slice(0,5)+" · "+bin.low+(bin.low===bin.high?"":"–"+bin.high);
        el.title=bin.frames+" кадров";return el;
      });
      report.querySelector("[data-field-heat]").replaceChildren(...heat);
      const items=s.journal.map(j=>{const li=document.createElement("li");li.textContent=time(j.at)+" · "+j.kind+" · внутри "+j.occupancy;return li;});
      report.querySelector("[data-journal]").replaceChildren(...items);
      if(s.ended && active) await stop("Замер завершён.");
    } catch {report.querySelector("[data-condition]").textContent="Нет связи. На экране последние полученные значения.";}
  }
  if (report) {void refresh(); setInterval(refresh,2500);}
  function axes(){
    return setup ? [setup.querySelector("[name=axis]").value, Number(setup.querySelector("[name=line]").value)/100,
      Number(setup.querySelector("[name=inside]").value)] : [report.dataset.axis, Number(report.dataset.line)/100,Number(report.dataset.inside)];
  }
  function draw(){
    if(!host || !active) return;
    const video=host.querySelector("video"),c=host.querySelector("canvas");
    if(!video.videoWidth) return;
    c.width=video.videoWidth;c.height=video.videoHeight;
    const g=c.getContext("2d"),w=c.width,h=c.height;
    const [axis,line,inside]=axes(),vertical=axis==="x",x=w*line,y=h*line;
    g.strokeStyle="#10fed2";g.lineWidth=Math.max(3,w/160);
    for(const [l,t,r,b] of boxes)g.strokeRect(l*w,t*h,(r-l)*w,(b-t)*h);
    const begin=Number(setup?setup.querySelector("[name=gate_start]").value:report.dataset.begin)/100;
    const end=Number(setup?setup.querySelector("[name=gate_end]").value:report.dataset.end)/100;
    g.strokeStyle="#ffd14d";g.beginPath();g.moveTo(vertical?x:w*begin,vertical?h*begin:y);
    g.lineTo(vertical?x:w*end,vertical?h*end:y);g.stroke();
    for(const p of [begin,end]){g.beginPath();g.arc(vertical?x:w*p,vertical?h*p:y,Math.max(4,w/100),0,Math.PI*2);g.fillStyle="#ffd14d";g.fill();}
    g.font="bold "+Math.max(17,w/28)+"px sans-serif";g.fillStyle="#172226";g.fillRect(4,4,Math.min(w,185),38);
    g.fillStyle="#ffd14d";g.fillText("Вход "+(vertical?(inside===1?"→":"←"):(inside===1?"↓":"↑")),10,32);
  }
  function feedback(s){host.querySelector("[data-camera-feedback]").textContent=s;}
  async function stop(message){
    ++epoch;
    const key=camera;
    camera="";boxes=[];busy=false;
    if(active) for(const t of active.getTracks())t.stop();
    active=null;
    const v=host.querySelector("video");v.srcObject=null;v.classList.remove("field-playing");
    host.querySelector("[data-camera-start]").disabled=false;
    host.querySelector("[data-camera-stop]").disabled=true;host.querySelector("[data-devices]").disabled=false;
    feedback(message || "Камера выключена.");
    if(key) {
      try{await post("pause",{camera:key,request_id:uid()});}catch{}
      void refresh();
    }
  }
  async function cycle(token,video,canvas,ctx){
    if(token!==epoch || !active)return;
    try{
      if (video.readyState<2 || !video.videoWidth || video.currentTime===feedStarted) {
        if(performance.now()-lastFresh>6000)throw Error("Камера перестала присылать кадры.");
        setTimeout(()=>cycle(token,video,canvas,ctx),80);return;
      }
      feedStarted=video.currentTime;lastFresh=performance.now();
      const scale=Math.min(1,640/Math.max(video.videoWidth,video.videoHeight));
      canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);
      ctx.drawImage(video,0,0,canvas.width,canvas.height);
      const body={camera, seq:String(++seq), jpeg:canvas.toDataURL("image/jpeg",.76).split(",")[1]};
      // Retrying exactly the same numbered frame cannot duplicate an entrance.
      let result;
      try{result=await post("frame",body);}catch(e){if(!(e instanceof TypeError)||token!==epoch)throw e;result=await post("frame",body);}
      if(token!==epoch)return;
      boxes=result.boxes;draw();feedback("В кадре людей: "+boxes.length+". Подсчёт включён.");
      setTimeout(()=>cycle(token,video,canvas,ctx),65);
    }catch(e){if(token===epoch)await stop(e.message+" Камера остановлена.");}
  }
  async function start(){
    if(active||busy)return;
    busy=true;const token=++epoch,v=host.querySelector("video"),sel=host.querySelector("[data-devices]");
    host.querySelector("[data-camera-start]").disabled=true;
    feedback("Ожидаю доступ к выбранной камере…");
    try{
      if(!navigator.mediaDevices?.getUserMedia)throw Error("Камера доступна через localhost или HTTPS.");
      // Keep the calibrated camera when the operator moves from setup to the measurement.
      const dev=sel.value || (live && sessionStorage.getItem("field-camera-device")) || "";
      const stream=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:960},height:{ideal:540},
        ...(dev?{deviceId:{exact:dev}}:{facingMode:{ideal:"environment"}})},audio:false});
      if(token!==epoch){stream.getTracks().forEach(t=>t.stop());return;}
      active=stream;v.srcObject=stream;v.classList.add("field-playing");await v.play();
      if (token!==epoch)return;
      const track=stream.getVideoTracks()[0];
      track.addEventListener("ended",()=>{if(active===stream)void stop("Камера отключена. После подключения сверь число людей.");});
      const available=(await navigator.mediaDevices.enumerateDevices()).filter(d=>d.kind==="videoinput");
      for(const d of available)if(!Array.from(sel.options).some(o=>o.value===d.deviceId))
        sel.add(new Option(d.label||"Камера "+sel.length,d.deviceId));
      if(track.getSettings().deviceId){
        sel.value=track.getSettings().deviceId;
        sessionStorage.setItem("field-camera-device",sel.value);
      }
      sel.disabled=true;host.querySelector("[data-camera-stop]").disabled=false;
      if(live){
        const key=uid();
        const result=await post("claim",{camera:key});
        if(token!==epoch){await post("pause",{camera:key,request_id:uid()});return;}
        camera=key;seq=result.seq;feedStarted=-1;lastFresh=performance.now();
        const c=document.createElement("canvas");
        feedback("Загружается модель распознавания людей…");
        void cycle(token,v,c,c.getContext("2d"));
      }else{feedback("Предпросмотр. Настрой линию и проверь стрелку входа.");draw();}
    }catch(e){if(token===epoch)await stop(({NotAllowedError:"Разреши доступ к камере в настройках браузера.",NotFoundError:"Подключи камеру и повтори.",NotReadableError:"Освободи камеру в другом приложении.",OverconstrainedError:"Выбранная камера отключена. Подключи её или выбери другую."})[e.name]||e.message);}
  }
  if(host){
    navigator.mediaDevices?.enumerateDevices().then(devices=>{
      const sel=host.querySelector("[data-devices]");
      for(const d of devices.filter(x=>x.kind==="videoinput" && x.deviceId && x.label))
        if(!Array.from(sel.options).some(o=>o.value===d.deviceId))sel.add(new Option(d.label,d.deviceId));
      const saved=sessionStorage.getItem("field-camera-device");
      if(saved && Array.from(sel.options).some(o=>o.value===saved))sel.value=saved;
    }).catch(()=>{});
    host.querySelector("[data-devices]").addEventListener("change",ev=>{
      sessionStorage.setItem("field-camera-device",ev.target.value);
    });
    host.querySelector("[data-camera-start]").addEventListener("click",start);
    host.querySelector("[data-camera-stop]").addEventListener("click",()=>stop());
    setup?.addEventListener("input",draw);
    document.addEventListener("visibilitychange",()=>{if(document.hidden&&(active||busy))void stop("Вкладка скрыта. Перед продолжением сверь фактическое число людей.");});
    window.addEventListener("pagehide",()=>{if(camera)navigator.sendBeacon(path+"/pause",new URLSearchParams({csrf,camera,request_id:uid()}));});
  }
  const correction=report?.querySelector(".field-recount");
  correction?.addEventListener("submit",async ev=>{
    ev.preventDefault();
    try {
      const data=new URLSearchParams(new FormData(correction));
      const res=await fetch(correction.action,{method:"POST",body:data});
      if(!res.ok||!res.url.endsWith(path))throw Error();
      correction.querySelector("[name=request_id]").value=uid();
      correction.querySelector("[name=count]").value="";await refresh();
    }catch{window.alert("Не удалось сохранить сверку. Проверь связь и повтори.");}
  });
  function locate(target,onLocation){
    const output=target.querySelector("[data-location]");
    if(!navigator.geolocation){output.textContent="На устройстве недоступна геолокация.";return;}
    output.textContent="Определяем местоположение…";
    navigator.geolocation.getCurrentPosition(({coords:c})=>onLocation(c,output),
      ()=>{output.textContent="Местоположение недоступно. Проверь разрешение или укажи центр вручную.";},
      {enableHighAccuracy:true,maximumAge:0,timeout:12000});
  }
  setup?.querySelector("[data-mark-site]")?.addEventListener("click",()=>locate(setup,(c,out)=>{
    if(c.accuracy>Number(setup.querySelector("[name=radius]").value)){out.textContent="Погрешность больше радиуса. Введи координаты центра вручную.";return;}
    setup.querySelector("[name=latitude]").value=c.latitude.toFixed(6);
    setup.querySelector("[name=longitude]").value=c.longitude.toFixed(6);
    out.textContent="Центр заполнен. Погрешность устройства ±"+Math.round(c.accuracy)+" м.";
  }));
  const zone=report?.querySelector("[data-zone-lat]");
  zone?.querySelector("[data-zone]").addEventListener("click",()=>locate(zone,(c,out)=>{
    const rad=Math.PI/180,lat=Number(zone.dataset.zoneLat)*rad,lon=Number(zone.dataset.zoneLon)*rad;
    const a=Math.sin((c.latitude*rad-lat)/2)**2+Math.cos(lat)*Math.cos(c.latitude*rad)*Math.sin((c.longitude*rad-lon)/2)**2;
    const d=12742000*Math.asin(Math.sqrt(Math.min(1,a))),r=Number(zone.dataset.radius);
    out.textContent=(d+c.accuracy<=r?"В зоне кампуса":d-c.accuracy>r?"За пределами зоны":"Погрешность пересекает границу зоны")+" · ±"+Math.round(c.accuracy)+" м.";
  }));
})();
