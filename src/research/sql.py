"""Athena SQL for the research loads.

Both flows (quarterly snapshot and daily API feed) stage their changed works
into an Iceberg table with the same columns, then share one MERGE into
`research.works`. Every value interpolated into SQL is validated first.
"""

from __future__ import annotations

import re

from research import scope
from research.manifest import parse_date

# research.works, one row per work. Institutions and countries only: no
# author names, ORCIDs or raw affiliation strings.
WORK_COLUMNS: list[tuple[str, str]] = [
    ("id", "string"),
    ("doi", "string"),
    ("title", "string"),
    ("publication_date", "date"),
    ("publication_year", "int"),
    ("type", "string"),
    ("language", "string"),
    ("is_retracted", "boolean"),
    # OpenAlex's expansion set: works from newer sources with thinner metadata,
    # hidden from its API and website by default. Kept and flagged here; the
    # dbt products leave them out (docs/design.md, Findings).
    ("is_xpac", "boolean"),
    ("topic_id", "string"),
    ("topic_name", "string"),
    ("topic_score", "double"),
    ("sdg_ids", "array<string>"),
    ("is_oa", "boolean"),
    ("oa_status", "string"),
    ("cited_by_count", "int"),
    ("fwci", "double"),
    ("institution_ids", "array<string>"),
    ("country_codes", "array<string>"),
    ("created_date", "timestamp"),
    ("updated_date", "timestamp"),
    ("source", "string"),
    ("loaded_at", "timestamp"),
]
COLUMN_NAMES = [name for name, _ in WORK_COLUMNS]

_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
FLOWS = ("snapshot", "api")


def _bucket(name: str) -> str:
    if not _BUCKET.match(name or ""):
        raise ValueError(f"not an S3 bucket name: {name!r}")
    return name


def _run_id(value: str) -> str:
    if not _RUN_ID.match(value or ""):
        raise ValueError(f"not a run id: {value!r}")
    return value


def stage_table(flow: str) -> str:
    if flow not in FLOWS:
        raise ValueError(f"unknown flow: {flow!r}")
    return f"research.works_changes_{flow}"


def create_works(bucket: str) -> str:
    columns = ",\n  ".join(f"{name} {type_}" for name, type_ in WORK_COLUMNS)
    return (
        f"CREATE TABLE IF NOT EXISTS {scope.WORKS} (\n  {columns}\n)\n"
        f"LOCATION 's3://{_bucket(bucket)}/{scope.ICEBERG_PREFIX}'\n"
        "TBLPROPERTIES (\n"
        "  'table_type' = 'ICEBERG',\n"
        "  'format' = 'parquet',\n"
        "  'write_compression' = 'zstd',\n"
        "  'vacuum_max_snapshot_age_seconds' = '604800'\n"
        ")"
    )


def drop_stage(flow: str) -> str:
    return f"DROP TABLE IF EXISTS {stage_table(flow)}"


def _ctas(flow: str, bucket: str, run_id: str, select: str) -> str:
    location = f"s3://{_bucket(bucket)}/{scope.STAGING_PREFIX}{flow}/{_run_id(run_id)}/"
    return (
        f"CREATE TABLE {stage_table(flow)}\n"
        f"WITH (table_type = 'ICEBERG', location = '{location}', is_external = false)\n"
        f"AS\n{select}"
    )


def _institution_field(field: str) -> str:
    # authorships -> institutions -> field, flattened, without nulls or repeats.
    return (
        "array_distinct(filter(flatten(transform(w.authorships, "
        f"a -> transform(a.institutions, i -> i.{field}))), x -> x IS NOT NULL))"
    )


def stage_snapshot(bucket: str, run_id: str, after: str, until: str) -> str:
    """Changed works from the snapshot partitions in (after, until].

    In-scope works are polymer works. A work already in `research.works` whose
    new version moved to another subfield is staged too, as out of scope, so
    the MERGE removes it.
    """
    after, until = parse_date(after), parse_date(until)
    if until <= after:
        raise ValueError(f"empty partition range: ({after}, {until}]")
    in_scope = f"coalesce(w.primary_topic.subfield.id = '{scope.SUBFIELD_ID}', false)"
    select = f"""SELECT
  w.id,
  w.doi,
  w.title,
  w.publication_date,
  w.publication_year,
  w.type,
  w.language,
  w.is_retracted,
  coalesce(w.is_xpac, false) AS is_xpac,
  w.primary_topic.id AS topic_id,
  w.primary_topic.display_name AS topic_name,
  CAST(w.primary_topic.score AS double) AS topic_score,
  transform(w.sustainable_development_goals, g -> g.id) AS sdg_ids,
  w.open_access.is_oa AS is_oa,
  w.open_access.oa_status AS oa_status,
  w.cited_by_count,
  w.fwci,
  {_institution_field("id")} AS institution_ids,
  {_institution_field("country_code")} AS country_codes,
  CAST(w.created_date AS timestamp(6)) AS created_date,
  CAST(w.updated_date AS timestamp(6)) AS updated_date,
  'snapshot' AS source,
  {in_scope} AS in_scope
FROM {scope.SOURCE_WORKS} w
WHERE w.partition_date > '{after}'
  AND w.partition_date <= '{until}'
  AND ({in_scope} OR w.id IN (SELECT id FROM {scope.WORKS}))"""
    return _ctas("snapshot", bucket, run_id, select)


