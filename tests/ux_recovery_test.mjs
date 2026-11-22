import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createContext, runInContext } from "node:vm";

const script = readFileSync(new URL("../dean/static/main.js", import.meta.url), "utf8");
const card = "/requests/0123456789abcdef0123456789abcdef";
const url = "/services/enrolment-certificate";
const body = new URLSearchParams({ request_id: "abcdef0123456789abcdef0123456789", purpose: "Демо" });
const receipt = "/requests?" + new URLSearchParams({ receipt: body.get("request_id"), service: "enrolment-certificate", draft: "0" });

function harness(fetch) {
  const navigations = [];
  const messages = { setAttribute() {}, focus() {}, append() {} };
  const retry = {};
  const attrs = new Map();
  const controls = [{ disabled: false }];
  const form = {
    querySelector(selector) { return selector === "[data-form-retry]" ? retry : messages; },
    hasAttribute(name) { return attrs.has(name); },
    setAttribute(name, value) { attrs.set(name, value); },
    removeAttribute(name) { attrs.delete(name); },
  };
  const env = createContext({
    document: {
      addEventListener() {},
      querySelector() { return null; },
      querySelectorAll() { return []; },
      createElement() { return {}; },
    },
    window: { addEventListener() {} },
    location: { assign(target) { navigations.push(target); } },
    navigator: { onLine: true },
    fetch, AbortController, setTimeout, clearTimeout,
  });
  runInContext(script, env);
  const attempt = { url, receipt, body, controls, label: "Отправить заявку" };
  return { send: () => env.sendApplication(form, attempt), env, retry, controls, messages, navigations };
}

test("Offline before the POST leaves fields editable and sends the same key after reconnecting", async () => {
  const calls = [];
  const app = harness((path, options) => {
    calls.push([path, options.method, options.body]);
    return Response.json({ location: card });
  });
  app.env.navigator.onLine = false;
  await app.send();
  assert.equal(calls.length, 0);
  assert.equal(app.controls[0].disabled, false);
  assert.match(app.messages.textContent, /Связи нет/);
  app.env.navigator.onLine = true;
  await app.send();
  assert.deepEqual(calls, [[url, "POST", body]]);
  assert.deepEqual(app.navigations, [card]);
});

test("A lost response after commit opens the receipt without a second POST", async () => {
  const calls = [];
  const app = harness((path, options) => {
    calls.push([path, options.method ?? "GET", options.body]);
    if (options.method === "POST") throw new TypeError("Connection reset after commit");
    return Response.json({ location: card });
  });
  await app.send();
  assert.equal(app.retry.hidden, false);
  assert.match(app.messages.textContent, /Результат пока неизвестен/);
  app.env.navigator.onLine = false;
  await app.retry.onclick();
  assert.equal(calls.length, 1);
  app.env.navigator.onLine = true;
  await app.retry.onclick();
  assert.deepEqual(calls, [[url, "POST", body], [receipt, "GET", undefined]]);
  assert.deepEqual(app.navigations, [card]);
});

test("A missing receipt after a connection error replays the original payload", async () => {
  const calls = [];
  const app = harness((path, options) => {
    calls.push([path, options.method ?? "GET", options.body]);
    if (calls.length === 1) throw new TypeError("Connection reset before commit");
    if (path === receipt) return Response.json({}, { status: 404 });
    return Response.json({ location: card });
  });
  await app.send();
  await app.retry.onclick();
  assert.deepEqual(calls, [[url, "POST", body], [receipt, "GET", undefined], [url, "POST", body]]);
  assert.deepEqual(app.navigations, [card]);
});

test("Navigation feedback stays hidden when a click is handled or downloads a file", () => {
  const callbacks = {};
  const status = { hidden: true };
  const location = new URL("https://dean.test/schedule");
  const timers = new Set();
  const env = createContext({
    document: {
      body: { hasAttribute() { return false; } },
      addEventListener() {},
      querySelector() { return null; },
      querySelectorAll() { return []; },
      getElementById() { return status; },
    },
    window: { addEventListener(name, handler) { (callbacks[name] ??= []).push(handler); } },
    location, URL,
    setTimeout(callback) { timers.add(callback); return callback; },
    clearTimeout(callback) { timers.delete(callback); },
  });
  runInContext(script, env);
  const click = (href, defaultPrevented = false) => callbacks.click[0]({
    button: 0, defaultPrevented,
    target: { closest: () => ({ href, hasAttribute: () => false }) },
  });
  click("/requests", true);
  click("/calendar/import/abc/calendar.ics");
  click("/coworking/abc/export.csv");
  click("/profile/data/export");
  click("/lecture-library/abc/notes.pdf?download=1");
  assert.equal(timers.size, 0);

  click("/requests");
  assert.equal(timers.size, 1);
  timers.forEach(callback => callback());
  assert.equal(status.hidden, false);
  callbacks.pagehide.forEach(handler => handler({ persisted: true }));
  assert.equal(status.hidden, true);
});

