"""Names and limits shared by the stacks and the tests."""

from __future__ import annotations

PROJECT = "polymer-research-lakehouse"
# us-east-1 holds the public OpenAlex bucket, so Athena reads it without
# cross-region transfer (see docs/design.md, Architecture).
REGION = "us-east-1"

GITHUB_REPOSITORY = "kaeldrin-gh/polymer-research-lakehouse"
GITHUB_DEPLOY_BRANCH = "main"

# Each domain owns one bucket and one Glue database. `products` holds the
# shared data products that join the domains.
DOMAINS = ("research", "sustainability", "products")
# External tables over the public OpenAlex snapshot; nothing is stored here.
SOURCE_DATABASE = "openalex_source"

OPENALEX_WORKS_LOCATION = "s3://openalex/data/parquet/works/"

# Athena guardrails: a query that would scan more than this fails instead of
# spending credits. The first backfill is estimated at about 130 GB.
ATHENA_SCAN_LIMIT_BYTES = 200 * 1000**3
ATHENA_RESULTS_RETENTION_DAYS = 7
LANDING_RETENTION_DAYS = 90
