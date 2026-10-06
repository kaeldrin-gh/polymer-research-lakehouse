"""What every domain pipeline stack shares: Lambda functions with their own
roles and log groups, the planning step, logged and traced state machines,
and acknowledging cdk-nag's wildcard findings one by one with a reason."""

from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

SRC = Path(__file__).resolve().parents[2] / "src"
LOG_RETENTION = logs.RetentionDays.TWO_WEEKS


class PipelineStack(cdk.Stack):
    @staticmethod
    def _objects(bucket: str, prefix: str = "") -> str:
        return f"arn:aws:s3:::{bucket}/{prefix}*"

    def _acknowledge_wildcards(self, construct: Construct, findings: dict[str, str]) -> None:
        """Acknowledge cdk-nag IAM5 findings one by one, each with its reason."""
        for resource, reason in findings.items():
            cdk.Validations.of(construct).acknowledge(
                cdk.Acknowledgment(
                    id=f"AwsSolutions::AwsSolutions-IAM5[Resource::{resource}]", reason=reason
                )
            )

    def _nag_arn(self, arn: str) -> str:
        # How cdk-nag prints an ARN built from config: tokens become <...>.
        return arn.replace(self.account, "<AWS::AccountId>")

    def _glue_arn(self, resource: str) -> str:
        # Plain strings rather than format_arn, so the ARN reads the same with
        # or without the CLI's feature flags (the partition stays literal).
        return f"arn:aws:glue:{self.region}:{self.account}:{resource}"

    def _parameter_arn(self, name: str) -> str:
        return self.format_arn(service="ssm", resource="parameter", resource_name=name.lstrip("/"))

    def _logical(self, construct: Construct) -> str:
        return self.get_logical_id(construct.node.default_child)

    def _function(
        self,
        construct_id: str,
        *,
        handler: str,
        timeout: cdk.Duration,
        statements: list[iam.PolicyStatement],
        environment: dict[str, str],
        wildcards: dict[str, str] | None = None,
        memory_size: int = 256,
        ephemeral_storage_mib: int = 512,
    ) -> lambda_.Function:
        log_group = logs.LogGroup(
            self,
            f"{construct_id}Logs",
            retention=LOG_RETENTION,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        # An explicit role instead of the managed basic-execution policy: it
        # can write to its own log group and do nothing it isn't given.
        role = iam.Role(
            self, f"{construct_id}Role", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com")
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[f"{log_group.log_group_arn}:*"],
            )
        )
        for statement in statements:
            role.add_to_policy(statement)
        self._acknowledge_wildcards(
            role,
            {
                f"<{self._logical(log_group)}.Arn>:*": "Log streams are created at run time; "
                "the wildcard is limited to this function's own log group.",
                **(wildcards or {}),
            },
        )
        return lambda_.Function(
            self,
            construct_id,
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.ARM_64,
            handler=handler,
            code=lambda_.Code.from_asset(str(SRC), exclude=["**/__pycache__", "*.egg-info"]),
            timeout=timeout,
            memory_size=memory_size,
            ephemeral_storage_size=cdk.Size.mebibytes(ephemeral_storage_mib),
            role=role,
            log_group=log_group,
            environment=environment,
        )

    def _plan(self, construct_id: str, function: lambda_.Function) -> tasks.LambdaInvoke:
        """Invoke a planning Lambda; its answer lands in $.plan."""
        return tasks.LambdaInvoke(
            self,
            construct_id,
            lambda_function=function,
            payload=sfn.TaskInput.from_object({"run_id": sfn.JsonPath.execution_name}),
            payload_response_only=True,
            result_path="$.plan",
            retry_on_service_exceptions=True,
        )

    def _state_machine(
        self,
        construct_id: str,
        definition: sfn.IChainable,
        *,
        invoked: lambda_.Function,
        timeout: cdk.Duration,
    ) -> sfn.StateMachine:
        log_group = logs.LogGroup(
            self,
            f"{construct_id}Logs",
            retention=LOG_RETENTION,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        machine = sfn.StateMachine(
            self,
            construct_id,
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=timeout,
            tracing_enabled=True,
            logs=sfn.LogOptions(
                destination=log_group, level=sfn.LogLevel.ALL, include_execution_data=True
            ),
        )
        self._acknowledge_wildcards(
            machine.role,
            {
                "*": "Required by AWS: X-Ray tracing and CloudWatch Logs delivery actions "
                "do not support resource-level permissions.",
                f"<{self._logical(invoked)}.Arn>:*": "Invokes any version of its own "
                "planning function.",
            },
        )
        return machine
