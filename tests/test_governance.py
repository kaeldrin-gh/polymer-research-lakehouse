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
    grants = [
        g
        for g in _props(template, "AWS::LakeFormation::PrincipalPermissions")
        if "LFTagPolicy" in g["Resource"]
    ]
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


def _opt_ins(template):
    for resource in template.find_resources("Custom::AWS").values():
        create = resource["Properties"]["Create"]
        text = create if isinstance(create, str) else json.dumps(create)
        if "Fn::Join" in text:
            text = "".join(p if isinstance(p, str) else "<ref>" for p in create["Fn::Join"][1])
        assert "createLakeFormationOptIn" in text
        yield json.loads(text)["parameters"]["Resource"]


def test_the_reader_is_opted_in_on_every_database_and_all_its_tables(template):
    databases, tables = set(), set()
    for resource in _opt_ins(template):
        if "Database" in resource:
            databases.add(resource["Database"]["Name"])
        else:
            assert resource["Table"]["TableWildcard"] == {}
            tables.add(resource["Table"]["DatabaseName"])
    # A database opt-in alone does not cover its tables.
    assert databases == tables == set(DATABASE_TAGS)


def test_locations_register_one_at_a_time(template):
    # Parallel registrations race on the service-linked role's S3 policy.
    locations = template.find_resources("AWS::LakeFormation::Resource")
    depends = [set(r.get("DependsOn", [])) & set(locations) for r in locations.values()]
    assert sorted(len(d) for d in depends) == [0, 1, 1]


def test_only_table_writing_pipeline_roles_get_data_location_access(template):
    # A registered location needs Lake Formation's data location access to
    # create tables in it, also for IAM principals in hybrid mode.
    grants = [
        g
        for g in _props(template, "AWS::LakeFormation::PrincipalPermissions")
        if "DataLocation" in g["Resource"]
    ]
    per_bucket = {}
    for grant in grants:
        assert grant["Permissions"] == ["DATA_LOCATION_ACCESS"]
        assert grant["PermissionsWithGrantOption"] == []
        arn = "".join(
            p if isinstance(p, str) else "<ref>"
            for p in grant["Resource"]["DataLocation"]["ResourceArn"]["Fn::Join"][1]
        )
        bucket = arn.removeprefix("arn:aws:s3:::prl-").split("-")[0]
        per_bucket[bucket] = per_bucket.get(bucket, 0) + 1
        # The reader reads through Lake Formation and never creates tables.
        assert "ProductReaderRole" not in json.dumps(grant["Principal"])
    # research: snapshot load, daily feed, dbt; sustainability: Glue job,
    # dbt; products: dbt.
    assert per_bucket == {"research": 3, "sustainability": 2, "products": 1}
