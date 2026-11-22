"""Reconcile the room and wall traces for display, in source-plan coordinates."""
from collections import Counter
from functools import cache
from math import hypot
import re
import json
from pathlib import Path

PIXEL_RING = re.compile(r"M(\d+) (\d+)((?:[HV]\d+)+)Z")
PIXEL_STEP = re.compile(r"([HV])(\d+)")


def vertices(match):
    x, y = int(match[1]), int(match[2])
    points = [(x, y)]
    for axis, value in PIXEL_STEP.findall(match[3]):
        x, y = (int(value), y) if axis == "H" else (x, int(value))
        if (x, y) != points[-1]:
            points.append((x, y))
    if points[-1] == points[0]:
        points.pop()
    return points


def bounds(points):
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def signed_area(points):
    return sum(x*b - y*a for (x, y), (a, b) in zip(points, points[1:] + points[:1])) / 2


def rectangular_path(points):
    """Snap only where the trace supplies long straight runs on all four sides."""
    left, top, right, bottom = bounds(points)
    w, h = right-left, bottom-top
    if min(w, h) <= 6 or abs(signed_area(points)) < .92 * w * h:
        return None
    if any(min(x-left, right-x, y-top, bottom-y) > 2 for x, y in points):
        return None
    sides = [Counter() for _ in range(4)]
    for (x, y), (a, b) in zip(points, points[1:] + points[:1]):
        if x == a:
            if x-left <= 2:
                sides[0][x] += abs(y-b)
            elif right-x <= 2:
                sides[2][x] += abs(y-b)
        elif y == b:
            if y-top <= 2:
                sides[1][y] += abs(x-a)
            elif bottom-y <= 2:
                sides[3][y] += abs(x-a)
    if any(sum(side.values()) < .85 * length for side, length in zip(sides, (h,w,h,w))):
        return None
    x, y, a, b = [side.most_common(1)[0][0] for side in sides]
    return f"M{x} {y}H{a}V{b}H{x}Z"


def simplify_edge(points, tolerance=1.35):
    last = len(points) - 1
    retained = {0, last}
    intervals = [(0, last)]
    while intervals:
        left, right = intervals.pop()
        if right - left < 2:
            continue
        x, y = points[left]
        dx, dy = points[right][0] - x, points[right][1] - y
        length = hypot(dx, dy)
        if not length:
            continue
        distance, pivot = max((abs(dx * (py - y) - dy * (px - x)) / length, index)
                              for index, (px, py) in enumerate(points[left+1:right], left+1))
        if distance > tolerance:
            retained.add(pivot)
            intervals.extend(((left, pivot), (pivot, right)))
    return [points[index] for index in sorted(retained)]


def outline(ring, corners):
    """Round shallow joins on traced slopes. Keep architectural corners sharp."""
    def toward(point, other, radius):
        dx, dy = other[0] - point[0], other[1] - point[1]
        ratio = radius / hypot(dx, dy)
        return f"{point[0] + dx * ratio:.2f} {point[1] + dy * ratio:.2f}"

    segments = []
    for index, point in enumerate(ring):
        previous, following = ring[index - 1], ring[(index + 1) % len(ring)]
        a, b = (point[0] - previous[0], point[1] - previous[1]), (following[0] - point[0], following[1] - point[1])
        before, after = hypot(*a), hypot(*b)
        coord = f"{point[0]} {point[1]}"
        slope = a[0] * a[1] or b[0] * b[1]
        if point not in corners and slope and a[0] * b[0] + a[1] * b[1] > .45 * before * after:
            radius = min(6, before * .45, after * .45)
            segments.append(f"{toward(point, previous, radius)}Q{coord} {toward(point, following, radius)}")
        else:
            segments.append(coord)
    return "M" + "L".join(segments) + "Z"


@cache
def drawing_path(path):
    def smooth(match):
        points = vertices(match)
        if len(points) <= 4:
            return match[0]
        rectangle = rectangular_path(points)
        if rectangle:
            return rectangle
        if len(points) <= 7 or min(max(axis)-min(axis) for axis in zip(*points)) <= 6:
            return match[0]
        # Preserve corners formed by two actual wall runs, including notches
        # in outer walls. Only simplify the pixel stairs *between* corners.
        anchors = [i for i, (x, y) in enumerate(points)
                   if min(abs(x-a)+abs(y-b) for a, b in (points[i-1], points[(i+1) % len(points)])) >= 6]
        corners = {points[i] for i in anchors}
        if len(anchors) < 2:
            start = anchors[0] if anchors else 0
            x, y = points[start]
            opposite = max(range(len(points)), key=lambda i: hypot(points[i][0]-x, points[i][1]-y))
            anchors = sorted((start, opposite))
        ring = []
        for i, left in enumerate(anchors):
            right = anchors[(i+1) % len(anchors)]
            edge = points[left:right+1] if left < right else points[left:] + points[:right+1]
            ring.extend(simplify_edge(edge)[:-1])
        return outline(ring, corners) if len(ring) >= 3 else match[0]

    return PIXEL_RING.sub(smooth, path)


@cache
def floor_paths(walls, areas, occupied):
    """Return a stable source-indexed table of drawing paths. None denotes a trace artifact."""
    holes = []
    matches = {}
    traced = PIXEL_RING.sub("", walls) == ""
    if not traced:
        return walls, tuple(areas)
    for ring in PIXEL_RING.finditer(walls):
        points = vertices(ring)
        area = signed_area(points)
        if area < 0:
            box = bounds(points)
            holes.append((ring[0], box, -area / ((box[2]-box[0]) * (box[3]-box[1]))))
    shapes = []
    for i, path in enumerate(areas):
        ring = PIXEL_RING.fullmatch(path)
        if not ring:
            shapes.append(path)
            continue
        points = vertices(ring)
        box = bounds(points)
        area = abs(signed_area(points))
        w, h = box[2]-box[0], box[3]-box[1]
        coverage = area / (w*h) if w and h else 0
        candidates = [hole for hole, hole_box, density in holes
                      if max(abs(a-b) for a,b in zip(box, hole_box)) <= 3
                      and abs(coverage - density) <= .12]
        if len(candidates) == 1:
            matches[i] = candidates[0]
        # Unassigned traces of small printed symbols can look like rooms.
        # Keep ambiguous spaces, named areas and any wall-supported footprint.
        symbol = (i not in occupied and not candidates and w and h
                  and ((max(w,h) <= 25 and max(w,h) <= 1.7*min(w,h) and area < .90*w*h)
                       or (max(w,h) <= 45 and area < .17*w*h)))
        shapes.append(None if symbol else drawing_path(path))
    unique = Counter(matches.values())
    for i, hole in matches.items():
        if unique[hole] == 1:
            shapes[i] = drawing_path(hole)
    return drawing_path(walls), tuple(shapes)

@cache
def display_plans():
    """Measured display paths use the stable area indices from the source plans."""
    source = Path(__file__).with_name("data") / "mgimo-display-plans.json"
    return json.loads(source.read_text(encoding="utf-8"))
