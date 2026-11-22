"""Parser for the public desktop group grid. No page source is persisted."""
from dataclasses import dataclass, field
from datetime import timedelta
from html.parser import HTMLParser
import re

from .public_schedule import normalize

MONTHS = ("янв.", "февр.", "марта", "апр.", "мая", "июня", "июля", "авг.",
          "сент.", "окт.", "нояб.", "дек.")
DAYS = ("пн", "вт", "ср", "чт", "пт", "сб")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    parts: list = field(default_factory=list)

    def children(self):
        return (part for part in self.parts if isinstance(part, Node))

    def descendants(self):
        for child in self.children():
            yield child
            yield from child.descendants()

    def text(self):
        return " ".join("".join(part.text() if isinstance(part, Node) else part
                                for part in self.parts).split())

    def has(self, name):
        return name in self.attrs.get("class", "").split()


class GridHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("")
        self.stack = [self.root]
        self.nodes = 0

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if len(self.stack) > 80 or self.nodes > 40000:
            raise ValueError("html_limit")
        child = Node(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].parts.append(child)
        if tag not in VOID:
            self.stack.append(child)

    def handle_endtag(self, tag):
        for position in range(len(self.stack) - 1, 0, -1):
            if self.stack[position].tag == tag:
                del self.stack[position:]
                break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        self.stack[-1].parts.append(data)


def cells(row, tag):
    return [child for child in row.children() if child.tag == tag]


def only(nodes):
    nodes = list(nodes)
    if len(nodes) != 1:
        raise ValueError("unexpected_structure")
    return nodes[0]


def parse(week, content):
    if re.search(rb"captcha|cf-chl", content[:12000], re.I) and b"table-lessons" not in content:
        raise ValueError("challenge")
    page = GridHTML()
    page.feed(content.decode("utf-8-sig", errors="strict"))
    page.close()
    title = only(node for node in page.root.descendants() if node.tag == "h1")
    if title.text() != "Расписание занятий группы " + week.group["label"]:
        raise ValueError("wrong_group")
    date_marker = week.group["path"] + "?week_id=" + week.info["id"]
    source_week = [node for node in page.root.descendants()
                   if node.tag == "a" and node.attrs.get("href") == date_marker]
    if not source_week or not any(str(week.monday.year) in node.text() for node in source_week):
        raise ValueError("wrong_period")
    table = only(node for node in page.root.descendants() if node.tag == "table" and node.has("table-lessons"))
    rows = [row for row in table.descendants() if row.tag == "tr"]
    if len(rows) < 2:
        raise ValueError("unexpected_structure")
    headings = []
    for index, label in enumerate(DAYS):
        day = week.monday + timedelta(days=index)
        headings.append(f"{label}, {day.day} {MONTHS[day.month - 1]}")
    if [node.text() for node in cells(rows[0], "th")] != ["", *headings]:
        raise ValueError("wrong_week")
    records = []
    for row in rows[1:]:
        clock, = cells(row, "th")
        starts, ends = [node.text() for node in clock.children() if node.tag == "span"]
        day_cells = cells(row, "td")
        if len(day_cells) != len(DAYS):
            raise ValueError("partial_grid")
        for day, cell in enumerate(day_cells, 1):
            if not cell.has(f"day_{day}") or cell.attrs.get("colspan", "1") != "1":
                raise ValueError("partial_grid")
            group = only(node for node in cell.children() if node.has("lessons-wrapper"))
            for lesson in group.children():
                if not lesson.has("lesson-cell"):
                    raise ValueError("unexpected_lesson")
                training = only(node for node in lesson.children() if node.has("training"))
                subject = only(node for node in training.children() if node.has("js-lesson-info"))
                kind = only(node for node in training.children() if node.has("kind"))
                places = [node for node in training.children() if node.has("auditoriums")]
                rooms = [
                    (room.text(), room.attrs.get("href", ""))
                    for place in places for room in place.children() if room.tag == "a"
                ]
                records.append([subject.attrs.get("data-lesson-id", ""), day, starts, ends,
                                subject.text(), kind.text(), rooms])
    return normalize(week, records)
