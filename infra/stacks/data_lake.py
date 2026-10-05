"""Storage, catalog and query engine shared by every domain.

One bucket and one Glue database per domain, an external table over the public
OpenAlex snapshot, and an Athena workgroup with a per-query scan limit.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_athena as athena
from aws_cdk import aws_glue as glue
from aws_cdk import aws_s3 as s3
from constructs import Construct

from infra import config

# Only the columns the research domain needs. Leaving out abstracts (about a
# third of the snapshot's bytes) keeps scans cheap; the authorships struct is
# narrowed to institutions, so author names and ORCIDs cannot be selected.
OPENALEX_WORKS_COLUMNS: list[tuple[str, str]] = [
    ("id", "string"),
    ("doi", "string"),
    ("title", "string"),
    ("publication_date", "date"),
    ("publication_year", "int"),
    ("type", "string"),
    ("language", "string"),
    ("is_retracted", "boolean"),
    (
        "primary_topic",
        "struct<id:string,display_name:string,score:float,"
        "subfield:struct<id:string,display_name:string>,"
        "field:struct<id:string,display_name:string>,"
        "domain:struct<id:string,display_name:string>>",
    ),
    ("sustainable_development_goals", "array<struct<id:string,display_name:string,score:double>>"),
    ("open_access", "struct<is_oa:boolean,oa_status:string>"),
    ("cited_by_count", "int"),
    ("fwci", "double"),
    ("authorships", "array<struct<institutions:array<struct<id:string,country_code:string>>>>"),
    ("created_date", "timestamp"),
    ("updated_date", "timestamp"),
]


class DataLakeStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.domain_buckets = {
            domain: self._bucket(f"{domain.title()}Bucket") for domain in config.DOMAINS
        }
        self.domain_buckets["research"].add_lifecycle_rule(
            id="ExpireLanding",
            prefix="landing/",
            expiration=cdk.Duration.days(config.LANDING_RETENTION_DAYS),
        )
        self.results_bucket = self._bucket("AthenaResultsBucket")
        self.results_bucket.add_lifecycle_rule(
            id="ExpireQueryResults",
            expiration=cdk.Duration.days(config.ATHENA_RESULTS_RETENTION_DAYS),
        )

        for domain, bucket in self.domain_buckets.items():
            glue.CfnDatabase(
                self,
                f"{domain.title()}Database",
                catalog_id=self.account,
                database_input=glue.CfnDatabase.DatabaseInputProperty(
                    name=domain,
                    description=f"Iceberg tables owned by the {domain} domain.",
                    location_uri=bucket.s3_url_for_object(),
                ),
            )

        source_db = glue.CfnDatabase(
            self,
            "OpenAlexSourceDatabase",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=config.SOURCE_DATABASE,
                description="External tables over the public OpenAlex snapshot (CC0).",
            ),
        )
        works = self._openalex_works_table()
        works.node.add_dependency(source_db)

        self.workgroup = athena.CfnWorkGroup(
            self,
            "Workgroup",
            name=config.PROJECT,
            description="All project queries; fails any query that would scan too much.",
            recursive_delete_option=True,
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                bytes_scanned_cutoff_per_query=config.ATHENA_SCAN_LIMIT_BYTES,
                enforce_work_group_configuration=True,
                publish_cloud_watch_metrics_enabled=True,
                engine_version=athena.CfnWorkGroup.EngineVersionProperty(
                    selected_engine_version="Athena engine version 3"
                ),
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=self.results_bucket.s3_url_for_object("results/"),
                    encryption_configuration=athena.CfnWorkGroup.EncryptionConfigurationProperty(
                        encryption_option="SSE_S3"
                    ),
                ),
            ),
        )

    def _bucket(self, construct_id: str) -> s3.Bucket:
        bucket = s3.Bucket(
            self,
            construct_id,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="AbortIncompleteUploads",
                    abort_incomplete_multipart_upload_after=cdk.Duration.days(1),
                )
            ],
        )
        cdk.Validations.of(bucket).acknowledge(
            cdk.Acknowledgment(
                id="AwsSolutions::AwsSolutions-S1",
                reason="Server access logs would cost more than the data they audit; "
                "CloudTrail management events cover bucket changes.",
            )
        )
        return bucket

    def _openalex_works_table(self) -> glue.CfnTable:
        # Partition projection instead of a crawler: Athena derives the
        # partitions from the date range, so nothing needs to list or register
        # them. The data column `updated_date` and the partition key must have
        # different names, so the key is `partition_date`.
        return glue.CfnTable(
            self,
            "OpenAlexWorksTable",
            catalog_id=self.account,
            database_name=config.SOURCE_DATABASE,
            table_input=glue.CfnTable.TableInputProperty(
                name="works",
                description="OpenAlex works snapshot, selected columns only.",
                table_type="EXTERNAL_TABLE",
                parameters={
                    "classification": "parquet",
                    "EXTERNAL": "TRUE",
                    "projection.enabled": "true",
                    "projection.partition_date.type": "date",
                    "projection.partition_date.format": "yyyy-MM-dd",
                    "projection.partition_date.range": "2016-01-01,NOW",
                    "projection.partition_date.interval": "1",
                    "projection.partition_date.interval.unit": "DAYS",
                    "storage.location.template": (
                        f"{config.OPENALEX_WORKS_LOCATION}updated_date=${{partition_date}}/"
                    ),
                },
                partition_keys=[glue.CfnTable.ColumnProperty(name="partition_date", type="string")],
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    location=config.OPENALEX_WORKS_LOCATION,
                    input_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                    output_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                    serde_info=glue.CfnTable.SerdeInfoProperty(
                        serialization_library="org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
                    ),
                    columns=[
                        glue.CfnTable.ColumnProperty(name=name, type=type_)
                        for name, type_ in OPENALEX_WORKS_COLUMNS
                    ],
                ),
            ),
        )
