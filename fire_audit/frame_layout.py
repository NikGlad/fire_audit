"""Verify the one permitted layout change when copying a displaced number frame."""
from math import isclose, isfinite


def _vector(value, size):
    return (isinstance(value, list) and len(value) == size
            and all(type(v) in (int, float) and isfinite(v) for v in value))


def _bounds(value):
    return _vector(value, 4) and value[0] < value[2] and value[1] < value[3]


def _frame(vertices):
    if not isinstance(vertices, list) or not all(_vector(p, 2) for p in vertices):
        return None
    corners = vertices[:-1] if vertices and vertices[0] == vertices[-1] else vertices
    if len(corners) != 4:
        return None
    xs, ys = zip(*corners)
    bounds = [min(xs), min(ys), max(xs), max(ys)]
    if not _bounds(bounds) or len(set(xs)) != 2 or len(set(ys)) != 2 or len(set(map(tuple, corners))) != 4:
        return None
    if any(a[0] != b[0] and a[1] != b[1] for a, b in zip(corners, corners[1:] + corners[:1])):
        return None
    return bounds


def _contains(frame, number):
    return all(frame[i] <= number[i] for i in (0, 1)) and all(frame[i] >= number[i] for i in (2, 3))


def _shift(a, b):
    return [y - x for x, y in zip(a, b)]


def _same(a, b):
    return all(isclose(x, y, rel_tol=0, abs_tol=0.01) for x, y in zip(a, b))


def verified_copy_layout(trace, source_ids, result_ids, result_number, result_frame):
    proof = trace.get("displaced_number_frame")
    if proof is None:
        return trace.get("relative_positions_preserved") is True
    if not isinstance(proof, dict) or not proof.get("reopen_evidence"):
        return False
    rows = proof.get("members")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return False
    if not all(isinstance(row.get(key), str) and row[key] for row in rows for key in ("source_id", "result_id")):
        return False
    sources = {row["source_id"] for row in rows}
    results = {row["result_id"] for row in rows}
    if (len(sources) != len(rows) or len(results) != len(rows) or len(rows) < 3
            or sources != set(source_ids) or results != set(result_ids) or result_number == result_frame):
        return False
    members = {row["result_id"]: row for row in rows}
    frame = members.get(result_frame, {})
    number = members.get(result_number, {})
    old = _frame(frame.get("source_vertices"))
    new = _frame(frame.get("result_vertices"))
    if not old or not new or not all(_bounds(number.get(k)) for k in ("source_bounds", "result_bounds")):
        return False
    if _contains(old, number["source_bounds"]) or not _contains(new, number["result_bounds"]):
        return False
    if frame.get("source_closed") is not True or frame.get("result_closed") is not True:
        return False
    old_vertices, new_vertices = frame["source_vertices"], frame["result_vertices"]
    if len(old_vertices) != len(new_vertices):
        return False
    frame_shift = _shift(old_vertices[0], new_vertices[0])
    if not all(_same(_shift(a, b), frame_shift) for a, b in zip(old_vertices, new_vertices)):
        return False
    shifts = []
    for row in rows:
        if row is frame:
            continue
        if not all(_vector(row.get(key), 2) for key in ("source_anchor", "result_anchor")):
            return False
        shifts.append(_shift(row["source_anchor"], row["result_anchor"]))
    return all(_same(s, shifts[0]) for s in shifts) and not _same(frame_shift, shifts[0])
