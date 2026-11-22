(() => {
  "use strict";
  const materialsForm = document.querySelector(".library-model-form");
  if (materialsForm) {
    const button = materialsForm.querySelector("button");
    const status = materialsForm.querySelector("[data-material-wait]");
    materialsForm.addEventListener("submit", () => {
      button.disabled = true;
      status.hidden = false;
    });
    window.addEventListener("pageshow", () => {
      button.disabled = false;
      status.hidden = true;
    });
  }
  const progress = document.querySelector("[data-lecture-progress]");
  if (progress) {
    const status = progress.querySelector("[data-job-status]");
    const detail = progress.querySelector("[data-job-detail]");
    const connection = progress.querySelector("[data-job-connection]");
    let timer, pending = false, active = true;
    function schedule(delay) {
      clearTimeout(timer);
      if (active && !document.hidden && !pending) timer = setTimeout(poll, delay);
    }
    async function poll() {
      if (!active || document.hidden || pending) return;
      pending = true;
      try {
        const response = await fetch(progress.dataset.lectureProgress, {
          cache: "no-store", headers: {Accept: "application/json"}, signal: AbortSignal.timeout?.(10000),
        });
        if (!response.ok || !response.headers.get("Content-Type")?.includes("application/json")) {
          throw new Error("Lecture status is unavailable");
        }
        const info = await response.json();
        if (!active) return;
        if (!info.running) {
          active = false;
          window.location.reload();
          return;
        }
        if (status.textContent !== info.status) status.textContent = info.status;
        if (detail.textContent !== info.detail) detail.textContent = info.detail;
        connection.hidden = true;
      } catch {
        if (active) connection.hidden = false;
      } finally {
        pending = false;
        schedule(4000);
      }
    }
    schedule(1000);
    document.addEventListener("visibilitychange", () => schedule(0));
    window.addEventListener("pagehide", () => {
      active = false;
      clearTimeout(timer);
    });
    window.addEventListener("pageshow", event => {
      if (event.persisted) {
        active = true;
        schedule(0);
      }
    });
  }
  const sections = [...document.querySelectorAll('.library-switch a[href^="#"]')];
  const transcript = document.querySelector("[data-library-transcript]");
  const chapterPanel = document.querySelector("[data-library-chapters]");
  if (chapterPanel && window.matchMedia("(min-width: 760px)").matches) chapterPanel.open = true;
  const questions = [...document.querySelectorAll("[data-quiz-question]")];
  const pendingQuestion = questions.find((question) => question.dataset.answerState === "unanswered") || questions[0];
  const questionLinks = [...document.querySelectorAll(".library-quiz-nav a")];
  function syncLocation(focusFeedback = false) {
    const hash = window.location.hash;
    const section = hash.startsWith("#question-") ? "#practice" : hash || "#video";
    for (const link of sections) {
      if (link.hash === section) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    }
    if (hash === "#transcript" && transcript) transcript.open = true;
    const target = questions.find((question) => "#" + question.id === hash);
    const selected = target || pendingQuestion;
    for (const question of questions) question.hidden = question !== selected;
    for (const link of questionLinks) {
      if (link.hash === "#" + selected?.id) link.setAttribute("aria-current", "step");
      else link.removeAttribute("aria-current");
    }
    if (target) {
      const feedback = focusFeedback && target.querySelector("[data-quiz-feedback]");
      if (feedback) feedback.focus();
      else {
        target.focus({preventScroll:true});
        target.scrollIntoView({block:"start"});
      }
    }
  }
  syncLocation(true);
  window.addEventListener("hashchange", () => syncLocation());
  for (const link of sections) {
    if (link.hash === "#transcript") link.addEventListener("click", () => { if (transcript) transcript.open = true; });
  }
  const watch = document.querySelector("[data-library-watch]");
  if (!watch) return;
  const video = watch.querySelector("video");
  const feedback = watch.querySelector("[data-video-feedback]");
  const url = watch.dataset.courseWatch;
  const cue = [...document.querySelectorAll("[data-cue]")];
  const chapters = [...document.querySelectorAll(".library-chapters [data-library-seek]")];
  let activeChapter = null;
  let saved = Number(watch.dataset.start);
  function goTo(seconds) {
    video.currentTime = Math.min(seconds, video.duration);
    return video.play().catch(() => {});
  }
  for (const button of document.querySelectorAll("[data-library-seek]")) {
    button.addEventListener("click", () => {
      if (video.readyState >= 1) void goTo(Number(button.dataset.librarySeek));
      else video.addEventListener("loadedmetadata", () => goTo(Number(button.dataset.librarySeek)), {once:true});
    });
  }
  function restore() { if (saved > 0 && saved < video.duration) video.currentTime = saved; }
  if (video.readyState >= 1) restore();
  else video.addEventListener("loadedmetadata", restore, {once:true});
  video.addEventListener("error", () => {
    feedback.textContent = "Видео не загрузилось. Обнови страницу или открой оригинал по ссылке.";
  });
  let currentCue = null;
  let watched = false;
  function record(force, ended = false) {
    const position = Math.floor(video.currentTime);
    if (!watched || !video.readyState || (!force && Math.abs(position - saved) < 12)) return;
    saved = position;
    const body = new URLSearchParams({csrf:watch.dataset.csrf,seconds:String(position),ended:ended?"1":"0"});
    if (done) return;
    pending = pending.catch(() => {}).then(async () => {
      const result = await fetch(url+"/progress",{method:"POST",body,keepalive:true});
      if (!result.ok) throw Error("progress");
    });
    pending.catch(() => { feedback.textContent = "Позиция не сохранилась. Воспроизведение можно продолжить."; });
  }
  let pending = Promise.resolve();
  let done = false;
  video.addEventListener("playing", () => { watched = true; });
  video.addEventListener("timeupdate", () => {
    const next = cue.find(row => Number(row.dataset.cue)<=video.currentTime && Number(row.dataset.cueEnd)>video.currentTime);
    if (next !== currentCue) {
      currentCue?.removeAttribute("aria-current");
      next?.setAttribute("aria-current", "true");
      currentCue = next;
    }
    const chapter = chapters.findLast(item => Number(item.dataset.librarySeek) <= video.currentTime);
    if (chapter !== activeChapter) {
      activeChapter?.removeAttribute("aria-current");
      chapter?.setAttribute("aria-current","true");
      activeChapter = chapter;
    }
    if (!video.paused && !video.seeking) record(false);
  });
  video.addEventListener("pause", () => { if (!video.ended) record(true); });
  video.addEventListener("ended", () => record(true,true));
  window.addEventListener("pagehide", () => { record(true,video.ended); done = true; });
  window.addEventListener("pageshow", () => { done = false; });
})();
