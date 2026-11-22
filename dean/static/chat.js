document.querySelectorAll("[data-dean-chat]").forEach((chat) => {
  const form = chat.querySelector(".dean-assistant-form");
  const text = form.elements.namedItem("message");
  const command = form.elements.namedItem("request_id");
  const selection = form.elements.namedItem("chat");
  const previous = form.elements.namedItem("previous");
  const send = form.querySelector('button[type="submit"]');
  const log = chat.querySelector("[data-chat-log]");
  const status = chat.querySelector("[data-chat-status]");
  const history = chat.querySelector("[data-chat-history]");
  const threadList = history.querySelector("[data-chat-thread-list]");
  const collapse = chat.querySelector("[data-chat-collapse]");
  const expand = chat.querySelector("[data-chat-expand]");
  const dashboard = chat.closest(".dean-dashboard-layout");
  const launcher = dashboard && (document.querySelector(".dean-header-assistant")
    || dashboard.querySelector(".dean-assistant-launcher"));
  const desktop = matchMedia("(min-width: 1040px)");
  const mobile = matchMedia("(max-width: 780px)");
  const standalone = chat.classList.contains("dean-chat-page");
  const preference = "dean-chat-collapsed:" + chat.dataset.chatOwner;
  const draft = "dean-chat-draft:" + chat.dataset.chatOwner + ":" + selection.value;
  let attempted = text.value;
  const hexKey = /^[a-f0-9]{32}$/;
  const root = document.documentElement;

  function setCollapsed(collapsed, moveFocus = false) {
    root.toggleAttribute("data-chat-collapsed", collapsed);
    if (desktop.matches) {
      launcher.setAttribute("aria-controls", chat.id);
      launcher.setAttribute("aria-expanded", String(!collapsed));
    } else {
      launcher.removeAttribute("aria-controls");
      launcher.removeAttribute("aria-expanded");
    }
    if (moveFocus) {
      if (collapsed) launcher.focus({ preventScroll: true });
      else {
        scrollToLatest();
        text.focus({ preventScroll: true });
      }
      const destination = collapsed ? launcher : chat;
      destination.scrollIntoView({ block: "nearest" });
    }
  }

  function saveCollapsed(collapsed) {
    try { sessionStorage.setItem(preference, collapsed ? "1" : "0"); }
    catch { /* The control still works on this page. */ }
  }

  function saveDraft() {
    if (send.disabled || !text.value.trim()) return;
    try { sessionStorage.setItem("dean-chat-draft:" + chat.dataset.chatOwner + ":" + selection.value, text.value); }
    catch { /* The editor remains available until navigation. */ }
  }

  try {
    if (!text.value) text.value = sessionStorage.getItem(draft) || "";
    sessionStorage.removeItem(draft);
  } catch { /* Storage can be disabled. */ }
  if (launcher && collapse) {
    setCollapsed(root.hasAttribute("data-chat-collapsed"));
    collapse.hidden = false;
    collapse.setAttribute("aria-controls", chat.id);
    collapse.addEventListener("click", () => {
      saveCollapsed(true);
      setCollapsed(true, true);
    });
    function syncCollapse() {
      let collapsed = root.hasAttribute("data-chat-collapsed");
      try { collapsed = sessionStorage.getItem(preference) === "1"; } catch { /* Keep the page state. */ }
      setCollapsed(collapsed);
    }
    desktop.addEventListener("change", syncCollapse);
    window.addEventListener("pageshow", (event) => { if (event.persisted) syncCollapse(); });
    launcher.addEventListener("click", (event) => {
      if (!desktop.matches || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        saveDraft();
        return;
      }
      event.preventDefault();
      saveCollapsed(false);
      setCollapsed(false, true);
    });
  } else {
    collapse?.addEventListener("click", () => {
      saveDraft();
      if (desktop.matches) saveCollapsed(true);
    });
  }
  expand?.addEventListener("click", saveDraft);

  if (standalone && window.visualViewport) {
    const syncViewport = () => {
      const viewport = window.visualViewport;
      const nearLatest = log.scrollHeight - log.clientHeight - log.scrollTop < 40;
      if (viewport.scale === 1 && mobile.matches) {
        chat.style.setProperty("--chat-viewport", `${Math.round(viewport.height)}px`);
      } else {
        chat.style.removeProperty("--chat-viewport");
      }
      if (nearLatest) requestAnimationFrame(scrollToLatest);
    };
    window.visualViewport.addEventListener("resize", syncViewport);
    syncViewport();
  }

  function feedback(message, state) {
    status.textContent = message;
    status.hidden = !message;
    status.dataset.state = state;
    const label = state === "error" ? "Повторить отправку" : "Отправить";
    send.setAttribute("aria-label", label);
  }

  function scrollToLatest() {
    log.scrollTop = log.scrollHeight;
  }

  function rememberThread(reply) {
    if (!hexKey.test(reply.chat)) return;
    selection.value = reply.chat;
    const href = "/assistant?chat=" + reply.chat;
    if (expand) expand.href = href;
    if (launcher) launcher.href = href;
    threadList.querySelector('[aria-current="page"]')?.removeAttribute("aria-current");
    let item = threadList.querySelector('[data-chat-thread="' + reply.chat + '"]');
    if (!item) {
      chat.querySelector("[data-chat-no-history]")?.remove();
      item = document.createElement("a");
      item.dataset.chatThread = reply.chat;
      item.href = href;
      item.textContent = reply.chat_title;
      const listItem = document.createElement("li");
      listItem.append(item);
      threadList.prepend(listItem);
    }
    item.setAttribute("aria-current", "page");
    threadList.prepend(item.parentElement);
    if (standalone) window.history.replaceState(null, "", href);
  }

  function appendReply(reply) {
    if (!hexKey.test(reply.command_id) || typeof reply.turn_html !== "string") return;
    if (log.querySelector('[data-chat-command="' + reply.command_id + '"]')) return;
    const template = document.createElement("template");
    // The same escaped server template renders the transcript on page load.
    template.innerHTML = reply.turn_html;
    const day = template.content.querySelector("[data-chat-day]");
    const lastDay = [...log.querySelectorAll("[data-chat-day]")].at(-1);
    if (day && day.dataset.chatDay === lastDay?.dataset.chatDay) day.remove();
    log.querySelector("[data-chat-welcome]")?.remove();
    const confirmation = template.content.querySelector(".dean-chat-confirmation button");
    template.content.querySelector(".dean-chat-turn")?.setAttribute("data-chat-new-turn", "");
    log.append(template.content);
    return confirmation;
  }

  function edit() {
    if (status.dataset.state !== "error") return;
    if (text.value.trim() !== attempted) {
      command.value = Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
      feedback("", "");
    }
  }

  text.addEventListener("input", edit);
  text.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || matchMedia("(pointer: coarse)").matches) return;
    event.preventDefault();
    if (text.value.trim() && !send.disabled) form.requestSubmit();
  });
  chat.querySelectorAll("[data-chat-question]").forEach((choice) => {
    choice.hidden = false;
    choice.addEventListener("click", () => {
      if (send.disabled) return;
      text.value = choice.dataset.chatQuestion;
      edit();
      form.requestSubmit();
    });
  });
  history.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && history.open) {
      event.preventDefault();
      history.open = false;
      history.querySelector("summary").focus();
    }
  });
  document.addEventListener("click", (event) => {
    if (history.open && !history.contains(event.target)) history.open = false;
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (send.disabled || !text.value.trim()) return;
    attempted = text.value.trim();
    const fields = new URLSearchParams(new FormData(form));
    if (mobile.matches) text.blur();
    const pending = document.createElement("div");
    pending.className = "dean-chat-message dean-chat-pending";
    pending.dataset.chatSpeaker = "user";
    pending.textContent = attempted;
    log.append(pending);
    scrollToLatest();
    send.disabled = text.readOnly = true;
    form.setAttribute("aria-busy", "true");
    feedback("Помощник готовит ответ…", "progress");
    let focus;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 29000);
    try {
      const response = await fetch(form.action, {
        method: "POST", body: fields, headers: { Accept: "application/json" }, cache: "no-store",
        signal: controller.signal,
      });
      if (response.status === 403 && !response.headers.get("Content-Type")?.includes("application/json")) {
        feedback("Сеанс закончился. Войди снова, чтобы продолжить.", "error");
        return;
      }
      const reply = await response.json();
      if (hexKey.test(reply.next_id)) command.value = reply.next_id;
      if (!response.ok) {
        feedback(reply.error || "Не удалось получить ответ. Повтори отправку.", "error");
        return;
      }
      pending.remove();
      rememberThread(reply);
      if (typeof reply.navigate === "string" && /^\/(?![\/\\])/.test(reply.navigate)) {
        location.assign(reply.navigate);
        return;
      }
      focus = appendReply(reply);
      previous.value = attempted;
      text.value = "";
      feedback("", "");
    } catch {
      feedback("Связь прервалась. Сообщение осталось в поле. Повтори отправку.", "error");
    } finally {
      clearTimeout(timeout);
      pending.remove();
      send.disabled = text.readOnly = false;
      form.removeAttribute("aria-busy");
      scrollToLatest();
      if (!mobile.matches && chat.getClientRects().length) {
        if (focus) focus.focus({ preventScroll: true });
        else if (chat.contains(document.activeElement) || status.dataset.state === "error") text.focus({ preventScroll: true });
      }
    }
  });
  scrollToLatest();
  document.fonts?.ready.then(scrollToLatest);
});
