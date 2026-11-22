import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import {runInNewContext} from "node:vm";

test("room conflict follows the edited interval and resets stale confirmation", () => {
  const script = readFileSync(new URL("../dean/static/bookings.js", import.meta.url), "utf8");
  const field = (value) => ({
    value,
    validity: {valid: true},
    events: {},
    addEventListener(type, listener) {
      this.events[type] = listener;
    },
  });
  const start = field("2026-11-22T10:45");
  const end = field("2026-11-22T11:45");
  const warning = {};
  const bookingWarning = {};
  const checkbox = {};
  const acknowledgement = {querySelector: () => checkbox};
  const selection = {
    dataset: {
      roomLessons: JSON.stringify([{start: "2026-11-22T09:00:00", end: "2026-11-22T10:30:00", title: "Общая физика"}]),
      roomBookings: JSON.stringify([{start: "2026-11-22T11:45:00", end: "2026-11-22T12:30:00", title: "Маяк"}]),
    },
    querySelector(selector) {
      return {'[name="starts"]': start, '[name="ends"]': end,
        "[data-room-conflict]": warning, "[data-room-ack]": acknowledgement,
        "[data-room-booking-conflict]": bookingWarning}[selector];
    },
  };
  runInNewContext(script, {
    document: {querySelectorAll: () => [selection]},
    addEventListener() {},
  });
  assert.equal(warning.hidden, true);
  assert.equal(checkbox.disabled, true);

  start.value = "2026-11-22T10:29";
  start.events.input();
  assert.equal(warning.hidden, false);
  assert.match(warning.textContent, /Общая физика/);
  assert.equal(checkbox.required, true);

  checkbox.checked = true;
  start.value = "2026-11-22T10:30";
  start.events.change();
  assert.equal(warning.hidden, true);
  assert.equal(acknowledgement.hidden, true);
  assert.equal(checkbox.checked, false);
  assert.equal(checkbox.required, false);
  assert.equal(bookingWarning.hidden, true);
  end.value = "2026-11-22T12:00";
  end.events.change();
  assert.equal(bookingWarning.hidden, false);
  assert.match(bookingWarning.textContent, /Маяк/);
  assert.equal(checkbox.required, false);
});
