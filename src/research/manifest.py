"""Decide whether a new OpenAlex snapshot release needs loading.

OpenAlex writes `manifest.json` last, so its presence and date mark a complete
release. Partitions are named by the date their records last changed; a load
from watermark W up to release R reads exactly the partitions in (W, R].
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


def parse_date(value: str) -> str:
    """Return `value` if it is an ISO date (YYYY-MM-DD); raise otherwise.

    Every date that reaches SQL passes through here, so a malformed manifest or
    parameter can never inject SQL.
    """
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError(f"not an ISO date: {value!r}")
    return dt.date.fromisoformat(value).isoformat()


def release_date(manifest: dict) -> str:
    return parse_date(manifest["date"])


@dataclass(frozen=True)
class SnapshotPlan:
    new_release: bool
    release_date: str
    watermark: str


def plan_snapshot(manifest: dict, watermark: str) -> SnapshotPlan:
    release = release_date(manifest)
    watermark = parse_date(watermark)
    return SnapshotPlan(new_release=release > watermark, release_date=release, watermark=watermark)
