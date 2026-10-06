# polymer-research-lakehouse

A small data mesh on AWS for polymer research and industrial sustainability
data. A research domain loads polymer and plastics publications from the
[OpenAlex](https://openalex.org) snapshot, a sustainability domain loads EEA
industrial emissions, and a shared data product joins them. Everything is
serverless and defined in AWS CDK.

**Status:** both domains' loads, the dbt data products and the shared product
are written and tested without AWS; nothing runs on AWS yet, because the
account opens at M1. See the [design](docs/design.md) for the plan.

**Stack:** Python · PySpark · AWS CDK · S3 · Glue (Data Catalog, Spark jobs) ·
Athena · Iceberg · Lambda · Step Functions · ECS Fargate · ECR · Docker ·
EventBridge Scheduler · Lake Formation · dbt (Athena, DuckDB) ·
GitHub Actions (OIDC) · uv · ruff

## What exists now

- `DataLake` stack: one private, encrypted S3 bucket and one Glue database per
  domain; an Athena workgroup that fails any query scanning more than 200 GB;
  an external table over the public OpenAlex snapshot with partition
  projection, exposing no abstracts and no author identities.
- `ResearchPipeline` stack: two Step Functions state machines.
  - **Snapshot load** (weekly check): a Lambda compares the OpenAlex release
    date with a watermark; for a new release, Athena stages the polymer works
    from the changed partitions only, MERGEs them into the Iceberg table
    `research.works` (newer `updated_date` wins; works that left the subfield
    are removed), applies OpenAlex's deletions, compacts, and moves the
    watermark last, so a failed run is retried from the same point.
  - **Daily feed**: a Lambda lands the last 30 days of polymer publications
    from the OpenAlex API as JSON lines (about 1,700 works, ten API calls), and
    the same MERGE applies them.
  - The SQL is built and unit-tested in `src/research/`; every value in it is
    validated. Every IAM permission is written out; schedules stay disabled
    until the first runs are checked.
- `SustainabilityPipeline` stack: a Lambda finds the newest EEA industrial
  reporting release on the EEA's public WebDAV share, downloads it, and lands
  the air releases without facility names or places (372,178 rows, about
  10 seconds). A Glue PySpark job (Glue 5, Flex, 2 workers) applies them to
  the Iceberg table `sustainability.air_releases` as SCD Type 2: every value
  keeps the release versions it was valid in, so EEA revisions stay visible.
  The SQL is built in `src/sustainability/` and tested against DuckDB.
- `ProductsBuild` stack: dbt on Athena as an ECS Fargate task. CDK builds the
  image (`docker/dbt/`, versions pinned from `uv.lock`) and pushes it to ECR;
  a state machine runs the task and waits for it. The task runs in a VPC with
  public subnets only and no NAT gateway, accepts no inbound traffic, and can
  write only under each domain bucket's `dbt/` prefix.
- `Governance` stack: Lake Formation in hybrid access mode. Tags `domain` and
  `tier` on every database; a `product-reader` role sees `tier=product` tables
  only, with file access vended by Lake Formation and no S3 permission of its
  own. Staging and raw tables are refused by Lake Formation. The pipeline
  roles stay on IAM, with data location access only on the buckets they
  create tables in.
- `GitHubDeploy` stack: an OIDC role that only this repository's `main` branch
  can assume, and that can only assume the CDK bootstrap roles. No access keys.
- dbt project (`dbt/`): staging views per domain and the data products, all
  with enforced contracts, data tests and unit tests:
  - research: `works_by_country_year`, `topic_trends` (without OpenAlex's
    xpac works, as on openalex.org; `research.works` keeps them, flagged);
  - sustainability: `chemical_sector_by_country_year` (with polymer
    production plants, E-PRTR 4(a)(viii), separately), `air_release_revisions`;
  - shared: `research_vs_emissions`, which joins the two domains' products
    only, never their raw tables.

  It runs on DuckDB with committed samples of real data in CI (`sample/`:
  3,000 OpenAlex works, CC0; 27,695 chemical industry rows of EEA release 16,
  CC BY 4.0) and on Athena against the full data, each domain's product
  tables in that domain's bucket.
- Tests: Python unit tests, CDK assertion tests, and the cdk-nag AWS Solutions
  rules, which fail the synth on any finding without a written reason.

## Run locally

```bash
uv sync
npm ci

uv run pytest -q                 # tests, no AWS account needed
uv run ruff check . && uv run ruff format --check .
npx cdk synth --quiet            # CloudFormation templates in cdk.out/

uv run python scripts/load_sample.py                      # sample into DuckDB
uv run dbt build --project-dir dbt --profiles-dir dbt     # models and tests
```

Account setup and deploys: [docs/operations.md](docs/operations.md).

## License

MIT, see [LICENSE](LICENSE). OpenAlex data is CC0. EEA industrial reporting
data (including `sample/sustainability_air_releases.parquet`) is CC BY 4.0
© European Environment Agency.
