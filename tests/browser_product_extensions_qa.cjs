/* Run with Node.js, Playwright and installed Google Chrome. The model is a fixture. */
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createInterface } = require("node:readline");
const { chromium } = require(process.env.DEAN_PLAYWRIGHT || "playwright");

const root = path.resolve(__dirname, "..");
const first = "2031-05-12T08:00:00+00:00";
const week = "2031-05-12";
const photo = "/calendar/import/photo";
const subject = "Фото · Проектная практика";

async function server(directory) {
  const child = spawn(process.env.DEAN_PYTHON || "python3", ["-B", "-m", "tests.product_extensions_browser_server", directory], {
    cwd: root,
    stdio: ["ignore", "pipe", "inherit"],
    env: { ...process.env, DEAN_EXT_EVENTS: "1", DEAN_EXT_PHOTO_IMPORT: "1", DEAN_TELEGRAM_MODE: "off" },
  });
  const output = createInterface({ input: child.stdout });
  try {
    const response = await Promise.race([
      once(output, "line").then(([line]) => JSON.parse(line)),
      once(child, "exit").then(([code]) => { throw new Error("Loopback fixture stopped with code " + code); }),
    ]);
    const passwords = JSON.parse(fs.readFileSync(path.join(directory, "demo.sqlite3.accounts.json")));
    return { ...response, passwords, child };
  } finally {
    output.close();
  }
}

function control(directory, now, provider = "success") {
  const target = path.join(directory, "controls.json");
  fs.writeFileSync(target + ".tmp", JSON.stringify({ now, provider }));
  fs.renameSync(target + ".tmp", target);
}

const button = (page, name) => page.getByRole("button", { name, exact: true });
const link = (page, name) => page.getByRole("link", { name, exact: true });
const shown = locator => locator.first().waitFor({ state: "visible" });

async function fits(page, width) {
  const sizes = await page.evaluate(() => [innerWidth, document.documentElement.scrollWidth]);
  assert.deepEqual(sizes, [width, width], new URL(page.url()).pathname);
}

