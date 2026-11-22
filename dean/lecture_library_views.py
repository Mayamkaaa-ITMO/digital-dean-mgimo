"""Lecture catalog, course and lesson presentation."""
import json

from . import appearance, lecture_channel
from .lecture_library import (
    MATERIALS_FEEDBACK, PATH, catalog_data, duration, favorite_ids, keywords, link_live, seconds_value, selected,
    stored_material, watch_path,
)
from .ui import back, button, e, form, hidden, input_field, link, page, panel, select_field


def index(request, db):
    request.library_page = True
    q = request.query("q").strip()[:100]
    subject, author, kind = (request.query(name)[:100] for name in ("subject", "author", "kind"))
    sort = request.query("sort")
    own = request.query("mine") == "1"
    favorites = favorite_ids(db, request.user)
    entries = list(catalog_data().values())
    more_filters = ""
    for name, label, value in (("subject", "Дисциплина", subject), ("author", "Автор", author), ("kind", "Формат", kind)):
        options = [(choice, choice) for choice in sorted({course[name] for course in entries})]
        more_filters += select_field(name, label, [("", "Все"), *options], value)
    more_filters += select_field("sort", "Сортировка", [("", "По названию"), ("short", "Сначала короткие")], sort)
    checked = " checked" if own else ""
    more_filters += f'<label class="check"><input type="checkbox" name="mine" value="1"{checked}>Избранное</label>'
    expanded = " open" if any((subject, author, kind, sort, own)) else ""
    filters = (
        f'<form class="panel library-filters" action="{PATH}" method="get"><div class="library-search">'
        + input_field("q", "Поиск по курсам", q, 'maxlength="100" placeholder="Лекция, конспект или автор"', "search")
        + button("Найти") + '</div>'
        f'<details{expanded}><summary>Фильтры</summary><div class="library-filter-fields">{more_filters}'
        f'</div><div class="library-actions">{button("Применить фильтры")}{link(PATH, "Сбросить фильтры")}</div></details></form>'
    )
    matches = []
    for course in entries:
        if (subject and course["subject"] != subject or author and course["author"] != author
                or kind and course["kind"] != kind or own and course["slug"] not in favorites):
            continue
        haystack = keywords([course["title"], course["author"], course["subject"], course["description"],
                             *((lesson["title"], lesson["chapters"], lesson["notes"]) for lesson in course["lessons"].values())])
        if all(word in haystack for word in q.casefold().split()):
            matches.append(course)
    matches.sort(key=lambda course: (
        sum(lesson["duration"] for lesson in course["lessons"].values()) if sort == "short" else 0, course["title"],
    ))
    cards = "".join(
        f'<article class="library-card panel"><span class="library-icon">{appearance.icon("book")}</span>'
        f'<div><p class="meta">{e(course["subject"])}</p><h3>{link(PATH+"/"+course["slug"], course["title"])}</h3>'
        f'<p>{e(course["description"])}</p><p class="meta">{e(course["author"])} · '
        f'{appearance.count_label(len(course["lessons"]), "лекция", "лекции", "лекций")}</p></div></article>'
        for course in matches
    )
    catalog = (
        f'<section class="library-collection"><h2>Открытые курсы</h2><div class="library-grid">{cards}</div></section>'
        if cards else panel("Курсы не найдены", "<p>Измени запрос или сбрось фильтры.</p>")
    )
    live = link_live(request)
    live_action = f'<div class="library-actions">{live}</div>' if live else ""
    imports = lecture_channel.visible_imports(db, request.user)
    imported = ""
    for visibility, label in (("private", "Личные лекции"), ("group", "Лекции группы")):
        imported_rows = "".join(
            f'<li><a href="/lectures/{e(item["id"])}"><span><strong>{e(item["title"])}</strong>'
            f'<small class="meta">{e(lecture_channel.job_state(item,item["queued"])["status"] or "Запись лекции")}</small></span>'
            f'{appearance.icon("chevron-right")}</a></li>' for item in imports if item["visibility"] == visibility
        )
        if imported_rows:
            imported += panel(label, f'<ul class="library-lesson-list">{imported_rows}</ul>')
    add = link("/lectures/import", "Добавить лекцию", "rw-primary-link") if lecture_channel.can_import(db, request.user) else ""
    body = f'<div class="library-page">{live_action}{imported}{filters}{catalog}</div>'
    return page(request, "Лекции", body, section="lecture-library", heading_action=add)


