"""SCD Type 2 over EEA releases: every reported value with the versions it
was valid in.

`valid_from_version` is the first release that reported the value;
`valid_to_version` is the first release that no longer did (null while it is
current). A release is applied in two statements, in this order:

1. close: current rows whose value changed or which the release dropped get
   `valid_to_version` = the new version;
2. insert: release rows without a current row (new keys, and the new values
   of changed keys) are inserted as current.

Applying the same release twice changes nothing. The Glue job runs these in
Spark SQL against the Iceberg table; the tests run them in DuckDB. Both accept
the same text, which is kept to plain SQL for that reason.
"""

from __future__ import annotations

import re

from sustainability.air_releases import KEY, LANDING_COLUMNS

# Joins the statements into one Glue job argument (arguments are strings).
STATEMENT_SEPARATOR = "\n;\n"

# Values whose change makes a new version of a row.
TRACKED = [c for c in LANDING_COLUMNS if c not in KEY]
_NAME = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*){0,2}$")


def _name(value: str) -> str:
    if not _NAME.match(value or ""):
        raise ValueError(f"not a table name: {value!r}")
    return value


def _version(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"not a release version: {value!r}")
    return value


def _same(a: str, b: str) -> str:
    # Null-safe equality written out, so Spark and DuckDB read it alike.
    return f"({a} = {b} OR ({a} IS NULL AND {b} IS NULL))"


def _key_join(left: str, right: str) -> str:
    return " AND ".join(f"{left}.{k} = {right}.{k}" for k in KEY)


def create_table(target: str, location: str) -> str:
    """Spark DDL for the Iceberg table (Glue only; DuckDB tests make their own)."""
    if not location.startswith("s3://") or "'" in location:
        raise ValueError(f"not an S3 location: {location!r}")
    return f"""CREATE TABLE IF NOT EXISTS {_name(target)} (
  facility_id string,
  reporting_year int,
  pollutant string,
  country_name string,
  sector_code string,
  sector_name string,
  annex_activity string,
  releases_kg double,
  confidentiality_reason string,
  valid_from_version int,
  valid_to_version int,
  loaded_at timestamp
)
USING iceberg
LOCATION '{location}'
TBLPROPERTIES ('format-version' = '2', 'write.parquet.compression-codec' = 'zstd')"""


def close_changed(target: str, incoming: str, version: int) -> str:
    target, incoming, version = _name(target), _name(incoming), _version(version)
    unchanged = " AND ".join(_same(f"c.{col}", f"i.{col}") for col in TRACKED)
    return f"""MERGE INTO {target} t
USING (
  SELECT c.facility_id, c.reporting_year, c.pollutant
  FROM {target} c
  LEFT JOIN {incoming} i ON {_key_join("c", "i")}
  WHERE c.valid_to_version IS NULL
    AND (i.facility_id IS NULL OR NOT ({unchanged}))
) s
ON {_key_join("t", "s")} AND t.valid_to_version IS NULL
WHEN MATCHED THEN UPDATE SET valid_to_version = {version}"""


def insert_new(target: str, incoming: str, version: int) -> str:
    target, incoming, version = _name(target), _name(incoming), _version(version)
    columns = ", ".join(f"i.{c}" for c in LANDING_COLUMNS)
    return f"""INSERT INTO {target}
SELECT {columns},
  {version} AS valid_from_version,
  CAST(NULL AS INT) AS valid_to_version,
  current_timestamp AS loaded_at
FROM {incoming} i
LEFT JOIN {target} t ON {_key_join("i", "t")} AND t.valid_to_version IS NULL
WHERE t.facility_id IS NULL"""


def release_statements(target: str, incoming: str, version: int, location: str) -> list[str]:
    """Everything the Glue job runs for one release, in order."""
    return [
        create_table(target, location),
        close_changed(target, incoming, version),
        insert_new(target, incoming, version),
    ]
