(() => {
  "use strict";
  const mascot = document.querySelector("[data-vishka]");
  if (!mascot) return;
  const formArea = document.querySelector(".dean-login");
  const password = formArea.querySelector("#login-password");
  const eyes = mascot.querySelector("[data-vishka-eyes]");
  const pupils = mascot.querySelector("[data-vishka-pupils]");
  const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const confidential = (element) => element?.closest?.(".password-row, .dean-provider:not(:disabled), [data-passkey=login]");
  let pending = false;
  let frame = 0;
  function gaze(event) {
    if (motion.matches || mascot.hasAttribute("data-private-pose") || event.pointerType === "touch") return;
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      const rect = eyes.getBoundingClientRect();
      const dx = event.clientX - (rect.left + rect.width / 2);
      const dy = event.clientY - (rect.top + rect.height / 2);
      const distance = Math.max(45, Math.hypot(dx, dy));
      pupils.setAttribute("transform", `translate(${(dx / distance * 5.3).toFixed(2)} ${(dy / distance * 6.1).toFixed(2)})`);
    });
  }
  function pose(turn) {
    cancelAnimationFrame(frame);
    if (turn) pupils.removeAttribute("transform");
    mascot.toggleAttribute("data-private-pose", turn);
  }
  function refresh() {
    pose(pending || password.type === "text" || Boolean(confidential(document.activeElement)));
  }
  new MutationObserver(refresh).observe(password, {attributes:true,attributeFilter:["type"]});
  formArea.addEventListener("focusin", refresh);
  formArea.addEventListener("focusout", () => queueMicrotask(refresh));
  formArea.addEventListener("pointerdown", (event) => {
    if (confidential(event.target)) pose(true);
  });
  formArea.addEventListener("submit", () => { pending = true; pose(true); });
  window.addEventListener("pageshow", () => { pending = false; refresh(); });
  window.addEventListener("pointermove", gaze, {passive: true});
  document.documentElement.addEventListener("pointerleave", () => {
    cancelAnimationFrame(frame);
    pupils.removeAttribute("transform");
  });
  motion.addEventListener("change", () => pupils.removeAttribute("transform"));
  refresh();
})();
