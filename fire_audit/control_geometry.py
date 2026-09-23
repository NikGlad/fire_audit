from __future__ import annotations

import hashlib
import json
import math


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def finite(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Координата/величина должна быть конечным числом")
    return float(value)


def point(value) -> list[float]:
    if isinstance(value, dict):
        return [finite(value["x"]), finite(value["y"]), finite(value.get("z", 0))]
    if isinstance(value, (list, tuple)) and len(value) in {2, 3}:
        return [finite(value[0]), finite(value[1]), finite(value[2]) if len(value) == 3 else 0.0]
    raise ValueError("Ожидалась фактическая точка геометрии")


def bounds(value) -> list[float]:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        result = [finite(v) for v in value]
    elif isinstance(value, dict):
        value = value.get("boundingBox", value)
        if all(k in value for k in ("minX", "minY", "maxX", "maxY")):
            result = [finite(value[k]) for k in ("minX", "minY", "maxX", "maxY")]
        else:
            a, b = point(value["min"]), point(value["max"])
            result = [a[0], a[1], b[0], b[1]]
    else:
        raise ValueError("Габарит не определён коннектором")
    if result[0] > result[2] or result[1] > result[3]:
        raise ValueError("Перепутаны границы габарита")
    return result


def contains(box, p, tolerance=1.0) -> bool:
    x, y, *_ = point(p)
    a, b, c, d = bounds(box)
    return a-tolerance <= x <= c+tolerance and b-tolerance <= y <= d+tolerance


def overlaps(a, b) -> bool:
    a, b = bounds(a), bounds(b)
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def union(boxes) -> list[float]:
    boxes = [bounds(b) for b in boxes]
    if not boxes:
        raise ValueError("Не задан полный состав группы замечания")
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def ray_origin(box, targets) -> list[float]:
    a, b, c, d = bounds(box)
    if a == c or b == d or not targets:
        raise ValueError("Нужны габарит группы и подтверждённые цели")
    x, y = (a+c)/2, (b+d)/2
    targets = [point(p) for p in targets]
    dx = sum(p[0] for p in targets)/len(targets)-x
    dy = sum(p[1] for p in targets)/len(targets)-y
    if dx == dy == 0:
        raise ValueError("Цель совпала с центром замечания")
    scale = min((c-a)/2/abs(dx) if dx else math.inf, (d-b)/2/abs(dy) if dy else math.inf)
    return [x + dx*scale, y + dy*scale, 0.0]


def geometry_anchor(entity: dict, path: str) -> list[float]:
    # Bounds, block centres and inserted metadata must not masquerade as opening boundaries.
    parts = path.split(".")
    if any(p.lower() in {"bounds", "boundingbox", "center", "position", "insertionpoint"} for p in parts):
        raise ValueError("Габарит/центр/точка вставки не являются измеряемой границей")
    value = entity
    for key in parts:
        value = value[int(key)] if isinstance(value, list) and key.isdigit() else value[key]
    return point(value)


def compare_measurement(value, limit, violation_when: str) -> bool:
    value, limit = finite(value), finite(limit)
    if min(value, limit) < 0:
        raise ValueError("Размер не может быть отрицательным")
    if violation_when == "below":
        return value < limit
    if violation_when == "above":
        return value > limit
    raise ValueError("Сравнение должно быть below или above; предел берётся из индивидуального анализа нормы")


def properties_ok(entity: dict, role: str) -> bool:
    expected = "экспертиза пояснения" if role == "purple_explanation" else "экспертиза"
    weight = str(entity.get("lineWeight", entity.get("lineweight", ""))).lower()
    return (entity.get("layer") == expected and entity.get("colorIndex") == 256
            and str(entity.get("linetype", "")).lower() == "bylayer"
            and weight in {"bylayer", "-1"})
