function resetSubmission(button) {
  button.disabled = false;
  button.textContent = button.dataset.previousLabel;
  delete button.dataset.previousLabel;
  button.form.removeAttribute("aria-busy");
}

let navigationTimer;

function clearNavigation() {
  if (navigationTimer === undefined) return;
  clearTimeout(navigationTimer);
  document.getElementById("navigation-status").hidden = true;
  navigationTimer = undefined;
}

window.addEventListener("click", (event) => {
  if (event.defaultPrevented || event.button !== 0 ||
      event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const link = event.target.closest("a[href]");
  if (!link || link.hasAttribute("download") || (link.target && link.target !== "_self")) return;
  const url = new URL(link.href, location.href);
  if (url.origin !== location.origin || url.searchParams.get("download") === "1" ||
      /\.(?:ics|csv)$/.test(url.pathname) || url.pathname === "/profile/data/export" ||
      url.pathname.startsWith("/documents/") ||
      (url.pathname === location.pathname && url.search === location.search)) return;
  clearNavigation();
  navigationTimer = setTimeout(() => { document.getElementById("navigation-status").hidden = false; }, 280);
});

window.addEventListener("pagehide", clearNavigation);

const confirmation = document.querySelector("[data-confirmation]");
if (confirmation) {
  const dismiss = confirmation.querySelector("[data-confirmation-dismiss]");
  dismiss.hidden = false;
  dismiss.addEventListener("click", () => {
    confirmation.remove();
    document.getElementById("main").focus({preventScroll: true});
  });
}

const rail = document.querySelector("[data-dean-navigation]");
if (rail) {
  const toggle = document.querySelector("[data-dean-nav-toggle]");
  const compact = matchMedia("(max-width: 999px)");
  const background = [...document.querySelectorAll("body > .ui-header, body > #main, body > .skip")];
  const suspended = new Set();
  const viewport = window.visualViewport;
  let drawerScroll = 0;
  toggle.setAttribute("aria-expanded", "false");

  function sizeDrawer() {
    if (!viewport) return;
    rail.style.setProperty("--dean-drawer-top", viewport.offsetTop + "px");
    rail.style.setProperty("--dean-drawer-height", viewport.height + "px");
  }

  function showNavigation(open, restoreFocus = true) {
    if (open === rail.hasAttribute("data-nav-open")) return;
    if (open) drawerScroll = window.scrollY;
    rail.toggleAttribute("data-nav-open", open);
    toggle.setAttribute("aria-expanded", String(open));
    if (open) {
      document.body.style.setProperty("--dean-drawer-scroll", -drawerScroll + "px");
      document.body.setAttribute("data-drawer-lock", "");
      sizeDrawer();
      viewport?.addEventListener("resize", sizeDrawer);
      viewport?.addEventListener("scroll", sizeDrawer);
      rail.setAttribute("role", "dialog");
      rail.setAttribute("aria-modal", "true");
      rail.setAttribute("aria-label", "Меню");
      for (const element of background) {
        if (!element.inert) {
          element.inert = true;
          suspended.add(element);
        }
      }
      (rail.querySelector('nav [aria-current="page"]') || rail.querySelector("[data-dean-nav-close]")).focus();
    } else {
      viewport?.removeEventListener("resize", sizeDrawer);
      viewport?.removeEventListener("scroll", sizeDrawer);
      rail.style.removeProperty("--dean-drawer-top");
      rail.style.removeProperty("--dean-drawer-height");
      document.body.removeAttribute("data-drawer-lock");
      document.body.style.removeProperty("--dean-drawer-scroll");
      window.scrollTo(0, drawerScroll);
      for (const attribute of ["role", "aria-modal", "aria-label"]) rail.removeAttribute(attribute);
      for (const element of suspended) element.inert = false;
      suspended.clear();
      if (restoreFocus) (compact.matches ? toggle : rail.querySelector("nav a")).focus({ preventScroll: true });
    }
  }

  toggle.addEventListener("click", (event) => {
    event.preventDefault();
    showNavigation(true);
  });
  for (const close of document.querySelectorAll("[data-dean-nav-close]")) {
    close.addEventListener("click", (event) => {
      event.preventDefault();
      showNavigation(false);
    });
  }
  rail.addEventListener("keydown", (event) => {
    if (!rail.hasAttribute("data-nav-open")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      showNavigation(false);
    } else if (event.key === "Tab") {
      const stops = [...rail.querySelectorAll("a[href], button:not([disabled])")]
        .filter((control) => control.getClientRects().length);
      const first = stops[0], last = stops.at(-1);
      if (document.activeElement === (event.shiftKey ? first : last)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      }
    }
  });
  compact.addEventListener("change", () => { if (!compact.matches) showNavigation(false); });
  window.addEventListener("pageshow", (event) => { if (event.persisted) showNavigation(false, false); });
  if (compact.matches && location.hash === "#dean-navigation") {
    history.replaceState(history.state, "", location.pathname + location.search);
    showNavigation(true);
  }
}

const more = document.querySelector("[data-more-menu]");
if (more) {
  const summary = more.querySelector("summary");
  const sheet = more.querySelector(".rw-more-sheet");
  const dismiss = more.querySelector("[data-more-dismiss]");
  const background = [...document.querySelectorAll("body > .ui-header, body > #main, body > .rw-rail, body > .skip"),
    ...[...more.parentElement.children].filter((element) => element !== more)];
  const suspended = new Set();
  let shown = false;
  dismiss.hidden = false;

  function restoreMenuFocus() {
    const trigger = summary.getClientRects().length ? summary : document.getElementById("main");
    trigger?.focus({ preventScroll: true });
  }

  function syncMenu() {
    if (more.open && !more.getClientRects().length) more.open = false;
    const open = more.open;
    if (open === shown) return;
    shown = open;
    if (open) {
      for (const element of background) {
        if (!element.inert) {
          element.inert = true;
          suspended.add(element);
        }
      }
      if (!sheet.contains(document.activeElement)) {
        sheet.querySelector("a[href]")?.focus({ preventScroll: true });
      }
    } else {
      for (const element of suspended) element.inert = false;
      suspended.clear();
      restoreMenuFocus();
    }
  }

  function closeMenu() {
    const wasShown = shown;
    more.open = false;
    syncMenu();
    if (!wasShown) restoreMenuFocus();
  }

  more.addEventListener("toggle", syncMenu);
  dismiss.addEventListener("click", closeMenu);
  more.addEventListener("keydown", (event) => {
    if (!more.open) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeMenu();
    } else if (event.key === "Tab") {
      const stops = [summary, ...sheet.querySelectorAll("a[href], button:not([disabled]), summary")]
        .filter((control) => control.getClientRects().length);
      const first = stops[0], last = stops.at(-1);
      if (event.shiftKey ? document.activeElement === first : document.activeElement === last) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      }
    }
  });
  window.addEventListener("resize", syncMenu);
  window.addEventListener("pageshow", (event) => { if (event.persisted) more.open = false; });
}

