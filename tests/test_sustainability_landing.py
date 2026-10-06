from __future__ import annotations

import csv
import gzip
import io
import zipfile

import pytest

from sustainability import air_releases, eea_share, handlers, scope

HEADER = (
    "PublicationDate,countryName,reportingYear,EPRTR_SectorCode,EPRTR_SectorName,"
    "EPRTRAnnexIMainActivity,FacilityInspireId,facilityName,city,Longitude,Latitude,"
    "addressConfidentialityReason,TargetRelease,Pollutant,Releases,confidentialityReason"
)
# Shaped like the v16 release (6 October 2026); the farm and its owner are made up.
ROWS = [
    "2026/02/13,Germany,2024,4,Chemical industry,4(a)(viii),DE.F1,Polymer Plant GmbH,"
    "Leverkusen,7.0,51.0,,AIR,Carbon dioxide (CO2),1250.5,",
    "2026/02/13,Ireland,2024,7,Intensive livestock,7(a)(i),IE.F2,Mr Jan Example (Example Farm),"
    "Exampletown,-7.0,53.0,,AIR,Ammonia (NH3),20000,",
    # The release repeats confidential rows word for word.
    "2026/02/13,Poland,2024,4,Chemical industry,4(b),PL.F3,Firma,Miasto,20.0,50.0,,AIR,"
    "CONFIDENTIAL,,Article4(2)(d)",
    "2026/02/13,Poland,2024,4,Chemical industry,4(b),PL.F3,Firma,Miasto,20.0,50.0,,AIR,"
    "CONFIDENTIAL,,Article4(2)(d)",
]


def _reader(lines):
    return csv.DictReader(io.StringIO("\n".join([HEADER, *lines]) + "\n"))


def test_landing_rows_keep_the_facility_id_and_drop_names_and_places():
    rows = list(air_releases.clean_rows(_reader(ROWS)))
    assert list(rows[0]) == air_releases.LANDING_COLUMNS
    text = repr(rows)
    for personal in ("Jan Example", "Example Farm", "Exampletown", "Leverkusen", "53.0", "Firma"):
        assert personal not in text
    assert rows[1]["facility_id"] == "IE.F2"


def test_values_are_typed_and_blank_values_are_null():
    first, _, confidential = air_releases.clean_rows(_reader(ROWS))
    assert first["reporting_year"] == 2024
    assert first["releases_kg"] == 1250.5
    assert first["annex_activity"] == "4(a)(viii)"
    assert confidential["releases_kg"] is None
    assert confidential["confidentiality_reason"] == "Article4(2)(d)"


def test_repeated_identical_rows_are_kept_once():
    rows = list(air_releases.clean_rows(_reader(ROWS)))
    assert [r["facility_id"] for r in rows] == ["DE.F1", "IE.F2", "PL.F3"]


def test_a_repeated_key_with_different_values_fails_the_load():
    conflicting = ROWS[2].replace(",,Article4(2)(d)", ",5,Article4(2)(d)")
    with pytest.raises(air_releases.ReleaseFormatError, match="conflicting"):
        list(air_releases.clean_rows(_reader([ROWS[2], conflicting])))


def test_a_changed_release_format_fails_the_load():
    reader = csv.DictReader(io.StringIO("countryName,Releases\nGermany,1\n"))
    with pytest.raises(air_releases.ReleaseFormatError, match="columns missing"):
        list(air_releases.clean_rows(reader))


def _release_zip(path):
    with zipfile.ZipFile(path, "w") as archive:
        # The real file starts with a byte-order mark.
        data = "﻿" + "\r\n".join([HEADER, *ROWS]) + "\r\n"
        archive.writestr(scope.AIR_RELEASES_MEMBER, data.encode("utf-8"))
        archive.writestr("F6_1_IED_Installations.csv", "not,needed\n")


