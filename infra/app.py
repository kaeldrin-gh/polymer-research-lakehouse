"""CDK app: `cdk synth` runs this module (see cdk.json)."""

from __future__ import annotations

import os

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

from infra import config
from infra.stacks.data_lake import DataLakeStack
from infra.stacks.github_deploy import GitHubDeployStack


def build_app() -> cdk.App:
    app = cdk.App()
    # The account comes from the credentials at deploy time; synth and the
    # tests run without any.
    env = cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=config.REGION)
    tags = {"project": config.PROJECT}

    GitHubDeployStack(app, "GitHubDeploy", env=env, tags=tags)
    DataLakeStack(app, "DataLake", env=env, tags=tags)

    # Security rules from cdk-nag: a finding that is not acknowledged with a
    # written reason fails the synth.
    cdk.Validations.of(app).add_plugins(AwsSolutionsChecks(app))
    return app


if __name__ == "__main__":
    build_app().synth()