document.querySelector("[data-restore-focus]")?.focus();

const copyShare = document.querySelector("[data-copy-share]");
if (copyShare) {
  copyShare.hidden = false;
  copyShare.addEventListener("click", async () => {
    const url = document.getElementById("calendar-share-url");
    const status = document.querySelector("[data-copy-status]");
    try {
      await navigator.clipboard.writeText(url.value);
      status.textContent = "Ссылка скопирована.";
    } catch {
      url.focus();
      url.select();
      status.textContent = "Ссылка выделена. Скопируй её из поля.";
    }
  });
}

const shareCount = document.querySelector("[data-share-count]");
if (shareCount) {
  const selection = shareCount.closest("form");
  const update = () => {
    const count = selection.querySelectorAll('input[name="event"]:checked').length;
    shareCount.textContent = `Выбрано событий · ${count}`;
  };
  shareCount.hidden = false;
  selection.addEventListener("change", update);
  update();
}

const avatarForm = document.querySelector(".dean-profile-upload");
if (avatarForm) {
  const file = avatarForm.elements.namedItem("file");
  const status = document.getElementById("profile-upload-status");
  const checkSize = () => {
    const tooLarge = file.files[0]?.size > 20 * 1024 * 1024;
    status.textContent = tooLarge ? "Фото профиля должно быть не больше 20 МиБ. Выбери файл поменьше." : "";
    status.hidden = !tooLarge;
    return tooLarge;
  };
  file.addEventListener("change", checkSize);
  avatarForm.addEventListener("submit", event => { if (checkSize()) event.preventDefault(); });
}

