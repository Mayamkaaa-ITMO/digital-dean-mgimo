const mapZoom = document.querySelector("[data-campus-zoom]");
const viewport = document.querySelector("[data-campus-viewport]");
const map = viewport?.querySelector("svg");
const campusShell = document.querySelector(".campus-map");
const mobileMap = campusShell && matchMedia("(max-width: 780px)");
const mobileBar = campusShell?.querySelector(".campus-mobile-bar");
const mobileSearch = mobileBar?.querySelector("[data-campus-search-focus]");
const mobileDetails = mobileBar?.querySelector("[data-campus-details]");

function mapPane(next) {
  if (!mobileBar || !mobileMap.matches) return;
  campusShell.dataset.campusPane = next;
  mobileSearch.setAttribute("aria-expanded", String(next === "search"));
  mobileDetails.setAttribute("aria-expanded", String(next === "details"));
  const pane = campusShell.querySelector(next === "search" ? ".campus-search" : ".campus-details");
  if (pane) pane.scrollTop = 0;
}

if (mobileBar) {
  campusShell.setAttribute("data-campus-mobile-ready", "");
  const initial = () => {
    const hasResult = document.querySelector("#campus-query")?.value.trim();
    const showDetails = ["#campus-details", "#campus-place", "#campus-route", "#campus-route-fields"].includes(location.hash)
      || ["route", "place"].includes(campusShell.dataset.campusState)
      || Boolean(document.querySelector(".campus-place, .campus-route-error"));
    mapPane(hasResult || ["#campus-query", "#campus-search"].includes(location.hash) ? "search" : showDetails ? "details" : "map");
  };
  initial();
  mobileMap.addEventListener("change", initial);
  mobileDetails.addEventListener("click", (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    mapPane(campusShell.dataset.campusPane === "details" ? "map" : "details");
  });
  campusShell.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && mobileMap.matches && campusShell.dataset.campusPane !== "map") {
      const action = campusShell.dataset.campusPane === "search" ? mobileSearch : mobileDetails;
      mapPane("map");
      action.focus({preventScroll: true});
    }
  });
}

if (campusShell && window.visualViewport) {
  const updateVisibleHeight = () => {
    campusShell.style.setProperty("--campus-visible-height", `${Math.round(window.visualViewport.height)}px`);
  };
  window.visualViewport.addEventListener("resize", updateVisibleHeight);
  updateVisibleHeight();
}

