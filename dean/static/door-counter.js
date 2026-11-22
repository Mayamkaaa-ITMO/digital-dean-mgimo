// A stationary doorway viewed from the side. Process small frames on this device.
export function crossingDetector(width, height) {
  let background, origin, previous, stable = 0, idle = 0, crossedMiddle = false, counted = false;
  function clearPassage() {
    origin = previous = null;
    stable = idle = 0;
    crossedMiddle = counted = false;
  }
  return rgba => {
    if (rgba.length !== width*height*4) return;
    if (!background) {
      background = Float32Array.from({length:width*height}, (_,i)=>(rgba[4*i]+2*rgba[4*i+1]+rgba[4*i+2])/4);
      return;
    }
    let n = 0, totalX = 0;
    for (let i=0; i<background.length; i++) {
      const p=i*4, light=(rgba[p]+2*rgba[p+1]+rgba[p+2])/4;
      if (Math.abs(light-background[i])>32) { n++; totalX+=i%width; }
      else background[i]+=.04*(light-background[i]);
    }
    const fraction = n/background.length;
    if (fraction>.55) { background=null; clearPassage(); return; }
    if (fraction<.025) { if (++idle>=3) clearPassage(); return; }
    idle=0;
    const centre=totalX/n/width;
    const zone=centre<.38 ? "left" : centre>.62 ? "right" : "middle";
    if (zone===previous) stable++; else {previous=zone; stable=1;}
    if (zone==="middle" && origin) crossedMiddle=true;
    if (counted || stable<2 || zone==="middle") return;
    if (!origin) {origin=zone; return;}
    if (crossedMiddle && zone!==origin) {
      counted=true;
      return zone==="right" ? "enter" : "exit";
    }
  };
}

const panel = typeof document !== "undefined" && document.querySelector("[data-door-camera]");
if (panel) {
  const video=panel.querySelector("video"), room=panel.querySelector("select");
  const start=panel.querySelector("[data-camera-start]"), stop=panel.querySelector("[data-camera-stop]");
  const status=panel.querySelector("[role=status]"), invert=panel.querySelector("input[type=checkbox]");
  let stream, timer, generation=0, pending=0, queue=Promise.resolve();
  const close = () => {
    generation++; clearInterval(timer);
    stream?.getTracks().forEach(track=>track.stop()); stream=null; video.srcObject=null;
    start.disabled=pending>0; stop.disabled=true; room.disabled=pending>0; invert.disabled=pending>0;
  };
  async function send(payload) {
    for (let attempt=0; attempt<2; attempt++) {
      try {
        const response=await fetch("/campus-occupancy/door",{method:"POST",headers:{Accept:"application/json"},body:payload});
        if (response.ok) return true;
        if (response.status<500) return false;
      } catch { /* Repeat the same observation id after a lost response. */ }
    }
    return false;
  }
  function record(direction) {
    const payload=new URLSearchParams({csrf:panel.dataset.csrf,room:room.value,direction,request_id:crypto.randomUUID()});
    pending++;
    queue=queue.then(async()=>{
      const ok=await send(payload);
      pending--;
      if (!ok) { close(); status.textContent="Сигнал не подтверждён. Сверь число людей на карте."; return; }
      if (stream) status.textContent=direction==="enter"?"Учтён вход.":"Учтён выход.";
      else if (!pending) { start.disabled=room.disabled=invert.disabled=false; }
    });
  }
  start.onclick=async()=>{
    const ticket=++generation;
    start.disabled=room.disabled=invert.disabled=true; stop.disabled=false;
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw Error();
      const media=await navigator.mediaDevices.getUserMedia({audio:false,video:{width:320,height:180}});
      if (ticket!==generation) {media.getTracks().forEach(t=>t.stop());return;}
      stream=media; video.srcObject=media; await video.play();
      if (ticket!==generation) return;
      media.getVideoTracks()[0].onended=()=>{close();status.textContent="Камера отключена.";};
      const canvas=document.createElement("canvas");canvas.width=64;canvas.height=36;
      const ctx=canvas.getContext("2d",{willReadFrequently:true});
      const detect=crossingDetector(canvas.width,canvas.height);
      status.textContent="Камера включена. Проведи человека через линию.";
      timer=setInterval(()=>{
        if(video.readyState<2 || pending>=12) return;
        ctx.drawImage(video,0,0,64,36);
        let direction=detect(ctx.getImageData(0,0,64,36).data);
        if (!direction) return;
        if(invert.checked) direction=direction==="enter"?"exit":"enter";
        record(direction);
      },125);
    } catch {if(ticket===generation){close();status.textContent="Камера недоступна. Для демонстрации используй кнопки входа и выхода.";}}
  };
  stop.onclick=()=>{close();status.textContent="Камера выключена.";};
  window.addEventListener("pagehide",close);
}