const photoForm = document.querySelector(".dean-photo-form");
if (photoForm && typeof createImageBitmap === "function" && typeof DataTransfer === "function") {
  const file = photoForm.elements.namedItem("file");
  const status = photoForm.querySelector("[data-photo-status]");
  const buttons = [...photoForm.querySelectorAll("button[type=submit], button:not([type])")];
  const uploadLimit = 2 * 1024 * 1024;
  let prepared;
  let preparing = false;
  const useUpload = (blob, name, type) => {
    const selection = new DataTransfer();
    selection.items.add(new File([blob], name, { type }));
    file.files = selection.files;
  };
  const feedback = (text, state = "progress") => {
    status.dataset.state = state;
    status.hidden = false;
    status.textContent = text;
  };
  file.addEventListener("change", () => { status.hidden = true; });
  photoForm.addEventListener("submit", async (event) => {
    if (preparing) {
      event.preventDefault();
      return;
    }
    const submit = event.submitter ?? buttons[0];
    const source = file.files[0];
    if (source === prepared) {
      feedback("Распознаём расписание…");
      return;
    }
    event.preventDefault();
    if (!source) return;
    feedback("Готовим фото…");
    if (source.size > 20 * 1024 * 1024) {
      feedback("Файл слишком большой. Сделай снимок экрана или обрежь расписание до одной недели.", "error");
      return;
    }
    preparing = true;
    buttons.forEach(button => { button.disabled = true; });
    let bitmap;
    try {
      if (!["image/jpeg", "image/png"].includes(source.type)) throw new Error("Invalid format");
      bitmap = await createImageBitmap(source);
      const scale = Math.min(1, 2048 / Math.max(bitmap.width, bitmap.height));
      if (file.files[0] !== source) return;
      if (source.type === "image/png" && scale === 1 && source.size <= uploadLimit) {
        useUpload(source, "schedule.png", source.type);
      } else {
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(bitmap.width * scale));
        canvas.height = Math.max(1, Math.round(bitmap.height * scale));
        const context = canvas.getContext("2d", { alpha: false });
        context.fillStyle = "#fff";
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        const encode = (quality) => new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", quality));
        let blob = await encode(0.9);
        if (blob?.size > uploadLimit) blob = await encode(0.76);
        if (!blob || blob.size > uploadLimit) throw new Error("Image too large");
        if (file.files[0] !== source) return;
        useUpload(blob, "schedule.jpg", "image/jpeg");
      }
    } catch {
      if (file.files[0] !== source) {
        status.hidden = true;
        return;
      }
      if (source.size > uploadLimit) {
        feedback("Не удалось подготовить фото. Выбери чёткий JPEG или PNG до 2 МБ.", "error");
        return;
      }
      // The photo reader validates bytes. Pass a stable ASCII name even if preparation fails.
      useUpload(source, source.type === "image/png" ? "schedule.png" : "schedule.jpg", source.type);
    } finally {
      bitmap?.close();
      preparing = false;
      buttons.forEach(button => { button.disabled = false; });
    }
    prepared = file.files[0];
    photoForm.requestSubmit(submit);
  });
} else if (photoForm) {
  photoForm.addEventListener("submit", () => {
    const status = photoForm.querySelector("[data-photo-status]");
    status.hidden = false;
    status.textContent = "Распознаём расписание…";
  });
}

