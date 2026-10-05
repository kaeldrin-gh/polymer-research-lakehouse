from __future__ import annotations

import pytest
from aws_cdk import assertions

from infra.app import build_app


@pytest.fixture(scope="session")
def app():
    built = build_app()
    # Synthesizing runs the cdk-nag aspect, so its findings exist as annotations.
    built.synth()
    return built


@pytest.fixture(scope="session")
def data_lake(app):
    return app.node.find_child("DataLake")


@pytest.fixture(scope="session")
def github_deploy(app):
    return app.node.find_child("GitHubDeploy")


@pytest.fixture(scope="session")
def data_lake_template(data_lake):
    return assertions.Template.from_stack(data_lake)


@pytest.fixture(scope="session")
def github_deploy_template(github_deploy):
    return assertions.Template.from_stack(github_deploy)