if (mapZoom && map?.viewBox.baseVal.width) {
  const {x: minX, y: minY, width: fullWidth, height: fullHeight} = map.viewBox.baseVal;
  const smaller = mapZoom.querySelector('[data-campus-scale="-1"]');
  const larger = mapZoom.querySelector('[data-campus-scale="1"]');
  const areas = [...map.querySelectorAll(".campus-area")];
  const roomBounds = areas.map(area => area.getBBox());
  const byIndex = new Map(areas.map((area, i) =>
    [area.dataset.campusIndex ?? String(i), {area, room: roomBounds[i]}]));
  const labelHint = document.querySelector("[data-campus-label-hint]");
  const labels = [...map.querySelectorAll(".campus-room > text")].map(label => {
    const {area, room} = byIndex.get(label.dataset.campusArea) ?? {};
    const [x, y] = label.dataset.campusAnchor.split(" ").map(Number);
    const shared = label.hasAttribute("data-campus-shared");
    const text = label.textContent;
    return {label, area, room, x, y, shared, size: Number(label.getAttribute("font-size")),
      beacon: label.parentElement.querySelector("[data-campus-label-icon]"),
      callout: label.parentElement.querySelector("[data-campus-label-callout]"),
      names: [...new Set([label.dataset.campusOffice, text, label.dataset.campusShort].filter(Boolean))],
      selected: label.parentElement.classList.contains("campus-selected"),
      named: label.parentElement.classList.contains("campus-named-room")};
  }).sort((a, b) => Number(b.selected) - Number(a.selected) || Number(b.named) - Number(a.named) || b.size - a.size);
  const symbols = [...map.querySelectorAll("[data-campus-symbol]")];
  const groundsCaptions = [...map.querySelectorAll("[data-campus-grounds-caption]")];
  const fingers = new Map();
  let x = minX;
  let y = minY;
  let zoom = 1;
  let manualZoom = false;
  let dragged = false;
  const center = {x: minX + fullWidth / 2, y: minY + fullHeight / 2};
  const nearestRoom = labels.reduce((nearest, candidate) =>
    Math.hypot(candidate.x - center.x, candidate.y - center.y) <
    Math.hypot(nearest.x - center.x, nearest.y - center.y) ? candidate : nearest, labels[0] || center);
  let cameraFocus = {x: nearestRoom.x, y: nearestRoom.y};
  let defaultFocus = cameraFocus;
  let previousDetail = {};
  let detailFrame = 0;

  function queueDetail() {
    if (detailFrame) return;
    detailFrame = requestAnimationFrame(() => {
      detailFrame = 0;
      refreshDetail();
    });
  }

  function measureLabels() {
    if (!labels.length) return;
    const samples = document.createElementNS(map.namespaceURI, "g");
    samples.setAttribute("class", "campus-room");
    samples.setAttribute("visibility", "hidden");
    samples.setAttribute("aria-hidden", "true");
    const candidates = labels.map(row => row.names.map(text => {
      const label = row.label.cloneNode(false);
      label.removeAttribute("transform");
      label.setAttribute("font-size", row.size);
      label.textContent = text;
      samples.append(label);
      return label;
    }));
    // Read SVG geometry after all sample text has been written.
    map.append(samples);
    labels.forEach((row, index) => {
      const y = Number(row.label.getAttribute("y"));
      row.measures = candidates[index].map(label => {
        const bounds = label.getBBox();
        return {text: label.textContent, width: bounds.width / row.size,
          height: 2 * Math.max(y - bounds.y, bounds.y + bounds.height - y) / row.size};
      });
    });
    samples.remove();
    previousDetail = {};
  }

  function encloses(box, label) {
    return label.x - label.width / 2 >= box.x && label.x + label.width / 2 <= box.x + box.width &&
      label.y - label.height / 2 >= box.y && label.y + label.height / 2 <= box.y + box.height;
  }

  function fitsShape(area, rect) {
    if (!area?.isPointInFill) return true;
    const {x, y, width, height} = rect;
    const point = new DOMPoint(x, y);
    if (!area.isPointInFill(point)) return false;
    const test = (px, py) => {
      point.x = px;
      point.y = py;
      return area.isPointInFill(point);
    };
    const columns = Math.max(1, Math.min(32, Math.ceil(width / 2)));
    const rows = Math.max(1, Math.min(32, Math.ceil(height / 2)));
    for (let i = 0; i <= columns; i++) {
      const px = x + width * (i / columns - .5);
      if (!test(px, y - height / 2) || !test(px, y + height / 2)) return false;
    }
    for (let i = 1; i < rows; i++) {
      const py = y + height * (i / rows - .5);
      if (!test(x - width / 2, py) || !test(x + width / 2, py)) return false;
    }
    return true;
  }

  function overlapsLabel(rect, labels) {
    return labels.some(other => Math.abs(rect.x - other.x) * 2 < rect.width + other.width &&
      Math.abs(rect.y - other.y) * 2 < rect.height + other.height);
  }

  function placeLabel(row, screenScale, view, shown) {
    const {room, shared, named} = row;
    const minimum = (named ? 9.5 : 8) / screenScale;
    const preferred = Math.min((named ? 14.5 : 14) / screenScale, Math.max(minimum, row.size));
    const pad = 2.5 / screenScale;
    const spots = room && !shared ? [
      [room.x + room.width / 2, room.y + room.height / 2],
      [row.x, row.y],
      [room.x + room.width / 2, room.y + room.height / 4],
      [room.x + room.width / 2, room.y + room.height * .75],
    ] : [[row.x, row.y]];
    for (const measure of row.measures) {
      const canRotate = room && room.height > room.width * 1.35 && /^\d+[а-яa-z]?$/i.test(measure.text);
      for (const rotated of canRotate ? [false, true] : [false]) {
        const horizontal = rotated ? measure.height : measure.width;
        const vertical = rotated ? measure.width : measure.height;
        const font = room ? Math.min(preferred, (room.width - pad) / horizontal,
          (room.height - pad) / vertical) : preferred;
        if (font < minimum - .01) continue;
        const attempts = font > minimum + 1 ? [font, minimum] : [font];
        for (const size of attempts) {
          const width = horizontal * size + pad;
          const height = vertical * size + pad;
          for (const [left, top] of spots) {
            const x = room ? Math.min(room.x + room.width - width / 2, Math.max(room.x + width / 2, left)) : left;
            const y = room ? Math.min(room.y + room.height - height / 2, Math.max(room.y + height / 2, top)) : top;
            const rect = {x, y, width, height};
            if (!encloses(view, rect) || (room && (!encloses(room, rect) || !fitsShape(row.area, rect)))) continue;
            if (!overlapsLabel(rect, shown)) return {...rect, size, rotated, text: measure.text};
          }
        }
      }
    }
    return null;
  }

  function facilityCallout(row, screenScale, view, shown) {
    const {callout, x, y} = row;
    callout.setAttribute("hidden", "");
    if (!fitsShape(row.area, {x, y, width: 0, height: 0})) return false;
    const unit = 1 / screenScale;
    const width = (row.measures[0].width * 10.5 + 14) * unit;
    const height = 21 * unit;
    for (const gap of [14, 29, 46].map(value => value * unit)) {
      const spots = [[0, -height / 2 - gap], [0, height / 2 + gap],
        [-width / 2 - gap, 0], [width / 2 + gap, 0]];
      for (const [dx, dy] of spots) {
        const rect = {x: x + dx, y: y + dy, width: width + 4 * unit, height: height + 4 * unit};
        if (!encloses(view, rect) || overlapsLabel(rect, shown) || roomBounds.some(room =>
          rect.x + rect.width / 2 > room.x && rect.x - rect.width / 2 < room.x + room.width &&
          rect.y + rect.height / 2 > room.y && rect.y - rect.height / 2 < room.y + room.height)) continue;
        const [stem, dot, pill, title] = callout.children;
        stem.setAttribute("d", `M${x} ${y}L${rect.x} ${rect.y}`);
        for (const [node, attributes] of [[dot, {cx: x, cy: y, r: 2.5 * unit}],
          [pill, {x: rect.x - width / 2, y: rect.y - height / 2, width, height, rx: 5 * unit}],
          [title, {x: rect.x, y: rect.y, "font-size": 10.5 * unit}]]) {
          for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
        }
        callout.removeAttribute("hidden");
        shown.push(rect);
        return true;
      }
    }
    return false;
  }

  function labelBeacon(row, screenScale, view, shown) {
    const {beacon, area, x, y} = row;
    const size = 15 / screenScale;
    const rect = {x, y, width: size, height: size};
    const available = encloses(view, rect) && fitsShape(area, rect) && !overlapsLabel(rect, shown);
    beacon.toggleAttribute("hidden", !available);
    if (available) {
      beacon.setAttribute("transform", `translate(${x} ${y}) scale(${(size / 30).toFixed(3)})`);
      shown.push(rect);
    }
  }

  function refreshDetail() {
    const {clientWidth, clientHeight} = map;
    const screenScale = Math.min(clientWidth / fullWidth, clientHeight / fullHeight) * zoom;
    if (screenScale <= 0 || (previousDetail.width === clientWidth && previousDetail.height === clientHeight &&
        previousDetail.x === x && previousDetail.y === y &&
        Math.abs(previousDetail.scale - screenScale) < .001)) return;
    previousDetail = {x, y, scale: screenScale, width: clientWidth, height: clientHeight};
    if (groundsCaptions.length) {
      const visible = map.getBoundingClientRect();
      for (const caption of groundsCaptions) {
        caption.setAttribute("transform", `scale(${Math.min(2.3, .84 / screenScale)})`);
        const bounds = caption.getBoundingClientRect();
        caption.toggleAttribute("data-campus-clipped", bounds.left < visible.left || bounds.top < visible.top ||
          bounds.right > visible.right || bounds.bottom > visible.bottom);
      }
    }
    const view = map.viewBox.baseVal;
    const visibleWidth = clientWidth / screenScale;
    const visibleHeight = clientHeight / screenScale;
    // The SVG viewport can show extra floor space around its centered viewBox.
    const box = {x: view.x + (view.width - visibleWidth) / 2, y: view.y + (view.height - visibleHeight) / 2,
      width: visibleWidth, height: visibleHeight};
    const diameter = 30 * screenScale;
    const symbolScale = Math.min(2.8, Math.min(22, Math.max(15, diameter)) / diameter);
    const shown = symbols.map(symbol => {
      const {e: x, f: y} = symbol.parentElement.transform.baseVal.getItem(0).matrix;
      const size = (symbol.closest(".campus-route-marker") ? 42 : 30) * symbolScale + 3 / screenScale;
      symbol.setAttribute("transform", `scale(${symbolScale.toFixed(2)})`);
      return {x, y, width: size, height: size};
    });
    let displayed = 0;
    for (const row of labels) {
      const {label, beacon, callout} = row;
      const placed = placeLabel(row, screenScale, box, shown);
      label.toggleAttribute("data-campus-small", !placed);
      callout?.setAttribute("hidden", "");
      beacon?.setAttribute("hidden", "");
      if (!placed) {
        if (callout && !facilityCallout(row, screenScale, box, shown) && beacon) {
          labelBeacon(row, screenScale, box, shown);
        }
        continue;
      }
      displayed += 1;
      const {x, y, size, rotated, text} = placed;
      if (label.textContent !== text) label.textContent = text;
      label.setAttribute("x", x.toFixed(2));
      label.setAttribute("y", y.toFixed(2));
      label.setAttribute("font-size", size.toFixed(2));
      if (rotated) label.setAttribute("transform", `rotate(-90 ${x} ${y})`);
      else label.removeAttribute("transform");
      shown.push(placed);
    }
    if (labelHint) labelHint.hidden = displayed > 0 || zoom > 1.5;
  }

  function show() {
    const w = fullWidth / zoom;
    const h = fullHeight / zoom;
    x = Math.min(minX + fullWidth - w, Math.max(minX, x));
    y = Math.min(minY + fullHeight - h, Math.max(minY, y));
    map.setAttribute("viewBox", [x, y, w, h].join(" "));
    viewport.toggleAttribute("data-campus-enlarged", zoom > 1);
    viewport.toggleAttribute("data-campus-pannable", zoom > 1 && manualZoom);
    smaller.disabled = zoom <= 1;
    larger.disabled = zoom >= 5;
    queueDetail();
  }
  function point(clientX, clientY) {
    const local = map.createSVGPoint();
    local.x = clientX;
    local.y = clientY;
    return local.matrixTransform(map.getScreenCTM().inverse());
  }
  function scale(next, anchor) {
    const bounded = Math.min(5, Math.max(1, next));
    if (bounded === 1) cameraFocus = defaultFocus;
    if (cameraFocus && !anchor) {
      x = cameraFocus.x - fullWidth / (2 * bounded);
      y = cameraFocus.y - fullHeight / (2 * bounded);
    } else {
      const focusX = anchor?.x ?? x + fullWidth / (2 * zoom);
      const focusY = anchor?.y ?? y + fullHeight / (2 * zoom);
      x = focusX - (focusX - x) * zoom / bounded;
      y = focusY - (focusY - y) * zoom / bounded;
    }
    zoom = bounded;
    manualZoom = zoom > 1;
    show();
  }
  function fitSelection() {
    const canteen = map.querySelector(".campus-grounds-canteen[aria-current] .campus-grounds-food-foot");
    if (canteen) {
      const bounds = canteen.getBoundingClientRect();
      cameraFocus = point(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
      return;
    }
    const route = map.querySelector(".campus-route-line");
    const selected = map.querySelector("[data-campus-focus]");
    const extent = map.querySelector(".campus-route-marker") && map.querySelector("[data-campus-route-extent]");
    const target = extent || selected?.querySelector(".campus-room-hit") || selected;
    if (!target) return;
    const boxes = [target.getBBox()];
    if (extent) {
      for (const hit of map.querySelectorAll(".campus-room.campus-selected .campus-room-hit")) {
        boxes.push(hit.getBBox());
      }
    }
    const left = Math.min(...boxes.map(box => box.x));
    const top = Math.min(...boxes.map(box => box.y));
    const width = Math.max(...boxes.map(box => box.x + box.width)) - left;
    const height = Math.max(...boxes.map(box => box.y + box.height)) - top;
    const mobile = viewport.clientWidth < 450;
    const maxZoom = route ? (mobile ? 2.9 : 2.35) : (mobile ? 4.2 : 3);
    const margin = route ? .22 : .18;
    zoom = Math.max(1, Math.min(maxZoom, fullWidth / (width + fullWidth * margin),
      fullHeight / (height + fullHeight * margin)));
    cameraFocus = {x: left + width / 2, y: top + height / 2};
    x = cameraFocus.x - fullWidth / (2 * zoom);
    y = cameraFocus.y - fullHeight / (2 * zoom);
    show();
    if (route && map.querySelector(".campus-route-reference")) {
      const end = route.points.getItem(route.points.numberOfItems - 1);
      cameraFocus = {x: end.x, y: end.y};
    }
  }

  smaller.addEventListener("click", () => scale(zoom / 1.4));
  larger.addEventListener("click", () => scale(zoom * 1.4));
  mapZoom.querySelector("[data-campus-fit]").addEventListener("click", () => scale(1));
  viewport.addEventListener("wheel", (event) => {
    if (event.ctrlKey || event.metaKey || document.activeElement !== viewport) return;
    event.preventDefault();
    const anchor = point(event.clientX, event.clientY);
    cameraFocus = null;
    scale(zoom * (event.deltaY > 0 ? .88 : 1.14), anchor);
  }, { passive: false });
  viewport.addEventListener("keydown", (event) => {
    if (event.target !== viewport) return;
    const directions = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    if (event.key === "+" || event.key === "=" || event.key === "-") {
      event.preventDefault();
      scale(zoom * (event.key === "-" ? 1 / 1.4 : 1.4));
    } else if (event.key === "Home") {
      event.preventDefault();
      scale(1);
    } else if (directions[event.key] && zoom > 1) {
      event.preventDefault();
      cameraFocus = null;
      const [dx, dy] = directions[event.key];
      x += dx * fullWidth / (10 * zoom);
      y += dy * fullHeight / (10 * zoom);
      show();
    }
  });
  viewport.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || fingers.size >= 2) return;
    if (!fingers.size) dragged = false;
    fingers.set(event.pointerId, [event.clientX, event.clientY]);
  });
  viewport.addEventListener("pointermove", (event) => {
    const before = fingers.get(event.pointerId);
    if (!before) return;
    const after = [event.clientX, event.clientY];
    if (fingers.size === 1 && zoom <= 1) {
      fingers.set(event.pointerId, after);
      return;
    }
    if (event.pointerType === "touch" && fingers.size === 1 && !manualZoom && !dragged &&
        Math.abs(after[1] - before[1]) > Math.abs(after[0] - before[0])) return;
    const delta = Math.hypot(after[0] - before[0], after[1] - before[1]);
    if (!dragged && delta < 4 && fingers.size < 2) return;
    dragged = true;
    cameraFocus = null;
    viewport.dataset.dragging = "";
    if (!viewport.hasPointerCapture(event.pointerId)) viewport.setPointerCapture(event.pointerId);
    if (fingers.size === 2) {
      const other = [...fingers].find(([id]) => id !== event.pointerId)[1];
      const oldDistance = Math.hypot(before[0] - other[0], before[1] - other[1]);
      const distance = Math.hypot(after[0] - other[0], after[1] - other[1]);
      if (oldDistance > 0 && distance > 0) {
        const pivot = point((before[0] + other[0]) / 2, (before[1] + other[1]) / 2);
        scale(zoom * distance / oldDistance, pivot);
        const moved = point((after[0] + other[0]) / 2, (after[1] + other[1]) / 2);
        x += pivot.x - moved.x;
        y += pivot.y - moved.y;
        show();
      }
    } else if (zoom > 1) {
      const a = point(...before);
      const b = point(...after);
      x -= b.x - a.x;
      y -= b.y - a.y;
      show();
    }
    fingers.set(event.pointerId, after);
  });
  function release(event) {
    fingers.delete(event.pointerId);
    if (!fingers.size) delete viewport.dataset.dragging;
  }
  viewport.addEventListener("pointerup", release);
  viewport.addEventListener("pointercancel", release);
  viewport.addEventListener("lostpointercapture", (event) => {
    if (event.target === viewport) release(event);
  });
  viewport.addEventListener("pointerleave", (event) => {
    if (!viewport.hasPointerCapture(event.pointerId)) release(event);
  });
  viewport.addEventListener("click", (event) => {
    if (dragged && event.detail > 0) event.preventDefault();
    dragged = false;
  }, true);
  viewport.dataset.campusInteractive = "";
  mapZoom.hidden = false;
  measureLabels();
  show();
  fitSelection();
  defaultFocus = cameraFocus;
  if (document.fonts?.status === "loading") {
    document.fonts.ready.then(() => { measureLabels(); queueDetail(); });
  }
  if ("ResizeObserver" in window) new ResizeObserver(queueDetail).observe(map);
}

