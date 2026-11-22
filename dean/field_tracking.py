"""Anonymous short-lived tracks for confirmed person boxes, in normalized camera coordinates."""
from dataclasses import dataclass
from math import hypot


@dataclass
class Track:
    x: float
    y: float
    w: float
    h: float
    seen: float
    vx: float = 0
    vy: float = 0
    side: int = 0
    candidate: int = 0
    hits: int = 0
    crossing_at: float | None = None


class Counter:
    def __init__(self, axis, line, inside, gate_start=0, gate_end=100):
        self.axis, self.line, self.inside = axis, line / 100, inside
        self.gate = (gate_start/100, gate_end/100)
        self.tracks = []

    def observe_side(self, track):
        coordinate = track.x if self.axis == 'x' else track.y
        distance = coordinate - self.line
        side = 1 if distance > .035 else -1 if distance < -.035 else 0
        if side == 0:
            track.candidate = track.hits = 0
            return 0
        track.hits = track.hits + 1 if side == track.candidate else 1
        track.candidate = side
        if track.hits < 2 or track.side == side:
            return 0
        previous, track.side = track.side, side
        perpendicular = track.crossing_at
        track.crossing_at = None
        if previous and perpendicular is not None and self.gate[0] <= perpendicular <= self.gate[1]:
            return side * self.inside
        return 0

    def update(self, boxes, now):
        self.tracks = [t for t in self.tracks if 0 <= now - t.seen < .85]
        # Track the torso rather than the lower edge, which vanishes first behind a doorframe.
        points = [((x+x2)/2, y + .55*(y2-y), x2-x, y2-y) for x,y,x2,y2 in boxes]
        pairs = []
        for i, t in enumerate(self.tracks):
            dt = min(.36, now-t.seen)
            for j,(x,y,w,h) in enumerate(points):
                if min(t.h,h) < .48*max(t.h,h):
                    continue
                distance = hypot(x-t.x-t.vx*dt, y-t.y-t.vy*dt)
                gate = .04 + .35*max(w,t.w) + .66*dt
                if distance <= min(.32,gate):
                    pairs.append((distance+.10*abs(h-t.h)+.09*abs(w-t.w), i, j))
        used, matched, events = set(), set(), []
        for _, i, j in sorted(pairs):
            if i in used or j in matched:
                continue
            used.add(i)
            matched.add(j)
            t = self.tracks[i]
            x,y,w,h = points[j]
            a,b,u,v = (t.x,x,t.y,y) if self.axis=="x" else (t.y,y,t.x,x)
            if a!=b and min(a,b)<=self.line<=max(a,b):
                t.crossing_at = u + (v-u)*(self.line-a)/(b-a)
            dt = max(.03,now-t.seen)
            t.vx = .45*t.vx + .55*(x-t.x)/dt
            t.vy = .45*t.vy + .55*(y-t.y)/dt
            t.x,t.y,t.w,t.h,t.seen = x,y,w,h,now
            event = self.observe_side(t)
            if event:
                events.append(event)
        for j,p in enumerate(points):
            if j not in matched:
                t = Track(*p, now)
                self.observe_side(t)
                self.tracks.append(t)
        return events
