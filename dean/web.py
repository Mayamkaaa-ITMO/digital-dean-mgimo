import json
import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qs
from urllib.parse import quote, urlencode

from . import activity_extensions, ai, ai_views, assistant, assistant_voice, calendar_share, chat_history, onboarding, photo_import, share_views
from . import applications as appl, auth, learning, reminders, reservation_views, reservations, services as svc, study_views, views, workspace
from . import curriculum, extra_views, lending, portfolio
from . import academic_views, attachment, attendance, campus_views, equipment, finance_views, finances, individual_plan, library_queue, retakes
from . import campus_lab, lecture_channel, lecture_library, coworking, coworking_views
from . import campus_map_view
from . import campus_directory, campus_pay, lecture_studio, room_occupancy
from . import meetings, team_views, team_work, teams
from . import achievements, career_views, courses, practice
from . import calendar_tools, calendar_views, personal_study, personal_study_views, public_schedule, public_sync, public_views, study_context
from . import preparation, preparation_views, study_materials, study_timer
from . import calendar_export, calendar_import, portability_views
from . import personal_data, personal_transfer, profile_tools, profile_tools_views
from . import dashboard_settings, document_share, public_subjects, release_views
from . import coursework, coursework_views, event_tickets, event_ticket_views, staff_management, staff_management_views
from . import telegram, telegram_views
from . import raspay, raspay_lesson_views, raspay_views
from . import access_paths, access_views, public_directory
from .access_config import AccessConfig
from .telegram_transport import TelegramConfig
from .config import WebConfig
from .db import ROOT, connect, read_snapshot, schema_version, write
from .domain import DomainError, field, identifier, require, role
from .schedule import SQLiteScheduleProvider, staff_lessons
from .static_assets import asset_response
from .ui import confirmation

LOG = logging.getLogger(__name__)
SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Content-Security-Policy", "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; "
                                "img-src 'self' data:; font-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"),
)
FORM_LIMIT = 32_768


class FormExpired(DomainError):
    def __init__(self):
        super().__init__(403, "Срок действия формы истёк. Открой страницу заново.")


class Request:
    def __init__(self, env, config, access):
        secure = bool(config.public_origin)
        self.env = env
        self.path = env.get("PATH_INFO", "/")
        self.method = env["REQUEST_METHOD"]
        self.secure = secure
        self.access = access
        self.origin = None
        self.address = config.request_address(env)
        from os import getenv
        self.lab_mode = getenv("DEAN_CAMPUS_LAB") == "1"
        self.product_design = getenv("DEAN_PRODUCT") == "1"
        self.browser_demo = getenv("DEAN_BROWSER_DEMO") == "1" and not secure
        prefix = ("__Host-" if secure else "") + ("campus_lab_" if self.lab_mode else "")
        self.session_cookie = prefix + "dean_session"
        self.login_cookie = prefix + "dean_login"
        self.remember_cookie = prefix + "dean_remember"
        self.remember_notice_cookie = prefix + "dean_remember_notice"
        self.cookies = SimpleCookie()
        try:
            self.cookies.load(env.get("HTTP_COOKIE", ""))
        except CookieError:
            self.cookies.clear()
        self.user = None
        self.public = None
        self.session = self.cookie(self.session_cookie)
        self.csrf = auth.csrf_token(self.session)
        self.response_cookies = []
        self.params = {}
        self.fields = {}
        self.upload = None

    def load_query(self):
        try:
            if len(self.env.get("QUERY_STRING", "")) > 4096:
                raise ValueError
            self.params = parse_qs(self.env.get("QUERY_STRING", ""), keep_blank_values=self.path == "/schedule",
                                   max_num_fields=30)
        except ValueError:
            raise DomainError(400, "Сократи адрес или количество параметров запроса.") from None

    def cookie(self, key):
        return self.cookies[key].value if key in self.cookies else ""

    def set_cookie(self, name, value, max_age=None):
        expiry = f"Max-Age={max_age}; " if max_age is not None else ""
        self.response_cookies.append(("Set-Cookie", f"{name}={value}; Path=/; {expiry}"
                                      f"HttpOnly; SameSite=Lax{'; Secure' if self.secure else ''}"))

    def query(self, key, default=""):
        return self.params.get(key, [default])[-1]

    def field(self, key):
        return self.fields.get(key, [""])[-1]

    def check_form_origin(self, bearer):
        browser_origin = self.env.get("HTTP_ORIGIN")
        if (((bearer is None or browser_origin is not None) and browser_origin != self.origin)
                or self.env.get("HTTP_SEC_FETCH_SITE") == "cross-site"):
            raise DomainError(403, "Отправь форму из открытой страницы приложения.")

    def read_form(self, bearer, *, allow_upload=False, form_limit=FORM_LIMIT, upload_limit=attachment.MAX_BYTES,
                  upload_fields=(), repeated=()):
        self.check_form_origin(bearer)
        content_type = self.env.get("CONTENT_TYPE", "")
        kind = content_type.split(";")[0].strip().lower()
        is_upload = allow_upload and kind == "multipart/form-data"
        if kind != "application/x-www-form-urlencoded" and not is_upload:
            raise DomainError(400, "Не удалось прочитать форму.")
        size = self.env.get("CONTENT_LENGTH") or "0"
        limit = upload_limit + 8192 if is_upload else form_limit
        if not size.isascii() or not size.isdecimal() or len(size) > len(str(limit)) or int(size) > limit:
            raise DomainError(413, "Размер формы превышает допустимый предел.")
        try:
            raw = self.env["wsgi.input"].read(int(size))
            if len(raw) != int(size):
                raise ValueError
            if is_upload:
                self.fields, self.upload = attachment.parse_upload(raw, content_type, upload_fields)
            else:
                self.fields = parse_qs(raw.decode("ascii"), keep_blank_values=True, errors="strict",
                                       max_num_fields=104 if repeated else 24)
            if any(len(values) != 1 and name not in repeated for name, values in self.fields.items()):
                raise ValueError
        except (UnicodeError, ValueError):
            raise DomainError(400, "Не удалось прочитать форму.") from None
        if bearer is not None and not auth.valid_csrf(bearer, self.field("csrf")):
            raise FormExpired()


def redirect(path):
    return 303, "", [("Location", path)]


def render(body, code=200):
    return code, body, []


def home(user):
    return "/staff" if user["role"] == "staff" else "/"


def form_expired(request):
    target = access_paths.form_return(request)
    if request.user:
        target = access_paths.for_role(target, request.user["role"]) or home(request.user)
    else:
        target = access_paths.login_url(target)
    message = ("Срок действия формы истёк. Эта отправка не выполнена. Открой форму заново и проверь, в какой аккаунт ты вошёл."
               if request.user else "Сеанс завершён. Эта отправка не выполнена. Войди, чтобы открыть актуальную форму.")
    if request.env.get("HTTP_ACCEPT") == "application/json":
        return application_json({"message": message, "error": message, "reopen": target}, 403)
    return render(views.error(request, 403, message, recovery=target), 403)


NOTICES = {"cancelled": "Вход отменён. Выбери удобный способ.",
           "removed": "Способ входа удалён. Выбери другой способ входа.",
           "unlinked": "Этот способ пока не привязан. Войди привычным способом и добавь его в профиле.",
           "expired": "Срок действия попытки истёк. Попробуй ещё раз.",
           "provider": "Провайдер пока не подтвердил вход. Попробуй ещё раз или войди по паролю."}


def login_page(request, target="", *, message="", username="", invitation=False):
    bearer = request.cookie(request.login_cookie)
    if not auth.TOKEN.fullmatch(bearer):
        bearer = auth.token()
    request.set_cookie(request.login_cookie, bearer, 900)
    request.csrf = auth.csrf_token(bearer)
    return access_views.login(request, message=message, username=username, target=target, invitation=invitation)


def set_login(request, signed, *, resumed=False):
    request.set_cookie(request.session_cookie, signed.bearer)
    if signed.remember:
        expires = datetime.fromisoformat(signed.remember_expires)
        seconds = max(0, int((expires - auth.real_now()).total_seconds()))
        request.set_cookie(request.remember_cookie, signed.remember, seconds)
    elif not resumed and request.cookie(request.remember_cookie):
        request.set_cookie(request.remember_cookie, "", 0)
    if not resumed:
        request.set_cookie(request.login_cookie, "", 0)


def login(request, db):
    if request.method == "GET":
        target = access_paths.destination(request.query("next"))
        if request.user:
            return redirect(access_paths.for_role(target, request.user["role"]) or home(request.user))
        return render(login_page(request, target, message=NOTICES.get(request.query("notice"), "")))
    bearer = request.cookie(request.login_cookie)
    try:
        request.read_form(bearer)
    except DomainError as error:
        if error.status != 403 or not request.fields:
            raise
        target = access_paths.destination(request.field("next"))
        if request.user and request.field("username") == request.user["username"]:
            return redirect(access_paths.for_role(target, request.user["role"]) or home(request.user))
        return render(login_page(request, target, message="Форма входа обновлена. Введи пароль ещё раз.",
                                 username=request.field("username")), 403)
    target = access_paths.destination(request.field("next"))
    try:
        signed = auth.begin_session(
            db, request.field("username"), request.field("password"),
            request.address, request.session, request.cookie(request.remember_cookie),
            remember=request.field("remember") == "1", browser=bearer,
        )
    except DomainError as error:
        return render(login_page(request, target, message=error.message, username=request.field("username")), error.status)
    set_login(request, signed)
    if request.field("remember") == "1" and not signed.remember:
        request.set_cookie(request.remember_notice_cookie, "session", 90)
    fallback = home({"role": signed.role})
    if request.lab_mode and not (request.browser_demo or request.product_design):
        fallback = campus_lab.PATH
    return redirect(access_paths.for_role(target, signed.role) or fallback)


def application_inputs(request):
    return form_values(request)


def application_json(payload, code=200):
    return code, json.dumps(payload, ensure_ascii=False), [("Content-Type", "application/json; charset=utf-8")]


def application_list(request, db):
    receipt = request.query("receipt")
    if receipt and request.env.get("HTTP_ACCEPT") == "application/json":
        aid = appl.creation_receipt(db, request.user, request.query("service"), receipt,
                                    draft=request.query("draft") == "1")
        return application_json({"location": f"/requests/{aid}"}) if aid else application_json({}, 404)
    return views.requests(request, db)


def submit_request(request, db, service_id, draft=None):
    values = application_inputs(request)
    try:
        aid = appl.create_application(db, request.user, service_id, values, request.field("request_id"), draft=bool(draft))
    except DomainError as error:
        if request.env.get("HTTP_ACCEPT") == "application/json":
            return application_json({"message": error.message}, error.status)
        if error.status != 422:
            raise
        return render(views.request_form(request, db, service_id, error.message, values), 422)
    target = f"/requests/{aid}"
    return application_json({"location": target}) if request.env.get("HTTP_ACCEPT") == "application/json" else redirect(target)


