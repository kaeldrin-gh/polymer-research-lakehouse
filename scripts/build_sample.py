"""Build the committed sample of research.works from the OpenAlex API.

A seeded random sample of 3,000 polymer works (CC0), converted with the same
`to_landing_record` the daily feed uses, so it has exactly the columns of
research.works and no personal data. dbt runs on it in CI and locally.

Run once; the output is committed:

    uv run python scripts/build_sample.py
"""

from __future__ import annotations

import json
import tempfile
import urllib.parse
from pathlib import Path

import duckdb

from research import openalex_api, scope
from research.sql import WORK_COLUMNS

SIZE = 3000
SEED = 42
OUTPUT = Path(__file__).resolve().parents[1] / "sample" / "research_works.parquet"
# Fixed, so rebuilding from the same API answers gives the same file.
LOADED_AT = "2026-10-06 00:00:00"


def sample_url(page: int) -> str:
    query = urllib.parse.urlencode(
        {
            "filter": f"primary_topic.subfield.id:{scope.SUBFIELD_FILTER}",
            "sample": SIZE,
            "seed": SEED,
            "per_page": openalex_api.PER_PAGE,
            "page": page,
            "select": openalex_api.SELECT_FIELDS,
        }
    )
    return f"{openalex_api.API_URL}?{query}"


def fetch_sample() -> list[dict]:
    records = []
    for page in range(1, SIZE // openalex_api.PER_PAGE + 1):
        results = openalex_api.http_get_json(sample_url(page))["results"]
        records.extend(openalex_api.to_landing_record(work) for work in results)
    return records


def write_parquet(records: list[dict], output: Path) -> int:
    # Cast every landing column to its research.works type, in table order.
    casts = {
        "date": "CAST({c} AS DATE)",
        "timestamp": "CAST({c} AS TIMESTAMP)",
        "array<string>": "CAST({c} AS VARCHAR[])",
        "int": "CAST({c} AS INTEGER)",
        "double": "CAST({c} AS DOUBLE)",
        "boolean": "CAST({c} AS BOOLEAN)",
        "string": "CAST({c} AS VARCHAR)",
    }
    select = []
    for name, type_ in WORK_COLUMNS:
        if name == "source":
            select.append("'api' AS source")
        elif name == "loaded_at":
            select.append(f"TIMESTAMP '{LOADED_AT}' AS loaded_at")
        else:
            select.append(f"{casts[type_].format(c=name)} AS {name}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        lines = Path(tmp) / "works.jsonl"
        lines.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
        )
        con = duckdb.connect()
        con.execute(
            f"COPY (SELECT {', '.join(select)} "
            f"FROM read_json('{lines.as_posix()}', format = 'newline_delimited') ORDER BY id) "
            f"TO '{output.as_posix()}' (FORMAT parquet, COMPRESSION zstd)"
        )
        return con.execute(f"SELECT count(*) FROM '{output.as_posix()}'").fetchone()[0]


if __name__ == "__main__":
    records = fetch_sample()
    ids = {r["id"] for r in records}
    if len(ids) != len(records):
        raise SystemExit(f"duplicate ids in the sample: {len(records) - len(ids)}")
    rows = write_parquet(records, OUTPUT)
    print(f"wrote {rows} works to {OUTPUT}")
