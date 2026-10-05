"""Lambda entry points. They read and write; the decisions live in the pure
modules. Step Functions runs the SQL they return (see
infra/stacks/research_pipeline.py)."""

from __future__ import annotations

import datetime as dt
import json
import os

from research import openalex_api, scope, sql
from research.manifest import parse_date, plan_snapshot


def _boto3_client(name: str):
    import boto3  # provided by the Lambda runtime

    return boto3.client(name)


def read_watermark(ssm, name: str) -> str:
    try:
        return ssm.get_parameter(Name=name)["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        return scope.INITIAL_WATERMARK


def check_release(event: dict, context=None, s3=None, ssm=None) -> dict:
    """Snapshot flow, step 1: is there a release newer than the watermark?"""
    s3 = s3 or _boto3_client("s3")
    ssm = ssm or _boto3_client("ssm")
    manifest = json.loads(
        s3.get_object(Bucket=scope.OPENALEX_BUCKET, Key=scope.MANIFEST_KEY)["Body"].read()
    )
    plan = plan_snapshot(manifest, read_watermark(ssm, os.environ["WATERMARK_PARAMETER"]))
    result = {
        "new_release": plan.new_release,
        "release_date": plan.release_date,
        "watermark": plan.watermark,
    }
    if plan.new_release:
        result["queries"] = sql.snapshot_queries(
            os.environ["RESEARCH_BUCKET"], event["run_id"], plan.watermark, plan.release_date
        )
    return result


def fetch_recent(event: dict, context=None, s3=None, fetch=None, today=None) -> dict:
    """Daily flow, step 1: land works published in the window as JSON lines."""
    s3 = s3 or _boto3_client("s3")
    fetch = fetch or openalex_api.http_get_json
    today = today or dt.datetime.now(dt.UTC).date()
    fetch_date = parse_date(today.isoformat())

    records = [
        openalex_api.to_landing_record(work)
        for work in openalex_api.iter_works(openalex_api.window_start(today), fetch)
    ]
    bucket = os.environ["RESEARCH_BUCKET"]
    key = f"{scope.API_LANDING_PREFIX}fetch_date={fetch_date}/works.jsonl"
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=openalex_api.to_json_lines(records).encode("utf-8"),
        ContentType="application/x-ndjson",
    )
    result = {"fetch_date": fetch_date, "count": len(records), "key": key}
    if records:
        result["queries"] = sql.api_queries(bucket, event["run_id"], fetch_date)
    return result
