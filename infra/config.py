"""Names and limits shared by the stacks and the tests."""

from __future__ import annotations

PROJECT = "polymer-research-lakehouse"
# us-east-1 holds the public OpenAlex bucket, so Athena reads it without
# cross-region transfer (see docs/design.md, Architecture).
REGION = "us-east-1"

GITHUB_REPOSITORY = "kaeldrin-gh/polymer-research-lakehouse"
GITHUB_DEPLOY_BRANCH = "main"
# GitHub's immutable OIDC subject names the owner and repository with their
# numeric IDs as well, so a deleted and re-created repository of the same name
# cannot deploy. From `gh api repos/kaeldrin-gh/polymer-research-lakehouse`.
GITHUB_OWNER_ID = 76854761
GITHUB_REPOSITORY_ID = 1406366454


def github_oidc_subject() -> str:
    owner, repository = GITHUB_REPOSITORY.split("/")
    return (
        f"repo:{owner}@{GITHUB_OWNER_ID}/{repository}@{GITHUB_REPOSITORY_ID}"
        f":ref:refs/heads/{GITHUB_DEPLOY_BRANCH}"
    )


# Each domain owns one bucket and one Glue database. `products` holds the
# shared data products that join the domains.
DOMAINS = ("research", "sustainability", "products")
RESULTS_BUCKET_KEY = "athena-results"


def bucket_name(key: str, account: str, region: str) -> str:
    """Deterministic bucket names, so IAM policies and their review read as
    plain ARNs. The account ID makes them globally unique."""
    return f"prl-{key}-{account}-{region}"


# External tables over the public OpenAlex snapshot; nothing is stored here.
SOURCE_DATABASE = "openalex_source"
# dbt staging and intermediate views (dbt/dbt_project.yml); products go to the
# domain database itself.
STAGING_DATABASES = ("research_staging", "sustainability_staging")

OPENALEX_WORKS_LOCATION = "s3://openalex/data/parquet/works/"

# Athena guardrails: a query that would scan more than this fails instead of
# spending credits. The first backfill is estimated at about 130 GB.
ATHENA_SCAN_LIMIT_BYTES = 200 * 1000**3
ATHENA_RESULTS_RETENTION_DAYS = 7
LANDING_RETENTION_DAYS = 90
STAGING_RETENTION_DAYS = 7

# Scheduled loads stay off until the first runs have been checked by hand.
SCHEDULES_ENABLED = False
