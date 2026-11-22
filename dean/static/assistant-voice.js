(() => {
  if (!isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) return;

  for (const button of document.querySelectorAll("[data-dean-dictation]")) {
    const form = button.form;
    const chat = button.closest("[data-dean-chat]");
    const text = form.elements.namedItem("message");
    const send = form.querySelector('button[type="submit"]');
    const status = chat.querySelector("[data-dictation-status]");
    const mic = button.querySelector("[data-record-icon]");
    const stop = button.querySelector("[data-stop-icon]");
    const mimeType = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"]
      .find(type => MediaRecorder.isTypeSupported(type));
    const limit = 2 * 1024 * 1024;
    button.hidden = false;
    let active;

    const feedback = (message, state) => {
      status.textContent = message;
      status.hidden = !message;
      status.dataset.state = state;
    };
    const controls = (recording, busy = false) => {
      button.disabled = busy;
      button.setAttribute("aria-pressed", String(recording));
      button.setAttribute("aria-label", recording ? "Завершить запись" : "Голосовой ввод");
      mic.hidden = recording;
      stop.hidden = !recording;
    };
    const release = session => {
      clearTimeout(session.timer);
      session.stream?.getTracks().forEach(track => track.stop());
    };
    const close = () => {
      if (!active) return;
      const session = active;
      active = undefined;
      session.abort.abort();
      if (session.recorder?.state === "recording") session.recorder.stop();
      release(session);
      controls(false);
      feedback("", "");
    };

    async function recognize(session) {
      release(session);
      if (active !== session) return;
      controls(false, true);
      feedback("Распознаём речь…", "progress");
      const body = new FormData();
      body.set("csrf", form.elements.namedItem("csrf").value);
      body.set("file", new Blob(session.chunks, { type: session.recorder.mimeType }), "dictation");
      const timeout = setTimeout(() => session.abort.abort(), 75000);
      try {
        const response = await fetch(button.dataset.deanDictation, {
          method: "POST", body, headers: { Accept: "application/json" }, signal: session.abort.signal,
        });
        if (response.status === 413) throw new Error("Запись слишком большая. Надиктуй короче.");
        if (!response.headers.get("Content-Type")?.includes("application/json")) {
          throw new Error(response.status === 403 ? "Сеанс закончился. Войди снова." : "Не удалось распознать запись.");
        }
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Не удалось распознать запись.");
        if (active !== session) return;
        const prefix = text.value.trimEnd();
        const room = text.maxLength - prefix.length - (prefix ? 1 : 0);
        if (room <= 0) throw new Error("Поле сообщения заполнено. Сократи текст и запиши ещё раз.");
        const addition = result.text.slice(0, room).trimEnd();
        text.value = prefix ? `${prefix} ${addition}` : addition;
        text.dispatchEvent(new Event("input", { bubbles: true }));
        if (chat.contains(document.activeElement)) text.focus({ preventScroll: true });
        feedback(room < result.text.length ? "Текст добавлен до лимита сообщения." : "Проверь текст перед отправкой.", "ready");
      } catch (error) {
        if (active !== session) return;
        feedback(error.name === "AbortError" ? "Распознавание заняло слишком много времени. Попробуй короче."
          : error instanceof TypeError ? "Не удалось связаться с сервером. Попробуй ещё раз." : error.message, "error");
      } finally {
        clearTimeout(timeout);
        if (active === session) {
          active = undefined;
          controls(false);
        }
      }
    }

    button.addEventListener("click", async () => {
      if (active?.recorder?.state === "recording") {
        active.recorder.stop();
        return;
      }
      if (send.disabled || active) return;
      const session = { abort: new AbortController(), chunks: [], size: 0 };
      active = session;
      controls(false, true);
      feedback("Разреши доступ к микрофону.", "progress");
      session.timer = setTimeout(() => {
        close();
        feedback("Браузер не подтвердил доступ к микрофону. Проверь разрешение и попробуй снова.", "error");
      }, 18000);
      try {
        session.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (active !== session) {
          release(session);
          return;
        }
        clearTimeout(session.timer);
        const recorder = session.recorder = new MediaRecorder(session.stream, mimeType ? { mimeType } : undefined);
        recorder.addEventListener("dataavailable", ({ data }) => {
          if (active !== session || !data.size) return;
          session.size += data.size;
          if (session.size > limit) {
            close();
            feedback("Запись слишком большая. Попробуй надиктовать короче.", "error");
          } else session.chunks.push(data);
        });
        recorder.addEventListener("stop", () => recognize(session), { once: true });
        recorder.addEventListener("error", () => {
          if (active !== session) return;
          close();
          feedback("Не удалось записать звук. Проверь микрофон и попробуй снова.", "error");
        }, { once: true });
        recorder.start(1000);
        session.timer = setTimeout(() => {
          if (recorder.state === "recording") recorder.stop();
        }, 30000);
        controls(true);
        feedback("Запись включена. Нажми ещё раз, чтобы завершить. До 30 секунд.", "progress");
      } catch (error) {
        if (active !== session) return;
        close();
        feedback(error.name === "NotAllowedError" ? "Доступ к микрофону закрыт. Разреши его в настройках браузера."
          : "Микрофон недоступен. Проверь подключение или введи текст.", "error");
      }
    });
    form.addEventListener("submit", event => {
      if (active) {
        event.preventDefault();
        event.stopImmediatePropagation();
      } else feedback("", "");
    }, true);
    chat.querySelector("[data-chat-collapse]")?.addEventListener("click", close);
    chat.addEventListener("keydown", event => { if (event.key === "Escape" && active) close(); });
    window.addEventListener("pagehide", close);
  }
})();
