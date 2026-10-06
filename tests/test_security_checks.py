"""cdk-nag (AWS Solutions rules) runs as a CDK validation plugin: synth fails
on any finding that is not acknowledged with a written reason."""

from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk import aws_s3 as s3
from cdk_nag import AwsSolutionsChecks


def test_the_app_has_no_unacknowledged_findings(app):
    # The `app` fixture has already synthesized; a finding would have raised.
    assert app.node.find_child("DataLake") is not None


def test_an_unacknowledged_finding_fails_the_synth():
    app = cdk.App()
    stack = cdk.Stack(app, "Insecure")
    # No access logs, no SSL enforcement: AwsSolutions-S1 and -S10.
    s3.Bucket(stack, "Bucket")
    cdk.Validations.of(app).add_plugins(AwsSolutionsChecks(app))
    with pytest.raises(RuntimeError, match="ValidationFailed") as failure:
        app.synth()
    assert "AwsSolutions-S1'" in str(failure.value)
    assert "AwsSolutions-S10'" in str(failure.value)


def test_the_app_also_synthesizes_with_a_real_account(monkeypatch):
    # With credentials, cdk-nag prints the account ID instead of the
    # <AWS::AccountId> placeholder; every acknowledgment must still match.
    from infra.app import build_app

    monkeypatch.setenv("CDK_DEFAULT_ACCOUNT", "123456789012")
    build_app().synth()
