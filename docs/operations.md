# Operations

## Set up the AWS account (once, milestone M1)

The free account plan runs for six months from sign-up, so create the account
only when M1 starts.

### Create and secure the account

1. Sign up at [aws.amazon.com](https://aws.amazon.com) and choose the **Free**
   account plan.
2. Sign in as the root user.
3. Add MFA to the root user (IAM > Security credentials).
4. Do not create root access keys.
5. Open IAM Identity Center in `us-east-1` and enable it.
6. Create a user for yourself in IAM Identity Center.
7. Create a permission set from the `AdministratorAccess` managed policy.
8. Assign the user and the permission set to the account.
9. Sign out of the root user.

### Configure the local CLI

1. Install the AWS CLI v2.
2. Run `aws configure sso` and name the profile `prl`.
3. Run `aws sso login --profile prl`.

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
6. Push to `main`. CI now deploys the `DataLake` stack.

`GitHubDeploy` is never deployed from CI, so CI cannot change who may assume
its role.

## Without an AWS account

Before M1 and after the free plan ends, CI runs every check except the deploy,
which it skips with a notice.
