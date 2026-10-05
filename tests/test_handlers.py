from __future__ import annotations

import datetime as dt
import io
import json

import pytest

from research import handlers, scope


class FakeS3:
    def __init__(self, manifest=None):
        self.manifest = manifest
        self.put = []

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3 argument names
        assert (Bucket, Key) == (scope.OPENALEX_BUCKET, scope.MANIFEST_KEY)
        return {"Body": io.BytesIO(json.dumps(self.manifest).encode())}

    def put_object(self, **kwargs):
        self.put.append(kwargs)


class FakeSSM:
    class exceptions:  # noqa: N801 - mirrors boto3
        class ParameterNotFound(Exception):
            pass

    def __init__(self, value=None):
        self.value = value

    def get_parameter(self, Name):  # noqa: N803
        if self.value is None:
            raise self.exceptions.ParameterNotFound()
        return {"Parameter": {"Value": self.value}}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("RESEARCH_BUCKET", "research-bucket-123")
    monkeypatch.setenv("WATERMARK_PARAMETER", "/prl/research/snapshot-watermark")


def test_first_run_loads_everything_up_to_the_release():
    result = handlers.check_release(
        {"run_id": "r1"}, s3=FakeS3({"date": "2026-09-23"}), ssm=FakeSSM()
    )
    assert result["new_release"]
    assert result["watermark"] == scope.INITIAL_WATERMARK
    assert "partition_date > '2000-01-01'" in result["queries"]["stage"]
    assert "partition_date <= '2026-09-23'" in result["queries"]["stage"]


def test_no_new_release_returns_no_queries():
    result = handlers.check_release(
        {"run_id": "r1"}, s3=FakeS3({"date": "2026-09-23"}), ssm=FakeSSM("2026-09-23")
    )
    assert result == {"new_release": False, "release_date": "2026-09-23", "watermark": "2026-09-23"}


def test_fetch_recent_lands_one_file_per_day_and_plans_the_merge():
    s3 = FakeS3()

    def fetch(url):
        return {
            "results": [{"id": "https://openalex.org/W1", "updated_date": "2026-10-04T01:02:03"}],
            "meta": {"next_cursor": None},
        }

    result = handlers.fetch_recent({"run_id": "r2"}, s3=s3, fetch=fetch, today=dt.date(2026, 10, 5))
    (put,) = s3.put
    assert put["Bucket"] == "research-bucket-123"
    assert put["Key"] == "landing/openalex_api/fetch_date=2026-10-05/works.jsonl"
    assert json.loads(put["Body"].decode())["id"] == "https://openalex.org/W1"
    assert result["count"] == 1
    assert "a.fetch_date = '2026-10-05'" in result["queries"]["stage"]


def test_an_empty_window_lands_an_empty_file_and_skips_the_merge():
    s3 = FakeS3()
    result = handlers.fetch_recent(
        {"run_id": "r3"},
        s3=s3,
        fetch=lambda url: {"results": [], "meta": {"next_cursor": None}},
        today=dt.date(2026, 10, 5),
    )
    assert result["count"] == 0
    assert "queries" not in result
    assert s3.put[0]["Body"] == b""
