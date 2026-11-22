document.querySelectorAll("[data-room-lessons]").forEach((selection) => {
  const lessons = JSON.parse(selection.dataset.roomLessons);
  const bookings = JSON.parse(selection.dataset.roomBookings);
  const start = selection.querySelector('[name="starts"]');
  const end = selection.querySelector('[name="ends"]');
  const warning = selection.querySelector("[data-room-conflict]");
  const bookingWarning = selection.querySelector("[data-room-booking-conflict]");
  const acknowledgement = selection.querySelector("[data-room-ack]");
  const checkbox = acknowledgement.querySelector("input");
  const normalized = (value) => value.length === 16 ? value + ":00" : value;
  let previousStart = start.value;
  let previousEnd = end.value;

  const update = () => {
    if (previousStart !== start.value || previousEnd !== end.value) checkbox.checked = false;
    previousStart = start.value;
    previousEnd = end.value;
    const valid = start.validity.valid && end.validity.valid && start.value < end.value;
    const overlaps = (item) => valid && normalized(start.value) < item.end && normalized(end.value) > item.start;
    const overlapping = lessons.filter(overlaps);
    const own = [...new Set(bookings.filter((item) => overlaps(item) && !(item.sameSlot &&
      normalized(start.value) === item.start && normalized(end.value) === item.end)).map(({title}) => title))];
    bookingWarning.hidden = own.length === 0;
    if (own.length) bookingWarning.textContent = "Интервал пересекается с твоей записью. " + own.join(", ") + ". Выбери другое время.";
    warning.hidden = acknowledgement.hidden = overlapping.length === 0;
    checkbox.disabled = warning.hidden;
    checkbox.required = !warning.hidden;
    if (overlapping.length) {
      warning.textContent = "Интервал пересекается с учебной парой. " + overlapping.map(({title}) => title).join(", ") + ".";
    }
  };

  for (const field of [start, end]) {
    field.addEventListener("input", update);
    field.addEventListener("change", update);
  }
  addEventListener("pageshow", update);
  update();
});
