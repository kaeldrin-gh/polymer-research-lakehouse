"""Sustainability domain load: one Step Functions state machine.

Weekly, a Lambda checks the EEA share for a release newer than the last one
loaded. If there is one, it lands the air releases (without names or places)
in S3 and plans the SCD Type 2 SQL (sustainability.scd2_sql, tested against
DuckDB); a Glue PySpark job applies it to the Iceberg table; then the version
watermark moves. Every permission is written out.
"""

from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_glue as glue
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3_assets as s3_assets
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_scheduler_targets as scheduler_targets
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

from infra import config
from infra.stacks.pipeline_base import PipelineStack
from sustainability import handlers as sustainability_handlers
from sustainability import scope as sustainability_scope

JOB_SCRIPT = Path(__file__).resolve().parents[2] / "jobs" / "air_releases_scd2.py"
JOB_NAME = f"{config.PROJECT}-air-releases-scd2"
VERSION_PARAMETER = f"/{config.PROJECT}/sustainability/eea-version"
GLUE_RETRYABLE = ["Glue.ConcurrentRunsExceededException", "Glue.ThrottlingException"]
NO_KMS = (
    "No KMS keys by design (docs/design.md, Cost): the data is public (CC BY 4.0) and "
    "S3-managed encryption covers it at rest."
)


