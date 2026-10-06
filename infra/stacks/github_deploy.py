"""Keyless deploys from GitHub Actions.

GitHub's OIDC token is exchanged for a short-lived role session. The role can
do one thing: assume the roles that `cdk bootstrap` created, which then deploy
the stacks. No access keys exist anywhere.

This stack is deployed once by hand (see docs/operations.md); CI deploys the
rest.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from constructs import Construct

from infra import config
from infra.stacks.pipeline_base import nag_account

GITHUB_OIDC_URL = "https://token.actions.githubusercontent.com"
DEPLOY_ROLE_NAME = f"{config.PROJECT}-github-deploy"
# The qualifier `cdk bootstrap` uses unless told otherwise.
CDK_QUALIFIER = "hnb659fds"


class GitHubDeployStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        provider = iam.OidcProviderNative(
            self,
            "GitHubOidcProvider",
            url=GITHUB_OIDC_URL,
            client_ids=["sts.amazonaws.com"],
        )

        self.role = iam.Role(
            self,
            "DeployRole",
            role_name=DEPLOY_ROLE_NAME,
            description=(
                f"GitHub Actions deploys from "
                f"{config.GITHUB_REPOSITORY}@{config.GITHUB_DEPLOY_BRANCH}."
            ),
            max_session_duration=cdk.Duration.hours(1),
            assumed_by=iam.WebIdentityPrincipal(
                provider.oidc_provider_arn,
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        "token.actions.githubusercontent.com:sub": (
                            f"repo:{config.GITHUB_REPOSITORY}:ref:refs/heads/{config.GITHUB_DEPLOY_BRANCH}"
                        ),
                    }
                },
            ),
        )
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole"],
                resources=[
                    f"arn:aws:iam::{self.account}:role/cdk-{CDK_QUALIFIER}-*-{self.account}-{self.region}"
                ],
            )
        )
        cdk.Validations.of(self.role).acknowledge(
            cdk.Acknowledgment(
                id=(
                    "AwsSolutions::AwsSolutions-IAM5[Resource::arn:aws:iam::"
                    f"{nag_account(self)}:role/cdk-{CDK_QUALIFIER}-*-{nag_account(self)}-"
                    f"{self.region}]"
                ),
                reason=(
                    "The wildcard matches only the CDK bootstrap roles of this account and "
                    "region (deploy, file and image publishing, lookup)."
                ),
            )
        )

        cdk.CfnOutput(self, "DeployRoleArn", value=self.role.role_arn)
