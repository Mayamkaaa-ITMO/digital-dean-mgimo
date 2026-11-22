"""A route to a confirmed physical teaching room."""
from urllib.parse import urlencode

from .activity_extensions import mapped_room
from .appearance import icon
from .campus_map import PATH
from .ui import e, link


UNMAPPED_ROOM = "Этой аудитории нет на карте кампуса."


def banner(room, return_path, *, explain_unmapped=False):
    target = mapped_room(room)
    if not target and not (room and explain_unmapped):
        return ""
    if target:
        query = urlencode({"to": target, "back": return_path})
        message = room
        action = link(f"{PATH}?{query}#campus-plan", "Построить маршрут", "rw-primary-link")
    else:
        message = f"{UNMAPPED_ROOM} Уточни, как пройти, у преподавателя или в деканате."
        action = link("/support", "Написать в поддержку", "dean-secondary-action")
    return (f'<section class="panel lesson-route-card" aria-label="Как пройти на занятие">'
            f'<span class="lesson-route-icon">{icon("map")}</span><div><h2>Как пройти на занятие</h2>'
            f'<p class="meta">{e(message)}</p></div>{action}</section>')
