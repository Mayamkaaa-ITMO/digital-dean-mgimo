const drawer = document.querySelector("[data-rp-dialog]");
if (drawer && typeof drawer.showModal === "function") {
  drawer.removeAttribute("open");
  drawer.showModal();
  const close = drawer.querySelector("[data-rp-close]");
  drawer.addEventListener("cancel", (event) => {
    event.preventDefault();
    window.location.assign(close.href);
  });
}

const strip = document.querySelector(".rp-pills");
const selectedDay = strip?.querySelector('[aria-current="date"]');
if (selectedDay && selectedDay.offsetLeft + selectedDay.offsetWidth > strip.clientWidth) {
  strip.scrollLeft = selectedDay.offsetLeft + selectedDay.offsetWidth - strip.clientWidth + 22;
}

const colorInput = document.querySelector("[data-rp-picker]");
const hexInput = document.querySelector("[data-rp-hex]");
if (colorInput && hexInput) {
  const preview = document.querySelector(".rp-preview circle");
  colorInput.addEventListener("input", () => {
    hexInput.value = colorInput.value.toUpperCase();
    preview.setAttribute("fill", colorInput.value);
  });
  hexInput.addEventListener("input", () => {
    if (!/^#[0-9a-f]{6}$/i.test(hexInput.value)) return;
    colorInput.value = hexInput.value;
    preview.setAttribute("fill", colorInput.value);
  });
}

const counter = document.querySelector("[data-rp-counter]");
if (counter) {
  const deadline = Date.parse(counter.dataset.rpCounter);
  const startedAt = Date.parse(counter.dataset.rpStart);
  const remaining = counter.querySelector("[data-rp-remaining]");
  const arc = counter.querySelector(".rp-ring-value");
  function updateCounter() {
    const left = Math.max(0, deadline - Date.now());
    const ratio = Math.min(100, 100 * left / Math.max(1, deadline - startedAt));
    arc.setAttribute("stroke-dasharray", ratio + " 100");
    remaining.textContent = Math.ceil(left / 60_000) + " мин";
  }
  updateCounter();
  window.setInterval(updateCounter, 1000);
}
