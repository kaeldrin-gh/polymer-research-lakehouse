"""Load the committed sample into DuckDB as research.works, the table the
pipeline writes on AWS, so dbt runs the same models in CI and locally.

    uv run python scripts/load_sample.py
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample" / "research_works.parquet"
DUCKDB_PATH = Path(os.environ.get("DUCKDB_PATH", ROOT / "dbt" / "target" / "lakehouse.duckdb"))

if __name__ == "__main__":
    DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DUCKDB_PATH))
    con.execute("CREATE SCHEMA IF NOT EXISTS research")
    con.execute(f"CREATE OR REPLACE TABLE research.works AS SELECT * FROM '{SAMPLE.as_posix()}'")
    rows = con.execute("SELECT count(*) FROM research.works").fetchone()[0]
    print(f"loaded {rows} works into {DUCKDB_PATH}")
