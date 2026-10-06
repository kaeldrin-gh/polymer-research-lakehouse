"""The SCD Type 2 statements, run for real in DuckDB across two releases."""

from __future__ import annotations

import ast
from pathlib import Path

import duckdb
import pytest

from sustainability import scd2_sql

TARGET = "air_releases"
COLUMNS = (
    "facility_id, reporting_year, pollutant, country_name, sector_code, sector_name, "
    "annex_activity, releases_kg, confidentiality_reason"
)


def _row(facility, year, pollutant, kg, reason=None, activity="4(a)(viii)"):
    return (facility, year, pollutant, "Germany", "4", "Chemical industry", activity, kg, reason)


V16 = [
    _row("F1", 2023, "CO2", 1000.0),  # unchanged in v17
    _row("F1", 2024, "CO2", 1200.0),  # revised in v17
    _row("F2", 2024, "NOX", 50.0),  # dropped in v17
    _row("F3", 2024, "CONFIDENTIAL", None, "Article4(2)(d)"),  # null value, unchanged
]
V17 = [
    _row("F1", 2023, "CO2", 1000.0),
    _row("F1", 2024, "CO2", 1250.0),
    _row("F3", 2024, "CONFIDENTIAL", None, "Article4(2)(d)"),
    _row("F4", 2024, "CO2", 7.5),  # new
]


@pytest.fixture
def db():
    con = duckdb.connect()
    con.execute(
        f"""CREATE TABLE {TARGET} (
            facility_id VARCHAR, reporting_year INTEGER, pollutant VARCHAR,
            country_name VARCHAR, sector_code VARCHAR, sector_name VARCHAR,
            annex_activity VARCHAR, releases_kg DOUBLE, confidentiality_reason VARCHAR,
            valid_from_version INTEGER, valid_to_version INTEGER, loaded_at TIMESTAMP)"""
    )
    return con


def _apply(con, rows, version):
    con.execute("DROP TABLE IF EXISTS incoming")
    con.execute(
        "CREATE TABLE incoming (facility_id VARCHAR, reporting_year INTEGER, pollutant VARCHAR, "
        "country_name VARCHAR, sector_code VARCHAR, sector_name VARCHAR, annex_activity VARCHAR, "
        "releases_kg DOUBLE, confidentiality_reason VARCHAR)"
    )
    con.executemany(f"INSERT INTO incoming ({COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    # create_table is Spark DDL; the fixture made the DuckDB table.
    _, close, insert = scd2_sql.release_statements(TARGET, "incoming", version, "s3://b/x/")
    con.execute(close)
    con.execute(insert)


def _history(con):
    return con.execute(
        f"SELECT facility_id, reporting_year, pollutant, releases_kg, valid_from_version, "
        f"valid_to_version FROM {TARGET} ORDER BY 1, 2, 3, 5"
    ).fetchall()


def test_first_release_loads_every_row_as_current(db):
    _apply(db, V16, 16)
    assert _history(db) == [
        ("F1", 2023, "CO2", 1000.0, 16, None),
        ("F1", 2024, "CO2", 1200.0, 16, None),
        ("F2", 2024, "NOX", 50.0, 16, None),
        ("F3", 2024, "CONFIDENTIAL", None, 16, None),
    ]


def test_next_release_keeps_revised_and_dropped_values_as_history(db):
    _apply(db, V16, 16)
    _apply(db, V17, 17)
    assert _history(db) == [
        ("F1", 2023, "CO2", 1000.0, 16, None),  # unchanged: still the v16 row
        ("F1", 2024, "CO2", 1200.0, 16, 17),  # revised: old value closed...
        ("F1", 2024, "CO2", 1250.0, 17, None),  # ...new value current
        ("F2", 2024, "NOX", 50.0, 16, 17),  # dropped by v17
        # A null value equals a null value: no new version for confidential rows.
        ("F3", 2024, "CONFIDENTIAL", None, 16, None),
        ("F4", 2024, "CO2", 7.5, 17, None),  # new in v17
    ]


def test_applying_a_release_twice_changes_nothing(db):
    _apply(db, V16, 16)
    _apply(db, V17, 17)
    before = _history(db)
    _apply(db, V17, 17)
    assert _history(db) == before


def test_current_rows_equal_the_latest_release(db):
    # The invariant the Glue job checks after every load.
    _apply(db, V16, 16)
    _apply(db, V17, 17)
    current = db.execute(f"SELECT count(*) FROM {TARGET} WHERE valid_to_version IS NULL")
    assert current.fetchone()[0] == len(V17)


def test_a_changed_activity_also_makes_a_new_version(db):
    _apply(db, V16, 16)
    _apply(db, [_row("F1", 2023, "CO2", 1000.0, activity="4(a)(i)")], 17)
    rows = db.execute(
        f"SELECT annex_activity, valid_from_version, valid_to_version FROM {TARGET} "
        "WHERE facility_id = 'F1' AND reporting_year = 2023 ORDER BY 2"
    ).fetchall()
    assert rows == [("4(a)(viii)", 16, 17), ("4(a)(i)", 17, None)]


@pytest.mark.parametrize(
    ("target", "version"),
    [("air_releases; DROP TABLE x", 17), (TARGET, 0), (TARGET, "17"), (TARGET, True)],
)
def test_unsafe_names_and_versions_are_rejected(target, version):
    with pytest.raises(ValueError):
        scd2_sql.close_changed(target, "incoming", version)


def test_spark_ddl_is_an_iceberg_table_at_the_given_location():
    ddl = scd2_sql.create_table("glue_catalog.sustainability.air_releases", "s3://b/iceberg/x/")
    assert ddl.startswith("CREATE TABLE IF NOT EXISTS glue_catalog.sustainability.air_releases")
    assert "USING iceberg" in ddl
    assert "LOCATION 's3://b/iceberg/x/'" in ddl
    with pytest.raises(ValueError):
        scd2_sql.create_table("t", "s3://b/x'; --")


def test_the_glue_job_splits_statements_on_the_same_marker():
    job = (Path(__file__).resolve().parents[1] / "jobs" / "air_releases_scd2.py").read_text()
    (separator,) = [
        ast.literal_eval(node.value)
        for node in ast.parse(job).body
        if isinstance(node, ast.Assign) and node.targets[0].id == "STATEMENT_SEPARATOR"
    ]
    assert separator == scd2_sql.STATEMENT_SEPARATOR
    statements = scd2_sql.release_statements(TARGET, "incoming", 17, "s3://b/x/")
    joined = scd2_sql.STATEMENT_SEPARATOR.join(statements)
    assert joined.split(scd2_sql.STATEMENT_SEPARATOR) == statements