def student_application_update(request, db, aid, action):
    feedback = request.env.get("HTTP_ACCEPT") == "application/json" and action in {"save", "send"}
    try:
        appl.update_application(db, request.user, aid, action, application_inputs(request),
                                request.field("version"), request.field("request_id"))
    except DomainError as error:
        if feedback:
            return application_json({"message": error.message}, error.status)
        raise
    path = f"/requests/{aid}"
    if action == "send":
        confirmation(request, path, "request")
    return application_json({"location": path}) if feedback else redirect(path)


def student_application_comment(request, db, aid):
    appl.student_comment(db, request.user, aid, request.field("note"), request.field("request_id"))
    return redirect(f"/requests/{aid}")


def attachment_change(request, db, aid, removing=None):
    if not (removing or request.upload):
        raise DomainError(422, "Выбери файл для вложения.")
    attachment.change(db, request.user, aid, request.field("version"), request.field("request_id"),
                      upload=request.upload, removing=removing)
    return redirect(f"/requests/{aid}")


def attachment_download(request, db, fid):
    with read_snapshot(db):
        row = attachment.download(db, request.user, fid)
    extension = ".png" if row["media_type"] == "image/png" else ".txt"
    name = quote(row["name"], safe="")
    return 200, row["content"], [
        ("Content-Type", "application/octet-stream"),
        ("Content-Disposition", f"attachment; filename=\"file-{fid[:8]}{extension}\"; filename*=UTF-8''{name}"),
    ]


def change_status(request, db, aid, action=None):
    appl.staff_update(db, request.user, aid, action or request.field("status"), request.field("note"),
                     request.field("assignee"), request.field("version"), request.field("request_id"))
    return redirect(f"/requests/{aid}")


def document(request, db, did):
    doc = appl.documents(db, request.user, did)
    download = request.query("download") == "1"
    if request.query("preview") == "1" and not download:
        return render(views.document_preview(request, doc))
    mode = "attachment" if download else "inline"
    return 200, doc["pdf"], [("Content-Type", "application/pdf"),
                              ("Content-Disposition", f'{mode}; filename="demo-{did[:8]}.pdf"')]


def publish(request, db):
    aid = svc.publish(db, request.user, request.field("group"), request.field("title"),
                      request.field("body"), request.field("request_id"))
    return redirect(f"/announcements/{aid}")


def move(request, db, eid):
    svc.move_lesson(db, request.user, eid, request.field("version"), request.field("starts"),
                    request.field("ends"), request.field("room"), request.field("request_id"))
    return redirect(f"/staff/schedule/{eid}")


def mark_read(request, db, nid):
    svc.mark_read(db, request.user, nid, request.public)
    return redirect(notification_return(request))


def notification_return(request):
    focus = request.field("focus")
    focus = focus if focus == "list" or re.fullmatch(ID, focus) else ""
    return views.notices_url(request.field("filter") == "unread", request.field("kind"), focus,
                             number=views.notice_page(request.field("page")))


def logout(request, db):
    auth.end_session(db, request.session, request.cookie(request.remember_cookie))
    request.set_cookie(request.session_cookie, "", 0)
    request.set_cookie(request.remember_cookie, "", 0)
    request.set_cookie(request.login_cookie, "", 0)
    request.set_cookie(request.remember_notice_cookie, "", 0)
    if request.browser_demo and request.field("next") == "/demo":
        return redirect("/demo")
    return redirect(access_paths.login_url(request.field("next")))


def form_values(request):
    return {name: request.field(name) for name in request.fields}


def save_task(request, db, tid=None):
    values = form_values(request)
    try:
        new_id = workspace.save_task(db, request.user, values, request.field("request_id"), tid)
    except DomainError as error:
        if error.status != 422:
            raise
        return render(study_views.task_form(request, db, tid, error=error.message, values=values), 422)
    return redirect(f"/tasks/{new_id}")


def task_action(request, db, tid, action):
    try:
        workspace.task_action(db, request.user, tid, action, request.field("version"), request.field("request_id"),
                              complete_subtasks=request.field("complete_subtasks") == "1")
    except DomainError as error:
        if error.status != 422:
            raise
        return render(study_views.task_detail(request, db, tid, error=error.message), 422)
    return redirect("/tasks" if action == "delete" else f"/tasks/{tid}")


def subtask_action(request, db, tid, sid=None, action="add"):
    workspace.subtask_action(db, request.user, tid, action, sid, request.field("title"), request.field("request_id"))
    return redirect(f"/tasks/{tid}")


def create_note(request, db, kind, target):
    nid = workspace.write_note(db, request.user, request.field("request_id"), request.field("body"),
                               kind=kind, target=target)
    return redirect(f"/notes/{nid}")


def update_note(request, db, nid, delete=None):
    href = workspace.write_note(db, request.user, request.field("request_id"), request.field("body"),
                                nid=nid, delete=bool(delete))
    return redirect(href)


def save_event(request, db, eid=None):
    values = form_values(request)
    try:
        new_id = workspace.save_event(db, request.user, values, request.field("request_id"), eid)
    except DomainError as error:
        if error.status != 422:
            raise
        return render(study_views.event_form(request, db, eid, error=error.message, values=values), 422)
    return redirect(f"/events/{new_id}")


def event_action(request, db, eid, action):
    workspace.event_action(db, request.user, eid, action, request.field("day"),
                           request.field("version"), request.field("request_id"))
    return redirect("/schedule")


def grade_scenario(request, db, rid):
    learning.save_scenario(db, request.user, rid, request.field("target"), request.field("exam_points"),
                           request.field("request_id"))
    return redirect(f"/grades/{rid}")


def create_plan(request, db, rid):
    workspace.create_plan(db, request.user, rid, request.field("request_id"))
    return redirect(f"/grades/{rid}")


def change_grade(request, db, rid, action="result"):
    learning.change_grade(db, request.user, rid, action, form_values(request), request.field("request_id"))
    return redirect(f"/staff/grades/{rid}")


def mark_all_read(request, db):
    kind = request.field("kind")
    svc.mark_all_read(db, request.user, request.public, kind if kind in views.NOTICE_FILTER_TYPES else "")
    return redirect(notification_return(request))


def notification_settings(request, db):
    chosen = {kind for kind in reminders.OPTIONAL if request.field(kind) == "1"}
    reminders.save_settings(db, request.user, chosen)
    return redirect("/notifications")


def reserve(request, db, sid):
    start, end = request.field("starts"), request.field("ends")
    acknowledge = request.field("ack") == "1"
    try:
        rid = reservations.reserve(db, request.user, sid, start, end, acknowledge,
                                   request.field("request_id"), request.field("version"))
    except DomainError as error:
        if error.status not in {409, 422}:
            raise
        values = {"starts": start, "ends": end, "ack": acknowledge}
        return render(reservation_views.slot_detail(request, db, sid, submitted=values, error=error.message), error.status)
    path = f"/bookings/{rid}"
    confirmation(request, path, "booking")
    return redirect(path)


def cancel_booking(request, db, rid):
    reservations.cancel(db, request.user, rid, request.field("request_id"))
    return redirect(f"/bookings/{rid}")


def activity_reminder(request, db, rid):
    if not activity_extensions.enabled():
        raise DomainError(404, "Напоминание о мероприятии недоступно.")
    active = request.field("active")
    if active not in {"0", "1"}:
        raise DomainError(422, "Выбери действие для напоминания.")
    activity_extensions.set_reminder(db, request.user, rid, active == "1", request.field("request_id"))
    return redirect(f"/bookings/{rid}")


def activity_calendar(request, db, rid):
    if not activity_extensions.enabled():
        raise DomainError(404, "Файл мероприятия недоступен.")
    booking = reservations.get_booking(db, request.user, rid)
    if booking["kind"] != "activity" or booking["status"] != "reserved":
        raise DomainError(404, "Файл доступен для действующей регистрации на мероприятие.")
    slot = db.execute("SELECT version FROM reservation_slot WHERE id=?", (booking["slot_id"],)).fetchone()
    item = calendar_export.Item("activity-" + rid, booking["title"], booking["starts_at"], booking["ends_at"],
                                request.user["timezone"], False, slot["version"])
    return portability_views.download(calendar_export.serialize([item]))


def demo_checkout(request, db):
    item = campus_pay.checkout(db, request.user, request.field("product"), request.field("request_id"))
    return redirect(campus_pay.PATH + "/" + item)


def demo_purchase_action(request, db, item, action):
    code = request.field("code")
    try:
        campus_pay.change(db, request.user, item, action, code, request.field("request_id"))
    except DomainError as error:
        if error.status not in {409, 422}:
            raise
        return render(campus_pay.detail(request, db, item, error=error.message), error.status)
    return redirect(campus_pay.PATH + "/" + item)


def prepare_occupancy_demo(request, db):
    room_occupancy.populate(db, request.user, request.field("request_id"))
    return redirect(room_occupancy.PATH)


def wait_booking(request, db, sid, action):
    reservations.wait(db, request.user, sid, action, request.field("request_id"), form_values(request))
    path = f"/slots/{sid}"
    if action in {"wait", "auto-wait"}:
        confirmation(request, path, "wait")
    return redirect(path)


def save_slot(request, db, target, creating=None):
    sid = reservations.update_slot(db, request.user, target, form_values(request),
                                   request.field("request_id"), creating=bool(creating))
    return redirect(f"/staff/slots/{sid}")


def choose_elective(request, db, item):
    curriculum.choose(db, request.user, item, request.field("request_id"))
    return redirect("/curriculum")


def choose_plan_block(request, db, bid):
    selected = [key[5:] for key in request.fields if key.startswith("item_") and request.field(key) == "1"]
    individual_plan.select(db, request.user, bid, selected, request.field("request_id"))
    return redirect("/curriculum")


def retake_change(request, db, did, action):
    key = request.field("request_id")
    if action == "open":
        retakes.open_debt(db, request.user, did, form_values(request), key)
    elif action in {"admit", "revoke"}:
        retakes.admit(db, request.user, did, action == "admit", request.field("reason"), key)
    elif action == "offer":
        bid = retakes.create_offer(db, request.user, did, request.field("place"), key)
        return redirect("/staff/bookables/" + bid)
    else:
        retakes.record_result(db, request.user, did, request.field("reservation"), request.field("result"),
                              request.field("reason"), key)
    return redirect("/staff/retakes/" + did)


def save_attendance(request, db, eid):
    attendance.record(db, request.user, eid, request.field("student"), request.field("state"),
                      request.field("reason"), request.field("request_id"))
    return redirect("/staff/attendance/" + eid)


def finance_pay(request, db, cid):
    finances.pay(db, request.user, cid, request.field("result"), request.field("request_id"))
    return redirect("/finance/charges/" + cid)


def finance_receipt(request, db, pid):
    pdf = finances.receipt(db, request.user, pid)
    return 200, pdf, [("Content-Type", "application/pdf"),
                      ("Content-Disposition", f'attachment; filename="demo-receipt-{pid[:8]}.pdf"')]


