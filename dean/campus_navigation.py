"""Indoor itineraries. A room pin and its corridor approach are distinct places."""
from collections import defaultdict
from functools import lru_cache
from heapq import heappop, heappush
from itertools import pairwise
from math import hypot, inf

from .campus_map import Place

# Centre lines read in floor-plan coordinates. Named ends join the original graph.
# Only these lines and existing corridors may receive a room approach.
AISLES = {
    "a1": [("stairs", "north", "east"), ("east", "foyer-turn", "hall")],
    "a2": [("stairs", (495,116), (495,75), "hall", "west", (166,78), (166,110),
            (67,110), (67,245)),
           ("stairs", (449,116), (455,193), (439,254), (407,312), (365,361),
            (307,401), (234,431), (168,436), (118,430), (118,393))],
    "v2": [("b","west","west-turn","near-2001","hall","east","near-2108",(918,335)),
           ("west",(86,318),(51,45)), ((918,335),(905,310),(927,156),(940,48))],
    "v3": [("west","west-turn","hall","east","east-turn","wing","g"),
           ("west",(87,317),(50,44)), ("wing",(904,313),(924,166),(927,150))],
    "v4": [("b","hall-3-turn","west","middle","west-inner"),
           ("east-inner","east","right","hall-5-turn"), ("hall-5-turn",(913,272))],
    "b1": [("a","west","middle","east","end"), ("west",(239,967),(239,452),
            (201,452),(201,148),(890,148),(890,442))],
    "b2": [("end","west","middle","east","near-2009","v"), ("end",(239,966),(239,452),
            (201,452),(201,148),(890,148),(890,452))],
    "b4": [("west","middle","east","last","v"),
           ("west","4022","bend","upper-left","upper-turn","upper-right","hall-4-v")],
    "g3": [("v","west","middle","center","right","new-bend","n"),
           ("right","canteen-turn","canteen-landing","canteen-stairs"),
           ("canteen-landing",(497,128),(475,132),(30,132)),
           ("canteen-stairs",(820,128))],
    "n4": [("g","441","440","left","north","neck","north-east","lobby"),
           ("left","south","middle","south-east","east-bend","lobby")],
}


class Navigation:
    def __init__(self, data):
        self.places = dict(data.places)
        self.edges = {k: dict(v) for k,v in data.neighbors.items()}
        self.approaches = {}
        aisles = defaultdict(set)

        def waypoint(floor, x, y):
            key = f"{floor}@{x:g},{y:g}"
            if key not in self.places:
                self.places[key] = Place(key, floor, "junction", "Отметка на маршруте", x, y)
                self.edges[key] = {}
            return key

        def connect(a, b):
            if a == b:
                return
            u, v = self.places[a], self.places[b]
            weight = hypot(u.x-v.x, u.y-v.y)
            self.edges[a][b] = self.edges[b][a] = weight

        # The northern partition of A2 has an opening east of the landing.
        del self.edges["a2-hall"]["a2-stairs"], self.edges["a2-stairs"]["a2-hall"]
        for floor, chains in AISLES.items():
            for chain in chains:
                keys = [f"{floor}-{p}" if isinstance(p,str) else waypoint(floor,*p) for p in chain]
                for a,b in pairwise(keys):
                    connect(a,b)
                    aisles[floor].add(tuple(sorted((a,b))))

        # In Б the western stairwell is at the lower left of the plans. The
        # same landing is marked on levels 2, 3 and 4. No turn is made on 3.
        landing = waypoint("b4",198,1002)
        self.places[landing] = Place(landing,"b4","stairs","Лестница западного крыла",198,1002)
        west = waypoint("b4",198,967)
        connect("b4-west", west)
        connect(west, landing)
        self.edges["b2-stairs"][landing] = self.edges[landing]["b2-stairs"] = 90

        # The east stair symbol is aligned on В2, В3 and В4. On В4 its
        # landing is isolated from the main corridor by the lecture halls.
        east_stairs = {}
        for floor in ("v2", "v3", "v4"):
            key = waypoint(floor, 886, 139)
            self.places[key] = Place(key, floor, "stairs", "Лестница восточного крыла", 886, 139)
            east_stairs[floor] = key
        connect(waypoint("v2",927,156), waypoint("v2",927,142))
        connect(waypoint("v2",927,142), east_stairs["v2"])
        connect(waypoint("v3",927,150), waypoint("v3",927,142))
        connect(waypoint("v3",927,142), east_stairs["v3"])
        for lower, upper in pairwise(east_stairs.values()):
            self.edges[lower][upper] = self.edges[upper][lower] = 45

        splits = defaultdict(dict)
        for room in data.directory():
            if room.kind != "location":
                continue
            if room.key == "v4-place-25":
                self.approaches[room.key] = east_stairs["v4"]
                continue
            candidates = []
            for a,b in aisles[room.floor]:
                u,v = self.places[a], self.places[b]
                dx,dy = v.x-u.x,v.y-u.y
                length2 = dx*dx+dy*dy
                if not length2:
                    continue
                t = max(0,min(1, ((room.x-u.x)*dx + (room.y-u.y)*dy)/length2))
                x,y = round(u.x+t*dx,3),round(u.y+t*dy,3)
                candidates.append((hypot(room.x-x,room.y-y),a,b,t,x,y))
            _,a,b,t,x,y = min(candidates)
            if t == 0 or t == 1:
                key = a if t == 0 else b
            else:
                key = waypoint(room.floor,x,y)
                splits[a,b][t] = key
            self.approaches[room.key] = key
        for (a,b), chain in splits.items():
            del self.edges[a][b], self.edges[b][a]
            for u,v in pairwise([a, *(chain[t] for t in sorted(chain)), b]):
                connect(u,v)

    @lru_cache(maxsize=640)
    def tree(self, start, scope):
        previous, distances, queue = {start:None}, {start:0}, [(0,start)]
        while queue:
            distance, key = heappop(queue)
            if distance != distances[key]:
                continue
            for neighbor, weight in self.edges[key].items():
                if not self.places[neighbor].floor.startswith(scope):
                    continue
                cost = distance+weight
                if cost < distances.get(neighbor,inf):
                    previous[neighbor], distances[neighbor] = key,cost
                    heappush(queue,(cost,neighbor))
        return previous

    def route(self, start, finish):
        if start not in self.places or finish not in self.places:
            return []
        origin, target = self.places[start], self.places[finish]
        if origin == target:
            return [origin]
        a,b = self.approaches.get(start,start), self.approaches.get(finish,finish)
        if a not in self.edges or b not in self.edges:
            return []
        scopes = []
        if origin.floor == target.floor:
            scopes.append(origin.floor)
        if origin.floor[0] == target.floor[0]:
            scopes.append(origin.floor[0])
        for scope in [*scopes,""]:
            tree = self.tree(a,scope)
            if b not in tree:
                continue
            result, key = [], b
            while key is not None:
                result.append(self.places[key])
                key = tree[key]
            result.reverse()
            return ([origin] if a != start else []) + result + ([target] if b != finish else [])
        return []
