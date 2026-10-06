# polymer-research-lakehouse

A small data mesh on AWS for polymer research and industrial sustainability
data. A research domain loads polymer and plastics publications from the
[OpenAlex](https://openalex.org) snapshot, a sustainability domain loads EEA
industrial emissions, and a shared data product joins them. Everything is
serverless and defined in AWS CDK.

**Status:** the research domain's load logic and orchestration are written and
tested (milestone M2 code); nothing runs on AWS yet, because the account opens
at M1. See the [design](docs/design.md) for the plan.

**Stack:** Python · AWS CDK · S3 · Glue Data Catalog · Athena · Iceberg ·
Lambda · Step Functions · EventBridge Scheduler · dbt · DuckDB ·
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
- `GitHubDeploy` stack: an OIDC role that only this repository's `main` branch
  can assume, and that can only assume the CDK bootstrap roles. No access keys.
- dbt project (`dbt/`): staging and intermediate views, and the research data
  products `works_by_country_year` and `topic_trends` with enforced contracts,
  data tests and unit tests. It runs on DuckDB with a committed, seeded sample
  of 3,000 real OpenAlex works (`sample/`, CC0) and will run on Athena from M3.
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
data is CC BY 4.0 © European Environment Agency.
