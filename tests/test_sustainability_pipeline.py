from __future__ import annotations

import json

import pytest
from aws_cdk import assertions

from sustainability import handlers


@pytest.fixture(scope="session")
def template(app):
    return assertions.Template.from_stack(app.node.find_child("SustainabilityPipeline"))


def _definition(template):
    (machine,) = template.find_resources("AWS::StepFunctions::StateMachine").values()
    parts = machine["Properties"]["DefinitionString"]["Fn::Join"][1]
    return json.loads("".join(p if isinstance(p, str) else "REF" for p in parts))


def _path(definition):
    names, name = [], definition["StartAt"]
    while name:
        names.append(name)
        state = definition["States"][name]
        name = state["Choices"][0]["Next"] if state["Type"] == "Choice" else state.get("Next")
    return names


def test_a_release_is_planned_applied_by_glue_then_the_watermark_moves(template):
    definition = _definition(template)
    assert _path(definition) == [
        "PlanRelease",
        "NewRelease?",
        "ApplyRelease",
        # Last, so a failed job is simply retried for the same release.
        "MoveVersion",
        "ReleaseApplied",
    ]
    apply = definition["States"]["ApplyRelease"]
    assert apply["Resource"] == "arn:aws:states:::glue:startJobRun.sync"
    assert apply["Parameters"]["Arguments"] == {
        "--landing_path.$": "$.plan.landing_path",
        "--statements.$": "$.plan.statements",
    }
    assert definition["States"]["MoveVersion"]["Parameters"]["Value.$"] == (
        "States.Format('{}', $.plan.version)"
    )


def test_glue_job_is_small_cheap_and_iceberg_ready(template):
    (job,) = template.find_resources("AWS::Glue::Job").values()
    props = job["Properties"]
    assert props["GlueVersion"] == "5.0"
    assert props["ExecutionClass"] == "FLEX"
    assert (props["WorkerType"], props["NumberOfWorkers"]) == ("G.1X", 2)
    assert props["Timeout"] == 30
    assert props["MaxRetries"] == 0
    args = props["DefaultArguments"]
    assert args["--datalake-formats"] == "iceberg"
    assert args["--target"] == handlers.GLUE_TABLE
    conf = "".join(p if isinstance(p, str) else "<ref>" for p in args["--conf"]["Fn::Join"][1])
    assert (
        "spark.sql.catalog.glue_catalog.catalog-impl=org.apache.iceberg.aws.glue.GlueCatalog"
        in (conf)
    )


def _statements(template, role_prefix):
    for logical_id, policy in template.find_resources("AWS::IAM::Policy").items():
        if logical_id.startswith(role_prefix):
            yield from policy["Properties"]["PolicyDocument"]["Statement"]


def _joined(resource):
    if isinstance(resource, str):
        return resource
    return "".join(p if isinstance(p, str) else "<ref>" for p in resource["Fn::Join"][1])


def test_glue_job_touches_only_its_own_bucket_database_and_logs(template):
    resources = []
    for statement in _statements(template, "GlueJobRole"):
        listed = statement["Resource"]
        resources += [_joined(r) for r in (listed if isinstance(listed, list) else [listed])]
    for resource in resources:
        assert (
            resource.startswith("arn:aws:s3:::prl-sustainability-")
            or resource.startswith("arn:aws:glue:us-east-1:<ref>:")
            or resource.startswith("arn:aws:logs:us-east-1:<ref>:log-group:/aws-glue/")
            # The job script, by its exact key in the CDK asset bucket.
            or (resource.startswith("arn:aws:s3:::<ref>/") and resource.endswith(".py"))
        ), resource
    glue = [r for r in resources if r.startswith("arn:aws:glue")]
    assert sorted(glue) == [
        "arn:aws:glue:us-east-1:<ref>:catalog",
        "arn:aws:glue:us-east-1:<ref>:database/sustainability",
        "arn:aws:glue:us-east-1:<ref>:table/sustainability/*",
    ]


def test_no_action_wildcards_and_no_account_wide_writes(template):
    for statement in _statements(template, ""):
        actions = statement["Action"]
        actions = actions if isinstance(actions, list) else [actions]
        assert not [a for a in actions if a.endswith("*")], actions
        if statement["Resource"] == "*":
            assert {a.split(":")[0] for a in actions} <= {"xray", "logs"}, actions


def test_the_release_lambda_has_room_for_the_zip(template):
    (function,) = template.find_resources("AWS::Lambda::Function").values()
    props = function["Properties"]
    assert props["Handler"] == "sustainability.handlers.check_release"
    assert props["MemorySize"] == 512
    assert props["EphemeralStorage"] == {"Size": 1024}


def test_the_schedule_is_off_until_the_first_run_is_checked(template):
    (schedule,) = template.find_resources("AWS::Scheduler::Schedule").values()
    assert schedule["Properties"]["State"] == "DISABLED"
    assert schedule["Properties"]["ScheduleExpression"] == "cron(0 7 ? * MON *)"
