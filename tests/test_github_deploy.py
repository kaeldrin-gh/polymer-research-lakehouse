from __future__ import annotations

from infra.stacks.github_deploy import DEPLOY_ROLE_NAME


def _deploy_role(template):
    roles = template.find_resources(
        "AWS::IAM::Role", {"Properties": {"RoleName": DEPLOY_ROLE_NAME}}
    )
    (role,) = roles.values()
    return role["Properties"]


def test_only_this_repository_main_branch_can_assume_the_role(github_deploy_template):
    (statement,) = _deploy_role(github_deploy_template)["AssumeRolePolicyDocument"]["Statement"]
    assert statement["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert statement["Condition"] == {
        "StringEquals": {
            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
            "token.actions.githubusercontent.com:sub": (
                "repo:kaeldrin-gh@76854761/polymer-research-lakehouse@1406366454"
                ":ref:refs/heads/main"
            ),
        }
    }


def test_role_can_only_assume_the_cdk_bootstrap_roles(github_deploy_template):
    policies = github_deploy_template.find_resources("AWS::IAM::Policy").values()
    statements = [s for p in policies for s in p["Properties"]["PolicyDocument"]["Statement"]]
    assert [s["Action"] for s in statements] == ["sts:AssumeRole"]
    (resource,) = [s["Resource"] for s in statements]
    joined = "".join(part if isinstance(part, str) else "<ref>" for part in resource["Fn::Join"][1])
    assert joined == "arn:aws:iam::<ref>:role/cdk-hnb659fds-*-<ref>-us-east-1"


def test_role_sessions_are_short(github_deploy_template):
    assert _deploy_role(github_deploy_template)["MaxSessionDuration"] == 3600


def test_no_access_keys_or_users_exist(github_deploy_template, data_lake_template):
    for template in (github_deploy_template, data_lake_template):
        assert template.find_resources("AWS::IAM::AccessKey") == {}
        assert template.find_resources("AWS::IAM::User") == {}
