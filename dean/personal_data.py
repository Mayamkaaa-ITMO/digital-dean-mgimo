"""Public privacy information and account-scoped consent records."""

from dataclasses import dataclass
from hashlib import sha256
from os import getenv
from urllib.parse import urlencode

from . import access_paths, auth
from .db import write
from .domain import DomainError, timestamp
from .ui import button, e, form, hidden, link, page, panel

POLICY = "/privacy"
CONSENT = "/privacy/consent"
WITHDRAW = CONSENT + "/withdraw"

PURPOSE = "Ведение личного кабинета и выполнение выбранных в нём учебных действий."
DATA = ("Имя, логин, учебная роль, вуз и группа; расписание, оценки и учебные записи; "
        "материалы, фотографии и обращения, которые пользователь добавляет в кабинет.")
ACTIONS = ("Сбор, запись, систематизация, хранение, уточнение, извлечение, использование, "
           "предоставление уполномоченным сотрудникам для выполнения выбранного учебного действия, "
           "блокирование, удаление и уничтожение. Обработка автоматизированная.")
WITHDRAWAL = ("Согласие действует до достижения цели обработки или отзыва. "
              "Отозвать его можно на этой странице или по адресу оператора.")


@dataclass(frozen=True)
class Settings:
    required: bool = False
    name: str = ""
    address: str = ""
    email: str = ""
    retention: str = ""
    processors: str = ""
    auth_basis: str = ""
    transfer: str = ""

    @classmethod
    def from_env(cls):
        flag = getenv("DEAN_PRIVACY_REQUIRE_CONSENT", "0")
        if flag not in {"0", "1"}:
            raise ValueError("DEAN_PRIVACY_REQUIRE_CONSENT принимает 0 или 1.")
        settings = cls(
            required=flag == "1",
            name=getenv("DEAN_PRIVACY_OPERATOR", "").strip(),
            address=getenv("DEAN_PRIVACY_ADDRESS", "").strip(),
            email=getenv("DEAN_PRIVACY_EMAIL", "").strip(),
            retention=getenv("DEAN_PRIVACY_RETENTION", "").strip(),
            processors=getenv("DEAN_PRIVACY_PROCESSORS", "").strip(),
            auth_basis=getenv("DEAN_PRIVACY_AUTH_BASIS", "").strip(),
            transfer=getenv("DEAN_PRIVACY_TRANSFER", "").strip(),
        )
        if settings.required and not settings.configured:
            raise ValueError("Заполни реквизиты и условия обработки DEAN_PRIVACY_* перед включением согласия.")
        return settings

    @property
    def configured(self):
        return bool(all((self.name, self.address, self.email, self.retention, self.processors,
                         self.auth_basis, self.transfer)))

    @property
    def consent_text(self):
        return (
            f"Я даю оператору {self.name} (адрес {self.address}) согласие на обработку моих "
            f"персональных данных для указанной цели.",
            "Цель обработки. " + PURPOSE,
            "Категории данных. " + DATA,
            "Действия и способ обработки. " + ACTIONS,
            "Лица, обрабатывающие данные по поручению оператора. " + self.processors,
            "Сроки хранения. " + self.retention,
            "Трансграничная передача. " + self.transfer,
            WITHDRAWAL + " Контакт оператора — " + self.email + ".",
        )

    @property
    def version(self):
        return sha256("\n".join(self.consent_text).encode()).hexdigest()


def latest(db, user):
    return db.execute(
        "SELECT id, version, action FROM privacy_consent_event WHERE owner_id=? ORDER BY id DESC LIMIT 1",
        (user["id"],),
    ).fetchone()


def record(db, request, action):
    config = request.privacy
    uid = request.user["id"]
    with write(db):
        auth.require_session(db, request.session, uid)
        current = latest(db, request.user)
        if action == "grant":
            if request.field("agree") != "1":
                raise DomainError(422, "Отметь поле согласия, чтобы продолжить.")
            if request.field("version") != config.version:
                raise DomainError(409, "Согласие обновилось. Открой его заново.")
            if current and current["action"] == "grant" and current["version"] == config.version:
                return
            version = config.version
        else:
            if not current or current["action"] != "grant":
                return
            if request.field("version") != current["version"]:
                raise DomainError(409, "Согласие изменилось. Открой страницу заново.")
            version = current["version"]
        if request.field("event") != str(current["id"] if current else 0):
            raise DomainError(409, "Согласие изменилось. Открой страницу заново.")
        if action == "grant":
            db.execute("INSERT INTO privacy_consent_document VALUES (?,?) ON CONFLICT DO NOTHING",
                       (version, "\n".join(config.consent_text)))
        db.execute(
            "INSERT INTO privacy_consent_event (owner_id,version,action,recorded_at) VALUES (?,?,?,?)",
            (uid, version, action, timestamp()),
        )


