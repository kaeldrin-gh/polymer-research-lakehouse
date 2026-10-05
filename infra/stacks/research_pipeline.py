"""Research domain loads: two Step Functions state machines.

- Snapshot: weekly, checks for a new quarterly OpenAlex release; if there is
  one, stages the changed polymer works, MERGEs them into the Iceberg table,
  applies deletions, compacts, and moves the watermark.
- Daily: lands recently published works from the OpenAlex API and MERGEs them.

A Lambda plans each run and returns the SQL (research.sql, unit-tested); Step
Functions runs it in Athena and waits for each statement.

Every permission is written out here. The CDK Athena task construct is not
used: without a result location of its own (the workgroup enforces one), it
grants S3 writes on every bucket in the account.
"""

from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_scheduler_targets as scheduler_targets
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

from infra import config
from research import scope as research_scope

SRC = Path(__file__).resolve().parents[2] / "src"
WATERMARK_PARAMETER = f"/{config.PROJECT}/research/snapshot-watermark"
LOG_RETENTION = logs.RetentionDays.TWO_WEEKS
ATHENA_RETRYABLE = ["Athena.TooManyRequestsException", "Athena.InternalServerException"]
OPENALEX = research_scope.OPENALEX_BUCKET


class ResearchPipelineStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.research_bucket = config.bucket_name("research", self.account, self.region)
        self.results_bucket = config.bucket_name(
            config.RESULTS_BUCKET_KEY, self.account, self.region
        )
        self.watermark_arn = self.format_arn(
            service="ssm", resource="parameter", resource_name=WATERMARK_PARAMETER.lstrip("/")
        )

        check_release = self._function(
            "CheckRelease",
            handler="research.handlers.check_release",
            timeout=cdk.Duration.seconds(60),
            statements=[
                iam.PolicyStatement(
                    actions=["s3:GetObject"],
                    resources=[f"arn:aws:s3:::{OPENALEX}/{research_scope.MANIFEST_KEY}"],
                ),
                iam.PolicyStatement(actions=["ssm:GetParameter"], resources=[self.watermark_arn]),
            ],
        )
        fetch_recent = self._function(
            "FetchRecent",
            handler="research.handlers.fetch_recent",
            timeout=cdk.Duration.minutes(5),
            statements=[
                iam.PolicyStatement(
                    actions=["s3:PutObject"],
                    resources=[
                        self._objects(self.research_bucket, research_scope.API_LANDING_PREFIX)
                    ],
                )
            ],
        )

        self.snapshot = self._state_machine(
            "SnapshotLoad",
            self._snapshot_definition(check_release),
            invoked=check_release,
            timeout=cdk.Duration.hours(2),
        )
        self.daily = self._state_machine(
            "DailyFeed",
            self._daily_definition(fetch_recent),
            invoked=fetch_recent,
            timeout=cdk.Duration.minutes(30),
        )
        self.snapshot.role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[f"arn:aws:s3:::{OPENALEX}/{research_scope.DELETED_IDS_KEY}"],
            )
        )
        self.snapshot.role.add_to_principal_policy(
            iam.PolicyStatement(actions=["ssm:PutParameter"], resources=[self.watermark_arn])
        )

        # Disabled until the first runs are verified by hand (milestone M2).
        scheduler.Schedule(
            self,
            "SnapshotSchedule",
            description="Weekly check for a new quarterly OpenAlex release.",
            schedule=scheduler.ScheduleExpression.cron(
                minute="0", hour="6", week_day="THU", time_zone=cdk.TimeZone.ETC_UTC
            ),
            target=scheduler_targets.StepFunctionsStartExecution(self.snapshot),
            enabled=config.SCHEDULES_ENABLED,
        )
        scheduler.Schedule(
            self,
            "DailySchedule",
            description="Daily OpenAlex API feed of recently published polymer works.",
            schedule=scheduler.ScheduleExpression.cron(
                minute="15", hour="5", time_zone=cdk.TimeZone.ETC_UTC
            ),
            target=scheduler_targets.StepFunctionsStartExecution(self.daily),
            enabled=config.SCHEDULES_ENABLED,
        )

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

    def _logical(self, construct: Construct) -> str:
        return self.get_logical_id(construct.node.default_child)

    # -- Lambda -------------------------------------------------------------

    def _function(
        self,
        construct_id: str,
        *,
        handler: str,
        timeout: cdk.Duration,
        statements: list[iam.PolicyStatement],
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
                self._nag_arn(
                    self._objects(self.research_bucket, research_scope.API_LANDING_PREFIX)
                ): "One JSON-lines file per fetch date under the landing prefix.",
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
            memory_size=256,
            role=role,
            log_group=log_group,
            environment={
                "RESEARCH_BUCKET": self.research_bucket,
                "WATERMARK_PARAMETER": WATERMARK_PARAMETER,
            },
        )

    # -- Step Functions -----------------------------------------------------

    def _athena(self, construct_id: str, query_key: str) -> sfn.CustomState:
        # The optimized Athena integration (.sync waits for the query to end).
        return sfn.CustomState(
            self,
            construct_id,
            state_json={
                "Type": "Task",
                "Resource": "arn:aws:states:::athena:startQueryExecution.sync",
                "Parameters": {
                    "QueryString.$": f"$.plan.queries.{query_key}",
                    "WorkGroup": config.PROJECT,
                },
                "ResultSelector": {"query_execution_id.$": "$.QueryExecution.QueryExecutionId"},
                "ResultPath": "$.last_query",
                "Retry": [
                    {
                        "ErrorEquals": ATHENA_RETRYABLE,
                        "IntervalSeconds": 30,
                        "MaxAttempts": 3,
                        "BackoffRate": 2,
                    }
                ],
            },
        )

    def _plan(self, construct_id: str, function: lambda_.Function) -> tasks.LambdaInvoke:
        return tasks.LambdaInvoke(
            self,
            construct_id,
            lambda_function=function,
            payload=sfn.TaskInput.from_object({"run_id": sfn.JsonPath.execution_name}),
            payload_response_only=True,
            result_path="$.plan",
            retry_on_service_exceptions=True,
        )

    def _snapshot_definition(self, check_release: lambda_.Function) -> sfn.IChainable:
        copy_deleted_ids = tasks.CallAwsService(
            self,
            "CopyDeletedIds",
            service="s3",
            action="copyObject",
            parameters={
                "Bucket": self.research_bucket,
                "Key": f"{research_scope.DELETED_IDS_PREFIX}deleted_ids.csv.gz",
                "CopySource": f"{OPENALEX}/{research_scope.DELETED_IDS_KEY}",
            },
            iam_resources=[self._objects(self.research_bucket, research_scope.DELETED_IDS_PREFIX)],
            iam_action="s3:PutObject",
            result_path=sfn.JsonPath.DISCARD,
        )
        move_watermark = tasks.CallAwsService(
            self,
            "MoveWatermark",
            service="ssm",
            action="putParameter",
            parameters={
                "Name": WATERMARK_PARAMETER,
                "Value": sfn.JsonPath.string_at("$.plan.release_date"),
                "Type": "String",
                "Overwrite": True,
            },
            iam_resources=[self.watermark_arn],
            result_path=sfn.JsonPath.DISCARD,
        )
        load = (
            self._athena("SnapshotCreateWorks", "create_works")
            .next(self._athena("SnapshotDropStage", "drop_stage"))
            .next(self._athena("SnapshotStage", "stage"))
            .next(self._athena("SnapshotMerge", "merge"))
            .next(copy_deleted_ids)
            .next(self._athena("ApplyDeletions", "apply_deletions"))
            .next(self._athena("SnapshotCleanupStage", "cleanup_stage"))
            .next(self._athena("Optimize", "optimize"))
            .next(self._athena("Vacuum", "vacuum"))
            .next(move_watermark)
            .next(sfn.Succeed(self, "SnapshotLoaded"))
        )
        return self._plan("PlanSnapshot", check_release).next(
            sfn.Choice(self, "NewRelease?")
            .when(sfn.Condition.boolean_equals("$.plan.new_release", True), load)
            .otherwise(sfn.Succeed(self, "NoNewRelease"))
        )

    def _daily_definition(self, fetch_recent: lambda_.Function) -> sfn.IChainable:
        load = (
            self._athena("DailyCreateWorks", "create_works")
            .next(self._athena("DailyDropStage", "drop_stage"))
            .next(self._athena("DailyStage", "stage"))
            .next(self._athena("DailyMerge", "merge"))
            .next(self._athena("DailyCleanupStage", "cleanup_stage"))
            .next(sfn.Succeed(self, "DailyLoaded"))
        )
        return self._plan("FetchAndPlan", fetch_recent).next(
            sfn.Choice(self, "AnyWorks?")
            .when(sfn.Condition.number_greater_than("$.plan.count", 0), load)
            .otherwise(sfn.Succeed(self, "NothingToLoad"))
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
        self._grant_queries(machine.role)
        glue_tables = self._nag_arn(self._glue_arn("table"))
        self._acknowledge_wildcards(
            machine.role,
            {
                "*": "Required by AWS: X-Ray tracing and CloudWatch Logs delivery actions "
                "do not support resource-level permissions.",
                f"<{self._logical(invoked)}.Arn>:*": "Invokes any version of its own "
                "planning function.",
                f"arn:aws:s3:::{OPENALEX}/data/parquet/works/*": "Reads the public OpenAlex "
                "works snapshot; Athena needs every object under the works prefix.",
                self._nag_arn(self._objects(self.research_bucket)): "Athena writes Iceberg "
                "data, staging tables and reads landing files in the research bucket.",
                self._nag_arn(self._objects(self.results_bucket)): "Athena writes query "
                "results to its own results bucket.",
                self._nag_arn(
                    self._objects(self.research_bucket, research_scope.DELETED_IDS_PREFIX)
                ): "Copies OpenAlex's deleted-IDs file into the reference prefix.",
                f"{glue_tables}/research/*": "Creates, merges into and drops tables in the "
                "research database only.",
                f"{glue_tables}/{config.SOURCE_DATABASE}/*": "Reads the external OpenAlex "
                "source tables.",
            },
        )
        return machine

    def _grant_queries(self, role: iam.IRole) -> None:
        """What Athena needs, with the state machine's own credentials, to run
        the research SQL: read the OpenAlex snapshot, read and write the
        research bucket and query results, and manage the research tables."""
        statements = [
            iam.PolicyStatement(
                actions=[
                    "athena:StartQueryExecution",
                    "athena:GetQueryExecution",
                    "athena:StopQueryExecution",
                ],
                resources=[
                    self.format_arn(
                        service="athena", resource="workgroup", resource_name=config.PROJECT
                    )
                ],
            ),
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[f"arn:aws:s3:::{OPENALEX}/data/parquet/works/*"],
            ),
            iam.PolicyStatement(
                actions=["s3:ListBucket", "s3:GetBucketLocation"],
                resources=[
                    f"arn:aws:s3:::{OPENALEX}",
                    f"arn:aws:s3:::{self.research_bucket}",
                    f"arn:aws:s3:::{self.results_bucket}",
                ],
            ),
            iam.PolicyStatement(
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                    "s3:AbortMultipartUpload",
                ],
                resources=[
                    self._objects(self.research_bucket),
                    self._objects(self.results_bucket),
                ],
            ),
            iam.PolicyStatement(
                actions=[
                    "glue:GetDatabase",
                    "glue:GetTable",
                    "glue:GetTables",
                    "glue:GetPartition",
                    "glue:GetPartitions",
                    "glue:BatchGetPartition",
                    "glue:CreateTable",
                    "glue:UpdateTable",
                    "glue:DeleteTable",
                ],
                resources=[
                    self._glue_arn("catalog"),
                    self._glue_arn("database/research"),
                    self._glue_arn(f"database/{config.SOURCE_DATABASE}"),
                    self._glue_arn("table/research/*"),
                    self._glue_arn(f"table/{config.SOURCE_DATABASE}/*"),
                ],
            ),
        ]
        for statement in statements:
            role.add_to_principal_policy(statement)
