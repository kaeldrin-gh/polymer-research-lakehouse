"""CDK app: `cdk synth` runs this module (see cdk.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

from infra import config
from infra.stacks.data_lake import DataLakeStack
from infra.stacks.github_deploy import GitHubDeployStack
from infra.stacks.research_pipeline import ResearchPipelineStack
from infra.stacks.sustainability_pipeline import SustainabilityPipelineStack

CDK_JSON = Path(__file__).resolve().parents[1] / "cdk.json"


def build_app() -> cdk.App:
    # The CLI passes cdk.json's context (feature flags) itself. Tests and
    # direct runs load it here, so they synthesize exactly what the CLI does.
    context = None if "CDK_CONTEXT_JSON" in os.environ else _cdk_json_context()
    app = cdk.App(context=context)
    # The account comes from the credentials at deploy time; synth and the
    # tests run without any.
    env = cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=config.REGION)
    tags = {"project": config.PROJECT}

    GitHubDeployStack(app, "GitHubDeploy", env=env, tags=tags)
    data_lake = DataLakeStack(app, "DataLake", env=env, tags=tags)
    pipeline = ResearchPipelineStack(app, "ResearchPipeline", env=env, tags=tags)
    pipeline.add_stack_dependency(data_lake)
    sustainability = SustainabilityPipelineStack(app, "SustainabilityPipeline", env=env, tags=tags)
    sustainability.add_stack_dependency(data_lake)

    # Security rules from cdk-nag: a finding that is not acknowledged with a
    # written reason fails the synth.
    cdk.Validations.of(app).add_plugins(AwsSolutionsChecks(app))
    return app


def _cdk_json_context() -> dict:
    return json.loads(CDK_JSON.read_text(encoding="utf-8"))["context"]


if __name__ == "__main__":
    build_app().synth()