def stage_api(bucket: str, run_id: str, fetch_date: str) -> str:
    """Works the daily feed landed for `fetch_date`; all are in scope."""
    fetch_date = parse_date(fetch_date)
    select = f"""SELECT
  a.id,
  a.doi,
  a.title,
  CAST(from_iso8601_date(a.publication_date) AS date) AS publication_date,
  a.publication_year,
  a.type,
  a.language,
  a.is_retracted,
  coalesce(a.is_xpac, false) AS is_xpac,
  a.topic_id,
  a.topic_name,
  a.topic_score,
  a.sdg_ids,
  a.is_oa,
  a.oa_status,
  a.cited_by_count,
  a.fwci,
  a.institution_ids,
  a.country_codes,
  CAST(from_iso8601_timestamp(a.created_date) AS timestamp(6)) AS created_date,
  CAST(from_iso8601_timestamp(a.updated_date) AS timestamp(6)) AS updated_date,
  'api' AS source,
  true AS in_scope
FROM {scope.SOURCE_API_WORKS} a
WHERE a.fetch_date = '{fetch_date}'"""
    return _ctas("api", bucket, run_id, select)


def merge(flow: str) -> str:
    """Upsert staged works by id; the newer `updated_date` wins.

    The source is deduplicated first, because MERGE fails when two source rows
    match one target row.
    """
    data_columns = [c for c in COLUMN_NAMES if c not in ("id", "loaded_at")]
    updates = ",\n    ".join(f"{c} = s.{c}" for c in data_columns)
    inserts = ", ".join(f"s.{c}" for c in COLUMN_NAMES if c != "loaded_at")
    loaded_at = "CAST(current_timestamp AS timestamp(6))"
    return f"""MERGE INTO {scope.WORKS} t
USING (
  SELECT * FROM (
    SELECT c.*, row_number() OVER (PARTITION BY c.id ORDER BY c.updated_date DESC) AS rn
    FROM {stage_table(flow)} c
  ) WHERE rn = 1
) s
ON t.id = s.id
WHEN MATCHED AND NOT s.in_scope THEN DELETE
WHEN MATCHED AND s.updated_date > t.updated_date THEN UPDATE SET
    {updates},
    loaded_at = {loaded_at}
WHEN NOT MATCHED AND s.in_scope THEN INSERT ({", ".join(COLUMN_NAMES)})
  VALUES ({inserts}, {loaded_at})"""


def apply_deletions(after: str) -> str:
    """Remove works OpenAlex deleted after the previous load.

    The deleted-IDs file is cumulative, so only rows newer than the watermark
    matter.
    """
    after = parse_date(after)
    return (
        f"DELETE FROM {scope.WORKS}\n"
        f"WHERE id IN (\n"
        f"  SELECT work_id FROM {scope.SOURCE_DELETED_WORKS} WHERE deleted_date > '{after}'\n"
        ")"
    )


def optimize() -> str:
    return f"OPTIMIZE {scope.WORKS} REWRITE DATA USING BIN_PACK"


def vacuum() -> str:
    return f"VACUUM {scope.WORKS}"


def snapshot_queries(bucket: str, run_id: str, after: str, until: str) -> dict[str, str]:
    """All statements of a snapshot load, in execution order."""
    return {
        "create_works": create_works(bucket),
        "drop_stage": drop_stage("snapshot"),
        "stage": stage_snapshot(bucket, run_id, after, until),
        "merge": merge("snapshot"),
        "apply_deletions": apply_deletions(after),
        "cleanup_stage": drop_stage("snapshot"),
        "optimize": optimize(),
        "vacuum": vacuum(),
    }


def api_queries(bucket: str, run_id: str, fetch_date: str) -> dict[str, str]:
    return {
        "create_works": create_works(bucket),
        "drop_stage": drop_stage("api"),
        "stage": stage_api(bucket, run_id, fetch_date),
        "merge": merge("api"),
        "cleanup_stage": drop_stage("api"),
    }
