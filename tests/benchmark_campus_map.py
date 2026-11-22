"""Run unchanged in both trees with PYTHONPATH=. python3 path/to/benchmark_campus_map.py."""
import json
from itertools import permutations
from random import Random
from statistics import median, quantiles
from time import perf_counter_ns

from dean import campus_map


def measure(cases, operation):
    times = []
    for case in cases:
        start = perf_counter_ns()
        operation(case)
        times.append((perf_counter_ns() - start) / 1000)
    return {
        "n": len(times), "median_us": round(median(times), 2),
        "p95_us": round(quantiles(times, n=100)[94], 2), "total_ms": round(sum(times) / 1000, 2)
    }


if __name__ == "__main__":
    data = campus_map.campus()
    queries = [point.label for point in data.directory()] + ["2018-а", "трешка", "ауд. 2018a", "главнй вход", "00000"]
    pairs = list(permutations((point.key for point in data.points()), 2))
    Random(423).shuffle(pairs)
    router = data.navigation.route if hasattr(data, "navigation") else lambda a, b: campus_map.route(data, a, b)
    print(json.dumps({
        "search": measure(queries * 3, lambda query: campus_map.find(data, query)),
        "routes": measure(pairs, lambda pair: router(*pair)),
    }, indent=2))