def finance_visibility(request, db):
    finances.set_visibility(db, request.user, request.field("private") == "1")
    return redirect("/finance")


def finance_disburse(request, db, aid):
    finances.disburse(db, request.user, aid, request.field("request_id"))
    return redirect("/staff/finance/" + aid)


def budget_add(request, db, direction):
    values = {**form_values(request), "direction": direction}
    finances.add_budget(db, request.user, values, request.field("request_id"))
    return redirect("/finance/budget")


def reserve_equipment(request, db, uid):
    lid = equipment.reserve(db, request.user, uid, form_values(request), request.field("request_id"))
    path = "/equipment/loans/" + lid
    confirmation(request, path, "equipment")
    return redirect(path)


def equipment_state(request, db, lid, action):
    equipment.change(db, request.user, lid, action, request.field("note"), request.field("request_id"))
    return redirect(("/equipment" if action == "cancel" else "/staff/equipment") + "/loans/" + lid)


def service_equipment(request, db, uid, action):
    equipment.maintenance(db, request.user, uid, action == "enable", request.field("reason"), request.field("request_id"))
    return redirect("/staff/equipment")


def reserve_book(request, db, bid):
    hid = library_queue.reserve(db, request.user, bid, request.field("request_id"))
    path = "/library/holds/" + hid
    confirmation(request, path, "library")
    return redirect(path)


def cancel_book_hold(request, db, hid):
    library_queue.cancel(db, request.user, hid, request.field("request_id"))
    return redirect("/library/holds/" + hid)


def reservation_attendance(request, db, sid, rid):
    reservations.confirm_attendance(db, request.user, sid, rid, request.field("request_id"))
    return redirect("/staff/slots/" + sid)


def team_create(request, db):
    tid = teams.create(db, request.user, request.field("title"), request.field("request_id"))
    return redirect("/teams/" + tid)


def team_invite(request, db, tid):
    teams.invite(db, request.user, tid, request.field("login"), request.field("request_id"))
    return redirect("/teams/" + tid)


def team_reply(request, db, iid, state):
    target = teams.decide_invite(db, request.user, iid, state, request.field("request_id"))
    return redirect(("/teams/" if state == "accepted" else "/teams/invitations/") + target)


def team_revoke(request, db, tid, iid):
    teams.revoke_invite(db, request.user, tid, iid, request.field("request_id"))
    return redirect("/teams/" + tid)


def team_remove(request, db, tid, person):
    teams.remove(db, request.user, tid, person, request.field("request_id"))
    return redirect("/teams/" + tid)


def team_consent(request, db, tid):
    teams.consent(db, request.user, tid, request.field("allow") == "1", request.field("request_id"))
    path = "/teams/" + tid
    if request.field("return_to_picker") == "1":
        params = {name: request.field(name) for name in ("from", "to", "minutes")}
        return redirect(path + "/free" + ("?" + urlencode(params) if params["from"] else ""))
    return redirect(path)


def team_save_task(request, db, tid, task_id=None):
    task_id = team_work.save_task(db, request.user, tid, form_values(request), request.field("request_id"), task_id)
    return redirect(f"/teams/{tid}/tasks/{task_id}")


def team_task_state(request, db, tid, task_id, state):
    team_work.task_state(db, request.user, tid, task_id, state, request.field("version"), request.field("request_id"))
    return redirect(f"/teams/{tid}/tasks/{task_id}")


def team_subtask(request, db, tid, task_id, sid=None, action="add"):
    def authorize():
        team_work.tasks(db, request.user, tid, task_id, manage=True)
    workspace.subtask_action(db, request.user, task_id, action, sid, request.field("title"),
                             request.field("request_id"), authorize=authorize)
    return redirect(f"/teams/{tid}/tasks/{task_id}")


def team_share_note(request, db, tid, nid=None):
    team_work.share(db, request.user, tid, nid or request.field("note"), nid is None, request.field("request_id"))
    return redirect("/teams/" + tid)


def team_post(request, db, tid, task_id=None):
    team_work.post(db, request.user, tid, task_id, request.field("body"), request.field("request_id"))
    return redirect(f"/teams/{tid}/tasks/{task_id}" if task_id else f"/teams/{tid}")


def team_report(request, db, tid, mid):
    team_work.report(db, request.user, tid, mid, request.field("reason"), request.field("request_id"))
    return redirect("/teams/" + tid)


def team_moderate(request, db, rid):
    outcome = request.field("outcome")
    if outcome not in {"hidden", "dismissed"}:
        raise DomainError(422, "Выбери решение по жалобе.")
    team_work.moderate(db, request.user, rid, outcome, request.field("decision"), request.field("request_id"))
    return redirect("/staff/moderation")


def team_meeting_create(request, db, tid):
    mid = meetings.save(db, request.user, tid, form_values(request), request.field("request_id"))
    return redirect("/teams/meetings/" + mid)


def team_meeting_change(request, db, mid, action):
    user, key, version = request.user, request.field("request_id"), request.field("version")
    if action == "move":
        row = meetings.meeting(db, user, mid)
        meetings.save(db, user, row["team_id"], form_values(request), key, mid)
    elif action == "cancel":
        meetings.cancel(db, user, mid, version, key)
    else:
        meetings.respond(db, user, mid, action, version, key)
    return redirect("/teams/meetings/" + mid)


def practice_diary(request, db, pid):
    practice.diary(db, request.user, pid, request.field("day"), request.field("body"), request.field("request_id"))
    return redirect("/practice/" + pid)


def practice_report(request, db, pid):
    practice.submit_report(db, request.user, pid, request.field("body"), request.field("version"),
                           request.field("request_id"))
    return redirect("/practice/" + pid)


def practice_review(request, db, pid):
    practice.review(db, request.user, pid, request.field("state"), request.field("feedback"),
                    request.field("version"), request.field("request_id"))
    return redirect("/staff/practice/" + pid)


def achievement_request(request, db, eid, action):
    achievements.request_review(db, request.user, eid, request.field("request_id"), withdraw=action == "withdraw")
    return redirect("/portfolio/" + eid)


def achievement_review(request, db, eid):
    achievements.decide(db, request.user, eid, request.field("state"), request.field("note"),
                        request.field("request"), request.field("request_id"))
    return redirect("/staff/achievements")


def save_resume(request, db):
    selected = [name[5:] for name in request.fields if name.startswith("item_") and request.field(name) == "1"]
    rid = achievements.create_resume(db, request.user, request.field("title"), selected,
                                     request.field("name") == "1", request.field("request_id"))
    return redirect("/portfolio/resume/" + rid)


def resume_pdf(request, db, rid):
    return 200, achievements.resume_pdf(db, request.user, rid), [
        ("Content-Type", "application/pdf"), ("Content-Disposition", f'attachment; filename="demo-resume-{rid[:8]}.pdf"'),
    ]


def course_change(request, db, cid, action):
    courses.course_action(db, request.user, cid, action, request.field("request_id"))
    path = "/courses/" + cid
    if action == "enroll":
        confirmation(request, path, "course")
    return redirect(path)


def complete_lesson(request, db, cid, lid):
    courses.finish(db, request.user, cid, lid, request.field("request_id"))
    return redirect(f"/courses/{cid}/lessons/{lid}")


def lend_book(request, db, hold_id=None):
    lid = lending.issue(db, request.user, request.field("student"), request.field("copy"), request.field("request_id"), hold_id)
    return redirect(f"/staff/library/{lid}")


def loan_update(request, db, lid, action):
    lending.update(db, request.user, lid, action, request.field("request_id"))
    section = "/staff/library/" if action == "return" else "/library/"
    return redirect(section + lid)


def save_portfolio(request, db, eid=None, deleting=None):
    item = portfolio.save(db, request.user, form_values(request), request.field("request_id"),
                          eid, deleting=bool(deleting))
    return redirect("/portfolio" if deleting else f"/portfolio/{item}")


def save_opportunity(request, db, oid):
    portfolio.track(db, request.user, oid, request.field("status"), request.field("note"), request.field("request_id"))
    return redirect(f"/opportunities/{oid}")


def reminder_page(view, request, db, *args):
    reminders.refresh(db, request.user)
    return view(request, db, *args)


def choose_study_context(request, db, operation=None):
    selection = study_context.parse_selection(request.field("selection"))
    action = operation or request.field("action") or "view"
    study_context.change(db, request.user, selection, action, request.field("revision"),
                         request.field("focus_day") if "focus_day" in request.fields else None)
    return redirect("/schedule/context" if action in {"favorite", "unfavorite"} else "/schedule")


def create_personal_subject(request, db):
    sid = personal_study.create(db, request.user, request.field("title"), request.public,
                                request.field("source_title"), request.field("request_id"))
    return redirect(f"/study/subjects/{sid}")


def change_personal_subject(request, db, sid, action):
    personal_study.edit(db, request.user, sid, action, form_values(request), request.public, request.field("request_id"))
    return redirect(f"/study/subjects/{sid}")


def save_calendar_filters(request, db):
    calendar_tools.save_filters(db, request.user, request.public, request.field("context_revision"),
                               request.field("kind"), request.field("subject"), request.field("visibility"))
    return redirect("/schedule?" + urlencode({"view": request.field("view"), "date": request.field("date")}))


def follow_calendar(request, db):
    calendar_tools.follow(db, request.user, request.public, request.field("enabled") == "1",
                         request.field("context_revision"))
    return redirect("/schedule/changes")


def adjust_calendar(request, db, event_id, action):
    calendar_tools.set_override(db, request.user, request.public, event_id, action,
                                form_values(request), request.field("request_id"))
    return redirect("/schedule/public/" + event_id)


def choose_raspay_group(request, db):
    revision = request.field("revision")
    if not revision:
        raise DomainError(409, "Открой выбор группы заново.")
    choice = study_context.parse_selection(request.field("selection"))
    study_context.change(db, request.user, choice, "view", revision)
    selection = public_schedule.context(db, request.user)
    if not raspay.preference(db, request.user, selection).room and raspay.language_rooms(db, request.user, selection):
        return redirect(raspay_views.ROOT + "/room?first=1")
    return redirect(raspay_views.ROOT)


def raspay_preference(request, db, action):
    day = study_context.date_value(request.field("date"))
    raspay.update(db, request.user, request.public, action, form_values(request))
    screen = "/colors" if action in {"color", "reset-colors"} else "/settings"
    if action == "room" and request.field("first") == "1":
        screen = ""
    return redirect(raspay_views.url(day, screen))


def raspay_change_lesson(request, db, lesson_id, action):
    day = study_context.date_value(request.field("date"))
    fields = form_values(request)
    if action in {"time", "time/remove"}:
        mode = "save" if action == "time" else "remove"
        calendar_tools.set_override(db, request.user, request.public, lesson_id, mode, fields, request.field("request_id"))
    else:
        raspay.set_hidden(db, request.user, request.public, lesson_id, action, fields)
    screen = "" if action == "hide" else "/lesson/" + lesson_id
    return redirect(raspay_views.url(day, screen))


