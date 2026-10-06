"""Lambda entry point. It reads and writes; the decisions live in the pure
modules. Step Functions passes the result to the Glue job (see
infra/stacks/sustainability_pipeline.py)."""

from __future__ import annotations

import os
import tempfile

from sustainability import air_releases, eea_share, scd2_sql, scope

# Spark names the landing CSV view this inside the Glue job.
INCOMING_VIEW = "incoming"
GLUE_TABLE = f"glue_catalog.{scope.AIR_RELEASES}"


def _boto3_client(name: str):
    import boto3  # provided by the Lambda runtime

    return boto3.client(name)


def read_version(ssm, name: str) -> int:
    try:
        return int(ssm.get_parameter(Name=name)["Parameter"]["Value"])
    except ssm.exceptions.ParameterNotFound:
        return scope.INITIAL_VERSION


def check_release(
    event: dict, context=None, s3=None, ssm=None, listing=None, download=None
) -> dict:
    """If the EEA share has a newer release than the last one loaded, land its
    air releases in S3 and return the SQL that applies them."""
    s3 = s3 or _boto3_client("s3")
    ssm = ssm or _boto3_client("ssm")
    listing = listing or eea_share.fetch_listing
    download = download or eea_share.download

    loaded = read_version(ssm, os.environ["VERSION_PARAMETER"])
    release = eea_share.latest_release(eea_share.list_folder_names(listing()))
    if release is None:
        raise RuntimeError("no tabular release found in the EEA share; did the naming change?")
    result = {"new_release": release.version > loaded, "version": release.version, "loaded": loaded}
    if not result["new_release"]:
        return result

    bucket = os.environ["SUSTAINABILITY_BUCKET"]
    key = f"{scope.LANDING_PREFIX}version={release.version}/air_releases.csv.gz"
    with tempfile.TemporaryDirectory() as tmp:
        zip_path, landing_path = os.path.join(tmp, "release.zip"), os.path.join(tmp, "air.csv.gz")
        download(release.zip_url, zip_path)
        rows = air_releases.extract_landing_file(zip_path, landing_path)
        s3.upload_file(landing_path, bucket, key)

    result.update(
        {
            "folder": release.folder,
            "rows": rows,
            "landing_path": f"s3://{bucket}/{key}",
            # Glue job arguments are strings; the job splits on this marker.
            "statements": scd2_sql.STATEMENT_SEPARATOR.join(
                scd2_sql.release_statements(
                    GLUE_TABLE,
                    INCOMING_VIEW,
                    release.version,
                    f"s3://{bucket}/{scope.ICEBERG_PREFIX}",
                )
            ),
        }
    )
    return result
