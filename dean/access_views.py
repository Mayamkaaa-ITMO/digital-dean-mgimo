from base64 import b64encode

from . import access_config, auth, event_tickets, login_mascot, max_access
from .static_assets import asset_url
from .ui import back, button, e, form, hidden, input_field, link, local_label, page, panel

METHODS = "/profile/access"
DISMISS = METHODS + "/later"
REMEMBER = ('<label class="check login-remember"><input name="remember" type="checkbox" value="1">'
            '<span>Запомнить это устройство<small>На 14 дней. Только для личного кабинета.</small></span></label>')


def password_field(invalid=False, *, placeholder=""):
    feedback = ' aria-invalid="true" aria-describedby="login-error" data-restore-focus' if invalid else ""
    hint = f' placeholder="{e(placeholder)}"' if placeholder else ""
    return ('<label for="login-password">Пароль</label><div class="password-row">'
              f'<input id="login-password" name="password" type="password" autocomplete="current-password"{feedback}{hint} '
              'maxlength="256" required><button type="button" class="quiet" data-show-password '
              'aria-controls="login-password" aria-pressed="false" hidden>Показать</button></div>')


def start_button(provider, label=None, *, disabled=False):
    text = label or "Войти через " + access_config.LABELS[provider]
    if provider in access_config.SOCIAL:
        logo = asset_url("vk-id.svg" if provider == "vk" else "yandex-id.svg")
        state = ' type="button" disabled' if disabled else ' type="submit"'
        return (f'<button{state} class="quiet dean-provider" data-provider="{provider}">'
                f'<img src="{logo}" alt="" width="24" height="24"><span>{e(text)}</span></button>')
    if provider == "key":
        return f'<button type="button" class="quiet" data-passkey="login" hidden>{e(text)}</button>'
    return f'<button type="submit"{" class=quiet" if provider != "max" else ""}>{e(text)}</button>'


def setup_fields(target):
    return hidden("setup", "1") + hidden("next", target) if target else ""


def first_access(request):
    request.social_links = True
    target, providers = request.access_offer
    controls = "".join(
        form(request, f"/auth/{provider}/start", hidden("intent", "link") + setup_fields(target)
             + start_button(provider, "Подключить " + access_config.LABELS[provider]))
        for provider in providers
    )
    later = form(request, DISMISS, hidden("next", target)
                 + '<button class="quiet" data-overlay-dismiss>Позже</button>')
    return ('<dialog open class="dean-first-access" data-dean-overlay aria-labelledby="access-offer-title">'
            '<h2 id="access-offer-title" tabindex="-1" autofocus>Подключи быстрый вход</h2>'
            '<p>В следующий раз сможешь войти в этот же кабинет через привязанный аккаунт.</p>'
            f'<div class="dean-providers">{controls}</div>{later}</dialog>')


