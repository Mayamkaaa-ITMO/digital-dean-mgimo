const automatic = document.querySelector("[data-tour-automatic]");
if (automatic && location.hash) automatic.remove();
const overlay = document.querySelector("[data-dean-overlay]");
if (overlay && typeof overlay.showModal === "function") {
  const anchor = overlay.dataset.tourAnchor && (
    [...document.querySelectorAll(overlay.dataset.tourAnchor)].find(element => element.getClientRects().length) ||
    document.querySelector("#main h1")
  );
  overlay.close();
  overlay.showModal();
  overlay.querySelector("h2")?.focus({preventScroll: true});
  if (anchor) {
    const narrow = window.matchMedia("(max-width: 780px)");
    const gap = 12;
    const clamp = (value, min, max) => Math.max(min, Math.min(value, max));
    anchor.classList.add("dean-tour-anchor");
    anchor.scrollIntoView({block: "center", behavior: "instant"});

    const position = () => {
      const target = anchor.getBoundingClientRect();
      const box = overlay.getBoundingClientRect();
      if (narrow.matches) {
        const focusBottom = Math.min(target.bottom, target.top + 70);
        const correction = focusBottom - box.top + gap;
        if (correction > 0) window.scrollBy({top: correction, behavior: "instant"});
        return;
      }
      const w = document.documentElement.clientWidth;
      const h = document.documentElement.clientHeight;
      let left = target.left;
      let top = target.bottom + gap;
      if (target.right + box.width + gap * 2 <= w) {
        left = target.right + gap;
        top = target.top;
      } else if (target.left >= box.width + gap * 2) {
        left = target.left - box.width - gap;
        top = target.top;
      } else if (top + box.height + gap > h) {
        top = target.top - box.height - gap;
      }
      overlay.style.setProperty("--tour-left", `${clamp(left, gap, w - box.width - gap)}px`);
      overlay.style.setProperty("--tour-top", `${clamp(top, gap, h - box.height - gap)}px`);
    };
    position();
    window.addEventListener("resize", position);
    document.fonts?.ready.then(position);
  }
  overlay.addEventListener("cancel", (event) => {
    const dismiss = overlay.querySelector("[data-overlay-dismiss]");
    if (dismiss) {
      event.preventDefault();
      dismiss.form.requestSubmit(dismiss);
    }
  });
  overlay.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const actions = [...overlay.querySelectorAll("a[href], button:not(:disabled)")]
      .filter(element => element.getClientRects().length);
    const first = actions[0];
    const last = actions.at(-1);
    const active = document.activeElement;
    const wrap = event.shiftKey ? active === first || !actions.includes(active) : active === last;
    if (wrap && first) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    }
  });
}