def course_page(request, db, slug):
    course, _ = selected(slug)
    request.library_page = True
    own = slug in favorite_ids(db, request.user)
    base = PATH + "/" + slug
    pinned = form(request, base + "/favorite", hidden("value", str(int(not own)))
                  + '<button class="quiet">' + ("Убрать из избранного" if own else "В избранное") + "</button>")
    progress = {row["lesson"]: row for row in db.execute(
        "SELECT * FROM lecture_library_progress WHERE account_id=? AND course=?", (request.user["id"], slug),
    )}
    latest = max(progress.values(), key=lambda row: row["updated_at"], default=None)
    resume = ""
    if latest and latest["lesson"] in course["lessons"] and not latest["completed"]:
        resume = link(watch_path(course, course["lessons"][latest["lesson"]]),
                      "Продолжить с " + duration(latest["seconds"]), "rw-primary-link")
    add = (link(lecture_channel.IMPORT_PATH, "Добавить лекцию по ссылке", "dean-action-link")
           if lecture_channel.can_import(db, request.user) else "")
    rows = []
    outline = []
    for lesson in course["lessons"].values():
        url = watch_path(course, lesson)
        seen = progress.get(lesson["slug"])
        state = " · просмотрено" if seen and seen["completed"] else ""
        rows.append(f'<li><a href="{e(url)}"><span><strong>{e(lesson["title"])}</strong>'
                    f'<small class="meta">{duration(lesson["duration"])}{state}</small></span>'
                    f'{appearance.icon("chevron-right")}</a></li>')
        chapters = "".join(f'<li>{link(f"{url}?t={seconds}", duration(seconds)+" · "+label)}</li>'
                           for seconds, label in lesson["chapters"])
        if chapters:
            outline.append(f'<h3>{e(lesson["title"])}</h3><ul class="library-downloads">{chapters}</ul>')
    course_outline = ('<details class="panel library-transcript"><summary>Содержание курса</summary>'
                      + "".join(outline) + '</details>') if outline else ""
    body = (f'<div class="library-page">{back(PATH, "К списку лекций")}<div class="library-course-summary">'
            f'<p>{e(course["description"])}</p>'
            f'<p class="meta">{e(course["author"])} · {e(course["subject"])} · {e(course["kind"])}</p></div>'
            f'<div class="library-actions">{resume}{pinned}{add}</div>'
            + panel("Лекции курса", '<ol class="library-lesson-list">' + "".join(rows) + "</ol>") + course_outline + '</div>')
    return page(request, course["title"], body, section="lecture-library")


