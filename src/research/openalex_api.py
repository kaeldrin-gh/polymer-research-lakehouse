"""Daily feed from the OpenAlex API: recently published polymer works.

The API's change filters need a paid plan, so the feed re-reads a
publication-date window instead (see scope.API_WINDOW_DAYS). Each list call
costs 0.0001 USD of the free daily budget; a 30-day window is about ten calls.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator

from research import scope

API_URL = "https://api.openalex.org/works"
PER_PAGE = 200
# Top-level fields only; `authorships` is reduced to institutions below.
SELECT_FIELDS = (
    "id,doi,title,publication_date,publication_year,type,language,is_retracted,"
    "primary_topic,sustainable_development_goals,open_access,cited_by_count,fwci,"
    "authorships,created_date,updated_date"
)
MAX_PAGES = 100
USER_AGENT = (
    "polymer-research-lakehouse (https://github.com/kaeldrin-gh/polymer-research-lakehouse)"
)

Fetch = Callable[[str], dict]


def http_get_json(url: str, attempts: int = 3) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except OSError:
            if attempt == attempts:
                raise
            time.sleep(5 * attempt)
    raise AssertionError("unreachable")


def page_url(from_date: str, cursor: str) -> str:
    query = urllib.parse.urlencode(
        {
            "filter": f"primary_topic.subfield.id:{scope.SUBFIELD_FILTER},"
            f"from_publication_date:{from_date}",
            "select": SELECT_FIELDS,
            "per_page": PER_PAGE,
            "cursor": cursor,
        }
    )
    return f"{API_URL}?{query}"


def iter_works(from_date: str, fetch: Fetch = http_get_json) -> Iterator[dict]:
    """Every work in the window, following the cursor until it runs out."""
    cursor = "*"
    for _ in range(MAX_PAGES):
        page = fetch(page_url(from_date, cursor))
        yield from page["results"]
        cursor = page["meta"].get("next_cursor")
        if not cursor or not page["results"]:
            return
    raise RuntimeError(f"more than {MAX_PAGES} pages; is the filter right?")


def _iso_timestamp(value: str | None) -> str | None:
    # The API returns naive UTC timestamps, sometimes date-only.
    if not value:
        return None
    return dt.datetime.fromisoformat(value).isoformat(timespec="microseconds")


def _institutions(work: dict, field: str) -> list[str]:
    seen: list[str] = []
    for authorship in work.get("authorships") or []:
        for institution in authorship.get("institutions") or []:
            value = institution.get(field)
            if value and value not in seen:
                seen.append(value)
    return seen


def to_landing_record(work: dict) -> dict:
    """The columns of research.works, flattened as `stage_api` reads them.

    Author names, ORCIDs and raw affiliation strings are dropped here, before
    anything is written.
    """
    topic = work.get("primary_topic") or {}
    open_access = work.get("open_access") or {}
    return {
        "id": work["id"],
        "doi": work.get("doi"),
        "title": work.get("title"),
        "publication_date": work.get("publication_date"),
        "publication_year": work.get("publication_year"),
        "type": work.get("type"),
        "language": work.get("language"),
        "is_retracted": work.get("is_retracted"),
        "topic_id": topic.get("id"),
        "topic_name": topic.get("display_name"),
        "topic_score": topic.get("score"),
        "sdg_ids": [g["id"] for g in work.get("sustainable_development_goals") or []],
        "is_oa": open_access.get("is_oa"),
        "oa_status": open_access.get("oa_status"),
        "cited_by_count": work.get("cited_by_count"),
        "fwci": work.get("fwci"),
        "institution_ids": _institutions(work, "id"),
        "country_codes": _institutions(work, "country_code"),
        "created_date": _iso_timestamp(work.get("created_date")),
        "updated_date": _iso_timestamp(work.get("updated_date")),
    }


def window_start(today: dt.date) -> str:
    return (today - dt.timedelta(days=scope.API_WINDOW_DAYS)).isoformat()


def to_json_lines(records: list[dict]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