test("A named submit button stays enabled until its value is serialized", () => {
  const listeners = {};
  const timers = new Map();
  const attrs = new Map();
  class HTMLButtonElement {}
  const form = {
    method: "post",
    hasAttribute: name => attrs.has(name),
    setAttribute: (name, value) => attrs.set(name, value),
    removeAttribute: name => attrs.delete(name),
  };
  const button = Object.assign(new HTMLButtonElement(), {
    form, dataset: {}, disabled: false, textContent: "Обработать", name: "background", value: "1",
  });
  const env = createContext({
    document: {
      addEventListener(name, handler) { listeners[name] = handler; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      contains(element) { return element === button; },
    },
    window: {
      addEventListener() {},
      setTimeout(callback, delay) { timers.set(delay, callback); },
    },
    HTMLButtonElement,
  });
  runInContext(script, env);
  const submit = { target: form, submitter: button, preventDefault() { this.defaultPrevented = true; } };
  listeners.submit(submit);
  assert.equal(button.disabled, false);
  assert.equal(button.value, "1");
  assert.equal(form.hasAttribute("aria-busy"), true);
  listeners.submit(submit);
  assert.equal(submit.defaultPrevented, true);
  timers.get(0)();
  assert.equal(button.disabled, true);
  timers.get(10000)();
  assert.equal(form.hasAttribute("aria-busy"), false);
  assert.equal(button.textContent, "Обработать");
  assert.equal(button.disabled, false);
});

test("Mobile menu follows the visible viewport and restores the page scroll on close", () => {
  function element() {
    const attributes = new Map();
    const styles = new Map();
    const handlers = new Map();
    return {
      attributes, styles, handlers, inert: false,
      setAttribute: (name, value) => attributes.set(name, value),
      hasAttribute: name => attributes.has(name),
      removeAttribute: name => attributes.delete(name),
      toggleAttribute: (name, set) => set ? attributes.set(name, "") : attributes.delete(name),
      addEventListener: (name, handler) => handlers.set(name, handler),
      removeEventListener: name => handlers.delete(name),
      style: {
        setProperty: (name, value) => styles.set(name, value),
        removeProperty: name => styles.delete(name),
      },
      focus() { this.focused = true; },
    };
  }
  const rail = element(), toggle = element(), close = element(), body = element(), main = element();
  const viewport = Object.assign(element(), { height: 620, offsetTop: 0 });
  const anchor = element();
  rail.querySelector = () => anchor;
  const snapshots = [];
  const env = createContext({
    document: {
      body,
      addEventListener() {},
      querySelector(selector) {
        return ({ "[data-dean-navigation]": rail, "[data-dean-nav-toggle]": toggle })[selector] ?? null;
      },
      querySelectorAll(selector) {
        if (selector === "[data-dean-nav-close]") return [close];
        return selector.startsWith("body >") ? [main] : [];
      },
    },
    window: { addEventListener() {}, visualViewport: viewport, scrollY: 467, scrollTo: (...args) => snapshots.push(args) },
    location: { hash: "" },
    matchMedia: () => Object.assign(element(), { matches: true }),
  });
  runInContext(script, env);
  toggle.handlers.get("click")({ preventDefault() {} });
  assert.equal(body.styles.get("--dean-drawer-scroll"), "-467px");
  assert.equal(rail.styles.get("--dean-drawer-height"), "620px");
  assert.equal(main.inert, true);
  viewport.height = 684;
  viewport.offsetTop = 13;
  viewport.handlers.get("resize")();
  assert.equal(rail.styles.get("--dean-drawer-height"), "684px");
  assert.equal(rail.styles.get("--dean-drawer-top"), "13px");
  close.handlers.get("click")({ preventDefault() {} });
  assert.equal(body.hasAttribute("data-drawer-lock"), false);
  assert.equal(rail.styles.size, 0);
  assert.equal(viewport.handlers.size, 0);
  assert.equal(main.inert, false);
  assert.equal(toggle.focused, true);
  assert.deepEqual(snapshots, [[0, 467]]);
});

test("Lesson details dismiss only for a deliberate downward swipe on the grip", () => {
  const edition = readFileSync(new URL("../dean/static/edition.js", import.meta.url), "utf8");
  const bind = () => ({
    handlers: new Map(),
    addEventListener(event, handler) { this.handlers.set(event, handler); },
  });
  const grip = bind(), dismiss = bind(), sheet = bind();
  let closes = 0;
  Object.assign(sheet, {
    showModal() {}, close() { closes += 1; }, scrollTop: 0,
    querySelector(selector) { return selector === ".dean-sheet-grip" ? grip : dismiss; },
  });
  runInContext(edition, createContext({
    window: { matchMedia: () => ({ matches: true }) },
    document: {
      body: { hasAttribute: () => true },
      getElementById: () => bind(),
      querySelector: () => sheet,
    },
  }));
  assert.equal(sheet.handlers.has("touchstart"), false);
  const swipe = (event, clientX, clientY) => {
    const touch = { identifier: 5, clientX, clientY };
    grip.handlers.get(event)({ touches: [touch], changedTouches: { 0: touch, length: 1 } });
  };
  swipe("touchstart", 20, 120);
  swipe("touchend", 180, 193);
  assert.equal(closes, 0);
  swipe("touchstart", 20, 120);
  grip.handlers.get("touchcancel")();
  swipe("touchend", 20, 205);
  assert.equal(closes, 0);
  swipe("touchstart", 20, 120);
  swipe("touchend", 20, 205);
  assert.equal(closes, 1);
});
