import re
from urllib.parse import parse_qsl, urlencode, urlsplit

DETAIL = re.compile(r"/(?:requests|documents|schedule(?:/public|/raspay/lesson)?|grades|tasks|work|announcements"
                    r"|slots|bookings|bookables|calendar/import|teams(?:/invitations|/meetings)?"
                    r"|staff/(?:grades|retakes|schedule|slots|work|work/review|support))/[a-f0-9]{32}")
SIMPLE = frozenset({"/", "/requests", "/documents", "/grades", "/notifications", "/profile", "/profile/access",
                    "/profile/sessions", "/staff", "/tasks", "/bookings", "/all-services", "/assistant", "/lecture-library", "/lectures", "/campus-lab", "/coworking",
                    "/onboarding", "/calendar/import", "/calendar/import/photo", "/calendar/share",
                    "/profile/telegram", "/work", "/staff/grades", "/staff/retakes", "/staff/manage", "/campus-pay/terminal",
                    "/privacy/consent", "/tasks/new", "/bookings/mine", "/teams", "/support", "/staff/support",
                    "/staff/bookings", "/staff/work", "/staff/work/new", "/staff/library", "/staff/announcements"})
SCHEDULE = frozenset({"/schedule", "/schedule/raspay"})
SCHEDULE_LESSON = re.compile(r"/schedule/(?:public/|raspay/lesson/)?[a-f0-9]{32}")
STUDENT_SECTIONS = frozenset({"all-services", "bookables", "bookings", "calendar", "grades",
                              "notifications", "onboarding", "schedule", "services", "slots", "tasks", "teams", "work"})


def destination(value):
    if not value.startswith("/") or value.startswith("//") or len(value) > 900:
        return ""
    if "\\" in value or any(ord(char) < 33 or ord(char) == 127 for char in value):
        return ""
    try:
        target = urlsplit(value)
        pairs = parse_qsl(target.query, strict_parsing=True, max_num_fields=5)
        args = dict(pairs)
    except ValueError:
        return ""
    if target.scheme or target.netloc or target.fragment or len(args) != len(pairs):
        return ""
    if target.path == "/universities/group":
        if not re.fullmatch(r"[a-z0-9-]{1,45}", args.get("source", "")) or not re.fullmatch(
            r"[a-zA-Zа-яА-Я0-9_./-]{1,50}", args.get("group", "")
        ):
            return ""
        allowed = {key: args[key] for key in ("source", "group")}
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.get("week", "")):
            allowed["week"] = args["week"]
        return target.path + "?" + urlencode(allowed)
    if target.path in SCHEDULE or SCHEDULE_LESSON.fullmatch(target.path):
        allowed = {}
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.get("date", "")):
            allowed["date"] = args["date"]
        if args.get("view") in {"day", "week", "month", "list"}:
            allowed["view"] = args["view"]
        for key in ("kind", "subject", "visibility"):
            if args.get(key):
                allowed[key] = args[key][:240]
        return target.path + ("?" + urlencode(allowed) if allowed else "")
    if re.fullmatch(r"/lecture-library/[a-z0-9-]{1,64}(?:/[a-z0-9-]{1,64})?", target.path):
        return target.path
    if re.fullmatch(r"/documents/[a-f0-9]{32}", target.path) and args.get("preview") == "1":
        return target.path + "?preview=1"
    if re.fullmatch(r"/services/[a-z0-9-]{1,72}", target.path):
        return target.path
    if target.path in SIMPLE or DETAIL.fullmatch(target.path) or re.fullmatch(r"/calendar/shared/[A-Za-z0-9_-]{43}", target.path):
        return target.path
    return ""


def for_role(value, role):
    target = destination(value)
    path = urlsplit(target).path
    section = path.split("/", 2)[1] if path else ""
    if section == "staff" and role != "staff":
        return ""
    if role == "staff" and (section in STUDENT_SECTIONS or path in {"/", "/documents"}):
        return ""
    return target


def form_return(request):
    try:
        source = urlsplit(request.env.get("HTTP_REFERER", ""))
    except ValueError:
        source = urlsplit("")
    if request.origin == f"{source.scheme}://{source.netloc}":
        target = destination(source.path + ("?" + source.query if source.query else ""))
        if target:
            return target

    path = request.path
    if path.startswith("/staff/requests/"):
        path = path.removeprefix("/staff")
    if path in {"/calendar/import/csv", "/calendar/import/draft"}:
        return "/calendar/import"
    if path in {"/profile/photo", "/profile/photo/remove"}:
        return "/profile"
    while path:
        if target := destination(path):
            return target
        path = path.rpartition("/")[0]
    return ""


def login_url(target="", notice=""):
    args = {key: value for key, value in (("next", destination(target)), ("notice", notice)) if value}
    return "/login" + ("?" + urlencode(args) if args else "")


def protected_entry(request):
    query = request.env.get("QUERY_STRING", "")
    return login_url(request.path + ("?" + query if query else "")) if request.path != "/" else "/login"
