from __future__ import annotations

import datetime as dt
import json
import urllib.parse

import pytest

from research import openalex_api

# Shaped like an API record (checked against the live API on 5 October 2026);
# the people in it are made up.
WORK = {
    "id": "https://openalex.org/W1",
    "doi": "https://doi.org/10.1000/example",
    "title": "Recycling of polyurethane foams",
    "publication_date": "2026-09-20",
    "publication_year": 2026,
    "type": "article",
    "language": "en",
    "is_retracted": False,
    "is_xpac": True,
    "primary_topic": {
        "id": "https://openalex.org/T1",
        "display_name": "Polyurethane chemistry",
        "score": 0.94,
        "subfield": {"id": "https://openalex.org/subfields/2507"},
    },
    "sustainable_development_goals": [
        {"id": "https://metadata.un.org/sdg/12", "display_name": "x", "score": 1.0}
    ],
    "open_access": {"is_oa": True, "oa_status": "green", "oa_url": "https://example.org"},
    "cited_by_count": 3,
    "fwci": 1.5,
    "authorships": [
        {
            "author": {"id": "https://openalex.org/A1", "display_name": "Jane Example"},
            "raw_author_name": "Jane Example",
            "raw_orcid": "0000-0000-0000-0001",
            "raw_affiliation_strings": ["Example University, Leverkusen"],
            "countries": ["DE"],
            "institutions": [
                {"id": "https://openalex.org/I1", "country_code": "DE", "display_name": "U"}
            ],
        },
        {
            "author": {"id": "https://openalex.org/A2", "display_name": "John Example"},
            "institutions": [
                {"id": "https://openalex.org/I1", "country_code": "DE"},
                {"id": "https://openalex.org/I2", "country_code": "NL"},
                {"id": "https://openalex.org/I3", "country_code": None},
            ],
        },
    ],
    "created_date": "2026-09-13T00:00:00",
    "updated_date": "2026-09-23T05:00:35.095934",
}


def test_landing_record_keeps_institutions_and_drops_people():
    record = openalex_api.to_landing_record(WORK)
    text = json.dumps(record)
    for personal in ("Jane", "John", "0000-0000", "openalex.org/A", "Leverkusen"):
        assert personal not in text
    assert record["institution_ids"] == [
        "https://openalex.org/I1",
        "https://openalex.org/I2",
        "https://openalex.org/I3",
    ]
    assert record["country_codes"] == ["DE", "NL"]


def test_landing_record_matches_the_staging_columns():
    from research.sql import COLUMN_NAMES

    record = openalex_api.to_landing_record(WORK)
    assert list(record) == [c for c in COLUMN_NAMES if c not in ("source", "loaded_at")]
    assert record["topic_id"] == "https://openalex.org/T1"
    assert record["is_xpac"] is True
    assert record["sdg_ids"] == ["https://metadata.un.org/sdg/12"]
    assert record["created_date"] == "2026-09-13T00:00:00.000000"
    assert record["updated_date"] == "2026-09-23T05:00:35.095934"


def test_missing_nested_fields_become_nulls_and_empty_lists():
    record = openalex_api.to_landing_record({"id": "https://openalex.org/W2"})
    assert record["topic_id"] is None
    assert record["sdg_ids"] == []
    assert record["institution_ids"] == []
    assert record["updated_date"] is None


def test_paging_follows_the_cursor_until_it_runs_out():
    pages = {
        "*": {"results": [{"id": "1"}, {"id": "2"}], "meta": {"next_cursor": "c2"}},
        "c2": {"results": [{"id": "3"}], "meta": {"next_cursor": "c3"}},
        "c3": {"results": [], "meta": {"next_cursor": None}},
    }
    seen = []

    def fetch(url):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        seen.append(query)
        return pages[query["cursor"][0]]

    works = list(openalex_api.iter_works("2026-09-05", fetch))
    assert [w["id"] for w in works] == ["1", "2", "3"]
    assert seen[0]["filter"] == ["primary_topic.subfield.id:2507,from_publication_date:2026-09-05"]
    assert seen[0]["per_page"] == ["200"]
    # Without this the API hides xpac works, which the snapshot includes.
    assert seen[0]["include_xpac"] == ["true"]
    assert "is_xpac" in seen[0]["select"][0].split(",")


def test_runaway_paging_stops():
    def fetch(url):
        return {"results": [{"id": "x"}], "meta": {"next_cursor": "again"}}

    with pytest.raises(RuntimeError):
        list(openalex_api.iter_works("2026-09-05", fetch))


def test_window_is_thirty_days():
    assert openalex_api.window_start(dt.date(2026, 10, 5)) == "2026-09-05"