async function post(page, locator, status = 303) {
  const response = page.waitForResponse(r => r.request().method() === "POST" && r.request().isNavigationRequest());
  await locator.click();
  const submitted = await response;
  assert.equal(submitted.status(), status, new URL(submitted.url()).pathname);
  const u = new URL(submitted.url());
  assert.equal(await submitted.request().headerValue("origin"), u.origin);
  const location = await submitted.headerValue("location");
  await page.waitForURL(location ? new URL(location, u).href : u.href, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("domcontentloaded");
}

async function signIn(page, fixture, role) {
  await page.goto(fixture.url + "/login");
  await page.getByLabel("Логин", { exact: true }).fill(role);
  await page.getByLabel("Пароль", { exact: true }).fill(fixture.passwords[role]);
  await post(page, button(page, "Войти"));
  const dismiss = page.locator("dialog[open].dean-tour [data-overlay-dismiss]");
  if (await dismiss.count()) {
    await dismiss.click();
    await page.waitForLoadState("domcontentloaded");
  }
}

async function createEvent(page, fixture, title, start, end, mapped) {
  await page.goto(fixture.url + "/staff/manage");
  await link(page, "Создать мероприятие").click();
  await page.locator('[name="group"]').selectOption(fixture.group);
  for (const [name, value] of Object.entries({
    title, category: "Встречи", host: "Лада Синтетова", rules: "Учебный практикум.",
    place: "Учебная аудитория", starts: start, ends: end,
  })) await page.locator('[name="' + name + '"]').fill(value);
  if (mapped) await page.locator('select[name="map_room"]').selectOption({
    label: await page.locator('select[name="map_room"] option').filter({ hasText: / · 1012$/ }).first().textContent(),
  });
  await post(page, button(page, "Сохранить черновик"));
  return page.url();
}

async function register(page) {
  const ack = page.locator('[name="ack"]');
  if (await ack.count()) await ack.check();
  await post(page, button(page, "Подтвердить запись"));
  return page.url();
}

async function events(fixture, directory, student, other, staff, width) {
  const title = "Практикум · Живой макет";
  const staffEvent = await createEvent(staff, fixture, title, "2031-05-14T18:00", "2031-05-14T19:00", true);
  await shown(staff.getByText("Черновик", { exact: true }));
  await fits(staff, width);
  await student.goto(fixture.url + "/all-services");
  await link(student, "Афиша").click();
  assert.equal(await link(student, title).count(), 0);
  await post(staff, button(staff, "Опубликовать"));
  await student.reload();
  await student.getByLabel("Поиск", { exact: true }).fill("макет синтетова");
  await student.getByLabel("Категория", { exact: true }).selectOption("Встречи");
  await student.getByLabel("Когда", { exact: true }).selectOption("week");
  await student.getByLabel("Запись", { exact: true }).selectOption("1");
  await button(student, "Найти").click();
  await shown(student.getByText("Свободно 1 место", { exact: true }));
  await fits(student, width);
  await link(student, title).click();
  await shown(student.getByText("Организатор: Лада Синтетова"));
  const card = student.url();
  await link(student, "Маршрут до аудитории").click();
  assert.ok(new URL(student.url()).searchParams.get("to"));
  await student.goto(card);
  await student.locator('a[href^="/slots/"]').first().click();
  const slot = student.url();
  const initial = await register(student);
  await shown(student.getByText("Регистрация добавлена в твой календарь."));
  await fits(student, width);
  const download = student.waitForEvent("download");
  await link(student, "Скачать .ics").click();
  assert.match((await download).suggestedFilename(), /\.ics$/);
  await link(student, "Открыть в календаре").click();
  await shown(link(student, title));
  await fits(student, width);
  assert.equal((await other.goto(initial)).status(), 404);
  await other.goto(slot);
  await shown(other.getByText("Свободных мест нет."));
  await student.goto(initial);
  await post(student, button(student, "Отменить запись"));
  await shown(student.getByText("Отменена", { exact: true }));
  await student.goto(slot);
  const booking = await register(student);
  assert.notEqual(booking, initial);
  await post(student, button(student, "Напомнить за 24 часа"));
  await shown(student.getByText("Напоминание за 24 часа включено."));
  await staff.locator('[name="starts"]').fill("2031-05-15T18:00");
  await staff.locator('[name="ends"]').fill("2031-05-15T19:00");
  await staff.locator('[name="capacity"]').fill("2");
  await post(staff, button(staff, "Сохранить изменения"));
  await shown(staff.getByText("Правка", { exact: true }));
  await link(staff, "Открыть список участников").click();
  await shown(staff.getByText("Мира Яроветова", { exact: true }));
  await fits(staff, width);
  await student.goto(booking);
  await post(student, button(student, "Получить билет"), 200);
  const qr = student.getByAltText("QR-код билета");
  await shown(qr);
  assert.ok(await qr.evaluate(img => img.naturalWidth > 0));
  const code = await student.getByLabel("Код для ручного ввода").inputValue();
  await fits(student, width);
  control(directory, "2031-05-15T14:50:00+00:00");
  await student.goto(fixture.url + "/notifications");
  await shown(student.getByText("Мероприятие перенесено", { exact: true }));
  await shown(student.getByText("Скоро мероприятие", { exact: true }));
  await link(staff, "Проверить билет").click();
  await staff.getByLabel("Код билета", { exact: true }).fill(code);
  await post(staff, button(staff, "Проверить код"));
  await post(staff, button(staff, "Подтвердить посещение"));
  await shown(staff.getByText("Посещение уже отмечено.", { exact: true }));
  await student.goto(booking);
  await shown(student.getByText("Посещение подтверждено", { exact: true }));
  await link(student, "Открыть достижение в портфолио").click();
  await shown(student.getByRole("heading", { name: "Участие · " + title, exact: true }));
  await fits(student, width);

  const cancel = await createEvent(staff, fixture, "Отмена · Практикум", "2031-05-17T18:00", "2031-05-17T19:00", false);
  await post(staff, button(staff, "Опубликовать"));
  await student.goto(fixture.url + "/activities");
  await link(student, "Отмена · Практикум").click();
  assert.equal(await link(student, "Маршрут до аудитории").count(), 0);
  await student.locator('a[href^="/slots/"]').first().click();
  const retired = await register(student);
  await staff.goto(cancel);
  await staff.locator("summary").filter({ hasText: "Отменить мероприятие" }).click();
  await post(staff, button(staff, "Отменить мероприятие"));
  await shown(staff.getByText("Отменено регистраций: 1", { exact: true }));
  await student.goto(retired);
  await shown(student.getByText("Отменена", { exact: true }));
  assert.equal((await other.goto(staffEvent)).status(), 403);
  return booking;
}

async function photoPath(fixture, directory, student, other, width) {
  await student.goto(fixture.url + "/calendar/import");
  await link(student, "Распознать расписание по фото").click();
  const images = {};
  for (const kind of ["png", "jpeg"]) {
    const url = await student.evaluate(format => {
      const canvas = document.createElement("canvas");
      canvas.width = 600;
      canvas.height = 220;
      const paint = canvas.getContext("2d");
      paint.fillStyle = "#fff";
      paint.fillRect(0, 0, 600, 220);
      paint.fillStyle = "#111";
      paint.font = "20px sans-serif";
      paint.fillText("Синтетическое расписание · 09:00 Картография", 20, 70);
      return canvas.toDataURL("image/" + format);
    }, kind);
    images[kind] = Buffer.from(url.split(",")[1], "base64");
  }
  await student.locator('[name="week"]').fill("2031-05-14");
  const upload = async (bytes, format, status) => {
    await student.locator('[name="file"]').setInputFiles({
      name: "Расписание." + (format === "jpeg" ? "jpg" : format), mimeType: "image/" + format, buffer: bytes,
    });
    await post(student, button(student, "Распознать расписание по фото"), status);
  };
  await upload(Buffer.from("Некорректный файл"), "png", 422);
  await shown(student.getByRole("alert").filter({ hasText: "JPEG или PNG" }));
  control(directory, first, "unreadable");
  await upload(images.png, "png", 422);
  await shown(student.getByRole("alert").filter({ hasText: "более чёткий снимок" }));
  control(directory, first, "unavailable");
  await upload(images.jpeg, "jpeg", 503);
  await shown(student.getByRole("alert").filter({ hasText: "Распознавание временно недоступно" }));
  assert.equal(await student.locator('[name="week"]').inputValue(), "2031-05-14");
  control(directory, first);
  await student.evaluate(() => {
    const status = document.querySelector("[data-photo-status]");
    const states = [];
    new MutationObserver(() => {
      if (!states.includes(status.textContent)) states.push(status.textContent);
      sessionStorage.setItem("qa-photo-progress", JSON.stringify(states));
    }).observe(status, { childList: true });
  });
  await upload(images.png, "png", 303);
  const draft = student.url();
  assert.deepEqual(await student.evaluate(() => JSON.parse(sessionStorage.getItem("qa-photo-progress"))),
    ["Готовим фото…", "Распознаём расписание…"]);
  await shown(student.getByText("Неделя 12.05.2031 — 18.05.2031"));
  assert.equal((await other.goto(draft)).status(), 404);
  const second = student.locator(".dean-photo-row").filter({ hasText: subject });
  const confirm = button(student, "Подтвердить личный импорт");
  assert.equal(await confirm.count(), 0);
  await post(student, button(second, "Исключить строку"));
  await shown(confirm);
  await post(student, button(second, "Вернуть строку"));
  assert.equal(await confirm.count(), 0);
  await second.locator("summary").click();
  await second.locator('[name="room"]').fill("2018а");
  await second.getByLabel("Сверил строку").check();
  await fits(student, width);
  await post(student, button(second, "Сохранить строку"));
  await post(student, confirm);
  await link(student, "Открыть в календаре").click();
  await shown(link(student, subject));
  await fits(student, width);
  await other.goto(student.url());
  assert.equal(await link(other, subject).count(), 0);
  await student.goto(fixture.url + photo + "?week=" + week);
  await upload(images.png, "png", 303);
  assert.equal(student.url(), draft);
  await student.goto(fixture.url + photo + "?week=" + week);
  await upload(images.jpeg, "jpeg", 303);
  if (student.url() !== draft) {
    await shown(student.getByText("Событие уже есть", { exact: true }));
    await student.goto(draft);
  }
  return draft;
}

async function run() {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  console.log("Google Chrome " + browser.version() + " · synthetic model fixture");
  const failures = [];
  try {
    for (const width of [360, 1280]) {
      const directory = fs.mkdtempSync(path.join(os.tmpdir(), "dean-ext-browser-"));
      let fixture;
      const contexts = [];
      try {
        control(directory, first);
        fixture = await server(directory);
        const pages = [];
        for (const role of ["student-a", "student-b", "staff"]) {
          const context = await browser.newContext({ viewport: { width, height: 820 }, hasTouch: width === 360 });
          contexts.push(context);
          const page = await context.newPage();
          page.setDefaultTimeout(9000);
          await signIn(page, fixture, role);
          pages.push(page);
        }
        const [student, other, staff] = pages;
        let booking, draft;
        try {
          booking = await events(fixture, directory, student, other, staff, width);
          console.log("Events " + width + " px PASS");
        } catch (error) {
          console.error("Events " + width + " px FAIL", error.message);
          failures.push(error);
        }
        try {
          draft = await photoPath(fixture, directory, student, other, width);
          console.log("Photo import " + width + " px PASS");
        } catch (error) {
          console.error("Photo import " + width + " px FAIL", error.message);
          failures.push(error);
        }
        if (!booking && !draft) continue;
        const stopped = once(fixture.child, "exit");
        fixture.child.kill();
        await stopped;
        fixture = await server(directory);
        if (booking) {
          await student.goto(fixture.url + new URL(booking).pathname);
          await shown(student.getByText("Посещение подтверждено", { exact: true }));
        }
        if (draft) {
          await student.goto(fixture.url + new URL(draft).pathname);
          await shown(link(student, "Открыть в календаре"));
        }
        console.log("Process restart " + width + " px PASS");
      } finally {
        if (fixture && fixture.child.exitCode === null && !fixture.child.signalCode) {
          const stopped = once(fixture.child, "exit");
          fixture.child.kill();
          await stopped;
        }
        await Promise.all(contexts.map(context => context.close()));
        fs.rmSync(directory, { recursive: true, force: true });
      }
    }
  } finally {
    await browser.close();
  }
  assert.equal(failures.length, 0, "P1 Chrome scenarios failed");
}

run().catch(error => { console.error(error); process.exitCode = 1; });