def test_the_landing_file_is_streamed_out_of_the_zip(tmp_path):
    _release_zip(tmp_path / "release.zip")
    count = air_releases.extract_landing_file(
        str(tmp_path / "release.zip"), str(tmp_path / "air.csv.gz")
    )
    assert count == 3
    with gzip.open(tmp_path / "air.csv.gz", "rt", encoding="utf-8") as landed:
        lines = landed.read().splitlines()
    assert lines[0] == ",".join(air_releases.LANDING_COLUMNS)
    assert lines[1].startswith("DE.F1,2024,Carbon dioxide (CO2),Germany,4,")


LISTING = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>/datashare/public.php/dav/files/t/</d:href></d:response>
  <d:response><d:href>/datashare/public.php/dav/files/t/eea_t_ied-eprtr_p_2007-2023_v15_r00/</d:href></d:response>
  <d:response><d:href>/datashare/public.php/dav/files/t/eea_t_ied-eprtr_p_2007-2024_v16_r00/</d:href></d:response>
  <d:response><d:href>/datashare/public.php/dav/files/t/eea_v_4326_10_m_ied-eprtr_p_2007-2024_v17_r00/</d:href></d:response>
  <d:response><d:href>/datashare/public.php/dav/files/t/some_other%20dataset/</d:href></d:response>
</d:multistatus>"""


def test_the_newest_tabular_release_is_found():
    names = eea_share.list_folder_names(LISTING)
    assert "some_other dataset" in names
    release = eea_share.latest_release(names)
    # The geospatial v17 folder is not a tabular release.
    assert (release.version, release.first_year, release.last_year) == (16, 2007, 2024)
    assert release.zip_url.endswith(
        "eea_t_ied-eprtr_p_2007-2024_v16_r00/User%20friendly%20.csv%20files.zip"
    )


def test_no_release_in_the_listing():
    assert eea_share.latest_release(["README.md"]) is None


class FakeS3:
    def __init__(self):
        self.uploads = []

    def upload_file(self, path, bucket, key):
        with gzip.open(path, "rt", encoding="utf-8") as landed:
            self.uploads.append((bucket, key, landed.read().count("\n")))


class FakeSSM:
    class exceptions:  # noqa: N801 - mirrors boto3
        class ParameterNotFound(Exception):
            pass

    def __init__(self, value=None):
        self.value = value

    def get_parameter(self, Name):  # noqa: N803
        if self.value is None:
            raise self.exceptions.ParameterNotFound()
        return {"Parameter": {"Value": self.value}}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SUSTAINABILITY_BUCKET", "prl-sustainability-1-us-east-1")
    monkeypatch.setenv("VERSION_PARAMETER", "/prl/sustainability/eea-version")


def test_a_new_release_is_landed_and_its_sql_planned(env):
    s3 = FakeS3()
    result = handlers.check_release(
        {"run_id": "r"},
        s3=s3,
        ssm=FakeSSM("15"),
        listing=lambda: LISTING,
        download=lambda url, path: _release_zip(path),
    )
    assert (result["new_release"], result["version"], result["loaded"]) == (True, 16, 15)
    assert s3.uploads == [
        ("prl-sustainability-1-us-east-1", "landing/eea/version=16/air_releases.csv.gz", 4)
    ]
    assert result["landing_path"] == (
        "s3://prl-sustainability-1-us-east-1/landing/eea/version=16/air_releases.csv.gz"
    )
    statements = result["statements"].split("\n;\n")
    assert [s.split()[0] for s in statements] == ["CREATE", "MERGE", "INSERT"]
    assert "glue_catalog.sustainability.air_releases" in statements[1]


def test_the_loaded_release_is_not_downloaded_again(env):
    def no_download(url, path):
        raise AssertionError("downloaded an already loaded release")

    result = handlers.check_release(
        {"run_id": "r"},
        s3=FakeS3(),
        ssm=FakeSSM("16"),
        listing=lambda: LISTING,
        download=no_download,
    )
    assert result == {"new_release": False, "version": 16, "loaded": 16}
