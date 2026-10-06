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
2. Bootstrap CDK:

   ```bash
   npx cdk bootstrap --profile prl
   ```

3. Deploy the GitHub deploy role:

   ```bash
   npx cdk deploy GitHubDeploy --profile prl
   ```

4. Copy the `DeployRoleArn` output.
5. In the GitHub repository, add the secret `AWS_DEPLOY_ROLE_ARN` with that
   value (Settings > Secrets and variables > Actions).
6. Push to `main`. CI now deploys `DataLake`, `ResearchPipeline` and
   `SustainabilityPipeline`.

`GitHubDeploy` is never deployed from CI, so CI cannot change who may assume
its role.

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

## Without an AWS account

Before M1 and after the free plan ends, CI runs every check except the deploy,
which it skips with a notice.
