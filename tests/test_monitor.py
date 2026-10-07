from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from aws_cdk import assertions

from infra import config
from infra.stacks.monitor import MONITOR_ROLE_NAME
from monitor import watch

NOW = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)
LIMITS = {"DailyFeed": 26, "SnapshotLoad": 192}


@pytest.fixture(scope="session")
def template(app):
    return assertions.Template.from_stack(app.node.find_child("Monitor"))


def _statements(template):
    policies = template.find_resources("AWS::IAM::Policy").values()
    return [s for p in policies for s in p["Properties"]["PolicyDocument"]["Statement"]]


def _arn(resource) -> str:
    parts = resource["Fn::Join"][1]
    return "".join(part if isinstance(part, str) else "<ref>" for part in parts)


def test_only_this_repository_main_branch_can_assume_the_role(template):
    (role,) = template.find_resources(
        "AWS::IAM::Role", {"Properties": {"RoleName": MONITOR_ROLE_NAME}}
    ).values()
    (statement,) = role["Properties"]["AssumeRolePolicyDocument"]["Statement"]
    assert statement["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert statement["Condition"]["StringEquals"] == {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": config.github_oidc_subject(),
    }
    assert role["Properties"]["MaxSessionDuration"] == 3600


def test_the_role_reads_run_status_and_nothing_else(template):
    statements = {s["Action"]: s["Resource"] for s in _statements(template)}
    assert set(statements) == {
        "states:ListStateMachines",
        "states:ListExecutions",
        "states:DescribeExecution",
    }
    assert statements["states:ListStateMachines"] == "*"
    machines = sorted(_arn(r) for r in statements["states:ListExecutions"])
    assert machines == sorted(
        f"arn:aws:states:us-east-1:<ref>:stateMachine:{p}*" for p in config.WATCHED_STATE_MACHINES
    )
    runs = sorted(_arn(r) for r in statements["states:DescribeExecution"])
    assert runs == sorted(
        f"arn:aws:states:us-east-1:<ref>:execution:{p}*:*" for p in config.WATCHED_STATE_MACHINES
    )


def _run(status="SUCCEEDED", hours_ago=2, error=None):
    return {
        "name": "run-1",
        "status": status,
        "startDate": NOW - timedelta(hours=hours_ago),
        "error": error,
    }


def test_healthy_runs_report_nothing():
    latest = {"DailyFeed": _run(), "SnapshotLoad": _run(hours_ago=150)}
    assert watch.problems(latest, NOW, LIMITS) == []


def test_a_failed_run_is_reported_with_its_error_code():
    latest = {"DailyFeed": _run("FAILED", error="States.TaskFailed"), "SnapshotLoad": _run()}
    (line,) = watch.problems(latest, NOW, LIMITS)
    assert line == (
        "**DailyFeed**: run `run-1` FAILED, error `States.TaskFailed`, "
        "started 2026-10-07 06:00 UTC."
    )


def test_timed_out_and_aborted_runs_count_as_failures():
    latest = {"DailyFeed": _run("TIMED_OUT"), "SnapshotLoad": _run("ABORTED")}
    assert [line.split(":")[0] for line in watch.problems(latest, NOW, LIMITS)] == [
        "**DailyFeed**",
        "**SnapshotLoad**",
    ]


def test_a_schedule_that_stopped_firing_is_reported():
    latest = {"DailyFeed": _run(hours_ago=30), "SnapshotLoad": _run()}
    (line,) = watch.problems(latest, NOW, LIMITS)
    assert "more than 26 h ago" in line


def test_a_long_run_still_in_progress_is_not_reported():
    latest = {"DailyFeed": _run("RUNNING", hours_ago=30), "SnapshotLoad": _run()}
    assert watch.problems(latest, NOW, LIMITS) == []


def test_the_summary_logs_every_newest_run():
    latest = {"DailyFeed": _run(hours_ago=2.75), "SnapshotLoad": None}
    assert watch.summary(latest) == [
        "DailyFeed: SUCCEEDED, run run-1, started 2026-10-07 05:15 UTC",
        "SnapshotLoad: no run found",
    ]


def test_a_state_machine_without_runs_is_reported():
    (line,) = watch.problems({"DailyFeed": _run(), "SnapshotLoad": None}, NOW, LIMITS)
    assert line == "**SnapshotLoad**: no run found."


class _FakeStepFunctions:
    """Just enough of the boto3 client: two machines, one failed run."""

    def get_paginator(self, name):
        assert name == "list_state_machines"
        machines = [
            {"name": "DailyFeed9EF0BDFD-abc", "stateMachineArn": "arn:machine:daily"},
            {"name": "SomethingElse-xyz", "stateMachineArn": "arn:machine:other"},
        ]
        return type("Paginator", (), {"paginate": lambda self: [{"stateMachines": machines}]})()

    def list_executions(self, stateMachineArn, maxResults):  # noqa: N803 (boto3 names)
        assert (stateMachineArn, maxResults) == ("arn:machine:daily", 1)
        start = datetime(2026, 10, 7, 5, 15, tzinfo=UTC)
        return {
            "executions": [
                {"name": "r1", "status": "FAILED", "startDate": start, "executionArn": "arn:run"}
            ]
        }

    def describe_execution(self, executionArn):  # noqa: N803
        return {"error": "Athena.QueryFailed", "cause": "arn:aws:states:...:123456789012"}


def test_latest_runs_matches_by_prefix_and_keeps_only_the_error_code():
    latest = watch.latest_runs(_FakeStepFunctions(), ["DailyFeed", "SnapshotLoad"])
    assert latest["SnapshotLoad"] is None
    assert latest["DailyFeed"]["error"] == "Athena.QueryFailed"
    # The cause can carry ARNs (and so the account ID); it never reaches the report.
    assert "cause" not in latest["DailyFeed"]


def test_the_report_links_the_runbook_and_carries_no_arn():
    text = watch.report(["**DailyFeed**: no run found."], NOW)
    assert "- **DailyFeed**: no run found." in text
    assert watch.RUNBOOK in text
    assert "arn:" not in text