def raspay_note(request, db, lesson_id):
    day = study_context.date_value(request.field("date"))
    calendar_tools.verify_context(db, request.user, request.public, request.field("context_revision"))
    body, command_id = request.field("body"), request.field("request_id")
    if request.public:
        public_schedule.save_note(db, request.user, request.public, lesson_id, body,
                                  request.field("remind") == "1", command_id)
    else:
        workspace.write_note(db, request.user, command_id, body, kind="lesson", target=lesson_id)
    return redirect(raspay_views.url(day, "/lesson/" + lesson_id) + "&note=1")


def add_free_time(request, db):
    eid = calendar_tools.book_free(db, request.user, request.public, form_values(request), request.field("request_id"))
    return redirect("/events/" + eid)


def task_effort(request, db, tid):
    preparation.set_effort(db, request.user, tid, request.field("minutes"), request.field("request_id"))
    return redirect(f"/tasks/{tid}")


def preparation_create(request, db):
    pid = preparation.create(db, request.user, request.public, form_values(request), request.field("request_id"))
    return redirect(f"/study/preparation/{pid}")


def preparation_change(request, db, pid, action):
    preparation.change(db, request.user, request.public, pid, action, form_values(request), request.field("request_id"))
    return redirect(f"/study/preparation/{pid}")


def timer_start(request, db):
    try:
        study_timer.start(db, request.user, request.field("task"), request.field("request_id"))
    except DomainError as error:
        if error.status != 409:
            raise
        return render(preparation_views.timer(request, db, error.message, request.field("task")), 409)
    return redirect("/study/timer")


def timer_change(request, db, sid, action):
    study_timer.change(db, request.user, sid, action, request.field("version"), request.field("request_id"))
    task = request.field("task")
    return redirect("/study/timer?" + urlencode({"task": task}) if task else "/study/timer")


def material_create(request, db, sid):
    study_materials.add(db, request.user, sid, form_values(request), request.upload, request.field("request_id"))
    return redirect(f"/study/subjects/{sid}/materials")


def material_remove(request, db, sid, mid):
    study_materials.remove(db, request.user, sid, mid, request.field("request_id"))
    return redirect(f"/study/subjects/{sid}/materials")


def material_download(request, db, mid):
    row = study_materials.value(db, request.user, mid, file_only=True)
    name = quote(row["title"], safe="")
    return 200, row["content"], [
        ("Content-Type", "application/octet-stream"),
        ("Content-Disposition", f"attachment; filename*=UTF-8''{name}"),
    ]


def start_import(request, db, format_name):
    body = request.upload[1] if request.upload else request.field("data").encode("utf-8")
    try:
        draft = calendar_import.start(db, request.user, request.public, format_name, body, request.field("request_id"))
    except DomainError as error:
        if format_name != "csv" or error.status not in {413, 422}:
            raise
        return render(portability_views.import_index(request, db, csv_error=error.message,
                      csv_text=request.field("data"), csv_upload=request.upload is not None), error.status)
    return redirect("/calendar/import/" + draft)


def import_photo(request, db):
    week = request.field("week")
    region = request.field("photo_region") or "whole"
    request_id = request.field("request_id")
    retry_id = ""
    try:
        content = request.upload[1] if request.upload else b""
        result = photo_import.recognize(db, request.user, request.public, request.ai,
                                        content, week, request_id, region=region)
        if "error" in result:
            if result.get("pending"):
                retry_id = request_id
            raise DomainError(result["status"], result["error"])
    except DomainError as error:
        if error.status == 403:
            raise
        return render(ai_views.photo(request, db, error.message, week,
                                     retry_id=retry_id, error_status=error.status, region=region), error.status)
    draft = calendar_import.get(db, request.user, result["draft"])
    repeat = "?repeat=1" if draft["state"] == "confirmed" else ""
    return redirect("/calendar/import/" + draft["id"] + repeat)


def profile_photo(request, db):
    role(request.user, "student")
    row = require(db.execute("SELECT media_type, content FROM profile_photo WHERE owner_id=?",
                             (request.user["id"],)).fetchone())
    return 200, row["content"], [("Content-Type", row["media_type"]), ("Cache-Control", "no-store")]


def save_profile_photo(request, db):
    role(request.user, "student")
    try:
        media_type, content = photo_import.profile_image(request.upload[1] if request.upload else b"")
    except DomainError as error:
        return render(views.profile(request, db, photo_error=error.message), error.status)
    with write(db):
        db.execute("""
            INSERT INTO profile_photo VALUES (?, ?, ?)
            ON CONFLICT(owner_id) DO UPDATE SET media_type=excluded.media_type, content=excluded.content
        """, (request.user["id"], media_type, content))
    return redirect("/profile")


def remove_profile_photo(request, db):
    role(request.user, "student")
    with write(db):
        db.execute("DELETE FROM profile_photo WHERE owner_id=?", (request.user["id"],))
    return redirect("/profile")


def dictate(request, db):
    try:
        request.read_form(request.session, allow_upload=True, upload_limit=assistant_voice.MAX_BYTES)
        result = assistant_voice.transcribe(request.upload[1] if request.upload else b"")
    except DomainError as error:
        message = ("Запись занимает больше 2 МиБ. Попробуй надиктовать короче."
                   if error.status == 413 and not request.upload else error.message)
        return application_json({"error": message}, error.status)
    return application_json({"text": result})


def ask_assistant(request, db):
    message = request.field("message")
    selected = request.field("chat")
    request_id = request.field("request_id") or identifier()
    rendered = {}
    try:
        message = field(message, "Сообщение", 700)
        if not re.fullmatch(ID, request_id):
            raise DomainError(422, "Не удалось распознать запрос. Повтори отправку.")
        user = request.user
        conversation = chat_history.current(db, user, selected)
        recorded = chat_history.response(db, user, request_id, message, selected)
        if recorded is None:
            previous = chat_history.previous_question(db, user, conversation, request.field("previous"))
            reply = assistant.ask(db, user, request.ai, message, previous, request_id)
            if not reply.get("error"):
                target = conversation["id"] if conversation else "new"
                recorded = chat_history.record(db, user, target, message, request_id, reply)
        if recorded is not None:
            selected = recorded["conversation_id"]
            reply = json.loads(recorded["reply_json"])
            rendered = {"chat": selected, "chat_title": recorded["title"], "command_id": request_id,
                        "turn_html": ai_views.log_content(request, [recorded])}
    except DomainError as error:
        reply = {"error":error.message,"status":error.status}
    code = reply.get("status", 200)
    retry_id = request_id if reply.get("pending") else ""
    if request.env.get("HTTP_ACCEPT") == "application/json":
        return application_json({**reply, **rendered, "next_id":retry_id or identifier()}, code)
    if reply.get("navigate"):
        return redirect(reply["navigate"])
    return render(ai_views.assistant(request, db, selected=selected, question=message[:700] if code >= 400 else "",
                                     error=reply.get("error", ""), retry_id=retry_id,
                                     previous=request.field("previous")[:700]), code)


def confirm_assistant(request, db, pid):
    try:
        path = assistant.confirm(db, request.user, pid, reviewed=request.field("reviewed") == "yes")
        confirmation(request, path, "assistant")
        return redirect(path)
    except assistant.ConfirmationExpired:
        return redirect(request.path)
    except DomainError as error:
        if error.status not in {409, 422}:
            raise
        return render(ai_views.review_action(request, db, pid, error=error.message), error.status)


def finish_onboarding(request, db, decision):
    return redirect(onboarding.decide(db, request.user, decision, request.field("next")))


def publish_calendar_share(request, db):
    token = calendar_share.publish(db, request.user, request.public, request.field("week"),
                                   request.fields.get("event", ()), request.field("context_revision"),
                                   request.field("request_id"))
    return render(share_views.reveal(request,token),201)


def revoke_calendar_share(request, db, sid):
    calendar_share.revoke(db,request.user,sid)
    return redirect(share_views.ROOT)


def open_calendar_share(request, db):
    tail = request.path.removeprefix(share_views.PUBLIC)
    token, slash, operation = tail.partition("/")
    if not auth.TOKEN.fullmatch(token) or slash and operation != "copy":
        raise DomainError(404,"Ссылка недоступна.")
    if not slash and request.method in {"GET","HEAD"}:
        with read_snapshot(db):
            return render(share_views.public(request,db,token))
    if slash and request.method == "POST":
        if not request.user:
            raise DomainError(403,"Войди, чтобы подготовить личный импорт.")
        request.read_form(request.session)
        selection = public_schedule.context(db,request.user)
        draft = calendar_share.copy(db,request.user,selection,token,request.field("request_id"))
        return redirect("/calendar/import/"+draft)
    return 405,views.error(request,405,"Метод запроса недоступен."),[("Allow","POST" if slash else "GET, HEAD")]


def edit_import(request, db, did, action):
    try:
        calendar_import.change(db, request.user, did, action, form_values(request), request.field("request_id"))
    except DomainError as error:
        if error.status not in {409, 422} or not calendar_import.photo_week(db, request.user, did):
            raise
        return render(portability_views.import_detail(request, db, did, problem=error.message), error.status)
    path = "/calendar/import/" + did
    if action == "confirm":
        confirmation(request, path, "calendar")
    return redirect(path)


def new_subscription(request, db):
    scope = request.field("scope")
    first, last = calendar_export.period(request.field("first"), request.field("last"), scope)
    token = calendar_export.new_feed(db, request.user, request.public, first, last, scope,
                                     request.field("context_revision"), request.field("request_id"))
    return render(portability_views.reveal(request, token))


def revoke_subscription(request, db, feed_id):
    calendar_export.revoke(db, request.user, feed_id)
    return redirect("/calendar/export")


def revoke_sessions(request, db, handle=None):
    profile_tools.revoke_sessions(db, request.user, request.session, handle)
    return redirect("/profile/sessions")


def revoke_device(request, db, identity):
    current = profile_tools.revoke_device(db, request.user, request.session, identity)
    if current:
        request.set_cookie(request.session_cookie, "", 0)
        request.set_cookie(request.remember_cookie, "", 0)
        return redirect("/login")
    return redirect("/profile/sessions")


def profile_privacy(request, db):
    profile_tools.set_preview(db, request.user, request.field("hide") == "1")
    return redirect("/profile/privacy")


def favorite_link(request, db):
    profile_tools.add_link(db, request.user, request.public, request.field("kind"), request.field("service"),
                           request.field("title"), request.field("url"), request.field("request_id"))
    return redirect("/profile/links")


def remove_favorite_link(request, db, lid):
    profile_tools.remove_link(db, request.user, lid)
    return redirect("/profile/links")


def submit_ticket(request, db):
    tid = profile_tools.ticket_create(db, request.user, request.public, form_values(request), request.field("request_id"))
    return redirect("/support/" + tid)


def change_ticket(request, db, tid):
    profile_tools.ticket_update(db, request.user, tid, request.field("state"), request.field("version"), request.field("request_id"), request.field("reply"))
    return redirect("/staff/support/" + tid)


