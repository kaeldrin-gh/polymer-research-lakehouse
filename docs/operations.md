# Operations

## Set up the AWS account (once, milestone M1)

The free account plan runs for six months from sign-up, so create the account
only when M1 starts.

### Create and secure the account

Do not enable IAM Identity Center or AWS Organizations. Giving a person access
to the account through Identity Center needs an organization, and creating an
organization moves the account from the free plan to pay-as-you-go and ends
the free credits at once.

1. Sign up at [aws.amazon.com](https://aws.amazon.com) and choose the **Free**
   account plan.
2. Sign in as the root user.
3. Add MFA to the root user (IAM > Security credentials).
4. Do not create root access keys.
5. In IAM, create a user for yourself with console access.
6. Attach the `AdministratorAccess` managed policy to the user.
7. Sign out, sign in as that user, and add MFA to it.
8. Do not create access keys for the user.

### Configure the local CLI

The CLI gets short-lived credentials from your console sign-in
([`aws login`](https://aws.amazon.com/blogs/security/simplified-developer-access-to-aws-with-aws-login),
AWS CLI 2.32 or later); no access keys exist anywhere.

1. Install or update the AWS CLI v2 to 2.32 or later.
2. Run `aws login --profile prl --region us-east-1`.
3. Sign in as your IAM user in the browser window that opens.
4. Check the session with `aws sts get-caller-identity --profile prl`.

The session refreshes for up to 12 hours; run `aws login` again after that.

### Bootstrap CDK and the deploy role

1. Run `npm ci` and `uv sync`.
2. Create the CloudFormation execution policy from
   `infra/bootstrap/cfn-execution-policy.json` (replace `${ACCOUNT}` with the
   account ID first):

   ```bash
   aws iam create-policy --policy-name polymer-research-lakehouse-cfn-execution --policy-document file://cfn-execution-policy.json --profile prl
   ```

3. Bootstrap CDK with that policy instead of the default administrator
   access:

   ```bash
   npx cdk bootstrap --profile prl --cloudformation-execution-policies arn:aws:iam::<account>:policy/polymer-research-lakehouse-cfn-execution
   ```

   CloudFormation can then manage only this project's services, `prl-*`
   buckets and roles named after its stacks. A new AWS service in a stack
   needs a line in the policy first; update it with
   `aws iam create-policy-version --set-as-default`.

4. Deploy the GitHub deploy role:

   ```bash
   npx cdk deploy GitHubDeploy --profile prl
   ```

5. Copy the `DeployRoleArn` output.
6. In the GitHub repository, add the secret `AWS_DEPLOY_ROLE_ARN` with that
   value (Settings > Secrets and variables > Actions).
7. Push to `main`. CI now deploys `DataLake`, `ResearchPipeline`,
   `SustainabilityPipeline`, `ProductsBuild`, `Governance` and `Monitor`.

`GitHubDeploy` is never deployed from CI, so CI cannot change who may assume
its role.

### Connect the report and the run watch

Both workflows assume their own role through GitHub OIDC; without the
secret, each skips its AWS steps with a notice.

1. Print the reader role's ARN:

   ```bash
   aws cloudformation describe-stacks --stack-name Governance --query "Stacks[0].Outputs" --profile prl
   ```

2. Add it as the repository secret `AWS_READER_ROLE_ARN`.
3. Print the monitor role's ARN:

   ```bash
   aws cloudformation describe-stacks --stack-name Monitor --query "Stacks[0].Outputs" --profile prl
   ```

4. Add it as the repository secret `AWS_MONITOR_ROLE_ARN`.
5. Run the `report` and `watch` workflows once from the Actions tab.

## Lake Formation

### Make yourself a Lake Formation administrator

The stack adds the CDK deploy role as an administrator, not your IAM user, so
the Lake Formation console hides tags, grants and opt-ins from you until you
add yourself (once). The settings use APPEND, so a later deploy keeps you.

1. Open the Lake Formation console in `us-east-1`.
2. Open Administration > Administrative roles and tasks.
3. Add your IAM user as a data lake administrator.

### Check what the product reader can see

1. Assume the reader role:

   ```bash
   aws sts assume-role --profile prl --role-arn arn:aws:iam::<account>:role/polymer-research-lakehouse-product-reader --role-session-name check
   ```

2. Export the three credential values from the answer.
3. In Athena (workgroup `polymer-research-lakehouse`), query a product, for
   example `SELECT count(*) FROM products.research_vs_emissions`: it works.
4. Query a staging view, for example
   `SELECT count(*) FROM research_staging.stg_research__works`: Lake
   Formation refuses it.

## Rebuild research.works

Needed after a change to the table's columns: the MERGE only updates a row
when OpenAlex changed the work, so existing rows would keep the old shape. The
rebuild costs one backfill (48 GB scanned, about 0.24 USD, about 8 minutes).

1. Deploy the change (push to `main`).
2. Drop the table in Athena (workgroup `polymer-research-lakehouse`):

   ```sql
   DROP TABLE research.works
   ```

3. Delete the snapshot watermark:

   ```bash
   MSYS_NO_PATHCONV=1 aws ssm delete-parameter --name /polymer-research-lakehouse/research/snapshot-watermark --profile prl
   ```

4. Start the `SnapshotLoad` state machine. With no watermark it loads every
   partition up to the newest release.
5. Start the `DailyFeed` state machine, so recent works newer than the
   snapshot come back.

`MSYS_NO_PATHCONV=1` stops Git Bash on Windows from turning the parameter
name into a file path.

## When the run watch opens an issue

The `watch` workflow opens an issue labeled `run-failure` when a state
machine's newest run failed, timed out or was aborted, or when a schedule did
not fire in time. While that issue is open, later findings are added to it as
comments.

1. Open the Step Functions console in us-east-1 (N. Virginia).
2. Open the state machine the issue names, then its failed run.
3. Select the red step to read its error and cause.
4. Follow the step's link to its CloudWatch logs if the cause is unclear.
5. Fix the cause; push code changes to `main`.
6. Start the state machine again with the input `{}`.
7. Close the issue once the run succeeds.

A rerun is safe: each load moves its watermark last, so it starts from the
same point, and the daily feed reads a 30-day window, so a missed day is
covered by the next run. For a "did not fire in time" finding, check the
schedule's state in the EventBridge Scheduler console.

## Without an AWS account

Before M1 and after the free plan ends, CI runs every check except the deploy,
which it skips with a notice. The `report` workflow then builds from the
committed samples but publishes only pages built from Athena, so the live
page keeps its last build; the `watch` workflow skips its check.

Each workflow skips its AWS steps only while its secret is missing. When the
account closes, delete the secrets `AWS_DEPLOY_ROLE_ARN`,
`AWS_READER_ROLE_ARN` and `AWS_MONITOR_ROLE_ARN`, or the workflows fail on
the role they can no longer assume.
