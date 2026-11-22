"use strict";

function authFeedback(message) {
  const notice = document.querySelector("[data-auth-error]");
  if (!notice) return;
  notice.textContent = message;
  notice.hidden = !message;
}

for (const button of document.querySelectorAll("[data-show-password]")) {
  button.hidden = false;
  button.addEventListener("click", () => {
    const password = document.getElementById(button.getAttribute("aria-controls"));
    const open = password.type === "password";
    password.type = open ? "text" : "password";
    button.setAttribute("aria-pressed", String(open));
    button.textContent = open ? "Скрыть" : "Показать";
  });
}

const invite = document.querySelector('input[name="invitation"]');
if (invite && location.hash) {
  const secret = location.hash.slice(1);
  if (/^[A-Za-z0-9_-]{43}$/.test(secret)) invite.value = secret;
  else authFeedback("Ссылка приглашения повреждена. Открой её заново.");
  history.replaceState(null, "", location.pathname + location.search);
}

function rememberChoice() {
  return document.querySelector('.login-form input[name="remember"]')?.checked;
}

for (const form of document.querySelectorAll("form.login-alternative")) {
  form.addEventListener("formdata", (event) => {
    if (rememberChoice()) event.formData.set("remember", "1");
  });
}

async function authPost(path, fields) {
  const response = await fetch(path, { method: "POST", body: fields, redirect: "error" });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Действие не удалось. Попробуй снова.");
  return result;
}

function decodeBinary(text) {
  const base64 = text.replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
}

function encodeBinary(buffer) {
  if (buffer == null) return null;
  const binary = Array.from(new Uint8Array(buffer), (unit) => String.fromCharCode(unit)).join("");
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function publicKey(data) {
  data.challenge = decodeBinary(data.challenge);
  if (data.user) data.user.id = decodeBinary(data.user.id);
  for (const descriptor of [...(data.allowCredentials || []), ...(data.excludeCredentials || [])]) {
    descriptor.id = decodeBinary(descriptor.id);
  }
  return data;
}

function serialize(credential, register) {
  const res = credential.response;
  const response = { clientDataJSON: encodeBinary(res.clientDataJSON) };
  if (register) {
    response.attestationObject = encodeBinary(res.attestationObject);
    if (res.getTransports) response.transports = res.getTransports();
  } else {
    response.authenticatorData = encodeBinary(res.authenticatorData);
    response.signature = encodeBinary(res.signature);
    response.userHandle = encodeBinary(res.userHandle);
  }
  return JSON.stringify({
    id: credential.id, rawId: encodeBinary(credential.rawId), type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment, clientExtensionResults: {},
    response,
  });
}

function sameOrigin(url) {
  const target = new URL(url, location.origin);
  if (target.origin !== location.origin) throw new Error("Открой страницу входа заново.");
  return target;
}

const keySupport = window.isSecureContext && window.PublicKeyCredential && navigator.credentials;
for (const hint of document.querySelectorAll("[data-key-unsupported]")) hint.hidden = Boolean(keySupport);
for (const button of document.querySelectorAll("[data-passkey]")) {
  if (!keySupport) continue;
  button.hidden = false;
  button.addEventListener("click", async () => {
    authFeedback("");
    button.disabled = true;
    const kind = button.dataset.passkey;
    const register = kind === "register";
    const fields = new URLSearchParams(new FormData(button.form));
    fields.set("intent", register ? "link" : kind);
    if (kind === "login" && rememberChoice()) fields.set("remember", "1");
    try {
      const issued = await authPost("/auth/key/start", fields);
      const options = { publicKey: publicKey(issued.publicKey) };
      const credential = register ? await navigator.credentials.create(options) : await navigator.credentials.get(options);
      if (!credential) throw new Error("Действие с ключом отменено.");
      const result = await authPost("/auth/key/finish", new URLSearchParams({
        state: issued.state, csrf: fields.get("csrf"), credential: serialize(credential, register),
      }));
      location.assign(sameOrigin(result.return));
    } catch (error) {
      authFeedback(error.name === "NotAllowedError" ? "Подтверждение отменено или недоступно на устройстве." : error.message);
    } finally {
      button.disabled = false;
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("form[data-max-approval]");
  if (!form) return;
  const bridge = window.WebApp;
  if (!bridge?.initData) {
    authFeedback("Открой мини-приложение по ссылке со страницы входа в MAX.");
    return;
  }
  bridge.ready?.();
  form.hidden = false;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button");
    button.disabled = true;
    authFeedback("");
    const fields = new URLSearchParams(new FormData(form));
    fields.set("init_data", bridge.initData);
    try {
      const result = await authPost("/auth/max/approve", fields);
      const target = sameOrigin(result.return);
      const returnLink = document.querySelector("[data-max-return]");
      returnLink.href = target.href;
      returnLink.hidden = false;
      if (bridge.openLink) {
        returnLink.addEventListener("click", (click) => {
          click.preventDefault();
          bridge.openLink(target.href);
        });
      }
      document.querySelector("[data-max-done]").hidden = false;
      form.reset();
      form.hidden = true;
    } catch (error) {
      authFeedback(error.message);
    } finally {
      button.disabled = false;
    }
  });
});
