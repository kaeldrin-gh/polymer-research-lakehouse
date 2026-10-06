from __future__ import annotations

import re

import pytest

from research import scope, sql

BUCKET = "research-bucket-123"
RUN = "manual-2026-10-05"


def test_snapshot_stage_reads_only_the_new_partitions():
    query = sql.stage_snapshot(BUCKET, RUN, "2026-06-25", "2026-09-23")
    assert "w.partition_date > '2026-06-25'" in query
    assert "w.partition_date <= '2026-09-23'" in query
    assert f"location = 's3://{BUCKET}/staging/snapshot/{RUN}/'" in query


def test_snapshot_stage_keeps_works_that_left_the_subfield_for_deletion():
    query = sql.stage_snapshot(BUCKET, RUN, "2026-06-25", "2026-09-23")
    in_scope = f"coalesce(w.primary_topic.subfield.id = '{scope.SUBFIELD_ID}', false)"
    assert f"{in_scope} AS in_scope" in query
    assert f"({in_scope} OR w.id IN (SELECT id FROM research.works))" in query


@pytest.mark.parametrize(
    "query",
    [
        sql.stage_snapshot(BUCKET, RUN, "2026-06-25", "2026-09-23"),
        sql.stage_api(BUCKET, RUN, "2026-10-05"),
    ],
)
def test_staging_never_reads_abstracts_or_author_identities(query):
    lowered = query.lower()
    for forbidden in ("abstract", "author.", "display_name AS author", "orcid", "raw_"):
        assert forbidden.lower() not in lowered
    # authorships is read only to reach institutions.
    assert re.findall(r"a\.institutions, i -> i\.(\w+)", query) in ([], ["id", "country_code"])


def test_both_flows_stage_the_same_columns_in_the_same_order():
    def staged(query):
        select = query.split("SELECT", 1)[1].split("FROM", 1)[0]
        return [
            line.strip().rstrip(",").split(" AS ")[-1].split(".")[-1]
            for line in select.strip().splitlines()
        ]

    snapshot = staged(sql.stage_snapshot(BUCKET, RUN, "2026-06-25", "2026-09-23"))
    api = staged(sql.stage_api(BUCKET, RUN, "2026-10-05"))
    assert snapshot == api
    assert snapshot == [c for c in sql.COLUMN_NAMES if c != "loaded_at"] + ["in_scope"]


def test_merge_deletes_out_of_scope_updates_newer_and_inserts_new():
    query = sql.merge("snapshot")
    assert "FROM research.works_changes_snapshot c" in query
    assert "row_number() OVER (PARTITION BY c.id ORDER BY c.updated_date DESC)" in query
    clauses = re.findall(r"WHEN (.+?) THEN (\w+)", query)
    assert clauses == [
        ("MATCHED AND NOT s.in_scope", "DELETE"),
        ("MATCHED AND s.updated_date > t.updated_date", "UPDATE"),
        ("NOT MATCHED AND s.in_scope", "INSERT"),
    ]
    # Every column is written on insert, ids are never updated.
    update_set = query.split("UPDATE SET", 1)[1].split("WHEN NOT MATCHED", 1)[0]
    assert "doi = s.doi" in update_set
    assert not re.search(r"(^|\s)id = s\.id", update_set)
    inserted = query.split("INSERT (", 1)[1].split(")", 1)[0].split(", ")
    assert inserted == sql.COLUMN_NAMES


def test_deletions_apply_only_since_the_previous_load():
    assert "deleted_date > '2026-06-25'" in sql.apply_deletions("2026-06-25")


def test_works_table_is_iceberg_in_the_research_bucket():
    query = sql.create_works(BUCKET)
    assert query.startswith("CREATE TABLE IF NOT EXISTS research.works (")
    assert f"LOCATION 's3://{BUCKET}/iceberg/works/'" in query
    assert "'table_type' = 'ICEBERG'" in query


def test_queries_run_in_a_safe_order():
    assert list(sql.snapshot_queries(BUCKET, RUN, "2026-06-25", "2026-09-23")) == [
        "create_works",
        "drop_stage",
        "stage",
        "merge",
        "apply_deletions",
        "cleanup_stage",
        "optimize",
        "vacuum",
    ]
    assert list(sql.api_queries(BUCKET, RUN, "2026-10-05")) == [
        "create_works",
        "drop_stage",
        "stage",
        "merge",
        "cleanup_stage",
    ]


@pytest.mark.parametrize(
    ("bucket", "run_id"),
    [("Bad_Bucket", RUN), (BUCKET, "run'; --"), (BUCKET, ""), ("x", RUN)],
)
def test_unsafe_names_are_rejected(bucket, run_id):
    with pytest.raises(ValueError):
        sql.stage_api(bucket, run_id, "2026-10-05")


def test_an_empty_partition_range_is_rejected():
    with pytest.raises(ValueError):
        sql.stage_snapshot(BUCKET, RUN, "2026-09-23", "2026-09-23")


def test_both_flows_keep_xpac_works_flagged_not_dropped():
    snapshot = sql.stage_snapshot(BUCKET, RUN, "2026-06-25", "2026-09-23")
    api = sql.stage_api(BUCKET, RUN, "2026-10-05")
    assert "coalesce(w.is_xpac, false) AS is_xpac" in snapshot
    assert "coalesce(a.is_xpac, false) AS is_xpac" in api
    assert "is_xpac" not in snapshot.split("WHERE", 1)[1]
