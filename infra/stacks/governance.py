"""Lake Formation governance, in hybrid access mode.

Tags describe the data: `domain` (research, sustainability, shared) and
`tier` (product, staging, raw), set on each Glue database and inherited by its
tables, including the ones dbt creates later. A `product-reader` role gets
SELECT on `tier=product` through Lake Formation and nothing else: it has no
S3 permission on the data buckets, and Lake Formation vends it temporary
access to the files of the tables it may read.

Hybrid mode: only the reader role is opted in to Lake Formation. The pipeline
roles keep their IAM permissions, so turning governance on cannot break a
load (docs/design.md, Governance).
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lakeformation as lakeformation
from aws_cdk import aws_logs as logs
from aws_cdk import custom_resources as cr
from constructs import Construct

from infra import config
from infra.stacks.pipeline_base import LOG_RETENTION, PipelineStack

READER_ROLE_NAME = f"{config.PROJECT}-product-reader"
TAG_VALUES = {
    "domain": ["research", "sustainability", "shared"],
    "tier": ["product", "staging", "raw"],
}
# database -> its tags; tables inherit them.
DATABASE_TAGS = {
    "research": {"domain": "research", "tier": "product"},
    "research_staging": {"domain": "research", "tier": "staging"},
    config.SOURCE_DATABASE: {"domain": "research", "tier": "raw"},
    "sustainability": {"domain": "sustainability", "tier": "product"},
    "sustainability_staging": {"domain": "sustainability", "tier": "staging"},
    "products": {"domain": "shared", "tier": "product"},
}
# Grants that keep "use only IAM access control" for new databases and
# tables: the account's default, written out so the settings below cannot
# change it.
IAM_DEFAULT = [
    lakeformation.CfnDataLakeSettings.PrincipalPermissionsProperty(
        principal=lakeformation.CfnDataLakeSettings.DataLakePrincipalProperty(
            data_lake_principal_identifier="IAM_ALLOWED_PRINCIPALS"
        ),
        permissions=["ALL"],
    )
]


class GovernanceStack(PipelineStack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        table_writers: dict[str, list[iam.IRole]],
        **kwargs,
    ) -> None:
        """`table_writers`: per domain bucket, the pipeline roles that create
        tables in it (they need data location access once it is registered)."""
        super().__init__(scope, construct_id, **kwargs)
        catalog = self.account

        # One log group for the SDK-call function (shared by all opt-ins), so
        # its role can be scoped to it.
        opt_in_logs = logs.LogGroup(
            self, "OptInLogs", retention=LOG_RETENTION, removal_policy=cdk.RemovalPolicy.DESTROY
        )
        opt_in_role = self._opt_in_role(opt_in_logs)
        # Lake Formation administrators: the CloudFormation execution role
        # (creates tags and grants) and the opt-in role. APPEND adds them to
        # whoever is already an administrator.
        settings = lakeformation.CfnDataLakeSettings(
            self,
            "DataLakeSettings",
            mutation_type="APPEND",
            admins=[
                lakeformation.CfnDataLakeSettings.DataLakePrincipalProperty(
                    data_lake_principal_identifier=arn
                )
                for arn in (
                    f"arn:aws:iam::{self.account}:role/cdk-hnb659fds-cfn-exec-role-"
                    f"{self.account}-{self.region}",
                    opt_in_role.role_arn,
                )
            ],
            create_database_default_permissions=IAM_DEFAULT,
            create_table_default_permissions=IAM_DEFAULT,
        )

        tags = []
        for key, values in TAG_VALUES.items():
            tag = lakeformation.CfnTag(
                self, f"Tag{key.title()}", tag_key=key, tag_values=values, catalog_id=catalog
            )
            tag.node.add_dependency(settings)
            tags.append(tag)

        for database, assigned in DATABASE_TAGS.items():
            association = lakeformation.CfnTagAssociation(
                self,
                f"Tags{database.title().replace('_', '')}",
                resource=lakeformation.CfnTagAssociation.ResourceProperty(
                    database=lakeformation.CfnTagAssociation.DatabaseResourceProperty(
                        catalog_id=catalog, name=database
                    )
                ),
                lf_tags=[
                    lakeformation.CfnTagAssociation.LFTagPairProperty(
                        catalog_id=catalog, tag_key=k, tag_values=[v]
                    )
                    for k, v in assigned.items()
                ],
            )
            for tag in tags:
                association.node.add_dependency(tag)

        # Lake Formation can vend file access only for registered locations.
        # Hybrid: IAM principals keep their access to the same files. One at a
        # time: each registration rewrites the service-linked role's S3 policy,
        # and parallel registrations lost one bucket (docs/design.md, Findings).
        previous: cdk.CfnResource = settings
        self.locations: dict[str, lakeformation.CfnResource] = {}
        for domain in config.DOMAINS:
            location = lakeformation.CfnResource(
                self,
                f"Location{domain.title()}",
                resource_arn="arn:aws:s3:::"
                + config.bucket_name(domain, self.account, self.region),
                use_service_linked_role=True,
                hybrid_access_enabled=True,
            )
            location.node.add_dependency(previous)
            previous = location
            self.locations[domain] = location

        # Registering a location means creating a table there needs Lake
        # Formation's data location access, also for IAM principals in hybrid
        # mode. Granted to exactly the roles that create tables in each bucket
        # (docs/design.md, Findings).
        for domain, roles in table_writers.items():
            for index, role in enumerate(roles):
                grant = lakeformation.CfnPrincipalPermissions(
                    self,
                    f"LocationAccess{domain.title()}{index}",
                    principal=lakeformation.CfnPrincipalPermissions.DataLakePrincipalProperty(
                        data_lake_principal_identifier=role.role_arn
                    ),
                    resource=lakeformation.CfnPrincipalPermissions.ResourceProperty(
                        data_location=lakeformation.CfnPrincipalPermissions.DataLocationResourceProperty(
                            catalog_id=catalog,
                            resource_arn="arn:aws:s3:::"
                            + config.bucket_name(domain, self.account, self.region),
                        )
                    ),
                    permissions=["DATA_LOCATION_ACCESS"],
                    permissions_with_grant_option=[],
                )
                grant.node.add_dependency(self.locations[domain])

        self.reader = self._reader_role()
        for resource_type, permissions in (
            ("DATABASE", ["DESCRIBE"]),
            ("TABLE", ["SELECT", "DESCRIBE"]),
        ):
            grant = lakeformation.CfnPrincipalPermissions(
                self,
                f"ReaderProducts{resource_type.title()}",
                principal=lakeformation.CfnPrincipalPermissions.DataLakePrincipalProperty(
                    data_lake_principal_identifier=self.reader.role_arn
                ),
                resource=lakeformation.CfnPrincipalPermissions.ResourceProperty(
                    lf_tag_policy=lakeformation.CfnPrincipalPermissions.LFTagPolicyResourceProperty(
                        catalog_id=catalog,
                        resource_type=resource_type,
                        expression=[
                            lakeformation.CfnPrincipalPermissions.LFTagProperty(
                                tag_key="tier", tag_values=["product"]
                            )
                        ],
                    )
                ),
                permissions=permissions,
                permissions_with_grant_option=[],
            )
            for tag in tags:
                grant.node.add_dependency(tag)

        # Opt the reader in on every project database and on all its tables,
        # so Lake Formation, not IAM, decides what it may read everywhere. The
        # database opt-in alone does not cover the tables (docs/design.md,
        # Findings). CloudFormation has no resource for this, hence SDK calls.
        for database in DATABASE_TAGS:
            name = database.title().replace("_", "")
            for scope_name, resource in (
                ("Database", {"Database": {"CatalogId": catalog, "Name": database}}),
                (
                    "Tables",
                    {
                        "Table": {
                            "CatalogId": catalog,
                            "DatabaseName": database,
                            "TableWildcard": {},
                        }
                    },
                ),
            ):
                call = {
                    "service": "LakeFormation",
                    "parameters": {
                        "Principal": {"DataLakePrincipalIdentifier": self.reader.role_arn},
                        "Resource": resource,
                    },
                    # Database opt-ins keep the ID they were first deployed
                    # with, so CloudFormation does not replace them.
                    "physical_resource_id": cr.PhysicalResourceId.of(
                        f"opt-in-reader-{database}"
                        if scope_name == "Database"
                        else f"opt-in-reader-{database}-tables"
                    ),
                }
                opt_in = cr.AwsCustomResource(
                    self,
                    f"OptIn{name}{scope_name if scope_name == 'Tables' else ''}",
                    on_create=cr.AwsSdkCall(action="createLakeFormationOptIn", **call),
                    on_delete=cr.AwsSdkCall(action="deleteLakeFormationOptIn", **call),
                    role=opt_in_role,
                    install_latest_aws_sdk=False,
                    log_group=opt_in_logs,
                )
                opt_in.node.add_dependency(settings)

        cdk.CfnOutput(self, "ProductReaderRoleArn", value=self.reader.role_arn)

    def _opt_in_role(self, log_group: logs.LogGroup) -> iam.Role:
        role = iam.Role(self, "OptInRole", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"))
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "lakeformation:CreateLakeFormationOptIn",
                    "lakeformation:DeleteLakeFormationOptIn",
                    "lakeformation:ListLakeFormationOptIns",
                ],
                # Lake Formation's opt-in actions have no resource-level
                # permissions.
                resources=["*"],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["glue:GetDatabase"],
                resources=[
                    self._glue_arn("catalog"),
                    *[self._glue_arn(f"database/{db}") for db in DATABASE_TAGS],
                ],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[f"{log_group.log_group_arn}:*"],
            )
        )
        self._acknowledge_wildcards(
            role,
            {
                "*": "Lake Formation's opt-in actions do not support resource-level permissions.",
                f"<{self._logical(log_group)}.Arn>:*": "Log streams in the SDK-call "
                "function's own log group.",
            },
        )
        return role

    def _reader_role(self) -> iam.Role:
        results = config.bucket_name(config.RESULTS_BUCKET_KEY, self.account, self.region)
        role = iam.Role(
            self,
            "ProductReaderRole",
            role_name=READER_ROLE_NAME,
            description="Reads the data products through Lake Formation, nothing else.",
            # Anyone in this account allowed to assume roles, e.g. an analyst.
            assumed_by=iam.AccountPrincipal(self.account),
            max_session_duration=cdk.Duration.hours(1),
        )
        tables = self._glue_arn("table/*")
        for statement in [
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
            # Catalog reads; Lake Formation filters what the reader sees.
            iam.PolicyStatement(
                actions=[
                    "glue:GetDatabase",
                    "glue:GetDatabases",
                    "glue:GetTable",
                    "glue:GetTables",
                    "glue:GetPartitions",
                ],
                resources=[
                    self._glue_arn("catalog"),
                    self._glue_arn("database/*"),
                    tables,
                ],
            ),
            # File access comes from Lake Formation, not from S3 permissions.
            iam.PolicyStatement(actions=["lakeformation:GetDataAccess"], resources=["*"]),
            # Its own query results only.
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:PutObject"],
                resources=[self._objects(results)],
            ),
            iam.PolicyStatement(
                actions=["s3:ListBucket", "s3:GetBucketLocation"],
                resources=[f"arn:aws:s3:::{results}"],
            ),
        ]:
            role.add_to_policy(statement)
        self._acknowledge_wildcards(
            role,
            {
                "*": "lakeformation:GetDataAccess has no resource-level permissions; Lake "
                "Formation's grants decide what it returns.",
                self._nag_arn(self._glue_arn("database/*")): "Catalog reads; Lake "
                "Formation hides every database the reader has no grant on.",
                self._nag_arn(tables): "Catalog reads; Lake Formation hides every table the "
                "reader has no grant on.",
                self._nag_arn(self._objects(results)): "Its own Athena query results.",
            },
        )
        return role