def preview_restore(request, db, settings):
    data = request.upload[1] if request.upload else request.field("data").encode("utf-8")
    pid = personal_transfer.stage(db, request.user, data, settings == "settings", request.field("request_id"))
    return redirect("/profile/data/restore/" + pid)


def finish_restore(request, db, pid, action):
    personal_transfer.finish(db, request.user, pid, action, request.field("request_id"))
    return redirect("/profile/data/restore/" + pid)


def dashboard_preferences(request, db):
    dashboard_settings.save(db, request.user, request.field("mode"), form_values(request))
    return redirect("/profile/dashboard?" + urlencode({"mode": request.field("mode")}))


def issue_document_share(request, db, did):
    token = document_share.create(db, request.user, did, request.field("duration"),
                                  request.field("confirm") == "1", request.field("request_id"))
    return render(release_views.reveal_document(request, did, token))


def revoke_document_share(request, db, did, sid):
    document_share.revoke(db, request.user, did, sid)
    return redirect(f"/documents/{did}/share")


def save_coursework(request, db, aid=None):
    key = coursework.save_assignment(db, request.user, form_values(request), request.field("request_id"), aid)
    return redirect("/staff/work/" + key)


def publish_coursework(request, db, aid, action):
    coursework.change_publication(db, request.user, aid, action, request.field("version"), request.field("request_id"))
    return redirect("/staff/work/" + aid)


def save_lecture_work(request, db):
    aid = lecture_studio.draft(db, request.user, form_values(request), request.field("request_id"))
    return redirect("/staff/work/" + aid)


def submit_coursework(request, db, wid, action):
    coursework.save_answer(db, request.user, wid, request.field("answer"), request.field("version"),
                            action, request.field("request_id"))
    path = "/work/" + wid
    if action == "submit":
        confirmation(request, path, "work")
    return redirect(path)


def attach_coursework(request, db, wid):
    coursework.attach(db, request.user, wid, request.upload, request.field("version"), request.field("request_id"))
    return redirect("/work/" + wid)


def review_coursework(request, db, wid, action):
    coursework.review(db, request.user, wid, action, form_values(request), request.field("request_id"))
    return redirect("/staff/work/review/" + wid)


def coursework_file(request, db, fid):
    row = coursework.file(db, request.user, fid)
    name = quote(row["name"], safe="")
    return 200, row["content"], [("Content-Type", "application/octet-stream"),
                                 ("Content-Disposition", f"attachment; filename*=UTF-8''{name}")]


def staff_service_save(request, db, sid=None):
    code = staff_management.save_service(db, request.user, form_values(request), request.field("request_id"), sid)
    return redirect("/staff/manage/services/" + code)


def staff_resource_new(request, db):
    fields = form_values(request)
    try:
        bid = staff_management.create_resource(db, request.user, fields, request.field("request_id"))
    except DomainError as error:
        if error.status != 422 or fields.get("kind") != "activity" or not activity_extensions.enabled():
            raise
        return render(staff_management_views.resource_form(request, db, problem=error.message, values=fields),
                      error.status)
    return redirect("/staff/equipment" if request.field("kind")=="equipment" else "/staff/manage/resources/" + bid)


def staff_resource_update(request, db, bid, action):
    fields = form_values(request)
    try:
        staff_management.manage_resource(db, request.user, bid, action, fields, request.field("request_id"))
    except DomainError as error:
        if error.status not in {409, 422} or not activity_extensions.enabled():
            raise
        event, _ = staff_management.scoped_offer(db, request.user, bid)
        if event["kind"] != "activity":
            raise
        return render(staff_management_views.resource_detail(request, db, bid, problem=error.message,
                      values=fields if action == "move" else None), error.status)
    slot = request.field("slot") if activity_extensions.enabled() and action == "move" else ""
    return redirect("/staff/manage/resources/" + bid + ("?" + urlencode({"slot": slot}) if slot else ""))


def staff_instructor_assign(request, db):
    staff_management.assign_instructor(db, request.user, request.field("assignment"), request.field("request_id"))
    return redirect("/staff/work/new")


def issue_event_ticket(request, db, rid):
    code = event_tickets.issue(db, request.user, rid, request.field("ticket_id"), request.field("request_id"))
    return render(event_ticket_views.issued(request, db, rid, code))


def prepare_event_ticket(request, db, sid):
    review = event_tickets.prepare(db, request.user, sid, request.field("code"))
    return redirect(f"/staff/slots/{sid}/tickets/{review}")


def redeem_event_ticket(request, db, sid, review):
    event_tickets.redeem(db, request.user, sid, review, request.field("request_id"))
    return redirect(f"/staff/slots/{sid}/tickets/{review}")


def correct_event_attendance(request, db, sid, rid):
    event_tickets.correct(db, request.user, sid, rid, request.field("reason"), request.field("confirmed_at"),
                          request.field("request_id"))
    return redirect(f"/staff/slots/{sid}")


def telegram_begin(request, db):
    config = TelegramConfig.from_env()
    try:
        nonce = telegram.begin(db, request.user, config)
    except DomainError as error:
        return render(telegram_views.index(request, db, message=error.message), error.status)
    return render(telegram_views.reveal(request, config, nonce))


def telegram_confirm(request, db):
    config = TelegramConfig.from_env()
    try:
        telegram.confirm(db, request.user, config, request.field("proof"))
    except DomainError as error:
        return render(telegram_views.index(request, db, message=error.message), error.status)
    return redirect("/profile/telegram")


def telegram_settings(request, db):
    try:
        telegram.settings(db, request.user, form_values(request))
    except DomainError as error:
        return render(telegram_views.index(request, db, message=error.message), error.status)
    return redirect("/profile/telegram")


def telegram_revoke(request, db):
    telegram.revoke(db, request.user)
    return redirect("/profile/telegram")


def public_note(request, db, event_id):
    public_schedule.save_note(db, request.user, request.public, event_id, request.field("body"),
                              request.field("remind") == "1", request.field("request_id"))
    return redirect(f"/schedule/public/{event_id}")


def source_preview(request, db):
    public_sync.require_operator(db, request.user)
    scope = request.field("scope").split("|")
    mode = request.field("mode")
    if len(scope) != 3 or mode not in {"snapshot", "live"}:
        raise DomainError(422, "Укажи область и режим получения данных.")
    week = public_schedule.week_scope(*scope)
    stage = public_sync.stage_sample if mode == "snapshot" else public_sync.stage_live
    preview = stage(db, week)
    return redirect(f"/staff/public/{preview}" if preview else "/staff/public")


def source_publish(request, db, key):
    public_sync.require_operator(db, request.user)
    public_sync.publish(db, key, operator=request.user)
    return redirect(f"/staff/public/{key}")


def source_rollback(request, db, key):
    preview = public_sync.stage_rollback(db, request.user, key)
    return redirect(f"/staff/public/{preview}")


