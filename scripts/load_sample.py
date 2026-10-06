"""Load the committed samples into DuckDB as the tables the pipelines write
on AWS (research.works, sustainability.air_releases), so dbt runs the same
models in CI and locally.

    uv run python scripts/load_sample.py
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = {
    "research.works": ROOT / "sample" / "research_works.parquet",
    "sustainability.air_releases": ROOT / "sample" / "sustainability_air_releases.parquet",
}
DUCKDB_PATH = Path(os.environ.get("DUCKDB_PATH", ROOT / "dbt" / "target" / "lakehouse.duckdb"))

if __name__ == "__main__":
    DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DUCKDB_PATH))
    for table, sample in SAMPLES.items():
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {table.split('.')[0]}")
        con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM '{sample.as_posix()}'")
        rows = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        print(f"loaded {rows} rows into {table} ({DUCKDB_PATH})")