function normalizePoint(value, latinB = "в") {
  const suffixes = {a: "а", b: latinB, v: "в", c: "с", k: "к", g: "г"};
  return value.replaceAll("№", " ").normalize("NFKC").toLowerCase().replaceAll("ё", "е")
    .replace(/(?<![\p{L}\d])(?:аудитория|ауд|кабинет|помещение)(?![\p{L}\d])\.?/gu, " ")
    .replace(/(?<![\p{L}\d])(?:этаж\s*(\d+)|(\d+)(?:\s*[-‐‑–]?\s*й)?\s*эт(?:аж)?\.?(?![\p{L}\d]))/gu,
      (_, before, after) => " этаж" + (before || after) + " ")
    .replace(/(\d+)[\s\-‐‑‒–—]*([а-яa-z])(?![\p{L}\d])/gu,
      (_, digits, letter) => digits + (suffixes[letter] || letter))
    .replace(/[^0-9a-zа-я]+/g, " ").trim();
}

function addPointLookup(select) {
  const caption = select.closest("label");
  const field = document.createElement("input");
  field.id = "campus-point-" + select.name;
  field.type = "search";
  field.placeholder = "Номер или название";
  field.autocomplete = "off";
  field.required = true;
  field.setAttribute("role", "combobox");
  field.setAttribute("aria-autocomplete", "list");
  field.setAttribute("aria-expanded", "false");
  const list = document.createElement("div");
  list.id = "campus-results-" + select.name;
  list.className = "campus-endpoint-results";
  list.setAttribute("role", "listbox");
  list.hidden = true;
  field.setAttribute("aria-controls", list.id);

  const options = Array.from(select.options).filter(option => option.value).map(option => {
    const title = option.textContent + " · " + option.parentElement.label;
    return { id: option.value, title, search: option.dataset.campusSearch.split(" "),
      name: normalizePoint(option.dataset.campusLabel) };
  });
  const selectedTitle = () => options.find(row => row.id === select.value)?.title || "";
  field.value = selectedTitle();
  caption.htmlFor = field.id;
  caption.append(field);
  caption.after(list);
  select.hidden = true;
  select.required = false;
  let active = -1;
  let results = [];
  function close() {
    list.hidden = true;
    field.setAttribute("aria-expanded", "false");
    field.removeAttribute("aria-activedescendant");
  }
  function use(row) {
    select.value = row.id;
    select.dispatchEvent(new Event("change", {bubbles: true}));
    field.focus();
  }
  select.addEventListener("change", () => {
    field.value = selectedTitle();
    field.setCustomValidity("");
    close();
  });
  function highlight(index) {
    active = index;
    const buttons = list.querySelectorAll("button");
    buttons.forEach((button, i) => button.setAttribute("aria-selected", String(i === index)));
    if (buttons[index]) {
      field.setAttribute("aria-activedescendant", buttons[index].id);
      buttons[index].scrollIntoView({block: "nearest"});
    }
  }
  function render(query) {
    const value = normalizePoint(query);
    const variants = [...new Set([value, normalizePoint(query, "б")])].map(q => q.split(" ").filter(Boolean));
    const score = (row) => row.name === value ? 0 : row.name.startsWith(value) ? 1 : 2;
    const matches = (word, token) => token === word ||
      (word.length > 1 && !/^(?:\d|этаж)/.test(word) && token.startsWith(word));
    results = options.filter(row => variants.some(words => words.every(word => row.search.some(token => matches(word, token)))))
      .sort((first, second) => score(first) - score(second)).slice(0, 8);
    list.replaceChildren();
    if (!results.length) {
      const state = document.createElement("p");
      state.textContent = "Такого места нет на этой схеме. Проверь номер или название.";
      state.setAttribute("role", "status");
      list.append(state);
    }
    results.forEach((row, i) => {
      const button = document.createElement("button");
      button.id = list.id + "-" + i;
      button.type = "button";
      button.className = "campus-button";
      button.setAttribute("role", "option");
      button.textContent = row.title;
      button.addEventListener("click", () => use(row));
      list.append(button);
    });
    highlight(-1);
    list.hidden = false;
    field.setAttribute("aria-expanded", "true");
  }
  field.addEventListener("input", () => {
    select.value = "";
    field.setCustomValidity("Выбери точку из подсказок.");
    render(field.value);
  });
  field.addEventListener("keydown", event => {
    if (event.key === "Escape") close();
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (list.hidden) render(select.value ? "" : field.value);
      if (results.length) {
        const next = active < 0 ? (event.key === "ArrowDown" ? 0 : results.length - 1)
          : (active + (event.key === "ArrowDown" ? 1 : -1) + results.length) % results.length;
        highlight(next);
      }
    }
    if (event.key === "Enter" && !list.hidden && results.length) {
      event.preventDefault();
      if (active < 0 && results.length > 1 && normalizePoint(field.value) !== normalizePoint(field.value, "б")) {
        highlight(0);
        return;
      }
      use(results[Math.max(0, active)]);
    }
  });
  field.addEventListener("focus", () => {
    if (select.value) field.select();
    else render(field.value);
  });
  field.addEventListener("blur", event => {
    if (!list.contains(event.relatedTarget)) close();
  });
  list.addEventListener("focusout", event => {
    if (!list.contains(event.relatedTarget) && event.relatedTarget !== field) close();
  });
}