ID = r"([a-f0-9]{32})"
ROUTES = [
    ("/", lambda r, db: reminder_page(views.dashboard, r, db, SQLiteScheduleProvider(db)), None),
    ("/all-services", views.all_services, None),
    ("/profile", views.profile, None),
    ("/profile/photo", profile_photo, save_profile_photo),
    ("/profile/photo/remove", None, remove_profile_photo),
    ("/profile/telegram", telegram_views.index, None),
    ("/profile/telegram/begin", None, telegram_begin),
    ("/profile/telegram/confirm", None, telegram_confirm),
    ("/profile/telegram/settings", None, telegram_settings),
    ("/profile/telegram/revoke", None, telegram_revoke),
    ("/profile/dashboard", release_views.dashboard, dashboard_preferences),
    (f"/profile/devices/{ID}/revoke", None, revoke_device),
    (f"/documents/{ID}/share", release_views.share, issue_document_share),
    (f"/documents/{ID}/share/{ID}/revoke", None, revoke_document_share),
    ("/profile/sessions", profile_tools_views.sessions, None),
    ("/profile/sessions/revoke-others", None, revoke_sessions),
    (f"/profile/sessions/{ID}/revoke", None, revoke_sessions),
    ("/profile/privacy", profile_tools_views.privacy, profile_privacy),
    ("/profile/links", profile_tools_views.links, favorite_link),
    (f"/profile/links/{ID}/remove", None, remove_favorite_link),
    ("/profile/data", profile_tools_views.data_index, None),
    ("/profile/data/export", profile_tools_views.data_index, None),
    ("/profile/data/upload/(keep|settings)", None, preview_restore),
    (f"/profile/data/restore/{ID}", profile_tools_views.data_review, None),
    (f"/profile/data/restore/{ID}/(apply|cancel)", None, finish_restore),
    ("/support", profile_tools_views.support, submit_ticket),
    (f"/support/{ID}", profile_tools_views.ticket, None),
    ("/staff/support", profile_tools_views.support, None),
    (f"/staff/support/{ID}", profile_tools_views.ticket, change_ticket),
    ("/schedule", lambda r, db: views.schedule(r, db, SQLiteScheduleProvider(db)), None),
    ("/schedule/raspay", raspay_views.day_view, None),
    ("/schedule/raspay/groups", raspay_views.groups, choose_raspay_group),
    ("/schedule/raspay/settings", raspay_views.settings, None),
    ("/schedule/raspay/room", raspay_views.room, None),
    ("/schedule/raspay/colors", raspay_views.color_screen, None),
    ("/schedule/raspay/colors/(lecture|practice|sport|lab|research)", raspay_views.color_screen, None),
    ("/schedule/raspay/preferences/(gaps|color|reset-colors|room)", None, raspay_preference),
    (f"/schedule/raspay/lesson/{ID}", raspay_lesson_views.detail, None),
    (f"/schedule/raspay/lesson/{ID}/(time|hide|restore)", raspay_lesson_views.detail, raspay_change_lesson),
    (f"/schedule/raspay/lesson/{ID}/(time/remove)", None, raspay_change_lesson),
    (f"/schedule/raspay/lesson/{ID}/note", None, raspay_note),
    ("/schedule/context", public_views.context_page, choose_study_context),
    ("/schedule/filters", None, save_calendar_filters),
    ("/schedule/filters/reset", None, save_calendar_filters),
    ("/schedule/changes", calendar_views.changes, follow_calendar),
    ("/schedule/free", calendar_views.free_time, add_free_time),
    ("/calendar/import", portability_views.import_index, None),
    ("/calendar/import/photo", ai_views.photo, import_photo),
    ("/calendar/import/(csv|draft)", None, start_import),
    (f"/calendar/import/{ID}/calendar[.]ics", portability_views.photo_ics, None),
    (f"/calendar/import/{ID}", portability_views.import_detail, None),
    (f"/calendar/import/{ID}/(row|zone|confirm|cancel|exclude)", None, edit_import),
    ("/assistant", ai_views.assistant, ask_assistant),
    (assistant_voice.PATH, None, dictate),
    (f"/assistant/actions/{ID}/confirm", ai_views.review_action, confirm_assistant),
    ("/onboarding", onboarding.entry, None),
    ("/onboarding/(skip|finish)", None, finish_onboarding),
    (share_views.ROOT, share_views.index, publish_calendar_share),
    (f"/calendar/share/{ID}/revoke", None, revoke_calendar_share),
    ("/calendar/export", portability_views.export_page, None),
    ("/calendar/export/subscription", None, new_subscription),
    (f"/calendar/export/subscription/{ID}/revoke", None, revoke_subscription),
    (f"/schedule/public/{ID}/adjustment/(save|remove)", None, adjust_calendar),
    ("/schedule/context/(view|return|favorite|unfavorite)", None, choose_study_context),
    ("/study/subjects", personal_study_views.index, create_personal_subject),
    ("/study/public/subjects", public_subjects.subjects, None),
    (f"/study/public/subjects/{ID}", public_subjects.detail, None),
    (r"/study/public/services/([a-z0-9-]+)", public_subjects.service, None),
    ("/study/preparation", preparation_views.index, preparation_create),
    (f"/study/preparation/{ID}", preparation_views.detail, None),
    (f"/study/preparation/{ID}/(move|confirm|cancel)", None, preparation_change),
    ("/study/timer", preparation_views.timer, timer_start),
    (f"/study/timer/{ID}/(pause|resume|finish)", None, timer_change),
    (f"/tasks/{ID}/effort", None, task_effort),
    (f"/study/subjects/{ID}/materials", preparation_views.materials, material_create),
    (f"/study/subjects/{ID}/materials/{ID}/remove", None, material_remove),
    (f"/study/materials/{ID}/download", material_download, None),
    (f"/study/subjects/{ID}", personal_study_views.detail, None),
    (f"/study/subjects/{ID}/(save|link|unlink|task|detach|result)", None, change_personal_subject),
    (f"/schedule/public/{ID}", public_views.lesson_detail, None),
    (f"/schedule/public/{ID}/note", None, public_note),
    (r"/schedule/rooms/([a-z0-9]+)/([a-z0-9-]+)", public_views.room_detail, None),
    (r"/schedule/room/([a-z0-9-]+)", public_views.reference_room, None),
    (f"/schedule/{ID}", lambda r, db, eid: views.lesson_detail(r, db, SQLiteScheduleProvider(db), eid), None),
    ("/grades", views.grades, None),
    ("/work", coursework_views.student_index, None),
    (f"/work/{ID}", coursework_views.student_detail, None),
    (f"/work/{ID}/(draft|submit)", None, submit_coursework),
    (f"/work/{ID}/attachment", None, attach_coursework),
    (f"/work/files/{ID}", coursework_file, None),
    ("/staff/manage", staff_management_views.hub, None),
    ("/staff/manage/services/new", staff_management_views.service_form, staff_service_save),
    (r"/staff/manage/services/(managed-[a-f0-9]{32})", staff_management_views.service_form, staff_service_save),
    ("/staff/manage/resources/new", staff_management_views.resource_form, staff_resource_new),
    (f"/staff/manage/resources/{ID}", staff_management_views.resource_detail, None),
    (f"/staff/manage/resources/{ID}/(publish|move|retire)", None, staff_resource_update),
    ("/staff/manage/instructors", None, staff_instructor_assign),
    ("/staff/work", coursework_views.teacher_index, None),
    ("/staff/work/new", coursework_views.assignment_form, save_coursework),
    (campus_lab.PATH, campus_lab.home, None),
    (campus_lab.PATH+"/terminal", campus_lab.terminal, None),
    (campus_lab.PATH+"/presence", None, campus_lab.share),
    (coworking.PATH, coworking_views.index, coworking.create),
    (f"{coworking.PATH}/{ID}", coworking_views.detail, None),
    (f"{coworking.PATH}/{ID}/status", coworking.status, None),
    (f"{coworking.PATH}/{ID}/export[.]csv", coworking.export, None),
    (f"{coworking.PATH}/{ID}/reference", None, coworking.reference),
    (f"{coworking.PATH}/{ID}/claim", None, coworking.claim),
    (f"{coworking.PATH}/{ID}/frame", None, coworking.frame),
    (f"{coworking.PATH}/{ID}/(pause|recount|end)", None, coworking.change),
    (room_occupancy.PATH+"/door", None, room_occupancy.door),
    (room_occupancy.PATH+"/live", room_occupancy.latest, None),
    (f"{campus_pay.PATH}/{ID}/tap", None, campus_pay.tap),
    (campus_lab.PATH + "/events", campus_lab.events, None),
    (lecture_library.PATH, lecture_library.index, None),
    (f"{lecture_library.PATH}/{lecture_library.SLUG}", lecture_library.course_page, None),
    (f"{lecture_library.PATH}/{lecture_library.SLUG}/favorite", None, lecture_library.favorite),
    (f"{lecture_library.PATH}/{lecture_library.SLUG}/{lecture_library.SLUG}", lecture_library.lesson_page, None),
    (f"{lecture_library.PATH}/{lecture_library.SLUG}/{lecture_library.SLUG}/video", lecture_library.video, None),
    (f"{lecture_library.PATH}/{lecture_library.SLUG}/{lecture_library.SLUG}/captions/([a-z]{{2,3}})", lecture_library.captions, None),
    (f"{lecture_library.PATH}/{lecture_library.SLUG}/{lecture_library.SLUG}/(notes|homework)[.]pdf", lecture_library.pdf, None),
    (f"{lecture_library.PATH}/{lecture_library.SLUG}/{lecture_library.SLUG}/progress", None, lecture_library.progress),
    (f"{lecture_library.PATH}/{lecture_library.SLUG}/{lecture_library.SLUG}/quiz", None, lecture_library.answer_quiz),
    (f"{lecture_library.PATH}/{lecture_library.SLUG}/{lecture_library.SLUG}/materials", None, lecture_library.generate),
    (lecture_channel.PATH, lecture_channel.catalogue, lecture_channel.create),
    (lecture_channel.IMPORT_PATH, lecture_channel.import_page, None),
    (lecture_channel.IMPORT_PATH+"/link", None, lecture_channel.import_link),
    (lecture_channel.IMPORT_PATH+"/upload", None, lecture_channel.import_upload),
    (f"/lectures/{ID}", lecture_channel.detail, None),
    (f"/lectures/{ID}/recording", lecture_channel.recording, None),
    (f"/lectures/{ID}/(notes|homework)[.]pdf", lecture_channel.pdf, None),
    (f"/lectures/{ID}/(sample|upload)", None, lecture_channel.receive),
    (f"/lectures/{ID}/end", None, lecture_channel.finish),
    (f"/lectures/{ID}/status", lecture_channel.status, None),
    (f"/lectures/{ID}/manifest", lecture_channel.manifest, None),
    (f"/lectures/{ID}/segments/([0-9]{{1,4}})", lecture_channel.media, None),
    (f"/lectures/{ID}/analyse", None, lecture_channel.analyse),
    (f"/lectures/{ID}/stop", None, lecture_channel.stop_analysis),
    (f"/lectures/{ID}/assignment", None, lecture_channel.assignment),
    (f"/lectures/{ID}/task", None, lecture_channel.personal_task),
    (f"/lectures/{ID}/quiz", None, lecture_channel.answer_quiz),
    (lecture_studio.PATH, lecture_studio.index, save_lecture_work),
    (lecture_studio.RECORDING, lecture_studio.recording, None),
    (f"/staff/work/{ID}", coursework_views.assignment_detail, None),
    (f"/staff/work/{ID}/edit", coursework_views.assignment_form, save_coursework),
    (f"/staff/work/{ID}/(publish|archive)", None, publish_coursework),
    (f"/staff/work/review/{ID}", coursework_views.review_form, None),
    (f"/staff/work/review/{ID}/(draft|return|publish)", None, review_coursework),
    (f"/grades/{ID}", views.grade_detail, None),
    (f"/grades/{ID}/scenario", None, grade_scenario),
    (f"/grades/{ID}/plan", None, create_plan),
    ("/tasks", study_views.tasks, None),
    ("/tasks/new", study_views.task_form, save_task),
    (f"/tasks/{ID}", study_views.task_detail, None),
    (f"/tasks/{ID}/edit", study_views.task_form, save_task),
    (f"/tasks/{ID}/(complete|reopen|delete)", None, task_action),
    (f"/tasks/{ID}/subtasks", None, subtask_action),
    (f"/tasks/{ID}/subtasks/{ID}/(complete|reopen|delete)", None, subtask_action),
    ("/notes/(subject|lesson|task)/([a-z0-9-]+)", None, create_note),
    (f"/notes/{ID}", study_views.note_detail, update_note),
    (f"/notes/{ID}/(delete)", None, update_note),
    ("/events/new", study_views.event_form, save_event),
    (f"/events/{ID}", study_views.event_detail, None),
    (f"/events/{ID}/edit", study_views.event_form, save_event),
    (f"/events/{ID}/(skip|delete)", None, event_action),
    ("/bookings", reservation_views.catalog_page, None),
    ("/bookings/mine", reservation_views.my_bookings, None),
    (f"/bookings/{ID}", reservation_views.booking_detail, None),
    (f"/bookings/{ID}/reminder", None, activity_reminder),
    (rf"/bookings/{ID}/calendar\.ics", activity_calendar, None),
    (f"/bookings/{ID}/cancel", None, cancel_booking),
    (f"/bookables/{ID}", reservation_views.bookable_detail, None),
    (f"/slots/{ID}", reservation_views.slot_detail, None),
    (f"/slots/{ID}/book", None, reserve),
    (f"/slots/{ID}/(wait|leave|auto-wait|withdraw-auto)", None, wait_booking),
    ("/student-services", lambda r, db: public_views.services_page(r, db)
     if r.public and r.query("mode") != "demo" else extra_views.services_hub(r, db), None),
    ("/teams", team_views.index, team_create),
    ("/clubs", team_views.clubs, None),
    (f"/teams/{ID}", team_views.team_page, None),
    (f"/teams/{ID}/invite", None, team_invite),
    (f"/teams/invitations/{ID}", team_views.invitation, None),
    (f"/teams/invitations/{ID}/(accepted|declined)", None, team_reply),
    (f"/teams/{ID}/invites/{ID}/revoke", None, team_revoke),
    (f"/teams/{ID}/remove/{ID}", None, team_remove),
    (f"/teams/{ID}/consent", None, team_consent),
    (f"/teams/{ID}/tasks/new", team_views.task_form, team_save_task),
    (f"/teams/{ID}/tasks/{ID}", team_views.task_detail, None),
    (f"/teams/{ID}/tasks/{ID}/edit", team_views.task_form, team_save_task),
    (f"/teams/{ID}/tasks/{ID}/(done|todo)", None, team_task_state),
    (f"/teams/{ID}/tasks/{ID}/subtasks", None, team_subtask),
    (f"/teams/{ID}/tasks/{ID}/subtasks/{ID}/(complete|reopen)", None, team_subtask),
    (f"/teams/{ID}/share-note", None, team_share_note),
    (f"/teams/{ID}/notes/{ID}", team_views.note_detail, None),
    (f"/teams/{ID}/notes/{ID}/revoke", None, team_share_note),
    (f"/teams/{ID}/post", None, team_post),
    (f"/teams/{ID}/tasks/{ID}/post", None, team_post),
    (f"/teams/{ID}/report/{ID}", None, team_report),
    (f"/teams/{ID}/free", team_views.free, None),
    (f"/teams/{ID}/meetings", None, team_meeting_create),
    (f"/teams/meetings/{ID}", team_views.meeting_detail, None),
    (f"/teams/meetings/{ID}/(move|cancel|accepted|declined)", None, team_meeting_change),
    ("/staff/moderation", team_views.moderation, None),
    (f"/staff/moderation/{ID}", None, team_moderate),
    ("/activities", lambda r, db: reservation_views.catalog_page(r, db, activities=True), None),
    (campus_pay.PATH, campus_pay.index, demo_checkout),
    (campus_pay.TERMINAL, campus_pay.terminal, None),
    (f"{campus_pay.PATH}/{ID}", campus_pay.detail, None),
    (f"{campus_pay.PATH}/{ID}/(confirm|cancel)", None, demo_purchase_action),
    (room_occupancy.PATH, room_occupancy.index, prepare_occupancy_demo),
    (campus_directory.PATH, campus_directory.index, None),
    (campus_directory.PATH+r"/contacts/([1-9][0-9]{0,8})",campus_directory.contact,None),
    ("/curriculum", extra_views.curriculum_page, None),
    (f"/curriculum/course/{ID}", academic_views.course_page, None),
    (f"/curriculum/block/{ID}", None, choose_plan_block),
    (f"/curriculum/{ID}/choose", None, choose_elective),
    ("/retakes", academic_views.debts_page, None),
    (f"/retakes/{ID}", academic_views.debt_detail, None),
    ("/staff/retakes", lambda r, db: academic_views.debts_page(r, db, manage=True), None),
    (f"/staff/retakes/{ID}", lambda r, db, did: academic_views.debt_detail(r, db, did, manage=True), None),
    (f"/staff/retakes/{ID}/(open|admit|revoke|offer|result)", None, retake_change),
    (f"/staff/attendance/{ID}", academic_views.attendance_page, save_attendance),
    ("/finance", finance_views.index, None),
    ("/finance/visibility", None, finance_visibility),
    ("/finance/budget", finance_views.budget_page, None),
    ("/finance/budget/(income|expense)", None, budget_add),
    ("/residence", finance_views.residence_page, None),
    ("/equipment", campus_views.equipment_page, None),
    (f"/equipment/{ID}", campus_views.unit_detail, None),
    (f"/equipment/{ID}/reserve", None, reserve_equipment),
    (f"/equipment/loans/{ID}", campus_views.equipment_loan, None),
    (f"/equipment/loans/{ID}/(cancel)", None, equipment_state),
    ("/staff/equipment", lambda r, db: campus_views.equipment_page(r, db, manage=True), None),
    (f"/staff/equipment/loans/{ID}", lambda r, db, lid: campus_views.equipment_loan(r, db, lid, manage=True), None),
    (f"/staff/equipment/loans/{ID}/(issue|return)", None, equipment_state),
    (f"/staff/equipment/{ID}/maintenance/(enable|disable)", None, service_equipment),
    (f"/finance/charges/{ID}", finance_views.charge_detail, None),
    (f"/finance/charges/{ID}/pay", None, finance_pay),
    (f"/finance/receipts/{ID}", finance_receipt, None),
    (f"/finance/awards/{ID}", finance_views.award_detail, None),
    ("/staff/finance", finance_views.staff_index, None),
    (f"/staff/finance/{ID}", lambda r, db, aid: finance_views.award_detail(r, db, aid, manage=True), None),
    (f"/staff/finance/{ID}/pay", None, finance_disburse),
    ("/library", extra_views.library_page, None),
    ("/library/mine", extra_views.my_loans, None),
    (f"/library/books/{ID}/reserve", None, reserve_book),
    (f"/library/holds/{ID}", campus_views.hold_detail, None),
    (f"/library/holds/{ID}/cancel", None, cancel_book_hold),
    (f"/library/{ID}", extra_views.loan_detail, None),
    (f"/library/{ID}/(renew)", None, loan_update),
    ("/portfolio", extra_views.portfolio_page, None),
    ("/portfolio/resume", career_views.resume_page, save_resume),
    (f"/portfolio/resume/{ID}", career_views.resume_detail, None),
    (f"/portfolio/resume/{ID}/download", resume_pdf, None),
    ("/portfolio/new", extra_views.portfolio_form, save_portfolio),
    (f"/portfolio/{ID}", extra_views.portfolio_detail, None),
    (f"/portfolio/{ID}/edit", extra_views.portfolio_form, save_portfolio),
    (f"/portfolio/{ID}/(delete)", None, save_portfolio),
    (f"/portfolio/{ID}/(verify|withdraw)", None, achievement_request),
    ("/opportunities", extra_views.opportunity_page, None),
    (f"/opportunities/{ID}", extra_views.opportunity_detail, save_opportunity),
    ("/practice", career_views.practice_list, None),
    (f"/practice/{ID}", career_views.practice_case, None),
    (f"/practice/{ID}/diary", None, practice_diary),
    (f"/practice/{ID}/report", None, practice_report),
    ("/staff/practice", lambda r, db: career_views.practice_list(r, db, manage=True), None),
    (f"/staff/practice/{ID}", lambda r, db, pid: career_views.practice_case(r, db, pid, manage=True), None),
    (f"/staff/practice/{ID}/review", None, practice_review),
    ("/staff/achievements", career_views.review_list, None),
    (f"/staff/achievements/{ID}", None, achievement_review),
    ("/courses", career_views.course_list, None),
    (f"/courses/{ID}", career_views.course_detail, None),
    (f"/courses/{ID}/(enroll|favorite|unfavorite)", None, course_change),
    (f"/courses/{ID}/lessons/{ID}", career_views.course_lesson, None),
    (f"/courses/{ID}/lessons/{ID}/finish", None, complete_lesson),
    ("/requests", application_list, None),
    (f"/requests/{ID}", views.request_detail, None),
    (f"/requests/{ID}/(save|send|cancel|receive)", None, student_application_update),
    (f"/requests/{ID}/comment", None, student_application_comment),
    (f"/requests/{ID}/attachments", None, attachment_change),
    (f"/requests/{ID}/attachments/{ID}/remove", None, attachment_change),
    (f"/attachments/{ID}", attachment_download, None),
    ("/documents", views.document_archive, None),
    (f"/documents/{ID}", document, None),
    (r"/services/([a-z0-9-]{1,72})", views.request_form, submit_request),
    (r"/services/([a-z0-9-]{1,72})/(draft)", None, submit_request),
    ("/notifications", lambda r, db: reminder_page(views.notifications, r, db), None),
    (f"/notifications/{ID}/read", None, mark_read),
    ("/notifications/read-all", None, mark_all_read),
    ("/notifications/settings", None, notification_settings),
    ("/search", views.search_page, None),
    (f"/announcements/{ID}", views.announcement, None),
    ("/staff", views.staff, None),
    ("/staff/public", public_views.review_list, source_preview),
    (f"/staff/public/{ID}", public_views.review, source_publish),
    (f"/staff/public/{ID}/rollback", None, source_rollback),
    (f"/staff/requests/{ID}", None, change_status),
    (f"/staff/requests/{ID}/(needs_changes|refused|assign|comment)", None, change_status),
    ("/staff/bookings", reservation_views.staff_catalog, None),
    (f"/staff/bookables/{ID}", reservation_views.staff_offer, None),
    (f"/staff/bookables/{ID}/(new)", reservation_views.staff_slot, save_slot),
    (f"/staff/slots/{ID}", reservation_views.staff_slot, save_slot),
    (f"/staff/slots/{ID}/attendance/{ID}", None, reservation_attendance),
    (f"/staff/slots/{ID}/tickets", event_ticket_views.check, prepare_event_ticket),
    (f"/staff/slots/{ID}/tickets/{ID}", event_ticket_views.review, redeem_event_ticket),
    (f"/staff/slots/{ID}/attendance/{ID}/correct", None, correct_event_attendance),
    (f"/bookings/{ID}/ticket", None, issue_event_ticket),
    ("/staff/library", extra_views.staff_library, None),
    ("/staff/library/issue", None, lend_book),
    (f"/staff/library/issue/{ID}", None, lend_book),
    (f"/staff/library/{ID}", lambda r, db, lid: extra_views.loan_detail(r, db, lid, manage=True), None),
    (f"/staff/library/{ID}/(return)", None, loan_update),
    ("/staff/grades", study_views.staff_grades, None),
    (f"/staff/grades/{ID}", study_views.staff_grade, change_grade),
    (f"/staff/grades/{ID}/(component)", None, change_grade),
    (f"/staff/schedule/{ID}", lambda r, db, eid: views.staff_lesson(r, staff_lessons(db, r.user, eid)), move),
    ("/staff/announcements", views.staff_announcements, publish),
    ("/logout", None, logout),
]


