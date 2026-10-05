from __future__ import annotations

import json

import pytest

from infra import config


def _definition(template, machine_prefix):
    (machine,) = [
        r
        for logical_id, r in template.find_resources("AWS::StepFunctions::StateMachine").items()
        if logical_id.startswith(machine_prefix)
    ]
    parts = machine["Properties"]["DefinitionString"]["Fn::Join"][1]
    # References to other resources sit inside JSON strings; any text will do.
    return json.loads("".join(p if isinstance(p, str) else "REF" for p in parts))


def _path(definition):
    """State names from StartAt, following Next and the Choice's first branch."""
    names, name = [], definition["StartAt"]
    while name:
        names.append(name)
        state = definition["States"][name]
        name = state["Choices"][0]["Next"] if state["Type"] == "Choice" else state.get("Next")
    return names


def test_snapshot_load_runs_its_steps_in_order(research_pipeline_template):
    definition = _definition(research_pipeline_template, "SnapshotLoad")
    assert _path(definition) == [
        "PlanSnapshot",
        "NewRelease?",
        "SnapshotCreateWorks",
        "SnapshotDropStage",
        "SnapshotStage",
        "SnapshotMerge",
        "CopyDeletedIds",
        "ApplyDeletions",
        "SnapshotCleanupStage",
        "Optimize",
        "Vacuum",
        # Last, so a failed load is simply retried from the same watermark.
        "MoveWatermark",
        "SnapshotLoaded",
    ]
    assert definition["States"]["NewRelease?"]["Default"] == "NoNewRelease"


def test_daily_feed_runs_its_steps_in_order(research_pipeline_template):
    definition = _definition(research_pipeline_template, "DailyFeed")
    assert _path(definition) == [
        "FetchAndPlan",
        "AnyWorks?",
        "DailyCreateWorks",
        "DailyDropStage",
        "DailyStage",
        "DailyMerge",
        "DailyCleanupStage",
        "DailyLoaded",
    ]


@pytest.mark.parametrize("machine", ["SnapshotLoad", "DailyFeed"])
def test_athena_steps_wait_in_the_project_workgroup_and_keep_the_plan(
    research_pipeline_template, machine
):
    states = _definition(research_pipeline_template, machine)["States"].values()
    athena = [
        s for s in states if s.get("Resource", "").endswith("athena:startQueryExecution.sync")
    ]
    assert athena
    for state in athena:
        assert state["Parameters"]["WorkGroup"] == config.PROJECT
        assert state["Parameters"]["QueryString.$"].startswith("$.plan.queries.")
        # Results go to their own key, so $.plan survives every step.
        assert state["ResultPath"] == "$.last_query"


def test_state_machines_are_traced_and_fully_logged(research_pipeline_template):
    machines = research_pipeline_template.find_resources("AWS::StepFunctions::StateMachine")
    assert len(machines) == 2
    for machine in machines.values():
        props = machine["Properties"]
        assert props["TracingConfiguration"] == {"Enabled": True}
        assert props["LoggingConfiguration"]["Level"] == "ALL"


def test_schedules_are_off_until_the_first_runs_are_checked(research_pipeline_template):
    schedules = research_pipeline_template.find_resources("AWS::Scheduler::Schedule")
    assert len(schedules) == 2
    assert {s["Properties"]["State"] for s in schedules.values()} == {"DISABLED"}
    expressions = sorted(s["Properties"]["ScheduleExpression"] for s in schedules.values())
    assert expressions == ["cron(0 6 ? * THU *)", "cron(15 5 * * ? *)"]


def _statements(template):
    for policy in template.find_resources("AWS::IAM::Policy").values():
        yield from policy["Properties"]["PolicyDocument"]["Statement"]


def test_nothing_can_write_to_every_bucket_or_use_action_wildcards(research_pipeline_template):
    for statement in _statements(research_pipeline_template):
        actions = (
            statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        )
        assert not [a for a in actions if a.endswith("*")], actions
        if statement["Resource"] == "*":
            # Only actions AWS offers without resource-level permissions.
            assert {a.split(":")[0] for a in actions} <= {"xray", "logs"}, actions


def test_lambdas_run_on_the_latest_python_with_their_own_roles(research_pipeline_template):
    functions = research_pipeline_template.find_resources("AWS::Lambda::Function")
    handlers = sorted(f["Properties"]["Handler"] for f in functions.values())
    assert handlers == ["research.handlers.check_release", "research.handlers.fetch_recent"]
    for function in functions.values():
        props = function["Properties"]
        assert props["Runtime"] == "python3.14"
        assert props["Architectures"] == ["arm64"]
    # No AWS managed policies: every permission is written out.
    for role in research_pipeline_template.find_resources("AWS::IAM::Role").values():
        assert "ManagedPolicyArns" not in role["Properties"]