class SustainabilityPipelineStack(PipelineStack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.bucket = config.bucket_name("sustainability", self.account, self.region)
        self.version_arn = self._parameter_arn(VERSION_PARAMETER)
        landing = self._objects(self.bucket, sustainability_scope.LANDING_PREFIX)

        check_release = self._function(
            "CheckEeaRelease",
            handler="sustainability.handlers.check_release",
            # Downloads a 148 MB zip and streams one CSV out of it: about 10 s
            # and 125 MB of memory on a laptop.
            timeout=cdk.Duration.minutes(5),
            memory_size=512,
            ephemeral_storage_mib=1024,
            statements=[
                iam.PolicyStatement(actions=["ssm:GetParameter"], resources=[self.version_arn]),
                iam.PolicyStatement(actions=["s3:PutObject"], resources=[landing]),
            ],
            environment={
                "SUSTAINABILITY_BUCKET": self.bucket,
                "VERSION_PARAMETER": VERSION_PARAMETER,
            },
            wildcards={
                self._nag_arn(landing): "One landing file per EEA release version.",
            },
        )
        self.job = self._glue_job(landing)

        self.load = self._state_machine(
            "EeaReleaseLoad",
            self._definition(check_release),
            invoked=check_release,
            timeout=cdk.Duration.hours(1),
        )
        self.load.role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=[
                    "glue:StartJobRun",
                    "glue:GetJobRun",
                    "glue:GetJobRuns",
                    "glue:BatchStopJobRun",
                ],
                resources=[self._glue_arn(f"job/{JOB_NAME}")],
            )
        )
        self.load.role.add_to_principal_policy(
            iam.PolicyStatement(actions=["ssm:PutParameter"], resources=[self.version_arn])
        )

        # Disabled until the first run is verified by hand.
        scheduler.Schedule(
            self,
            "EeaReleaseSchedule",
            description="Weekly check for a new EEA industrial reporting release.",
            schedule=scheduler.ScheduleExpression.cron(
                minute="0", hour="7", week_day="MON", time_zone=cdk.TimeZone.ETC_UTC
            ),
            target=scheduler_targets.StepFunctionsStartExecution(self.load),
            enabled=config.SCHEDULES_ENABLED,
        )

    def _glue_job(self, landing: str) -> glue.CfnJob:
        script = s3_assets.Asset(self, "AirReleasesJobScript", path=str(JOB_SCRIPT))
        iceberg = self._objects(self.bucket, sustainability_scope.ICEBERG_PREFIX)
        glue_logs = f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws-glue/*"
        tables = self._glue_arn("table/sustainability/*")
        role = iam.Role(self, "GlueJobRole", assumed_by=iam.ServicePrincipal("glue.amazonaws.com"))
        self.glue_role = role
        for statement in [
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[f"arn:aws:s3:::{script.s3_bucket_name}/{script.s3_object_key}"],
            ),
            iam.PolicyStatement(actions=["s3:GetObject"], resources=[landing]),
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                resources=[iceberg],
            ),
            iam.PolicyStatement(
                actions=["s3:ListBucket"], resources=[f"arn:aws:s3:::{self.bucket}"]
            ),
            iam.PolicyStatement(
                actions=[
                    "glue:GetDatabase",
                    "glue:GetTable",
                    "glue:GetTables",
                    "glue:CreateTable",
                    "glue:UpdateTable",
                ],
                resources=[
                    self._glue_arn("catalog"),
                    self._glue_arn("database/sustainability"),
                    tables,
                ],
            ),
            iam.PolicyStatement(
                actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[glue_logs, f"{glue_logs}:*"],
            ),
        ]:
            role.add_to_policy(statement)
        self._acknowledge_wildcards(
            role,
            {
                self._nag_arn(landing): "Reads the landed release files.",
                self._nag_arn(iceberg): "Iceberg writes data and metadata files under the "
                "table's prefix.",
                self._nag_arn(tables): "Creates and updates tables in the sustainability "
                "database only.",
                self._nag_arn(glue_logs): "Glue writes job logs to log groups under /aws-glue/.",
                self._nag_arn(f"{glue_logs}:*"): "Log streams under those log groups.",
            },
        )

        warehouse = f"s3://{self.bucket}/{sustainability_scope.ICEBERG_PREFIX}"
        iceberg_conf = " --conf ".join(
            [
                "spark.sql.extensions="
                "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
                "spark.sql.catalog.glue_catalog=org.apache.iceberg.spark.SparkCatalog",
                "spark.sql.catalog.glue_catalog.catalog-impl="
                "org.apache.iceberg.aws.glue.GlueCatalog",
                "spark.sql.catalog.glue_catalog.io-impl=org.apache.iceberg.aws.s3.S3FileIO",
                f"spark.sql.catalog.glue_catalog.warehouse={warehouse}",
            ]
        )
        job = glue.CfnJob(
            self,
            "AirReleasesJob",
            name=JOB_NAME,
            description="Applies one EEA release to sustainability.air_releases (SCD Type 2).",
            role=role.role_arn,
            command=glue.CfnJob.JobCommandProperty(
                name="glueetl", python_version="3", script_location=script.s3_object_url
            ),
            glue_version="5.0",
            # Flex runs on spare capacity at a lower price; a few minutes'
            # start-up delay is fine for a job that runs a few times a year.
            execution_class="FLEX",
            worker_type="G.1X",
            number_of_workers=2,
            timeout=30,
            max_retries=0,
            default_arguments={
                "--datalake-formats": "iceberg",
                "--conf": iceberg_conf,
                "--target": sustainability_handlers.GLUE_TABLE,
                "--job-language": "python",
            },
        )
        cdk.Validations.of(job).acknowledge(
            cdk.Acknowledgment(id="AwsSolutions::AwsSolutions-GL1", reason=NO_KMS),
            cdk.Acknowledgment(id="AwsSolutions::AwsSolutions-GL3", reason=NO_KMS),
        )
        return job

    def _definition(self, check_release: lambda_.Function) -> sfn.IChainable:
        run_job = sfn.CustomState(
            self,
            "ApplyRelease",
            state_json={
                "Type": "Task",
                # .sync waits for the job run to finish.
                "Resource": "arn:aws:states:::glue:startJobRun.sync",
                "Parameters": {
                    "JobName": JOB_NAME,
                    "Arguments": {
                        "--landing_path.$": "$.plan.landing_path",
                        "--statements.$": "$.plan.statements",
                    },
                },
                "ResultSelector": {"job_run_id.$": "$.Id", "state.$": "$.JobRunState"},
                "ResultPath": "$.glue_run",
                "Retry": [
                    {
                        "ErrorEquals": GLUE_RETRYABLE,
                        "IntervalSeconds": 60,
                        "MaxAttempts": 3,
                        "BackoffRate": 2,
                    }
                ],
            },
        )
        move_version = tasks.CallAwsService(
            self,
            "MoveVersion",
            service="ssm",
            action="putParameter",
            parameters={
                "Name": VERSION_PARAMETER,
                "Value": sfn.JsonPath.format("{}", sfn.JsonPath.string_at("$.plan.version")),
                "Type": "String",
                "Overwrite": True,
            },
            iam_resources=[self.version_arn],
            result_path=sfn.JsonPath.DISCARD,
        )
        load = run_job.next(move_version).next(sfn.Succeed(self, "ReleaseApplied"))
        return self._plan("PlanRelease", check_release).next(
            sfn.Choice(self, "NewRelease?")
            .when(sfn.Condition.boolean_equals("$.plan.new_release", True), load)
            .otherwise(sfn.Succeed(self, "NoNewRelease"))
        )
