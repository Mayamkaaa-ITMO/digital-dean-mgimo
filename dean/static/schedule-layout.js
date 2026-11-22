const timetable = document.querySelector("[data-schedule-hours]");
if (timetable) {
  timetable.style.setProperty("--schedule-hours", timetable.dataset.scheduleHours);
  let widestOverlap = 1;
  for (const card of timetable.querySelectorAll("[data-schedule-position]")) {
    const [start, duration, lane, lanes] = card.dataset.schedulePosition.split(" ");
    card.style.setProperty("--start", start);
    card.style.setProperty("--duration", duration);
    card.style.setProperty("--lane", lane);
    card.style.setProperty("--lanes", lanes);
    card.toggleAttribute("data-short-slot", Number(duration) < 62);
    widestOverlap = Math.max(widestOverlap, Number(lanes));
  }
  timetable.style.setProperty("--schedule-table-min", `${42 + 7 * (56 * widestOverlap + 4)}px`);
  timetable.setAttribute("data-timetable-ready", "");
}
