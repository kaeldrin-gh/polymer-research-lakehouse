from __future__ import annotations

import pytest

from research.manifest import parse_date, plan_snapshot


def test_a_newer_release_is_loaded_from_the_watermark():
    plan = plan_snapshot({"date": "2026-09-23"}, "2026-06-25")
    assert plan.new_release
    assert (plan.watermark, plan.release_date) == ("2026-06-25", "2026-09-23")


def test_the_same_release_is_not_loaded_twice():
    assert not plan_snapshot({"date": "2026-09-23"}, "2026-09-23").new_release


def test_an_older_manifest_never_moves_the_watermark_back():
    assert not plan_snapshot({"date": "2026-06-25"}, "2026-09-23").new_release


@pytest.mark.parametrize(
    "value",
    ["2026-9-23", "2026-09-23'; DROP TABLE research.works; --", "", None, "2026-02-30"],
)
def test_anything_but_an_iso_date_is_rejected(value):
    with pytest.raises(ValueError):
        parse_date(value)