def login(request, *, message="", username="", target="", invitation=False):
    browser_demo = getattr(request, "browser_demo", False)
    design = browser_demo or getattr(request, "product_design", False)
    error = f'<p id="login-error" class="error" role="alert">{e(message)}</p>' if message else ""
    enabled = lambda name: request.access.enabled(name, request.origin or "")
    invite = hidden("invitation", "") if invitation else ""
    max_login = ""
    if enabled("max"):
        max_login = form(request, "/auth/max/start", hidden("next", target) + invite + start_button("max"),
                         style="login-alternative login-max")
    providers = ("yandex", "vk") if design else access_config.SOCIAL
    unavailable = [access_config.LABELS[name] for name in providers if not enabled(name)]
    social = "".join(form(request, f"/auth/{name}/start", hidden("next", target)
                          + start_button(name, access_config.LABELS[name] if design else None),
                          style="login-alternative") if enabled(name) else
                     start_button(name, access_config.LABELS[name], disabled=True) if browser_demo else ""
                     for name in providers)
    methods = ""
    if social:
        request.social_links = any(enabled(name) for name in access_config.SOCIAL)
        provider_hint = ""
        if browser_demo and unavailable:
            provider_hint = '<p class="dean-login-provider-hint">В демо доступен вход по логину и паролю.</p>'
        divider = "Войти с помощью" if design else "Или войди через"
        methods = ('<section class="dean-login-social" aria-label="Вход через VK и Яндекс">'
                   f'<p class="login-divider">{divider}</p><div class="dean-providers">{social}</div>{provider_hint}</section>')
    if enabled("key"):
        methods += form(request, "/auth/key/start", hidden("next", target) + start_button("key", "Ключ доступа"),
                        style="login-alternative dean-login-key")
    target_notice = '<p class="login-context">После входа откроется нужная страница.</p>' if target and not browser_demo else ""
    if invitation:
        target_notice += '<p>Для входа по приглашению подтверди вход в MAX в течение пяти минут.</p>'
    username_options = 'autocomplete="username" autocapitalize="none" spellcheck="false" maxlength="64" required'
    username_options += ' placeholder="Введи логин"'
    username_field = input_field("username", "Логин", username, username_options)
    invalid = bool(message and username)
    fields = (hidden("next", target) + username_field + password_field(invalid, placeholder="Введи пароль")
              + (error if invalid else "")
              + REMEMBER + button("Войти"))
    feedback = '<p class="error" data-auth-error role="alert" hidden></p>'
    alternative = ""
    if (unavailable and not browser_demo) or (max_login and not invitation):
        unavailable_text = (f'<p class="meta">{e(" и ".join(unavailable))} пока не подключены на этом стенде.</p>'
                            if unavailable else "")
        alternative = ('<details class="dean-login-extra"><summary>Доступность способов входа</summary>'
                       + unavailable_text + (max_login if not invitation else "") + '</details>')
    title = '<h1>Вход в деканат</h1><p class="dean-login-caption">Личное расписание, оценки и заявки</p>'
    body = ('<div class="login panel dean-login">' + title + target_notice + (max_login if invitation else "")
            + ("" if invalid else error) + form(request, "/login", fields, style="login-form") + methods + feedback)
    body += alternative
    body += '<p class="guest-entry">' + link("/universities", "Посмотреть расписание без входа") + "</p>"
    mascot = f'<div class="dean-login-visual">{login_mascot.illustration()}</div>'
    layout = f'<div class="dean-login-layout">{mascot}{body}</div></div>'
    script = f'<script src="{asset_url("login-mascot.js")}" defer></script>'
    return page(request, "Вход в деканат", layout + script, section="login")


def profile(request, methods, credentials, personal, message="", is_error=False, setup=""):
    back_link = back("/profile", "К профилю")
    feedback = f'<p role="{'alert' if is_error else 'status'}">{e(message)}</p>' if message else ""
    if not personal:
        switch = form(request, "/logout", hidden("next", METHODS) + button("Сменить аккаунт"))
        return page(request, "Способы входа", back_link + panel(
            "Общий демо-аккаунт", f'<p>Ты вошёл как <strong>{e(request.user["username"])}</strong>. '
            'Для привязки VK ID или Яндекс ID войди в свой личный кабинет.</p>' + switch))
    recent = auth.recent_confirmation(request.user)
    enabled = lambda name: request.access.enabled(name, request.origin)
    request.social_links = any(enabled(name) for name in access_config.SOCIAL)
    rows = f'<li><strong>Логин и пароль</strong><p>Твой логин: {e(request.user["username"])}</p></li>'
    for provider in ("max", "vk", "yandex"):
        if provider == "max" and provider not in methods and not enabled(provider):
            continue
        label = access_config.LABELS[provider]
        address = f"/auth/{provider}"
        remove = form(request, address + "/unlink", button("Отвязать " + label))
        link_method = form(request, address + "/start", hidden("intent", "link") + setup_fields(setup)
                           + start_button(provider, "Привязать " + label))
        if provider in methods:
            state = "<p>Привязан</p>" if enabled(provider) else "<p>Привязан. Сейчас вход через него недоступен.</p>"
            action = remove if recent else ""
        elif not enabled(provider):
            state, action = "<p>Подключается администратором стенда.</p>", ""
        else:
            state, action = "<p>Доступен для привязки</p>", link_method if recent else ""
        rows += f"<li><strong>{e(label)}</strong>{state}{action}</li>"
    key_button = '<button type="button" data-passkey="register" hidden>Добавить ключ доступа</button>'
    keys = ""
    for row in credentials:
        added = local_label(row["created_at"], request.user["timezone"])
        remove = form(request, f'/auth/key/{row["id"]}/unlink', button("Удалить ключ")) if recent else ""
        keys += f"<li>Добавлен {e(added)}{remove}</li>"
    key_support = ('<p data-key-unsupported hidden>В этом браузере ключи доступа недоступны.</p>'
                   if enabled("key") else "<p>Ключи доступа подключаются администратором стенда.</p>")
    key_forms = f'<ul class="rows">{keys}</ul>' if keys else ""
    if enabled("key") and recent:
        key_forms += form(request, METHODS, key_button, style="key-form")
    rows += ("<li><strong>Ключ доступа</strong><p>Вход с подтверждением на устройстве.</p>"
             f"{key_support}{key_forms}</li>")
    confirm = ""
    if not recent:
        fields = (f'<input type="hidden" name="username" value="{e(request.user["username"])}" autocomplete="username">'
                  + setup_fields(setup) + password_field() + button("Подтвердить паролем"))
        alternatives = "".join(form(request, f"/auth/{provider}/start",
                                   hidden("intent", "confirm") + setup_fields(setup)
                                   + start_button(provider, "Подтвердить через " + access_config.LABELS[provider]))
                               for provider in methods if enabled(provider))
        if enabled("key") and credentials:
            alternatives += form(request, METHODS,
                                 '<button type="button" data-passkey="confirm" hidden>Подтвердить ключом доступа</button>')
        confirm = panel("Подтверди вход", '<p>Для изменения способов входа нужно подтвердить, что это твой кабинет.</p>'
                        + form(request, "/profile/access/confirm", fields) + alternatives)
    body = f'<div class="access-profile access-panel">{back_link}{feedback}{confirm}<p class="error" data-auth-error role="alert" hidden></p>'
    body += panel("Вход в один кабинет", f'<ul class="rows">{rows}</ul>')
    if setup:
        body += form(request, DISMISS, hidden("next", setup) + button("Продолжить в кабинет"))
    body += "</div>"
    return page(request, "Способы входа", body, section="access")


