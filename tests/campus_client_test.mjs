import {readFileSync} from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
import {test} from "node:test";

// Execute the shipped script with the same optional DOM panels as a server-rendered page.
const source = readFileSync(new URL("../dean/static/campus-map.js", import.meta.url), "utf8");
function setup() {
  const elements = {};
  for (const key of ["check", "clear", "status"]) {
    elements[key] = {addEventListener(event, fn) {this[event] = fn;}, focus() {}};
  }
  const panel = {querySelector(selector) {return elements[selector.slice(13, -1)];}};
  let pending;
  const window = {isSecureContext: true, addEventListener(event, fn) {this[event] = fn;}};
  const context = vm.createContext({
    location: {hash: ""}, window,
    document: {querySelector(sel) {return sel === "[data-campus-nearby]" ? panel : null;}, querySelectorAll() {return [];}},
    navigator: {geolocation: {getCurrentPosition(...args) {pending = args;}}}
  });
  vm.runInContext(source, context);
  return {elements, context, window, getPending: () => pending};
}
test("route editor normalizes letters, spacing, Cyrillic building and floor", () => {
  const {context} = setup();
  for (const q of ["2018а", "2018-а", "2018-А", "2018 a", "2018a"]) {
    assert.equal(context.normalizePoint(q), "2018а");
  }
  assert.equal(context.normalizePoint("2018"), "2018");
  assert.equal(context.normalizePoint("кабинет 2018-А · корпус Б · 2-й этаж"), "2018а корпус б этаж2");
  assert.equal(context.normalizePoint("2003b", "б"), "2003б");
  assert.equal(context.normalizePoint("14 g"), "14г");
});
test("proximity stays off until selected; ambiguity, denial, stale results and clearing", () => {
  const {elements, context, window, getPending} = setup();
  assert.equal(getPending(), undefined);
  const point = {latitude: 55.672082, longitude: 37.488170, accuracy: 20};
  assert.equal(context.campusProximity(point), "near");
  assert.equal(context.campusProximity({...point, accuracy: 650}), "unknown");
  assert.equal(context.campusProximity({...point, latitude: 55.69}), "far");
  assert.equal(context.campusProximity({...point, accuracy: NaN}), "unknown");
  elements.check.click();
  const [ok, , options] = getPending();
  assert.equal(options.maximumAge, 0);
  ok({coords:point});
  assert.equal(elements.status.textContent, "Ты рядом с кампусом.");
  elements.clear.click();
  ok({coords:point});
  assert.equal(elements.status.textContent, "");
  elements.check.click();
  getPending()[1]({code:1});
  assert.match(elements.status.textContent, /закрыт/);
  window.pagehide();
  assert.equal(elements.status.textContent, "");
});
