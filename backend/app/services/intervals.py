"""Half-open datetime interval arithmetic used by the availability engine.

Every interval is ``(start, end)`` with ``start < end`` and is treated as
``[start, end)``, so 10:00-11:00 and 11:00-12:00 touch but do not overlap.
All functions return sorted, merged (non-overlapping, non-adjacent) lists.
"""

from collections.abc import Iterable
from datetime import datetime

Interval = tuple[datetime, datetime]


def normalize(intervals: Iterable[Interval]) -> list[Interval]:
    """Sort and merge overlapping or touching intervals, dropping empty ones."""
    items = sorted((s, e) for s, e in intervals if s < e)
    merged: list[Interval] = []
    for start, end in items:
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def intersect(a: Iterable[Interval], b: Iterable[Interval]) -> list[Interval]:
    left, right = normalize(a), normalize(b)
    out: list[Interval] = []
    i = j = 0
    while i < len(left) and j < len(right):
        start = max(left[i][0], right[j][0])
        end = min(left[i][1], right[j][1])
        if start < end:
            out.append((start, end))
        if left[i][1] < right[j][1]:
            i += 1
        else:
            j += 1
    return out


def subtract(a: Iterable[Interval], b: Iterable[Interval]) -> list[Interval]:
    """Return the parts of ``a`` not covered by ``b``."""
    result = normalize(a)
    for cut_start, cut_end in normalize(b):
        next_result: list[Interval] = []
        for start, end in result:
            if cut_end <= start or cut_start >= end:
                next_result.append((start, end))
                continue
            if start < cut_start:
                next_result.append((start, cut_start))
            if cut_end < end:
                next_result.append((cut_end, end))
        result = next_result
    return result


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """The clash rule: newStart < existingEnd AND newEnd > existingStart."""
    return a_start < b_end and a_end > b_start


def contains(intervals: Iterable[Interval], start: datetime, end: datetime) -> bool:
    """True when [start, end) lies entirely inside one of the (merged) intervals."""
    return any(s <= start and end <= e for s, e in normalize(intervals))