def remove(request, provider, path):
    label = access_config.LABELS[provider]
    body = (back(METHODS, "К способам входа")
            + panel(f"Удалить способ «{label}»?", '<p>Сеансы с этим способом входа завершатся.</p>'
                    + form(request, path, hidden("confirm", "1") + button("Да, удалить"))))
    return page(request, "Подтверждение", body)


def wait_max(request, state, row, message=""):
    target = max_access.url(request.access, state)
    image = b64encode(event_tickets.png(target)).decode("ascii")
    action = {"login": "Войти в кабинет", "link": "Привязать MAX", "confirm": "Подтвердить личность"}[row["intent"]]
    status = '<p role="status">MAX подтвердил действие.</p>' if row["subject_hash"] else ""
    notice = f'<p role="status">{e(message)}</p>' if message else ""
    root = f"/auth/max/wait/{state}"
    body = (f'<p>{action} в цифровом деканате. Попытка действует пять минут.</p>'
            f'<p><a class="max-link" href="{e(target)}" rel="noreferrer" target="_blank">Открыть MAX</a></p>'
            '<details><summary>Подтвердить на телефоне</summary><p>Отсканируй QR-код камерой телефона.</p>'
            f'<img src="data:image/png;base64,{image}" alt="QR-код открытия мини-приложения MAX"></details>'
            '<p>Введи этот код в MAX, затем вернись сюда.</p>'
            f'<p class="auth-pin"><strong>{e(row["confirmation_code"])}</strong></p>{status}{notice}'
            + form(request, root + "/finish", button(action) if status else button("Проверить подтверждение"))
            + form(request, root + "/cancel", '<button class="quiet">Отменить</button>'))
    return page(request, "Подтверждение в MAX", '<div class="access-panel">' + panel("Код входа", body) + "</div>")


def max_miniapp(request):
    fields = ('<label>Код со страницы входа<input name="pin" inputmode="numeric" autocomplete="off" '
              'pattern="[0-9]{6}" maxlength="6" required></label>' + button("Подтвердить"))
    body = (f'<script src="{max_access.BRIDGE}" defer></script>'
            '<p>Введи шесть цифр из браузера, в котором ты начал вход или привязку.</p>'
            '<p class="error" data-auth-error role="alert" hidden></p>'
            '<p data-max-done role="status" hidden>Подтверждено. Заверши действие в исходном браузере.</p>'
            '<a data-max-return hidden>Вернуться в браузер</a>'
            '<form data-max-approval method="post" action="/auth/max/approve" hidden>' + fields + '</form>')
    return page(request, "Подтверждение в MAX", '<div class="login panel">' + body + '</div>')