const csvInput = document.querySelector("[data-csv-input]");
if (csvInput) {
  const insert = csvInput.form.querySelector("[data-insert-csv]");
  const update = () => { insert.disabled = csvInput.value.length > 0; };
  insert.hidden = false;
  csvInput.addEventListener("input", update);
  insert.addEventListener("click", () => {
    if (csvInput.value) return;
    csvInput.value = document.querySelector("[data-csv-example]").textContent;
    update();
    csvInput.focus();
  });
  update();
}

const studyTimer = document.querySelector("[data-study-seconds]");
if (studyTimer) {
  const initial = Number(studyTimer.dataset.studySeconds);
  const loaded = performance.now();
  const tick = () => {
    const elapsed = initial + Math.floor((performance.now() - loaded) / 1000);
    studyTimer.textContent = "Идёт · " + Math.floor(elapsed / 60) + " мин " + (elapsed % 60) + " с";
  };
  window.setInterval(tick, 1000);
  document.addEventListener("visibilitychange", tick);
}

function updatePaperFields(form) {
  const delivery = form.elements.namedItem("delivery");
  if (!delivery) return;
  const paper = delivery.value === "paper";
  form.querySelectorAll("[data-paper-field]").forEach((group) => {
    group.hidden = !paper;
    const control = group.querySelector("select");
    control.disabled = !paper;
    control.required = paper && control.name === "pickup";
  });
}

function formMessage(form, text, error = false) {
  const result = form.querySelector("[data-form-result]");
  result.hidden = false;
  result.setAttribute("role", error ? "alert" : "status");
  result.textContent = text;
  if (error) result.focus();
  return result;
}

async function applicationResponse(attempt, signal) {
  const options = { headers: { Accept: "application/json" }, cache: "no-store", signal };
  if (attempt.uncertain && attempt.receipt) {
    const receipt = await fetch(attempt.receipt, options);
    if (receipt.status !== 404 && !receipt.redirected) return receipt;
  }
  return fetch(attempt.url, { ...options, method: "POST", body: attempt.body });
}

async function sendApplication(form, attempt) {
  if (form.hasAttribute("aria-busy")) return;
  if (!navigator.onLine) {
    formMessage(form, attempt.uncertain
      ? "Связи нет. После подключения повтори попытку, чтобы открыть сохранённую заявку или отправить её."
      : "Связи нет. Данные на месте. Подключись к интернету и снова отправь форму.", true);
    return;
  }
  const retry = form.querySelector("[data-form-retry]");
  retry.hidden = true;
  form.setAttribute("aria-busy", "true");
  attempt.controls.forEach((control) => { control.disabled = true; });
  formMessage(form, attempt.uncertain ? "Проверяем результат…" :
    attempt.label.toLowerCase().includes("черновик") ? "Сохраняем черновик…" : "Сохраняем заявку…");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await applicationResponse(attempt, controller.signal);
    if (response.status >= 500) throw new Error("Uncertain response");
    const data = response.headers.get("Content-Type")?.startsWith("application/json") ? await response.json() : {};
    if (response.ok && /^\/requests\/[a-f0-9]{32}$/.test(data.location)) {
      const result = formMessage(form, "Сохранено. Открываем карточку…");
      const card = document.createElement("a");
      card.href = data.location;
      card.textContent = "Открыть карточку";
      result.append(" ", card);
      form.clearApplicationDraft?.();
      location.assign(data.location);
      return;
    }
    if (response.ok) throw new Error("Missing application receipt");
    form.removeAttribute("aria-busy");
    attempt.controls.forEach((control) => { control.disabled = false; });
    const error = formMessage(form, data.message || "Открой страницу заново или вернись к списку заявок. Данные формы пока на месте.", true);
    if (typeof data.reopen === "string" && /^\/(?![\/\\])/.test(data.reopen)) {
      const link = document.createElement("a");
      link.href = data.reopen;
      link.textContent = "Открыть актуальную страницу";
      error.append(" ", link);
    }
  } catch {
    form.removeAttribute("aria-busy");
    attempt.uncertain = true;
    retry.onclick = () => sendApplication(form, attempt);
    retry.hidden = false;
    formMessage(form, "Результат пока неизвестен. Повтори эту попытку с теми же данными. " +
                      "Если заявка уже сохранена, откроется та же карточка.", true);
  } finally {
    clearTimeout(timeout);
  }
}

