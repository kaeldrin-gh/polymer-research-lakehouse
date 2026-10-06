"""Build the data products: dbt on Athena, as an ECS Fargate task.

A Docker image (docker/dbt/) holds dbt-core, dbt-athena and the dbt project;
CDK builds it and pushes it to ECR on deploy. A Step Functions state machine
runs it as a Fargate task and waits for it to finish. The task runs in a small
VPC with public subnets only: no NAT gateway, so the network costs nothing
while idle. Every permission is written out.
"""

from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_scheduler_targets as scheduler_targets
from aws_cdk import aws_stepfunctions as sfn
from constructs import Construct

from infra import config
from infra.stacks.pipeline_base import LOG_RETENTION, PipelineStack

ROOT = Path(__file__).resolve().parents[2]
CLUSTER_NAME = f"{config.PROJECT}-dbt"
TASK_FAMILY = f"{config.PROJECT}-dbt"
CONTAINER_NAME = "dbt"
# The Glue databases dbt reads and writes (dbt/dbt_project.yml).
DBT_DATABASES = ("research", "sustainability", "products", *config.STAGING_DATABASES)


class ProductsBuildStack(PipelineStack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.buckets = {
            key: config.bucket_name(key, self.account, self.region)
            for key in (*config.DOMAINS, config.RESULTS_BUCKET_KEY)
        }

        # Public subnets only, no NAT gateway: the task gets a public IP for
        # the few minutes it runs and reaches Athena, Glue, S3 and ECR directly.
        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            restrict_default_security_group=False,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                )
            ],
        )
        cdk.Validations.of(vpc).acknowledge(
            cdk.Acknowledgment(
                id="AwsSolutions::AwsSolutions-VPC7",
                reason="No flow logs: the VPC only carries a short outbound-only dbt task; "
                "flow logs would cost more than the network they describe.",
            )
        )
        security_group = ec2.SecurityGroup(
            self,
            "TaskSecurityGroup",
            vpc=vpc,
            description="dbt task: no inbound traffic, outbound HTTPS to AWS APIs.",
            allow_all_outbound=False,
        )
        security_group.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "AWS APIs")

        cluster = ecs.Cluster(self, "Cluster", cluster_name=CLUSTER_NAME, vpc=vpc)
        cdk.Validations.of(cluster).acknowledge(
            cdk.Acknowledgment(
                id="AwsSolutions::AwsSolutions-ECS4",
                reason="No Container Insights: one short task a day; its logs and the "
                "state machine's history cover it, and Insights metrics are billed.",
            )
        )

        task = self._task_definition()
        self.build = self._state_machine(
            "ProductsBuild",
            self._run_task(cluster, task, vpc, security_group),
            invoked=None,
            timeout=cdk.Duration.minutes(30),
        )
        self._grant_run_task(self.build.role, cluster, task)

        # Disabled until the first runs are verified by hand. After the daily
        # feed (05:15 UTC), so the products include the newest works.
        scheduler.Schedule(
            self,
            "ProductsBuildSchedule",
            description="Daily dbt build of the data products on Athena.",
            schedule=scheduler.ScheduleExpression.cron(
                minute="0", hour="6", time_zone=cdk.TimeZone.ETC_UTC
            ),
            target=scheduler_targets.StepFunctionsStartExecution(self.build),
            enabled=config.SCHEDULES_ENABLED,
        )

    def _task_definition(self) -> ecs.FargateTaskDefinition:
        image = ecr_assets.DockerImageAsset(
            self,
            "DbtImage",
            directory=str(ROOT),
            file="docker/dbt/Dockerfile",
            platform=ecr_assets.Platform.LINUX_AMD64,
        )
        task_role = iam.Role(
            self, "DbtTaskRole", assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com")
        )
        self._grant_dbt(task_role)
        task = ecs.FargateTaskDefinition(
            self,
            "DbtTask",
            family=TASK_FAMILY,
            cpu=512,
            memory_limit_mib=1024,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
            task_role=task_role,
        )
        task.add_container(
            "DbtContainer",
            container_name=CONTAINER_NAME,
            image=ecs.ContainerImage.from_docker_image_asset(image),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="dbt",
                log_group=logs.LogGroup(
                    self,
                    "DbtLogs",
                    retention=LOG_RETENTION,
                    removal_policy=cdk.RemovalPolicy.DESTROY,
                ),
            ),
            environment={"AWS_ACCOUNT_ID": self.account, "AWS_DEFAULT_REGION": self.region},
        )
        cdk.Validations.of(task).acknowledge(
            cdk.Acknowledgment(
                id="AwsSolutions::AwsSolutions-ECS2",
                reason="The environment holds the account ID and region only, no secrets.",
            )
        )
        # The execution role pulls the image and writes logs; CDK scopes it to
        # this image's repository and log group. ECR's token call has no
        # resource-level permission.
        self._acknowledge_wildcards(
            task.obtain_execution_role(),
            {"*": "ecr:GetAuthorizationToken does not support resource-level permissions."},
        )
        return task

    def _grant_dbt(self, role: iam.Role) -> None:
        """What dbt-athena needs: run queries in the project workgroup, manage
        tables in the dbt databases, read the domains' Iceberg data, and write
        product data under each bucket's dbt/ prefix."""
        objects = self._objects
        b = self.buckets
        read = [
            objects(b["research"], "iceberg/"),
            objects(b["sustainability"], "iceberg/"),
        ]
        write = [objects(b[k], "dbt/") for k in config.DOMAINS] + [
            objects(b[config.RESULTS_BUCKET_KEY])
        ]
        tables = [self._glue_arn(f"table/{db}/*") for db in DBT_DATABASES]
        statements = [
            iam.PolicyStatement(
                actions=[
                    "athena:StartQueryExecution",
                    "athena:GetQueryExecution",
                    "athena:GetQueryResults",
                    "athena:StopQueryExecution",
                    "athena:GetWorkGroup",
                ],
                resources=[
                    self.format_arn(
                        service="athena", resource="workgroup", resource_name=config.PROJECT
                    )
                ],
            ),
            iam.PolicyStatement(
                actions=["athena:GetDataCatalog", "athena:GetTableMetadata"],
                resources=[
                    self.format_arn(
                        service="athena", resource="datacatalog", resource_name="AwsDataCatalog"
                    )
                ],
            ),
            iam.PolicyStatement(actions=["s3:GetObject"], resources=read),
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], resources=write
            ),
            iam.PolicyStatement(
                actions=["s3:ListBucket", "s3:GetBucketLocation"],
                resources=[f"arn:aws:s3:::{name}" for name in b.values()],
            ),
            iam.PolicyStatement(
                actions=[
                    "glue:GetDatabase",
                    "glue:GetDatabases",
                    "glue:GetTable",
                    "glue:GetTables",
                    "glue:GetTableVersions",
                    "glue:CreateTable",
                    "glue:UpdateTable",
                    "glue:DeleteTable",
                    "glue:BatchDeleteTable",
                    "glue:DeleteTableVersion",
                    "glue:BatchDeleteTableVersion",
                    "glue:GetPartition",
                    "glue:GetPartitions",
                    "glue:BatchGetPartition",
                    "glue:BatchCreatePartition",
                    "glue:BatchDeletePartition",
                ],
                resources=[
                    self._glue_arn("catalog"),
                    *[self._glue_arn(f"database/{db}") for db in DBT_DATABASES],
                    *tables,
                ],
            ),
        ]
        for statement in statements:
            role.add_to_policy(statement)
        self._acknowledge_wildcards(
            role,
            {
                **{self._nag_arn(r): "Reads the domains' Iceberg tables." for r in read},
                **{
                    self._nag_arn(r): "dbt writes product tables and seeds under each "
                    "bucket's dbt/ prefix, and query results."
                    for r in write
                },
                **{
                    self._nag_arn(t): "dbt creates, replaces and drops its own tables in "
                    "the dbt databases."
                    for t in tables
                },
            },
        )

    def _run_task(
        self,
        cluster: ecs.Cluster,
        task: ecs.FargateTaskDefinition,
        vpc: ec2.Vpc,
        security_group: ec2.SecurityGroup,
    ) -> sfn.IChainable:
        run = sfn.CustomState(
            self,
            "RunDbt",
            state_json={
                "Type": "Task",
                # .sync waits for the task to stop and fails if dbt fails.
                "Resource": "arn:aws:states:::ecs:runTask.sync",
                "Parameters": {
                    "Cluster": cluster.cluster_arn,
                    "TaskDefinition": task.task_definition_arn,
                    "LaunchType": "FARGATE",
                    "NetworkConfiguration": {
                        "AwsvpcConfiguration": {
                            "Subnets": [s.subnet_id for s in vpc.public_subnets],
                            "SecurityGroups": [security_group.security_group_id],
                            "AssignPublicIp": "ENABLED",
                        }
                    },
                },
                "ResultSelector": {"task_arn.$": "$.TaskArn", "stopped.$": "$.StoppedReason"},
                "ResultPath": "$.dbt",
            },
        )
        return run.next(sfn.Succeed(self, "ProductsBuilt"))

    def _grant_run_task(
        self, role: iam.IRole, cluster: ecs.Cluster, task: ecs.FargateTaskDefinition
    ) -> None:
        family = self.format_arn(
            service="ecs", resource="task-definition", resource_name=f"{TASK_FAMILY}:*"
        )
        tasks = self.format_arn(service="ecs", resource="task", resource_name=f"{CLUSTER_NAME}/*")
        # The rule Step Functions manages to learn when an ECS task stops.
        events_rule = self.format_arn(
            service="events", resource="rule", resource_name="StepFunctionsGetEventsForECSTaskRule"
        )
        for statement in [
            iam.PolicyStatement(actions=["ecs:RunTask"], resources=[family]),
            iam.PolicyStatement(actions=["ecs:StopTask", "ecs:DescribeTasks"], resources=[tasks]),
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[task.task_role.role_arn, task.obtain_execution_role().role_arn],
                conditions={"StringEquals": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}},
            ),
            iam.PolicyStatement(
                actions=["events:PutTargets", "events:PutRule", "events:DescribeRule"],
                resources=[events_rule],
            ),
        ]:
            role.add_to_principal_policy(statement)
        self._acknowledge_wildcards(
            role,
            {
                self._nag_arn(family): "Runs any revision of the dbt task definition.",
                self._nag_arn(tasks): "Stops and describes the tasks it started in the "
                "dbt cluster.",
            },
        )
