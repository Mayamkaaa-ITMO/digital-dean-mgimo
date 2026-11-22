(() => {
  const sheet = document.querySelector("[data-lesson-sheet]");
  const main = document.getElementById("main");
  if (!main || !sheet || typeof sheet.showModal !== "function") return;

  const mobile = window.matchMedia("(max-width: 780px)");
  const sharedShell = document.body.hasAttribute("data-responsive-shell");
  const title = sheet.querySelector("[data-sheet-title]");
  const timing = sheet.querySelector("[data-sheet-time]");
  const kind = sheet.querySelector("[data-sheet-kind]");
  const status = sheet.querySelector("[data-sheet-status]");
  const teacher = sheet.querySelector("[data-sheet-teacher]");
  const teacherRow = sheet.querySelector("[data-sheet-teacher-row]");
  const location = sheet.querySelector("[data-sheet-location]");
  const locationRow = sheet.querySelector("[data-sheet-location-row]");
  const oldRoom = sheet.querySelector("[data-sheet-old-room]");
  const map = sheet.querySelector("[data-sheet-map]");
  const mapUnavailable = sheet.querySelector("[data-sheet-map-unavailable]");
  const detail = sheet.querySelector("[data-sheet-detail]");
  let trigger = null;
  let gesture = null;

  main.addEventListener("click", (event) => {
    const anchor = event.target.closest("a[data-lesson-preview]");
    if (!anchor || (!sharedShell && !mobile.matches) || event.defaultPrevented || event.button !== 0
      || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    event.preventDefault();
    trigger = anchor;
    title.textContent = anchor.getAttribute("aria-label") || anchor.querySelector("strong").textContent;
    timing.textContent = [anchor.dataset.date, anchor.dataset.time].filter(Boolean).join(" · ");
    kind.textContent = anchor.dataset.kind || "";
    kind.dataset.tone = /лекц/i.test(kind.textContent) ? "lecture" : /лаборат/i.test(kind.textContent) ? "lab" : "practice";
    kind.hidden = !kind.textContent;
    status.textContent = anchor.dataset.status || "";
    status.hidden = !status.textContent;
    teacher.textContent = anchor.dataset.teacher || "";
    teacherRow.hidden = !teacher.textContent;
    location.textContent = anchor.dataset.location || "";
    locationRow.hidden = !location.textContent;
    oldRoom.textContent = anchor.dataset.oldRoom ? `Прежняя аудитория · ${anchor.dataset.oldRoom}` : "";
    oldRoom.hidden = !oldRoom.textContent;
    map.hidden = !anchor.dataset.room;
    if (map.hidden) map.removeAttribute("href");
    else map.href = anchor.dataset.room;
    mapUnavailable.hidden = !map.hidden || locationRow.hidden;
    detail.href = anchor.href;
    detail.classList.toggle("rw-primary-link", map.hidden);
    sheet.showModal();
  });

  sheet.addEventListener("click", (event) => {
    const bounds = sheet.getBoundingClientRect();
    if (event.target === sheet && (event.clientX < bounds.left || event.clientX > bounds.right
      || event.clientY < bounds.top || event.clientY > bounds.bottom)) sheet.close();
  });
  sheet.querySelector("[data-sheet-close]").addEventListener("click", () => sheet.close());
  sheet.addEventListener("close", () => {
    if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    trigger = null;
  });
  const grip = sheet.querySelector(".dean-sheet-grip");
  grip.addEventListener("touchstart", (event) => {
    gesture = sheet.scrollTop === 0 && event.touches.length === 1 ? event.changedTouches[0] : null;
  }, { passive: true });
  grip.addEventListener("touchend", (event) => {
    const touch = Array.from(event.changedTouches).find(({identifier}) => identifier === gesture?.identifier);
    if (touch && sheet.scrollTop === 0 && touch.clientY - gesture.clientY > 64
      && touch.clientY - gesture.clientY > Math.abs(touch.clientX - gesture.clientX)) sheet.close();
    gesture = null;
  }, { passive: true });
  grip.addEventListener("touchcancel", () => { gesture = null; }, { passive: true });
  if (!sharedShell) mobile.addEventListener("change", () => { if (!mobile.matches && sheet.open) sheet.close(); });
})();