document.querySelectorAll("[data-application-form]").forEach((form) => {
  if (form.dataset.draftOwner) {
    const key = ["dean-edit", form.dataset.draftOwner, form.action, form.elements.version.value].join(":");
    const fields = Array.from(form.elements).filter(el => el.name && el.matches(
      "textarea, select, input[type=text], input:not([type]), input[type=date], input[type=number]"));
    form.clearApplicationDraft = () => { try { sessionStorage.removeItem(key); } catch {} };
    try {
      const draft = JSON.parse(sessionStorage.getItem(key));
      if (draft && Date.now() - draft.at < 30 * 60 * 1000) {
        for (const el of fields) if (typeof draft.values[el.name] === "string") el.value = draft.values[el.name];
        formMessage(form, "Восстановлена введённая правка. Проверь поля перед отправкой.");
      } else { form.clearApplicationDraft(); }
    } catch {}
    const saveDraft = () => {
      const values = Object.fromEntries(fields.map(el => [el.name, el.value]));
      try { sessionStorage.setItem(key, JSON.stringify({at: Date.now(), values})); } catch {}
    };
    form.addEventListener("input", saveDraft);
    form.addEventListener("change", saveDraft);
  }
  updatePaperFields(form);
  form.addEventListener("change", (event) => {
    if (event.target.name === "delivery") updatePaperFields(form);
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (form.hasAttribute("aria-busy") || !form.querySelector("[data-form-retry]").hidden) return;
    const submitter = event.submitter;
    const controls = Array.from(form.elements).filter((control) =>
      !control.disabled && control.matches("input:not([type=hidden]), select, textarea, button:not([data-form-retry])"));
    const body = new URLSearchParams(new FormData(form));
    const url = new URL(submitter?.getAttribute("formaction") || form.action, location.href);
    const service = url.pathname.match(/^\/services\/([a-z0-9-]+)(\/draft)?$/);
    const query = service && new URLSearchParams({
      receipt: body.get("request_id"),
      service: service[1],
      draft: service[2] ? "1" : "0",
    });
    sendApplication(form, {
      url,
      label: submitter?.textContent || "",
      body,
      controls,
      receipt: query ? "/requests?" + query : "",
    });
  });
});

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (event.defaultPrevented || form.method !== "post" || !(event.submitter instanceof HTMLButtonElement)) return;
  if (form.hasAttribute("aria-busy")) {
    event.preventDefault();
    return;
  }
  const button = event.submitter;
  button.dataset.previousLabel = button.textContent;
  button.textContent = "Отправка…";
  form.setAttribute("aria-busy", "true");
  // Let the browser read the clicked button's name/value before disabling it.
  window.setTimeout(() => {
    if (Object.hasOwn(button.dataset, "previousLabel") && document.contains(button)) button.disabled = true;
  }, 0);
  window.setTimeout(() => {
    if (Object.hasOwn(button.dataset, "previousLabel") && document.contains(button)) resetSubmission(button);
  }, 10000);
});

window.addEventListener("pagehide", (event) => {
  if (event.persisted && document.body.hasAttribute("data-private")) document.body.hidden = true;
});

window.addEventListener("pageshow", (event) => {
  if (event.persisted && document.body.hasAttribute("data-private")) {
    document.body.hidden = true;
    location.replace(location.href);
    return;
  }
  document.querySelectorAll("button[data-previous-label]").forEach(resetSubmission);
});