document.querySelectorAll(".campus-route-form select").forEach(addPointLookup);

const routing = document.querySelector(".campus-route-form");
const swapButton = routing?.querySelector("[data-campus-swap]");
if (swapButton) {
  const start = routing.elements.namedItem("from");
  const finish = routing.elements.namedItem("to");
  const syncSwap = () => { swapButton.disabled = !start.value || !finish.value; };
  routing.addEventListener("input", syncSwap);
  routing.addEventListener("change", syncSwap);
  swapButton.addEventListener("click", () => {
    [start.value, finish.value] = [finish.value, start.value];
    for (const point of [start, finish]) point.dispatchEvent(new Event("change", {bubbles: true}));
  });
  syncSwap();
  swapButton.hidden = false;
}

const buildingMenu = document.querySelector(".campus-building");
if (buildingMenu) {
  document.addEventListener("pointerdown", (event) => {
    if (buildingMenu.open && !buildingMenu.contains(event.target)) buildingMenu.open = false;
  });
  buildingMenu.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && buildingMenu.open) {
      event.preventDefault();
      buildingMenu.open = false;
      buildingMenu.querySelector("summary").focus();
    }
  });
}

const campusSearch = document.querySelector("#campus-search input[name=q]");
const quickResults = document.querySelector("[data-campus-quick-results]");
if (campusSearch && quickResults) {
  const submitted = document.querySelector("[data-campus-search-submitted]");
  const shortcuts = document.querySelector("[data-campus-shortcuts]");
  let debounce;
  let pending;
  quickResults.id = "campus-quick-results";
  quickResults.setAttribute("aria-live", "polite");
  campusSearch.setAttribute("aria-controls", quickResults.id);

  async function loadMatches(controller) {
    const query = new URLSearchParams(new FormData(campusSearch.form));
    try {
      const response = await fetch("/campus-map/suggest?" + query, {signal: controller.signal});
      if (!response.ok) return;
      const {results} = await response.json();
      if (controller.signal.aborted) return;
      const list = document.createElement("ul");
      list.className = "campus-quick-results";
      for (const result of results) {
        if (!result.href.startsWith("/campus-map?") &&
            !(result.official && result.href.startsWith("https://mgimo.ru/"))) continue;
        const item = document.createElement("li");
        const open = document.createElement("a");
        open.href = result.href;
        if (result.place === "canteen") open.className = "campus-result-canteen";
        const name = document.createElement("span");
        name.textContent = result.display_title || result.title;
        const location = document.createElement("small");
        location.textContent = result.floor + (result.hint ?? (result.location_only ? " · Подход по плану" : ""));
        if (result.unavailable_start) open.dataset.unavailableStart = "";
        name.append(location);
        open.append(name);
        item.append(open);
        list.append(item);
      }
      if (list.childElementCount) {
        quickResults.replaceChildren(list);
      } else {
        const empty = document.createElement("p");
        empty.className = "campus-empty";
        empty.textContent = "Такого места нет на этой схеме. Проверь номер или название.";
        quickResults.replaceChildren(empty);
      }
      quickResults.hidden = false;
      if (shortcuts) shortcuts.hidden = true;
    } catch {
      // The search form still works if its suggestions cannot load.
    }
  }

  function closeMatches() {
    clearTimeout(debounce);
    pending?.abort();
    quickResults.hidden = true;
    if (shortcuts) shortcuts.hidden = false;
  }

  campusSearch.addEventListener("input", () => {
    closeMatches();
    if (submitted) submitted.hidden = true;
    if (campusSearch.value.trim().length < 2) return;
    pending = new AbortController();
    const current = pending;
    debounce = setTimeout(() => loadMatches(current), 140);
  });
  campusSearch.addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    if (event.key === "Escape") closeMatches();
    if (event.key === "Enter" && !quickResults.hidden) {
      const links = quickResults.querySelectorAll("a");
      if (links.length === 1 && !links[0].hasAttribute("data-unavailable-start") && !event.ctrlKey && !event.metaKey) {
        event.preventDefault();
        links[0].click();
      }
    }
    if (event.key === "ArrowDown" && !quickResults.hidden) {
      const first = quickResults.querySelector("a");
      if (first) {
        event.preventDefault();
        first.focus();
      }
    }
  });
  quickResults.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeMatches();
      campusSearch.focus();
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const links = [...quickResults.querySelectorAll("a")];
      const current = links.indexOf(event.target);
      if (current < 0) return;
      event.preventDefault();
      const next = current + (event.key === "ArrowDown" ? 1 : -1);
      if (next < 0) campusSearch.focus();
      else links[Math.min(next, links.length - 1)].focus();
    }
  });
  document.querySelectorAll("[data-campus-search-focus]").forEach(action => {
    action.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      if (mobileBar && mobileMap.matches) {
        if (campusShell.dataset.campusPane === "search") {
          mapPane("map");
          mobileSearch.focus({preventScroll: true});
          return;
        }
        mapPane("search");
      }
      campusSearch.focus({preventScroll: true});
      if (!mobileBar || !mobileMap.matches) campusSearch.closest(".campus-search").scrollIntoView({block: "start"});
    });
  });
}

