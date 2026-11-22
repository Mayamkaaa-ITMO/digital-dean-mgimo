(() => {
  const chatOwner = document.currentScript.dataset.chatOwner;
  if (chatOwner) {
    try {
      const collapsed = sessionStorage.getItem("dean-chat-collapsed:" + chatOwner) === "1";
      document.documentElement.toggleAttribute("data-chat-collapsed", collapsed);
    } catch { /* Use the default layout when storage is unavailable. */ }
  }

  const flyout = "[data-appearance-flyout]";
  document.addEventListener("click", (event) => {
    for (const panel of document.querySelectorAll(`${flyout}[open]`)) {
      if (!panel.contains(event.target)) panel.open = false;
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    const panel = event.target.closest(`${flyout}[open]`);
    if (!panel) return;
    panel.open = false;
    panel.querySelector("summary").focus();
    event.preventDefault();
  });
})();