def privacy_gate(request, db):
    if not personal_data.gated(request, db):
        return None
    if request.method in {"GET", "HEAD"}:
        return redirect(personal_data.consent_url(request))
    raise DomainError(403, "Открой согласие на обработку персональных данных и проверь свой выбор.")


def privacy_pages(request, db):
    if request.method in {"GET", "HEAD"} and request.path != personal_data.WITHDRAW:
        content = (personal_data.policy_page(request) if request.path == personal_data.POLICY
                   else personal_data.consent_page(request, db))
        return render(content)
    if request.method == "POST" and request.path != personal_data.POLICY:
        if not request.user:
            raise DomainError(403, "Войди в кабинет, чтобы управлять своим согласием.")
        request.read_form(request.session)
        if request.path == personal_data.WITHDRAW:
            personal_data.record(db, request, "withdraw")
            return redirect(personal_data.CONSENT)
        if not request.privacy.configured:
            raise DomainError(403, "Сведения об операторе пока не опубликованы.")
        personal_data.record(db, request, "grant")
        target = access_paths.for_role(personal_data.continuation(request), request.user["role"])
        return redirect(target or home(request.user))
    allowed = {personal_data.POLICY: "GET, HEAD", personal_data.CONSENT: "GET, HEAD, POST",
               personal_data.WITHDRAW: "POST"}[request.path]
    return 405, views.error(request, 405, "Метод запроса недоступен."), [("Allow", allowed)]