const routeFields = document.querySelector("#campus-route-fields");
if (routeFields) {
  document.querySelectorAll("[data-campus-edit]").forEach(action => action.addEventListener("click", (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (mobileBar && mobileMap.matches) mapPane("details");
    for (let panel = routeFields.closest("details"); panel; panel = panel.parentElement.closest("details")) {
      panel.open = true;
    }
    routeFields.querySelector('input[type="search"]')?.focus({preventScroll: true});
    routeFields.scrollIntoView({block: "start"});
  }));
}

const share = document.querySelector("[data-campus-share]");
if (share) {
  const status = document.querySelector("[data-campus-share-status]");
  const input = document.querySelector("[data-campus-share-url]");
  share.hidden = false;
  share.addEventListener("click", async () => {
    const address = new URL(share.dataset.campusShare, location.origin);
    try {
      await navigator.clipboard.writeText(address.href);
      status.textContent = "Ссылка скопирована.";
    } catch {
      input.value = address.href;
      input.hidden = false;
      input.focus();
      input.select();
      status.textContent = "Ссылка выделена для копирования.";
    }
  });
}


// A result link identifies the card even when the plan and the card are far apart.
if (location.hash === "#campus-query" && !mobileMap?.matches) campusSearch?.focus();
if (location.hash === "#campus-place") {
  document.getElementById("campus-place")?.focus({preventScroll: mobileMap?.matches});
}

