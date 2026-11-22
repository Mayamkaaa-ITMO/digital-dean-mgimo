"""Public campus facts and routing across mapped corridors and landings."""
from collections import defaultdict
from dataclasses import dataclass
from functools import cache, cached_property, lru_cache
from heapq import heappop, heappush
from itertools import groupby, pairwise
from math import hypot, inf
from pathlib import Path
import json
import re
import unicodedata

PATH = "/campus-map"
OFFICIAL = "https://mgimo.ru/territory/"
ENTRY = "v2-entry"
# The published G3 plan and the MGIMO Student Union guide locate the canteen stair by 3154.
CANTEEN_APPROACH = "g3-canteen-stairs"
DESTINATIONS = {"entrance", "room", "hall", "service"}
TYPES = DESTINATIONS | {"junction", "passage", "stairs"}

# Names from the published plans for A2 (room=a229) and V3 (room=3012).
OFFICE_NAMES = {
    ("a2", "14"): "Редакция университетского журнала",
    ("a2", "208к (229)"): "Профком",
    ("v3", "3012"): "Учебно-договорной отдел",
    ("v3", "3013"): "Учебно-договорной отдел",
}


@dataclass(frozen=True)
class Place:
    key: str
    floor: str
    kind: str
    label: str
    x: int
    y: int

    @property
    def route_endpoint(self):
        return self.kind in DESTINATIONS or self.key == CANTEEN_APPROACH


    @property
    def selectable(self):
        return self.route_endpoint or self.kind == "location"


@dataclass(frozen=True)
class Floor:
    building: str
    level: int
    places: tuple[str, ...]
    corridors: tuple[tuple[str, str], ...]
    width: int
    height: int
    walls: str
    areas: tuple
    labels: tuple

    @property
    def official(self):
        return f"https://mgimo.ru/territory/map/?building={self.building}&floor={self.level}"


@dataclass(frozen=True, eq=False)
class Campus:
    buildings: dict[str, str]
    floors: dict[str, Floor]
    places: dict[str, Place]
    neighbors: dict[str, list[tuple[str, float]]]

    def points(self):
        return (place for place in self.places.values() if place.route_endpoint)

    @cached_property
    def navigation(self):
        from .campus_navigation import Navigation
        return Navigation(self)

    @cached_property
    def choices(self):
        return {p.key: p for p in self.places.values() if p.selectable}

    def directory(self):
        return (place for place in self.places.values() if place.kind in DESTINATIONS or place.kind == "location")

    def location(self, floor):
        level = self.floors[floor]
        return f"{self.buildings[level.building]} · {level.level}-й этаж"

    @cached_property
    def search_index(self):
        rows = []
        for place in self.directory():
            name = normalize(place.label)
            location = normalize(self.location(place.floor))
            office = normalize(OFFICE_NAMES.get((place.floor, place.label), ""))
            rows.append((place, name, (name + " " + location + " " + office).split()))
        return rows

    @cached_property
    def room_numbers(self):
        numbers = defaultdict(list)
        for place, _, tokens in self.search_index:
            for token in set(tokens):
                if token[0].isdigit():
                    numbers[token].append(place)
        return numbers

    @lru_cache(maxsize=160)
    def shortest_tree(self, start):
        previous = {start: None}
        queue, distances = [(0, start)], {start: 0}
        while queue:
            distance, point = heappop(queue)
            if distance != distances[point]:
                continue
            for nearby, weight in self.neighbors.get(point, ()):
                cost = distance + weight
                if cost < distances.get(nearby, inf):
                    distances[nearby] = cost
                    previous[nearby] = point
                    heappush(queue, (cost, nearby))
        return previous