def lesson_page(request, db, slug, lesson_slug):
    course, lesson = selected(slug, lesson_slug)
    request.allow_lecture_media = True
    request.library_page = True
    url = watch_path(course, lesson)
    key = (request.user["id"], slug, lesson_slug)
    saved = db.execute(
        "SELECT * FROM lecture_library_progress WHERE account_id=? AND course=? AND lesson=?", key,
    ).fetchone()
    start = seconds_value(request.query("t"), lesson["duration"],
                          default=(saved["seconds"] if saved and not saved["completed"] else 0))
    tracks = "".join(
        f'<track kind="subtitles" src="{url}/captions/{lang}" srclang="{lang}" label="{e(track["label"])}"'
        + (' default' if number == 0 else '') + '>' for number, (lang, track) in enumerate(lesson["captions"].items())
    )
    player = (f'<section class="library-watch" id="video" aria-label="Видео лекции" data-library-watch '
              f'data-start="{start}" data-course-watch="{url}" data-csrf="{e(request.csrf)}">'
              f'<video controls playsinline preload="metadata" src="{url}/video">{tracks}</video>'
              '<p role="status" class="library-player-feedback meta" data-video-feedback></p></section>')
    chapters = "".join(
        f'<li><button type="button" class="quiet" data-library-seek="{seconds}">'
        f'<time>{duration(seconds)}</time><span>{e(label)}</span></button></li>' for seconds, label in lesson["chapters"]
    )
    cues = next(iter(lesson["captions"].values()))["cues"]
    transcript = "".join(
        f'<li data-cue="{cue["start"]}" data-cue-end="{cue["end"]}"><button class="quiet" type="button" '
        f'data-library-seek="{cue["start"]}"><time>{duration(int(cue["start"]))}</time><span>{e(cue["text"])}</span></button></li>'
        for cue in cues
    )
    downloads = "".join(f'<li><a href="{url}/captions/{lang}" download="{slug}-{lang}.vtt">{e(track["label"])} · VTT</a></li>'
                        for lang, track in lesson["captions"].items())
    sources = (f'{link(course["source"], "Оригинал")} · {link(course["license_url"], course["license"])}'
               + (f' · {link(course["archive"], "Архив источника")}' if course.get("archive") else ""))
    materials = lesson_materials(request, db, course, lesson, url)
    answers = dict(db.execute("SELECT question,choice FROM lecture_library_quiz WHERE account_id=? AND course=? AND lesson=?", key))
    practice = quiz_panel(request, lesson["quiz"], answers, url)
    course_links = list(course["lessons"].values())
    index = next(i for i, item in enumerate(course_links) if item["slug"] == lesson_slug)
    adjacent = " ".join(link(watch_path(course, course_links[i]), label, "dean-action-link")
                        for i, label in ((index-1, "Предыдущая лекция"), (index+1, "Следующая лекция"))
                        if 0 <= i < len(course_links))
    body = (
        f'<div class="library-page"><p class="meta">{e(course["author"])} · '
        f'{duration(lesson["duration"])} · {sources}</p>'
        '<nav class="library-switch" aria-label="Разделы лекции"><a href="#video">Видео</a>'
        '<a href="#transcript">Расшифровка</a><a href="#notes">Материалы</a><a href="#practice">Практика</a></nav>'
        + '<div class="library-player-grid">' + player
        + f'<details class="panel library-chapter-panel" id="chapters" data-library-chapters>'
          f'<summary>Главы лекции · {len(lesson["chapters"])}</summary><ol class="library-chapters">{chapters}</ol></details>'
        + '</div>'
        + f'<details id="transcript" class="panel library-transcript" data-library-transcript>'
          f'<summary>Расшифровка по таймкодам</summary><ul class="library-downloads" aria-label="Скачать субтитры">{downloads}</ul>'
          f'<ol class="library-cues">{transcript}</ol></details>'
        + materials + link("#practice", "Перейти к практике", "rw-primary-link library-practice-start")
        + practice + (f'<nav class="library-actions" aria-label="Лекции курса">{adjacent}</nav>' if adjacent else "")
        + '</div>'
    )
    return page(request, lesson["title"], body, section="lecture-library", breadcrumb=back(PATH+"/"+slug, "К курсу"))


def lesson_materials(request, db, course, lesson, url):
    notes = "".join(f'<li>{e(note)}</li>' for note in lesson["notes"])
    generated = stored_material(db, request.user, course, lesson)
    personal = ""
    if generated:
        material = json.loads(generated["payload"])
        personal = ('<details class="library-personal" open><summary>Мой конспект DeepSeek</summary><ul>'
                    + "".join(f'<li>{e(note)}</li>' for note in material["notes"])
                    + '</ul><h3>Дополнительные задачи</h3><ol>'
                    + "".join(f'<li>{e(task)}</li>' for task in material["practice"]) + '</ol></details>')
    else:
        feedback_code = request.query("materials")
        feedback = MATERIALS_FEEDBACK.get(feedback_code)
        available = request.ai.enabled and getattr(request.ai, "can_chat", True)
        controls = f'<p role="alert">{e(feedback)}</p>' if feedback else ""
        if not available and feedback_code != "unavailable":
            controls += f'<p class="meta">{e(MATERIALS_FEEDBACK["unavailable"])}</p>'
        if available and feedback_code == "pending":
            controls += link(url + "#notes", "Проверить результат", "dean-action-link")
        elif available:
            caption = "Повторить подготовку" if feedback else "Подготовить конспект и задачи"
            controls += form(
                request, url + "/materials",
                '<p class="meta">DeepSeek подготовит личный конспект и дополнительные задачи по субтитрам лекции.</p>'
                + button(caption) + '<p role="status" data-material-wait hidden>Готовим конспект и задачи…</p>',
                command=True, style="library-model-form",
            )
        ready_docs = (f'<p>{link(url + "/notes.pdf", "Открыть готовый конспект")} · '
                      f'{link(url + "/homework.pdf", "Открыть готовое домашнее задание")}</p>') if feedback or not available else ""
        opened = " open" if feedback else ""
        personal = f'<details class="library-personal"{opened}><summary>Мой конспект</summary>{controls}{ready_docs}</details>'
    downloads = "".join(
        f'<div class="library-document" role="group" aria-labelledby="library-{slug}-label">'
        f'<div><span class="library-icon">{appearance.icon(icon)}</span>'
        f'<strong id="library-{slug}-label">{label}</strong></div>'
        f'<div class="library-pdf-actions">'
        f'<a href="{e(url)}/{slug}.pdf" aria-label="Открыть PDF · {label}">Открыть PDF</a>'
        f'<a href="{e(url)}/{slug}.pdf?download=1" aria-label="Скачать PDF · {label}"'
        f' download="{e(lesson["slug"])}-{slug}.pdf">Скачать PDF</a></div></div>'
        for slug, icon, label in (("notes", "file", "Конспект лекции"), ("homework", "tasks", "Домашнее задание"))
    )
    tasks = "".join(f'<li>{e(task)}</li>' for task in lesson.get("homework", []))
    homework = f'<details class="library-personal"><summary>Читать домашнее задание</summary><ol>{tasks}</ol></details>' if tasks else ""
    return panel("Материалы", f'<div class="library-materials">{downloads}</div>'
                 f'<details class="library-personal"><summary>Читать конспект</summary><ul>{notes}</ul></details>'
                 + homework + personal, key="notes", style="library-material-panel")