def dispatch(request, db):
    for prefix in ("/calendar/feed/", "/shared/document/"):
        if not request.path.startswith(prefix):
            continue
        token = request.path.removeprefix(prefix)
        if not auth.TOKEN.fullmatch(token):
            raise DomainError(404, "Ссылка недоступна.")
        if request.method not in {"GET", "HEAD"}:
            return 405, "Method not allowed", [("Allow", "GET, HEAD")]
        with read_snapshot(db):
            if prefix == "/calendar/feed/":
                return portability_views.download(calendar_export.subscription(db, token))
            shared = document_share.shared(db, token)
            return 200, shared["pdf"], [
                ("Content-Type", "application/pdf"),
                ("Content-Disposition", 'attachment; filename="demo-document.pdf"'),
            ]
    request.user = auth.current_user(db, request.session)
    if request.path.startswith("/auth/") or request.path in {"/profile/access/confirm", access_views.DISMISS}:
        if (blocked := privacy_gate(request, db)):
            return blocked
        from . import access_routes
        return access_routes.dispatch(request, db)
    remembered = request.cookie(request.remember_cookie)
    if request.user is None and remembered and request.method == "GET":
        signed = auth.resume_device(db, remembered)
        if signed:
            set_login(request, signed, resumed=True)
            request.session = signed.bearer
            request.csrf = auth.csrf_token(signed.bearer)
            request.user = auth.current_user(db, request.session)
        else:
            request.set_cookie(request.remember_cookie, "", 0)
    if request.path in {personal_data.POLICY, personal_data.CONSENT, personal_data.WITHDRAW}:
        return privacy_pages(request, db)
    if request.path == "/profile/access":
        if (blocked := privacy_gate(request, db)):
            return blocked
        from . import access_routes
        return access_routes.dispatch(request, db)
    if request.method == "GET" and request.user and request.user["role"] == "student" and request.path != "/profile/photo":
        request.has_profile_photo = db.execute("SELECT 1 FROM profile_photo WHERE owner_id=?",
                                               (request.user["id"],)).fetchone() is not None
    if request.path.startswith(share_views.PUBLIC):
        return open_calendar_share(request,db)
    if request.path == "/campus-pass" or (request.browser_demo and request.path == "/demo"):
        from . import demo_views
        if request.path == "/campus-pass" and not (request.browser_demo or demo_views.campus_pass_available(request)):
            raise DomainError(404, "Пропуск недоступен для этого аккаунта.")
        if request.method not in {"GET", "HEAD"}:
            return 405, views.error(request, 405, "Этот экран доступен для просмотра."), [("Allow", "GET, HEAD")]
        if request.path == "/demo":
            return render(demo_views.index(request))
        if (blocked := privacy_gate(request, db)):
            return blocked
        if request.user is None:
            return redirect("/login")
        role(request.user, "student")
        return render(demo_views.campus_pass(request))
    public_handler = {"/guest": public_directory.guest, "/universities": public_directory.index,
                      "/universities/group": public_directory.group, "/universities/room": public_directory.room,
                      "/campus-map": campus_map_view.index,
                      "/campus-map/suggest": lambda req, conn: application_json(campus_map_view.suggestions(req)),
                      }.get(request.path)
    if public_handler:
        if request.method in {"GET", "HEAD"}:
            with read_snapshot(db):
                result = public_handler(request, db)
                return result if isinstance(result, tuple) else render(result)
        return 405, views.error(request, 405, "Этот раздел доступен для просмотра."), [("Allow", "GET, HEAD")]
    if request.path == "/login":
        if request.method in {"GET", "POST"}:
            return login(request, db)
        return 405, views.error(request, 405, "Метод запроса недоступен."), [("Allow", "GET, POST")]
    if request.user is None:
        if request.path == "/logout" and request.method == "POST":
            request.read_form(request.session)
            return logout(request, db)
        if request.method == "GET":
            return redirect(access_paths.protected_entry(request))
        if request.method == "POST":
            return form_expired(request)
        return render(views.error(request, 403, "Войди, чтобы выполнить действие."), 403)
    if (blocked := privacy_gate(request, db)):
        return blocked
    request.public = public_schedule.context(db, request.user)
    if request.method == "GET" and request.path != "/onboarding":
        from .access_routes import offer
        request.access_offer = None if request.query("tour") else offer(request, db)
        request.tour_step = onboarding.position(request, db)
    if request.method == "GET" and request.user["role"] == "student":
        request.has_unread = bool(svc.notifications(db, request.user, unread=True, limit=1, selection=request.public))
    if request.path == "/" and request.method == "GET" and request.user["role"] == "staff":
        return redirect("/staff")
    for pattern, read, change in ROUTES:
        match = re.fullmatch(pattern, request.path)
        if match is None:
            continue
        if read and (request.method == "GET" or (request.method == "HEAD" and read in {
                lecture_library.video, lecture_library.captions, lecture_library.pdf, lecture_channel.recording, lecture_channel.pdf})):
            result = read(request, db, *match.groups())
            return result if isinstance(result, tuple) else render(result)
        if request.method == "POST" and change:
            if change is dictate:
                return change(request, db)
            if change is lecture_channel.import_upload:
                request.check_form_origin(request.session)
                return change(request, db)
            allow_upload = ((change is attachment_change and len(match.groups()) == 1)
                            or change in {material_create, start_import, import_photo, preview_restore,
                                          attach_coursework, save_profile_photo, lecture_channel.receive})
            form_limit = calendar_import.CSV_FORM_LIMIT if change is start_import and match[1] == "csv" else FORM_LIMIT
            if change is coworking.frame:
                coworking.station(request)
                form_limit = 640000
            if change is save_profile_photo:
                role(request.user, "student")
            upload_limit = photo_import.PROFILE_MAX_BYTES if change is save_profile_photo else attachment.MAX_BYTES
            try:
                request.read_form(request.session, allow_upload=allow_upload, form_limit=form_limit,
                                  upload_limit=upload_limit,
                                  upload_fields=("week", "cloud", "photo_region") if change is import_photo else (("seq",) if change is lecture_channel.receive else ()),
                                  repeated=("event",) if change is publish_calendar_share else ())
            except FormExpired:
                if change is confirm_assistant:
                    return redirect(request.path)
                raise
            except DomainError as error:
                if error.status == 413 and change is save_profile_photo:
                    return render(views.profile(request, db, photo_error=photo_import.PROFILE_TOO_LARGE), 413)
                if error.status == 413 and change is import_photo:
                    return render(ai_views.photo(request, db, "Фото расписания превышает 2 МиБ. Выбери JPEG или PNG поменьше.",
                                                 error_status=413), 413)
                raise
            return change(request, db, *match.groups())
        methods = [method for method, handler in (("GET", read), ("POST", change)) if handler]
        return 405, views.error(request, 405, "Метод запроса недоступен."), [("Allow", ", ".join(methods))]
    raise DomainError(404, "Проверь адрес или вернись в кабинет.")


def create_app(db_path, config=None, *, access=None, ai_engine=None):
    config = config or WebConfig.from_env()
    access = access or AccessConfig.from_env()
    privacy = personal_data.Settings.from_env()
    engine = ai_engine if ai_engine is not None else ai.RouterAI.from_env()
    expected_schema = schema_version()

    def app(env, start_response):
        request = Request(env, config, access)
        request.privacy = privacy
        request.ai = engine
        base_headers = list(SECURITY_HEADERS)
        shared = request.path.startswith(share_views.PUBLIC)
        if shared:
            base_headers.append(("X-Robots-Tag","noindex, nofollow"))
        content_type = "text/html; charset=utf-8"
        try:
            request.origin = config.request_origin(env)
            if request.path == "/auth/max/miniapp" and request.access.enabled("max", request.origin):
                for index, (name, value) in enumerate(base_headers):
                    if name == "Content-Security-Policy":
                        value = value.replace("script-src 'self';", "script-src 'self' https://st.max.ru;")
                        value = value.replace("frame-ancestors 'none'", "frame-ancestors https://max.ru https://web.max.ru")
                        base_headers[index] = (name, value)
            if request.secure:
                base_headers.append(("Strict-Transport-Security", "max-age=31536000"))
            if request.path == "/favicon.ico" and request.method in {"GET", "HEAD"}:
                status, page, headers = 204, "", []
            elif request.method in {"GET", "HEAD"} and request.path.startswith("/static/"):
                status, page, headers = asset_response(request.path, env.get("QUERY_STRING", ""),
                                                       env.get("HTTP_IF_NONE_MATCH", ""))
            else:
                request.load_query()
                with closing(connect(db_path, create=False)) as db:
                    if request.path == "/healthz" and request.method in {"GET", "HEAD"}:
                        healthy = db.execute("PRAGMA user_version").fetchone()[0] == expected_schema
                        healthy = healthy and db.execute("SELECT EXISTS (SELECT 1 FROM account)").fetchone()[0]
                        status, page, headers = (200, "ok\n", []) if healthy else (503, "unavailable\n", [])
                        content_type = "text/plain; charset=utf-8"
                    else:
                        status, page, headers = dispatch(request, db)
        except FormExpired:
            status, page, headers = form_expired(request)
        except DomainError as error:
            status, page, headers = render(views.error(request, error.status, error.message), error.status)
        except sqlite3.OperationalError as error:
            LOG.error("SQLite operation failed: %s", type(error).__name__)
            status, page, headers = render(views.error(request, 503, "Попробуй обновить страницу через минуту."), 503)
        except Exception as error:
            LOG.error("Unhandled request error: %s", type(error).__name__)
            status, page, headers = render(views.error(request, 500, "Обнови страницу или вернись в кабинет."), 500)
        if getattr(request, "allow_lecture_media", False):
            base_headers = [(name, value + "; media-src 'self'" if name == "Content-Security-Policy" else value)
                            for name, value in base_headers]
        if getattr(request, "allow_door_camera", False):
            base_headers = [(name,value+"; media-src 'self' blob:" if name=="Content-Security-Policy" else value) for name,value in base_headers]
        if getattr(request, "social_links", False):
            allowed_forms = "".join(" " + host for provider, host in (
                ("vk", "https://id.vk.ru"), ("yandex", "https://oauth.yandex.ru"))
                if request.access.enabled(provider, request.origin))
            base_headers = [(name, value.replace("form-action 'self';", "form-action 'self'" + allowed_forms + ";")
                             if name == "Content-Security-Policy" else value) for name, value in base_headers]
        # Every authenticated share page, including an error, can render a
        # same-origin form (the account menu contains logout for both roles).
        if shared and not request.user:
            base_headers = [(name, "no-referrer" if name == "Referrer-Policy" else value)
                            for name, value in base_headers]
        body = page.encode("utf-8") if isinstance(page, str) else page
        headers.extend(base_headers)
        headers.extend(request.response_cookies)
        if not any(key == "Cache-Control" for key, _ in headers):
            headers.append(("Cache-Control", "no-store"))
        if status not in {204, 304}:
            if not any(key == "Content-Type" for key, _ in headers):
                headers.append(("Content-Type", content_type))
            headers.append(("Content-Length", str(len(body))))
        start_response(f"{status} {HTTPStatus(status).phrase}", headers)
        if request.method == "HEAD":
            return [b""]
        return iter(body) if isinstance(body, lecture_studio.MediaBody) else [body]

    return app
