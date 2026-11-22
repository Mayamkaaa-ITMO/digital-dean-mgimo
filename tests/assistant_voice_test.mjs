import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { setImmediate as nextTurn } from 'node:timers/promises';
import test from 'node:test';
import { runInNewContext } from 'node:vm';

const script = readFileSync(new URL('../dean/static/assistant-voice.js', import.meta.url), 'utf8');

function chatFixture(getAudio) {
  const send = { disabled: false };
  const text = Object.assign(new EventTarget(), { value: 'Подскажи,', maxLength: 700 });
  const status = { dataset: {}, hidden: true };
  const form = Object.assign(new EventTarget(), {
    elements: { namedItem: name => name === 'message' ? text : { value: 'csrf-fixture' } },
    querySelector: () => send,
  });
  const chat = Object.assign(new EventTarget(), { querySelector: query => (
    query === '[data-dictation-status]' ? status : null), contains: () => false });
  const attributes = {};
  const button = Object.assign(new EventTarget(), {
    form, dataset: { deanDictation: '/assistant/voice' }, hidden: true,
    closest: () => chat, querySelector: () => ({}),
    setAttribute: (key, value) => { attributes[key] = value; },
  });
  const requests = [];
  let stopped = 0;
  const stream = { getTracks: () => [{ stop: () => { stopped++; } }] };
  class Recorder extends EventTarget {
    static isTypeSupported(type) { return type === 'audio/mp4'; }
    constructor(source, { mimeType }) {
      super();
      assert.equal(source, stream);
      this.mimeType = mimeType;
      this.state = 'inactive';
    }
    start() { this.state = 'recording'; }
    stop() {
      this.state = 'inactive';
      const chunk = new Event('dataavailable');
      chunk.data = new Blob(['audio fixture']);
      this.dispatchEvent(chunk);
      this.dispatchEvent(new Event('stop'));
    }
  }
  const timers = new Map();
  const fetch = async (url, options) => {
    requests.push({ url, options });
    return new Response(JSON.stringify({ text: 'когда следующая пара?' }), {
      headers: { 'Content-Type': 'application/json' },
    });
  };
  runInNewContext(script, {
    window: Object.assign(new EventTarget(), { MediaRecorder: Recorder }), isSecureContext: true,
    navigator: { mediaDevices: { getUserMedia: () => getAudio(stream) } }, MediaRecorder: Recorder,
    document: { querySelectorAll: () => [button] }, Event, FormData, Blob, AbortController, fetch,
    setTimeout: callback => { const id = Symbol(); timers.set(id, callback); return id; },
    clearTimeout: id => timers.delete(id),
  });
  return { button, text, form, status, attributes, requests,
    stopped: () => stopped, fireTimer: () => [...timers.values()][0]() };
}

test('Диктовка дополняет черновик и освобождает микрофон', async () => {
  const chat = chatFixture(async stream => stream);
  assert.equal(chat.button.hidden, false);
  chat.button.dispatchEvent(new Event('click'));
  await nextTurn();
  assert.equal(chat.attributes['aria-pressed'], 'true');
  assert.equal(chat.form.dispatchEvent(new Event('submit', { cancelable: true })), false);
  chat.button.dispatchEvent(new Event('click'));
  await nextTurn();
  const [{ url, options }] = chat.requests;
  assert.equal(url, '/assistant/voice');
  assert.equal(options.body.get('file').type, 'audio/mp4');
  assert.equal(options.body.get('csrf'), 'csrf-fixture');
  assert.equal(chat.stopped(), 1);
  assert.equal(chat.text.value, 'Подскажи, когда следующая пара?');
  assert.equal(chat.status.dataset.state, 'ready');
});

test('Зависший запрос к микрофону освобождает кнопку и поздний аудиопоток', async () => {
  let grant;
  const chat = chatFixture(stream => new Promise(resolve => { grant = () => resolve(stream); }));
  chat.button.dispatchEvent(new Event('click'));
  assert.equal(chat.button.disabled, true);
  chat.fireTimer();
  assert.equal(chat.button.disabled, false);
  assert.equal(chat.status.dataset.state, 'error');
  grant();
  await nextTurn();
  assert.equal(chat.stopped(), 1);
  assert.equal(chat.requests.length, 0);
});
