from __future__ import annotations

import json

import pytest
from aws_cdk import assertions

from infra import config
from infra.stacks.governance import DATABASE_TAGS, READER_ROLE_NAME


@pytest.fixture(scope="session")
def template(app):
    return assertions.Template.from_stack(app.node.find_child("Governance"))


def _props(template, type_):
    return [r["Properties"] for r in template.find_resources(type_).values()]


def test_settings_add_admins_without_switching_iam_access_off(template):
    (settings,) = _props(template, "AWS::LakeFormation::DataLakeSettings")
    # APPEND keeps whoever is already an administrator.
    assert settings["MutationType"] == "APPEND"
    iam_default = [
        {
            "Principal": {"DataLakePrincipalIdentifier": "IAM_ALLOWED_PRINCIPALS"},
            "Permissions": ["ALL"],
        }
    ]
    # New databases and tables stay under IAM, so the pipelines keep working.
    assert settings["CreateDatabaseDefaultPermissions"] == iam_default
    assert settings["CreateTableDefaultPermissions"] == iam_default


def test_two_tags_describe_domain_and_tier(template):
    tags = {
        t["TagKey"]: sorted(t["TagValues"]) for t in _props(template, "AWS::LakeFormation::Tag")
    }
    assert tags == {
        "domain": ["research", "shared", "sustainability"],
        "tier": ["product", "raw", "staging"],
    }


def test_every_project_database_is_tagged(template):
    tagged = {}
    for association in _props(template, "AWS::LakeFormation::TagAssociation"):
        database = association["Resource"]["Database"]["Name"]
        tagged[database] = {t["TagKey"]: t["TagValues"][0] for t in association["LFTags"]}
    assert tagged == DATABASE_TAGS
    assert set(tagged) == {*config.DOMAINS, *config.STAGING_DATABASES, config.SOURCE_DATABASE}


def test_the_reader_is_granted_the_product_tier_only(template):
    grants = _props(template, "AWS::LakeFormation::PrincipalPermissions")
    assert len(grants) == 2
    for grant in grants:
        policy = grant["Resource"]["LFTagPolicy"]
        assert policy["Expression"] == [{"TagKey": "tier", "TagValues": ["product"]}]
        # The reader cannot pass access on.
        assert grant["PermissionsWithGrantOption"] == []
    by_type = {g["Resource"]["LFTagPolicy"]["ResourceType"]: g["Permissions"] for g in grants}
    assert by_type == {"DATABASE": ["DESCRIBE"], "TABLE": ["SELECT", "DESCRIBE"]}


def test_the_reader_has_no_s3_access_to_the_data(template):
    roles = template.find_resources(
        "AWS::IAM::Role", {"Properties": {"RoleName": READER_ROLE_NAME}}
    )
    (logical_id,) = roles
    for logical, policy in template.find_resources("AWS::IAM::Policy").items():
        if not logical.startswith("ProductReaderRole"):
            continue
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            actions = (
                statement["Action"]
                if isinstance(statement["Action"], list)
                else [statement["Action"]]
            )
            if any(a.startswith("s3:") for a in actions):
                text = json.dumps(statement["Resource"])
                # Only its own query results; data files come from Lake Formation.
                assert "prl-athena-results-" in text
                for domain in config.DOMAINS:
                    assert f"prl-{domain}-" not in text


def test_the_domain_buckets_are_registered_in_hybrid_mode(template):
    locations = _props(template, "AWS::LakeFormation::Resource")
    assert len(locations) == len(config.DOMAINS)
    assert {loc["HybridAccessEnabled"] for loc in locations} == {True}
    assert {loc["UseServiceLinkedRole"] for loc in locations} == {True}


def test_the_reader_is_opted_in_on_every_project_database(template):
    opted = set()
    for resource in template.find_resources("Custom::AWS").values():
        create = resource["Properties"]["Create"]
        text = create if isinstance(create, str) else json.dumps(create)
        assert "createLakeFormationOptIn" in text
        for database in DATABASE_TAGS:
            if f'\\"Name\\":\\"{database}\\"' in text or f'"Name":"{database}"' in text:
                opted.add(database)
    assert opted == set(DATABASE_TAGS)
