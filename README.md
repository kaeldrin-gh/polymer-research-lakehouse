# polymer-research-lakehouse

A small data mesh on AWS for polymer research and industrial sustainability
data. A research domain loads polymer and plastics publications from the
[OpenAlex](https://openalex.org) snapshot, a sustainability domain loads EEA
industrial emissions, and a shared data product joins them. Everything is
serverless and defined in AWS CDK.

**Status:** milestone M0. The CDK skeleton, its tests and CI exist; nothing
runs on AWS yet. See the [design](docs/design.md) for the plan.

**Stack:** Python · AWS CDK · S3 · Glue Data Catalog · Athena · Iceberg ·
GitHub Actions (OIDC) · uv · ruff

## What exists now

- `DataLake` stack: one private, encrypted S3 bucket and one Glue database per
  domain; an Athena workgroup that fails any query scanning more than 200 GB;
  an external table over the public OpenAlex snapshot with partition
  projection, exposing no abstracts and no author identities.
- `GitHubDeploy` stack: an OIDC role that only this repository's `main` branch
  can assume, and that can only assume the CDK bootstrap roles. No access keys.
- Tests: CDK assertion tests and the cdk-nag AWS Solutions rules, which fail
  the synth on any finding without a written reason.

## Run locally

```bash
uv sync
npm ci

uv run pytest -q                 # tests, no AWS account needed
uv run ruff check . && uv run ruff format --check .
npx cdk synth --quiet            # CloudFormation templates in cdk.out/
```

Account setup and deploys: [docs/operations.md](docs/operations.md).

## License

MIT, see [LICENSE](LICENSE). OpenAlex data is CC0. EEA industrial reporting
data is CC BY 4.0 © European Environment Agency.