function campusProximity({latitude, longitude, accuracy}) {
  if (![latitude, longitude, accuracy].every(Number.isFinite) || accuracy < 0 ||
      Math.abs(latitude) > 90 || Math.abs(longitude) > 180) return "unknown";
  // Address point for 76, building V. This circle is a proximity hint, not a campus boundary.
  const radians = Math.PI / 180;
  const lat = 55.672082 * radians;
  const dLat = latitude * radians - lat;
  const dLon = (longitude - 37.488170) * radians;
  const hav = Math.sin(dLat / 2) ** 2 + Math.cos(lat) * Math.cos(latitude * radians) * Math.sin(dLon / 2) ** 2;
  const distance = 2 * 6371000 * Math.asin(Math.sqrt(Math.min(1, hav)));
  if (distance + accuracy <= 500) return "near";
  if (distance - accuracy > 500) return "far";
  return "unknown";
}

const nearbyPanel = document.querySelector("[data-campus-nearby]");
if (nearbyPanel && window.isSecureContext && navigator.geolocation) {
  const check = nearbyPanel.querySelector("[data-nearby-check]");
  const clear = nearbyPanel.querySelector("[data-nearby-clear]");
  const status = nearbyPanel.querySelector("[data-nearby-status]");
  const messages = {near: "Ты рядом с кампусом.", far: "Ты вне радиуса 500 м.",
    unknown: "По текущей точности определить близость не удалось."};
  let requestNumber = 0;
  function stopCheck() {
    requestNumber++;
    status.textContent = "";
    check.disabled = false;
    clear.hidden = true;
  }
  check.addEventListener("click", () => {
    const current = ++requestNumber;
    const finish = (message) => {
      if (current !== requestNumber) return;
      status.textContent = message;
      check.disabled = false;
    };
    check.disabled = true;
    clear.hidden = false;
    status.textContent = "Запрашиваем местоположение один раз…";
    navigator.geolocation.getCurrentPosition(
      position => finish(messages[campusProximity(position.coords)]),
      error => finish(error.code === 1 ? "Доступ к местоположению закрыт." : "Местоположение определить не удалось."),
      {maximumAge: 0, timeout: 10000, enableHighAccuracy: false}
    );
  });
  clear.addEventListener("click", () => { stopCheck(); check.focus(); });
  window.addEventListener("pagehide", stopCheck);
  nearbyPanel.hidden = false;
}