@cache
def campus():
    directory = Path(__file__).with_name("data")
    data = json.loads((directory / "mgimo-map.json").read_text(encoding="utf-8"))
    plans = json.loads((directory / "mgimo-floor-plans.json").read_text(encoding="utf-8"))
    places, floors = {}, {}
    neighbors = defaultdict(list)

    def connect(start, end, weight):
        neighbors[start].append((end, weight))
        neighbors[end].append((start, weight))

    for floor in data["floors"]:
        building, level = floor["building"], floor["level"]
        key = f"{building}{level}"
        if key in floors or building not in data["buildings"]:
            raise ValueError(f"Invalid floor {key}")
        layout = plans[key]
        width, height = layout["width"], layout["height"]
        floor_places = []
        names = {}
        for suffix, (kind, label, x, y) in floor["places"].items():
            point = f"{key}-{suffix}"
            if kind not in TYPES or not (0 <= x <= width and 0 <= y <= height):
                raise ValueError(f"Invalid campus point {point}")
            places[point] = Place(point, key, kind, label, x, y)
            floor_places.append(point)
            if kind in DESTINATIONS:
                names[normalize(label)] = point
        corridors = []
        for path in floor["paths"]:
            for start, end in pairwise(f"{key}-{part}" for part in path):
                a, b = places[start], places[end]
                connect(start, end, hypot(a.x - b.x, a.y - b.y))
                corridors.append((start, end))
        labels = []
        for index, (label, x, y, area) in enumerate(layout["labels"]):
            point = names.get(normalize(label))
            if not point:
                point = f"{key}-place-{index}"
                places[point] = Place(point, key, "location", label, x, y)
            labels.append((point, x, y, area))
        floors[key] = Floor(building, level, tuple(floor_places), tuple(corridors), width, height,
                            layout["walls"], tuple(layout["areas"]), tuple(labels))

    for start, end, kind in data["links"]:
        a, b = places[start], places[end]
        if a.floor == b.floor or kind not in {"stairs", "passage"} or a.kind != kind or b.kind != kind:
            raise ValueError(f"Invalid campus link {start}:{end}")
        if kind == "stairs" and floors[a.floor].building != floors[b.floor].building:
            raise ValueError(f"Stair links distinct buildings: {start}:{end}")
        connect(start, end, 45 if kind == "stairs" else 24)
    return Campus(data["buildings"], floors, places, dict(neighbors))


SUFFIXES = str.maketrans("abcvkg", "авсвкг")
FLOOR_NUMBER = re.compile(r"\b(?:этаж\s*(\d+)|(\d+)(?:\s*[-‐‑–]?\s*й)?\s*эт(?:аж)?\.?(?!\w))")
ROOM_SUFFIX = re.compile(r"(\d+)[\s\-‐‑‒–—]*([а-яa-z])(?!\w)")


def normalize(text, *, latin_b="в"):
    value = unicodedata.normalize("NFKC", text.replace("№", " ")).casefold().replace("ё", "е")
    value = re.sub(r"\b(?:аудитория|ауд|кабинет|помещение)\b\.?\s*", "", value)
    value = FLOOR_NUMBER.sub(lambda m: f" этаж{m[1] or m[2]} ", value)
    value = ROOM_SUFFIX.sub(lambda m: m[1] + (latin_b if m[2] == "b" else m[2].translate(SUFFIXES)), value)
    return " ".join(re.findall(r"[0-9a-zа-я]+", value))


def near_word(a, b):
    """One insertion, deletion, substitution or adjacent transposition in a long word."""
    if min(len(a), len(b)) < 4 or not a.isalpha() or not b.isalpha() or abs(len(a)-len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = next((i for i, pair in enumerate(zip(a, b)) if pair[0] != pair[1]), len(a))
    if len(a) != len(b):
        return a[i:] == b[i+1:]
    return a[i+1:] == b[i+1:] or (a[i:i+2] == b[i:i+2][::-1] and a[i+2:] == b[i+2:])


def find(data, query):
    target = normalize(query)
    matches = find_normalized(data, target)
    if "b" in query.casefold():
        alternate = normalize(query, latin_b="б")
        if alternate != target:
            matches = list(dict.fromkeys([*matches, *find_normalized(data, alternate)]))
    return matches


def find_normalized(data, target):
    if not target:
        return []
    words = target.split()
    if len(words) == 1 and target[0].isdigit() and (target in data.room_numbers or len(target) >= 3):
        return sorted(data.room_numbers.get(target, ()), key=lambda p: (normalize(p.label) != target, p.key))

    def match(word, tokens):
        if word[0].isdigit() or word.startswith("этаж"):
            return word in tokens
        if len(word) == 1:
            return word in tokens
        return any(token.startswith(word) for token in tokens)

    exact, nearby = [], []
    for place, name, tokens in data.search_index:
        missing = [w for w in words if not match(w, tokens)]
        rank = (name != target, not name.startswith(target), name, place.key)
        if not missing:
            exact.append(rank)
        elif len(missing) == 1 and len(missing[0]) >= 4 and missing[0].isalpha() and any(near_word(missing[0], t) for t in tokens):
            nearby.append(rank)
    if not exact and len(words) == 1 and target.isdecimal() and len(target) <= 2:
        exact = [(True, True, name, place.key) for place, name, _ in data.search_index
                 if name.startswith(target)]
    return [data.places[row[-1]] for row in sorted(exact or nearby)]


def route(data, start, end):
    """Reconstruct a path from the cached Dijkstra tree for this campus and origin."""
    if start not in data.neighbors or end not in data.neighbors:
        return []
    previous = data.shortest_tree(start)
    if end not in previous:
        return []
    path = []
    point = end
    while point is not None:
        path.append(data.places[point])
        point = previous[point]
    return path[::-1]


def stages(path):
    return [tuple(points) for _, points in groupby(path, key=lambda point: point.floor)]