def quiz_panel(request, quiz, answers, url, *, title="Практика"):
    answered = len(answers.keys() & range(len(quiz)))
    correct = sum(answers.get(index) == question["correct"] for index, question in enumerate(quiz))
    question_links = []
    questions = []
    for index, question in enumerate(quiz):
        choice = answers.get(index)
        state = "unanswered" if choice is None else "correct" if choice == question["correct"] else "incorrect"
        state_label = {"unanswered": "нет ответа", "correct": "верно", "incorrect": "есть ошибка"}[state]
        state_mark = {"unanswered": "", "correct": "✓", "incorrect": "×"}[state]
        mark = f'<span aria-hidden="true">{state_mark}</span>' if state_mark else ""
        question_links.append(f'<a href="#question-{index}" data-answer-state="{state}" '
                              f'aria-label="Вопрос {index + 1}, {state_label}">{index + 1}{mark}</a>')
        choices = []
        for number, label in enumerate(question["options"]):
            checked = " checked" if choice == number else ""
            choices.append(f'<label class="library-option"><input type="radio" name="answer" value="{number}"'
                           f' required{checked}><span>{e(label)}</span></label>')
        feedback = ""
        if choice is not None:
            label = "Верно." if state == "correct" else "Пока неверно."
            feedback = (f'<p class="library-quiz-feedback" role="status" tabindex="-1" '
                        f'data-quiz-feedback data-answer-state="{state}"><strong>{label}</strong> '
                        f'{e(question["explanation"])}</p>')
        fields = (hidden("question", str(index))
                  + f'<fieldset><legend>{e(question["question"])}</legend><div class="library-options">'
                  + "".join(choices) + '</div></fieldset>'
                  + button("Проверить ответ" if choice is None else "Проверить снова"))
        next_question = (link(f"#question-{index + 1}", "Следующий вопрос", "rw-primary-link library-question-next")
                         if choice is not None and index + 1 < len(quiz) else "")
        questions.append(f'<article class="library-question" id="question-{index}" data-quiz-question'
                         f' data-answer-state="{state}" tabindex="-1">'
                         f'<p class="library-question-counter meta">Вопрос {index + 1} из {len(quiz)}</p>'
                         + form(request, url + "/quiz", fields) + feedback + next_question + '</article>')
    progress = (f'<div class="library-quiz-progress"><div><p>Ответы <strong>{answered} из {len(quiz)}</strong></p>'
                f'<progress value="{answered}" max="{len(quiz)}" aria-label="Прогресс практики"></progress></div>'
                f'<p>Правильные ответы <strong>{correct}</strong></p></div>')
    question_nav = '<nav class="library-quiz-nav" aria-label="Вопросы теста">' + "".join(question_links) + '</nav>'
    return panel(title, progress + question_nav + "".join(questions), key="practice", style="library-practice")