def continuation(request):
    return access_paths.destination(request.field("next") or request.query("next"))


def consent_url(request):
    query = request.env.get("QUERY_STRING", "")
    next_path = access_paths.destination(request.path + ("?" + query if query else ""))
    return CONSENT + ("?" + urlencode({"next": next_path}) if next_path else "")


def gated(request, db):
    return bool(request.user and request.path != "/logout" and not permits(db, request.user, request.privacy))


def permits(db, user, settings):
    if not settings.required:
        return True
    current = latest(db, user)
    return bool(current and current["version"] == settings.version and current["action"] == "grant")


def paragraphs(lines):
    return "".join(f"<p>{e(line)}</p>" for line in lines)


def policy_page(request):
    config = request.privacy
    if config.configured:
        operator = paragraphs((config.name, config.address, "Контакт по персональным данным — " + config.email))
        terms = paragraphs(("Основание обработки для входа и защиты учётных записей. " + config.auth_basis,
                            "Ведение личного кабинета в режиме согласия основано на согласии пользователя.",
                            "Сроки хранения. " + config.retention,
                            "Обработка по поручению оператора. " + config.processors,
                            "Трансграничная передача. " + config.transfer))
        inquiry = "По этим вопросам можно обратиться к оператору по указанному адресу."
    else:
        operator = paragraphs(("Реквизиты оператора для этого стенда не опубликованы. Используй тестовые данные.",))
        terms = ""
        inquiry = ""
    body = panel("Оператор", operator)
    body += panel("Цели и данные", paragraphs((
        "Сервис проверяет вход и поддерживает активный сеанс. Для этого использует логин, "
        "сведения об учётной записи, технические данные о запросах и служебные файлы cookie.",
        PURPOSE + " " + DATA,
        ACTIONS,
        "Вопросы и файлы в функции ИИ, данные входа через внешнюю службу и уведомления в мессенджере "
        "обрабатываются подключёнными сервисами при использовании соответствующих функций. "
        "Доступность этих функций зависит от настроек сайта.",
    )))
    if terms:
        body += panel("Основания и условия обработки", terms)
    body += panel("Защита и права пользователя", paragraphs((
        "Доступ к данным кабинета ограничен учётной записью, ролью и учебной группой. При входе "
        "по паролю приложение хранит его криптографический хеш. На HTTPS служебные cookie передаются по защищённому соединению.",
        "Можно запросить сведения об обработке, уточнение, блокирование или уничтожение данных "
        "при наличии предусмотренных законом оснований. Согласие можно отозвать. "
        "При отзыве оператор вправе продолжать обработку при наличии другого законного основания. " + inquiry,
    )))
    body += f'<p>{link(CONSENT, "Согласие и отзыв согласия")}</p>'
    return page(request, "Политика обработки персональных данных", body, section="privacy")


def consent_page(request, db):
    config = request.privacy
    current = latest(db, request.user) if request.user else None
    withdrawal = ""
    if current and current["action"] == "grant":
        fields = hidden("version", current["version"]) + hidden("event", current["id"])
        withdrawal = panel("Отзыв согласия", form(request, WITHDRAW, fields + button("Отозвать согласие")))
    elif current:
        withdrawal = panel("Согласие отозвано", "<p>Оператор зафиксировал отзыв.</p>")
    policy = f'<p>{link(POLICY, "Политика обработки данных")}</p>'
    if not config.configured:
        return page(request, "Согласие на обработку персональных данных",
                    panel("Демонстрационный режим", "<p>Форма согласия станет доступна после публикации "
                          "реквизитов оператора.</p>") + withdrawal + policy,
                    section="privacy")
    next_path = continuation(request)
    body = panel("Текст согласия", paragraphs(config.consent_text)) + withdrawal
    if not current or current["action"] != "grant" or current["version"] != config.version:
        if request.user:
            fields = (hidden("version", config.version) + hidden("next", next_path) +
                      hidden("event", current["id"] if current else 0))
            fields += ('<label class="check"><input name="agree" type="checkbox" value="1" required>'
                       'Даю согласие на обработку персональных данных на условиях выше</label>')
            body += form(request, CONSENT, fields + button("Подтвердить согласие"))
            body += form(request, "/logout", button("Отказаться и выйти"))
        else:
            body += f'<p>{link(access_paths.login_url(CONSENT), "Войти в кабинет")}</p>'
    else:
        body += f'<p>{link(next_path or ("/staff" if request.user["role"] == "staff" else "/"), "В кабинет")}</p>'
    return page(request, "Согласие на обработку персональных данных", body + policy, section="privacy")
