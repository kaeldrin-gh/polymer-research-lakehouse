from __future__ import annotations

import json

import pytest
from aws_cdk import assertions


@pytest.fixture(scope="session")
def template(app):
    return assertions.Template.from_stack(app.node.find_child("ProductsBuild"))


def _joined(value):
    if isinstance(value, str):
        return value
    if "Fn::Join" in value:
        return "".join(p if isinstance(p, str) else "<ref>" for p in value["Fn::Join"][1])
    return "<ref>"


def test_dbt_runs_as_one_fargate_task_that_step_functions_waits_for(template):
    (machine,) = template.find_resources("AWS::StepFunctions::StateMachine").values()
    parts = machine["Properties"]["DefinitionString"]["Fn::Join"][1]
    definition = json.loads("".join(p if isinstance(p, str) else "REF" for p in parts))
    run = definition["States"]["RunDbt"]
    assert definition["StartAt"] == "RunDbt"
    assert run["Resource"] == "arn:aws:states:::ecs:runTask.sync"
    network = run["Parameters"]["NetworkConfiguration"]["AwsvpcConfiguration"]
    # Public subnet and IP instead of a NAT gateway.
    assert network["AssignPublicIp"] == "ENABLED"
    assert run["Parameters"]["LaunchType"] == "FARGATE"


def test_the_network_costs_nothing_while_idle(template):
    assert template.find_resources("AWS::EC2::NatGateway") == {}
    subnets = template.find_resources("AWS::EC2::Subnet").values()
    assert {s["Properties"]["MapPublicIpOnLaunch"] for s in subnets} == {True}


def test_the_task_accepts_no_inbound_traffic_and_only_calls_https(template):
    (group,) = template.find_resources("AWS::EC2::SecurityGroup").values()
    props = group["Properties"]
    assert "SecurityGroupIngress" not in props
    assert props["SecurityGroupEgress"] == [
        {
            "CidrIp": "0.0.0.0/0",
            "Description": "AWS APIs",
            "FromPort": 443,
            "IpProtocol": "tcp",
            "ToPort": 443,
        }
    ]


def test_the_task_is_small(template):
    (task,) = template.find_resources("AWS::ECS::TaskDefinition").values()
    props = task["Properties"]
    assert (props["Cpu"], props["Memory"]) == ("512", "1024")
    assert props["RequiresCompatibilities"] == ["FARGATE"]
    (container,) = props["ContainerDefinitions"]
    env = {e["Name"] for e in container["Environment"]}
    assert env == {"AWS_ACCOUNT_ID", "AWS_DEFAULT_REGION"}


def _statements(template, prefix):
    for logical_id, policy in template.find_resources("AWS::IAM::Policy").items():
        if logical_id.startswith(prefix):
            yield from policy["Properties"]["PolicyDocument"]["Statement"]


def _resources(statement):
    listed = statement["Resource"]
    return [_joined(r) for r in (listed if isinstance(listed, list) else [listed])]


def test_dbt_writes_only_under_each_buckets_dbt_prefix(template):
    for statement in _statements(template, "DbtTaskRole"):
        actions = statement["Action"]
        actions = actions if isinstance(actions, list) else [actions]
        if any(a in ("s3:PutObject", "s3:DeleteObject") for a in actions):
            for resource in _resources(statement):
                assert resource.endswith("/dbt/*") or resource.startswith(
                    "arn:aws:s3:::prl-athena-results-"
                ), resource


def test_dbt_manages_tables_only_in_the_dbt_databases(template):
    tables = set()
    for statement in _statements(template, "DbtTaskRole"):
        tables |= {r for r in _resources(statement) if ":table/" in r}
    assert {t.split(":table/")[1] for t in tables} == {
        "research/*",
        "research_staging/*",
        "sustainability/*",
        "sustainability_staging/*",
        "products/*",
    }


def test_no_action_wildcards_and_star_only_where_aws_requires_it(template):
    for statement in _statements(template, ""):
        actions = statement["Action"]
        actions = actions if isinstance(actions, list) else [actions]
        assert not [a for a in actions if a.endswith("*")], actions
        if statement["Resource"] == "*":
            assert set(actions) <= {"ecr:GetAuthorizationToken"} or {
                a.split(":")[0] for a in actions
            } <= {"xray", "logs"}, actions


def test_the_schedule_is_off_until_the_first_run_is_checked(template):
    (schedule,) = template.find_resources("AWS::Scheduler::Schedule").values()
    assert schedule["Properties"]["State"] == "DISABLED"
    # After the daily feed at 05:15 UTC.
    assert schedule["Properties"]["ScheduleExpression"] == "cron(0 6 * * ? *)"
