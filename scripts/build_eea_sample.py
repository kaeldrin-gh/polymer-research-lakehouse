"""Build the committed sample of sustainability.air_releases from the newest
EEA release.

Downloads the release, lands it with the same code the Lambda uses (no
facility names or places), keeps the chemical industry (E-PRTR sector 4), and
adds the SCD Type 2 columns of a first load. dbt runs on it in CI and locally.
EEA data is CC BY 4.0 © European Environment Agency.

Run once; the output is committed:

    uv run python scripts/build_eea_sample.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import duckdb

from sustainability import air_releases, eea_share

OUTPUT = Path(__file__).resolve().parents[1] / "sample" / "sustainability_air_releases.parquet"
LOADED_AT = "2026-10-06 00:00:00"
CHEMICAL_INDUSTRY = "4"


def main() -> None:
    release = eea_share.latest_release(eea_share.list_folder_names(eea_share.fetch_listing()))
    with tempfile.TemporaryDirectory() as tmp:
        zip_path, landing = Path(tmp) / "release.zip", Path(tmp) / "air.csv.gz"
        eea_share.download(release.zip_url, str(zip_path))
        rows = air_releases.extract_landing_file(str(zip_path), str(landing))
        con = duckdb.connect()
        con.execute(
            f"""COPY (
                SELECT facility_id, CAST(reporting_year AS INTEGER) AS reporting_year, pollutant,
                       country_name, sector_code, sector_name, annex_activity,
                       CAST(releases_kg AS DOUBLE) AS releases_kg, confidentiality_reason,
                       {release.version} AS valid_from_version,
                       CAST(NULL AS INTEGER) AS valid_to_version,
                       TIMESTAMP '{LOADED_AT}' AS loaded_at
                FROM read_csv('{landing.as_posix()}', header = true, all_varchar = true)
                WHERE sector_code = '{CHEMICAL_INDUSTRY}'
                ORDER BY facility_id, reporting_year, pollutant
            ) TO '{OUTPUT.as_posix()}' (FORMAT parquet, COMPRESSION zstd)"""
        )
        kept = con.execute(f"SELECT count(*) FROM '{OUTPUT.as_posix()}'").fetchone()[0]
    print(f"v{release.version}: {rows} rows landed, {kept} chemical-industry rows in {OUTPUT}")


if __name__ == "__main__":
    main()
