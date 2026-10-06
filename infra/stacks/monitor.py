"""A read-only role for the run watch.

The scheduled loads run on AWS, not in GitHub Actions, so a failed run shows
up nowhere by itself. A daily GitHub workflow assumes this role through OIDC
and reads the latest run of each state machine; on a failure it opens a
GitHub issue. The role can list runs and read their status, nothing else: no
data, no logs, no way to start or stop anything.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from constructs import Construct

from infra import config
from infra.stacks.pipeline_base import PipelineStack

MONITOR_ROLE_NAME = f"{config.PROJECT}-monitor"


class MonitorStack(PipelineStack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.role = iam.Role(
            self,
            "MonitorRole",
            role_name=MONITOR_ROLE_NAME,
            description="Reads the status of the pipeline runs, nothing else.",
            assumed_by=iam.WebIdentityPrincipal(
                f"arn:aws:iam::{self.account}:oidc-provider/token.actions.githubusercontent.com",
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        "token.actions.githubusercontent.com:sub": config.github_oidc_subject(),
                    }
                },
            ),
            max_session_duration=cdk.Duration.hours(1),
        )
        machines = [
            self._states_arn("stateMachine", f"{p}*") for p in config.WATCHED_STATE_MACHINES
        ]
        executions = [
            self._states_arn("execution", f"{p}*:*") for p in config.WATCHED_STATE_MACHINES
        ]
        for statement in [
            iam.PolicyStatement(actions=["states:ListStateMachines"], resources=["*"]),
            iam.PolicyStatement(actions=["states:ListExecutions"], resources=machines),
            iam.PolicyStatement(actions=["states:DescribeExecution"], resources=executions),
        ]:
            self.role.add_to_policy(statement)
        self._acknowledge_wildcards(
            self.role,
            {
                "*": "states:ListStateMachines has no resource-level permissions; it returns "
                "names only.",
                **{
                    self._nag_arn(arn): "The state machines CDK names after this construct "
                    "ID, and their runs; read-only."
                    for arn in machines + executions
                },
            },
        )

        cdk.CfnOutput(self, "MonitorRoleArn", value=self.role.role_arn)

    def _states_arn(self, kind: str, name: str) -> str:
        return f"arn:aws:states:{self.region}:{self.account}:{kind}:{name}"
