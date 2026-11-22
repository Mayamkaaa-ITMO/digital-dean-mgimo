"use strict";
const geo = document.querySelector("[data-lab-geo]");
if (geo) {
  const result = geo.querySelector("[data-geo-result]");
  const start = geo.querySelector("[data-geo-start]");
  const stop = geo.querySelector("[data-geo-stop]");
  const share = geo.querySelector("[data-geo-share]");
  let watch, generation = 0;
  const release = () => {
    generation++;
    if (watch !== undefined) navigator.geolocation.clearWatch(watch);
    watch = undefined;
    share.disabled = true;
    share.form.elements.state.value = "";
    stop.hidden = true;
    start.disabled = false;
  };
  stop.onclick = () => { release(); result.textContent = "Определение выключено."; };
  window.addEventListener("pagehide", release);
  start.onclick = () => {
    if (!navigator.geolocation) { result.textContent = "Геолокация недоступна в этом браузере."; return; }
    start.disabled = true; stop.hidden = false;
    result.textContent = "Ожидаем координаты устройства…";
    const active = ++generation;
    watch = navigator.geolocation.watchPosition(({coords:c}) => {
      if (active !== generation) return;
      if (![c.latitude,c.longitude,c.accuracy].every(Number.isFinite) || c.accuracy < 0) {
        share.disabled = true; result.textContent = "Устройство пока не определило местоположение."; return;
      }
      const rad = Math.PI / 180, lat = 55.672082 * rad;
      const h = Math.sin((c.latitude*rad-lat)/2)**2 + Math.cos(lat)*Math.cos(c.latitude*rad)*Math.sin((c.longitude-37.488170)*rad/2)**2;
      const d = 12742000 * Math.asin(Math.sqrt(Math.min(h,1)));
      const state = d+c.accuracy <= 500 ? "near" : d-c.accuracy > 500 ? "far" : "unknown";
      result.textContent = ({near:"Ты в кампусе", far:"Ты вне кампуса", unknown:"У границы зоны: точности пока недостаточно"})[state] + " · точность ±" + Math.round(c.accuracy) + " м";
      share.form.elements.state.value = state;
      share.disabled = false;
    }, error => {
      if (active !== generation) return;
      result.textContent = error.code===1 ? "Доступ к геолокации закрыт в браузере." : "Устройство пока не определило местоположение.";
      release();
    }, {enableHighAccuracy:true, timeout:15000, maximumAge:10000});
  };
}
const presenceEmpty = document.querySelector("[data-presence-empty]");
if (presenceEmpty) {
  const expire = () => {
    const rows = document.querySelectorAll("[data-presence-expires]");
    let visible = 0;
    for (const row of rows) {
      if (Date.parse(row.dataset.presenceExpires) <= Date.now()) row.remove();
      else visible++;
    }
    presenceEmpty.hidden = visible > 0;
  };
  expire();
  const timer = setInterval(expire, 1000);
  window.addEventListener("pageshow", expire);
  document.addEventListener("visibilitychange", expire);
  window.addEventListener("pagehide", event => { if (!event.persisted) clearInterval(timer); });
}
const writer = document.querySelector("[data-write-terminal]");
if (writer) writer.onclick = async () => {
  const result = document.querySelector("[data-terminal-result]");
  try {
    if (!("NDEFReader" in window)) throw Error("Запись метки доступна в Chrome на Android с NFC.");
    writer.disabled = true;
    result.textContent = "Поднеси пустую метку к телефону.";
    await new NDEFReader().write({records:[
      {recordType:"url", data:writer.dataset.terminalUrl},
      {recordType:"text", data:writer.dataset.writeTerminal},
    ]}, {overwrite:false});
    result.textContent = "Метка учебной кассы записана.";
  } catch (err) { result.textContent = err.message; }
  finally { writer.disabled = false; }
};
const pay = document.querySelector("[data-nfc-pay]");
if (pay) {
  const confirm = pay.querySelector("[data-nfc-confirm]");
  const status = pay.querySelector("[data-nfc-status]");
  let cancel;
  const accept = (value, mode) => {
    const ok = value === pay.dataset.tag;
    confirm.disabled = !ok;
    confirm.form.elements.terminal.value = ok ? value : "";
    confirm.form.elements.mode.value = ok ? mode : "";
    status.textContent = !ok ? "Метка другой кассы. Поднеси учебную метку." : mode==="nfc" ? "Метка принята. Подтверди покупку." : "Демонстрационное касание. Подтверди покупку.";
  };
  pay.querySelector("[data-nfc-sim]").onclick = () => { cancel?.abort(); accept(pay.dataset.tag,"demo"); };
  pay.querySelector("[data-nfc-read]").onclick = async () => {
    cancel?.abort();
    const attempt = new AbortController();
    cancel = attempt; confirm.disabled = true;
    confirm.form.elements.terminal.value = ""; confirm.form.elements.mode.value = "";
    try {
      if (!("NDEFReader" in window)) throw Error("Открой кассу по ссылке или отсканируй её QR камерой телефона.");
      const reader = new NDEFReader();
      reader.onreading = ({message}) => {
        if (attempt.signal.aborted) return;
        const tag = message.records.find(r => r.recordType === "text");
        accept(tag ? new TextDecoder(tag.encoding || "utf-8").decode(tag.data) : "", "nfc");
        if (!confirm.disabled) attempt.abort();
      };
      reader.onreadingerror = () => { status.textContent = "Не удалось прочитать метку."; };
      await reader.scan({signal:attempt.signal}); if (!attempt.signal.aborted) status.textContent = "Поднеси телефон к метке кассы.";
    } catch (err) { if (!attempt.signal.aborted) status.textContent = err.message; }
  };
  window.addEventListener("pagehide", () => cancel?.abort());
}
const channel = document.querySelector("[data-lecture-publisher]");
if (channel) {
  const start = channel.querySelector("[data-broadcast]");
  const pause = channel.querySelector("[data-pause]");
  const status = channel.querySelector("[data-publish-status]");
  let live = false, finishing = false, stream, recorder, timer, pending = Promise.resolve(), queued = 0;
  let next = Number(channel.dataset.count);
  const buttons = () => {
    start.disabled = live || finishing || queued > 0;
    pause.disabled = !live;
    for (const formButton of document.querySelectorAll('form[action$="/end"] button, form[action$="/sample"] button, form[action$="/upload"] button')) {
      formButton.disabled = live || finishing || queued > 0;
    }
  };
  const halt = () => {
    live = false;
    clearTimeout(timer);
    if (recorder?.state === "recording") { finishing = true; recorder.stop(); }
    stream?.getTracks().forEach(t => t.stop());
    buttons();
  };
  const transmit = async (blob, seq, key, type) => {
    for (let attempt=0;attempt<3;attempt++) {
      try {
        const body = new FormData();
        body.set("csrf",channel.dataset.csrf); body.set("request_id",key);
        body.set("seq",String(seq)); body.set("file",blob,type.includes("mp4")?"lecture.mp4":"lecture.webm");
        const res = await fetch(channel.dataset.lecturePublisher+"/upload",{method:"POST",body,signal:AbortSignal.timeout(20000)});
        if (!res.ok) throw Error("HTTP "+res.status);
        const data = await res.json();
        if (data.seq !== seq) throw Error("Порядок записи изменился.");
        return;
      } catch (err) {
        if (attempt===2) throw err;
        await new Promise(r=>setTimeout(r,600));
      }
    }
  };
  const segment = (type) => {
    const part = new MediaRecorder(stream,{mimeType:type,videoBitsPerSecond:450000,audioBitsPerSecond:64000});
    recorder = part;
    const pieces = [];
    part.ondataavailable = event => { if (event.data.size) pieces.push(event.data); };
    part.onstop = () => {
      finishing = false;
      const blob = new Blob(pieces,{type});
      if (blob.size) {
        const seq = next++, id = crypto.randomUUID();
        queued++; buttons();
        pending = pending.then(() => transmit(blob,seq,id,type)).then(() => {
          status.textContent = "Сохранено фрагментов: "+(seq+1);
        }).catch(err => {
          halt(); start.disabled = true;
          status.textContent = "Передача прервана. Открой эфир заново. "+err.message;
          throw err;
        }).finally(() => { queued--; buttons(); });
        pending.catch(() => {});
      }
      if (queued>4) { halt(); status.textContent = "Соединение медленное. Дождись отправки."; }
      if (live) segment(type);
      buttons();
    };
    part.onerror = () => { halt(); status.textContent = "Браузер прервал запись."; };
    part.start();
    timer = setTimeout(() => { if(part.state==="recording")part.stop(); },4000);
  };
  start.onclick = async () => {
    start.disabled = true;
    try {
      if (!navigator.mediaDevices || !window.MediaRecorder) throw Error("Микрофон доступен при открытии с localhost или по HTTPS.");
      const video=channel.querySelector("[data-use-camera]").checked;
      const types=video?["video/webm;codecs=vp8,opus","video/mp4"]:["audio/webm;codecs=opus","audio/mp4"];
      const type=types.find(t=>MediaRecorder.isTypeSupported(t));
      if (!type) throw Error("Браузер не поддерживает формат записи.");
      const state=await fetch(channel.dataset.lecturePublisher+"/manifest",{cache:"no-store"}).then(r=>r.json());
      if(!state.live)throw Error("Этот эфир завершён.");
      next=state.count; pending=Promise.resolve();
      stream=await navigator.mediaDevices.getUserMedia({audio:true,video:video?{width:640,height:360}:false});
      live=true; buttons(); segment(type); status.textContent="Эфир идёт.";
    } catch(err) { halt(); status.textContent="Не удалось начать эфир: "+err.message; }
  };
  pause.onclick=halt;
  window.addEventListener("pagehide",halt);
  window.addEventListener("beforeunload",event=>{if(live||finishing||queued){event.preventDefault(); event.returnValue="";}});
}
const viewer = document.querySelector("[data-lecture-view]");
if (viewer) {
  const video=viewer.querySelector("video");
  const status=viewer.querySelector("[data-view-status]");
  const start=viewer.querySelector("[data-view-start]");
  let index = 0, active = false, timer, sequence = 0;
  const next = async () => {
    clearTimeout(timer);
    if (!active) return;
    const current = ++sequence;
    try {
      const response = await fetch(viewer.dataset.lectureView+"/manifest",{cache:"no-store"});
      if (!response.ok) throw Error();
      const manifest = await response.json();
      if (!active || current !== sequence) return;
      if (index >= manifest.count) {
        if (!manifest.live) {
          active=false; index=0; start.disabled=false; start.textContent="Смотреть сначала";
          status.textContent="Конец записи."; return;
        }
        status.textContent="Ожидаем следующий фрагмент эфира…";
        timer=setTimeout(next,1500); return;
      }
      video.src=viewer.dataset.lectureView+"/segments/"+index;
      await video.play();
      if (!active || current !== sequence) return;
      index++;
      status.textContent=manifest.live?"Прямой эфир":"Воспроизводится запись";
    } catch {
      if (current !== sequence) return;
      active=false; start.disabled=false; start.textContent="Продолжить просмотр";
      status.textContent="Не удалось загрузить фрагмент. Можно повторить с этого места.";
    }
  };
  start.onclick=()=>{if (!active) {active=true; start.disabled=true; next();}};
  video.onplay=()=>{if (!active) {active=true; index=Math.max(1,index); start.disabled=true;}};
  video.onended=next;
  window.addEventListener("pagehide",()=>{active=false; sequence++; clearTimeout(timer); start.disabled=false;});
}
const heat = document.querySelector("#door-flow");
if (heat) {
  const cards = Array.from(heat.querySelectorAll("[data-room]"));
  const busiest = heat.querySelector("[data-busiest]");
  let pending = false;
  const update = async () => {
    if (pending || document.hidden) return;
    pending = true;
    try {
      const reply = await fetch("/campus-occupancy/live", {cache: "no-store"});
      if (!reply.ok) return;
      const rooms = await reply.json();
      let busiestCard, highest = 0;
      for (const card of cards) {
        const room = rooms[card.dataset.room];
        if (!room) continue;
        if (room.count > highest) {
          highest = room.count;
          busiestCard = card;
        }
        card.dataset.level = room.level;
        card.querySelector("meter").value = room.count;
        card.querySelector("[data-room-share]").textContent = `${room.pct}%`;
        card.querySelector("[data-room-count]").textContent = `${room.count} из ${room.capacity}`;
        card.querySelector("[data-room-free]").textContent = String(room.capacity - room.count);
        card.querySelector("[data-room-source]").textContent = room.source;
        card.querySelector("[data-room-forecast]").textContent = room.prediction == null
          ? "Прогноз пока недоступен" : `Учебный прогноз через час · ${room.prediction} чел.`;
        for (const cell of card.querySelectorAll("[data-zone]")) {
          const zone = room.zones[cell.dataset.zone];
          const label = `${zone.label} · ${zone.count} из ${zone.capacity} мест`;
          cell.dataset.level = zone.level;
          cell.title = label;
          cell.setAttribute("aria-label", label);
          cell.textContent = zone.count;
        }
      }
      busiest.hidden = !busiestCard;
      if (!busiestCard) return;
      const anchor = busiest.querySelector("a");
      anchor.href = "#" + busiestCard.id;
      anchor.textContent = rooms[busiestCard.dataset.room].label;
      busiest.querySelector("strong").textContent = `${highest} чел.`;
    } catch { /* The last confirmed observation remains visible. */ }
    finally {pending = false;}
  };
  let tick;
  function startPolling() {
    clearInterval(tick);
    tick = setInterval(update, 10000);
  }
  startPolling();
  window.addEventListener("pagehide", () => clearInterval(tick));
  window.addEventListener("pageshow", event => {
    if (event.persisted) { update(); startPolling(); }
  });
  document.addEventListener("visibilitychange", update);
}

