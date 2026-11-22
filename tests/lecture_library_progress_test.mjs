import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { setImmediate } from "node:timers/promises";
import test from "node:test";
import vm from "node:vm";

const script = readFileSync(new URL("../dean/static/lecture-library.js", import.meta.url), "utf8");

function player(start) {
  const video = Object.assign(new EventTarget(), { readyState: 1, duration: 240, currentTime: 0 });
  const window = Object.assign(new EventTarget(), { location: { hash: "" } });
  const sends = [];
  const watch = {
    dataset: { start, courseWatch: "/lecture-library/example/one", csrf: "test" },
    querySelector: (selector) => selector === "video" ? video : {},
  };
  const document = {
    querySelector: (selector) => selector === "[data-library-watch]" ? watch : null,
    querySelectorAll: () => [],
  };
  vm.runInNewContext(script, {
    document, window, URLSearchParams,
    fetch: async (url, options) => {
      sends.push([url, options.body.get("seconds"), options.body.get("ended")]);
      return { ok: true };
    },
  });
  return { video, window, sends };
}

test("a chapter preview without playback does not overwrite progress", async () => {
  const { video, window, sends } = player(180);
  assert.equal(video.currentTime, 180);
  window.dispatchEvent(new Event("pagehide"));
  await setImmediate();
  assert.deepEqual(sends, []);
});

test("closing a chapter after playback saves the watched position", async () => {
  const { video, window, sends } = player(180);
  video.dispatchEvent(new Event("playing"));
  video.currentTime = 186;
  window.dispatchEvent(new Event("pagehide"));
  await setImmediate();
  assert.deepEqual(sends, [["/lecture-library/example/one/progress", "186", "0"]]);
});
