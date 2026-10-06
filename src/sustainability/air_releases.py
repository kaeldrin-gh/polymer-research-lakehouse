"""Turn the EEA's air releases CSV into the landing file.

Kept: the facility ID, what it is (sector, Annex I activity), where (country),
and what it released. Dropped: facility names, cities, coordinates and address
details. Some facilities are family farms named after their owners, so those
columns are personal data, and no product needs them.
"""

from __future__ import annotations

import csv
import gzip
import io
import zipfile
from collections.abc import Iterable, Iterator

from sustainability import scope

# Landing columns, in order: the CSV the Glue job reads.
LANDING_COLUMNS = [
    "facility_id",
    "reporting_year",
    "pollutant",
    "country_name",
    "sector_code",
    "sector_name",
    "annex_activity",
    "releases_kg",
    "confidentiality_reason",
]
KEY = ("facility_id", "reporting_year", "pollutant")

SOURCE_COLUMNS = {
    "facility_id": "FacilityInspireId",
    "reporting_year": "reportingYear",
    "pollutant": "Pollutant",
    "country_name": "countryName",
    "sector_code": "EPRTR_SectorCode",
    "sector_name": "EPRTR_SectorName",
    "annex_activity": "EPRTRAnnexIMainActivity",
    "releases_kg": "Releases",
    "confidentiality_reason": "confidentialityReason",
}
TARGET_RELEASE = "AIR"


class ReleaseFormatError(ValueError):
    """The release does not look like the one this code was written for."""


def _blank_to_none(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def clean_rows(rows: Iterable[dict[str, str]]) -> Iterator[dict]:
    """Landing rows, one per facility, year and pollutant.

    The release repeats some confidential rows (pollutant `CONFIDENTIAL`, no
    value) word for word; the first one is kept. A repeated key with different
    values would be a new kind of problem, so it fails the load.
    """
    # Key -> hash of the row's values: enough to tell repeats from conflicts
    # without holding 370,000 rows in Lambda memory.
    seen: dict[tuple, int] = {}
    for raw in rows:
        missing = [c for c in SOURCE_COLUMNS.values() if c not in raw]
        if missing:
            raise ReleaseFormatError(f"columns missing from the release: {missing}")
        if raw.get("TargetRelease", TARGET_RELEASE) != TARGET_RELEASE:
            continue
        row = {name: _blank_to_none(raw[source]) for name, source in SOURCE_COLUMNS.items()}
        if not (row["facility_id"] and row["reporting_year"] and row["pollutant"]):
            raise ReleaseFormatError(f"row without a key: {row}")
        row["reporting_year"] = int(row["reporting_year"])
        row["releases_kg"] = float(row["releases_kg"]) if row["releases_kg"] else None
        key = tuple(row[k] for k in KEY)
        fingerprint = hash(tuple(row.values()))
        if key in seen:
            if seen[key] != fingerprint:
                raise ReleaseFormatError(f"conflicting rows for {key}")
            continue
        seen[key] = fingerprint
        yield row


def to_csv(rows: Iterable[dict], out: io.TextIOBase) -> int:
    writer = csv.DictWriter(out, fieldnames=LANDING_COLUMNS, lineterminator="\n")
    writer.writeheader()
    count = 0
    for row in rows:
        writer.writerow(row)
        count += 1
    return count


def extract_landing_file(zip_path: str, out_path: str) -> int:
    """Stream the air releases out of the release zip into a gzipped landing
    CSV, without unpacking the rest; returns the row count."""
    with (
        zipfile.ZipFile(zip_path) as archive,
        archive.open(scope.AIR_RELEASES_MEMBER) as member,
        gzip.open(out_path, "wt", encoding="utf-8", newline="") as out,
    ):
        # utf-8-sig: the release starts with a byte-order mark.
        reader = csv.DictReader(io.TextIOWrapper(member, encoding="utf-8-sig", newline=""))
        return to_csv(clean_rows(reader), out)
