"""Storage, catalog and query engine shared by every domain.

One bucket and one Glue database per domain, external tables over the OpenAlex
sources, and an Athena workgroup with a per-query scan limit.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_athena as athena
from aws_cdk import aws_glue as glue
from aws_cdk import aws_s3 as s3
from constructs import Construct

from infra import config
from research import scope as research_scope

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
    ("is_xpac", "boolean"),
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

# The daily feed's JSON lines, as research.openalex_api.to_landing_record
# writes them: already flattened, dates as ISO strings.
API_WORKS_COLUMNS: list[tuple[str, str]] = [
    ("id", "string"),
    ("doi", "string"),
    ("title", "string"),
    ("publication_date", "string"),
    ("publication_year", "int"),
    ("type", "string"),
    ("language", "string"),
    ("is_retracted", "boolean"),
    ("is_xpac", "boolean"),
    ("topic_id", "string"),
    ("topic_name", "string"),
    ("topic_score", "double"),
    ("sdg_ids", "array<string>"),
    ("is_oa", "boolean"),
    ("oa_status", "string"),
    ("cited_by_count", "int"),
    ("fwci", "double"),
    ("institution_ids", "array<string>"),
    ("country_codes", "array<string>"),
    ("created_date", "string"),
    ("updated_date", "string"),
]

PARQUET = (
    "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
    "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
    "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe",
)
TEXT_INPUT = "org.apache.hadoop.mapred.TextInputFormat"
TEXT_OUTPUT = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"


def _date_projection(key: str, start: str, location_template: str) -> dict[str, str]:
    # Partition projection instead of a crawler: Athena derives the partitions
    # from the date range, so nothing needs to list or register them.
    return {
        "projection.enabled": "true",
        f"projection.{key}.type": "date",
        f"projection.{key}.format": "yyyy-MM-dd",
        f"projection.{key}.range": f"{start},NOW",
        f"projection.{key}.interval": "1",
        f"projection.{key}.interval.unit": "DAYS",
        "storage.location.template": location_template,
    }


class DataLakeStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.domain_buckets = {
            domain: self._bucket(f"{domain.title()}Bucket", domain) for domain in config.DOMAINS
        }
        research_bucket = self.domain_buckets["research"]
        research_bucket.add_lifecycle_rule(
            id="ExpireLanding",
            prefix="landing/",
            expiration=cdk.Duration.days(config.LANDING_RETENTION_DAYS),
        )
        # Staging tables are dropped after each load; this catches a run that
        # failed before its cleanup step.
        research_bucket.add_lifecycle_rule(
            id="ExpireStaging",
            prefix=research_scope.STAGING_PREFIX,
            expiration=cdk.Duration.days(config.STAGING_RETENTION_DAYS),
        )
        self.results_bucket = self._bucket("AthenaResultsBucket", config.RESULTS_BUCKET_KEY)
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

        for name in config.STAGING_DATABASES:
            glue.CfnDatabase(
                self,
                f"{name.title().replace('_', '')}Database",
                catalog_id=self.account,
                database_input=glue.CfnDatabase.DatabaseInputProperty(
                    name=name,
                    description="dbt staging and intermediate views; consumers read products only.",
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
        research_url = research_bucket.s3_url_for_object()
        tables = [
            self._external_table(
                "OpenAlexWorksTable",
                name="works",
                description="OpenAlex works snapshot, selected columns only.",
                location=config.OPENALEX_WORKS_LOCATION,
                columns=OPENALEX_WORKS_COLUMNS,
                formats=PARQUET,
                # The data column `updated_date` and the partition key need
                # different names, so the key is `partition_date`.
                partition_key="partition_date",
                parameters={
                    "classification": "parquet",
                    **_date_projection(
                        "partition_date",
                        "2016-01-01",
                        f"{config.OPENALEX_WORKS_LOCATION}updated_date=${{partition_date}}/",
                    ),
                },
            ),
            self._external_table(
                "ApiWorksTable",
                name="api_works",
                description="Daily OpenAlex API feed, one JSON-lines file per fetch date.",
                location=f"{research_url}/{research_scope.API_LANDING_PREFIX}",
                columns=API_WORKS_COLUMNS,
                formats=(TEXT_INPUT, TEXT_OUTPUT, "org.openx.data.jsonserde.JsonSerDe"),
                partition_key="fetch_date",
                parameters={
                    "classification": "json",
                    **_date_projection(
                        "fetch_date",
                        "2026-10-01",
                        f"{research_url}/{research_scope.API_LANDING_PREFIX}fetch_date=${{fetch_date}}/",
                    ),
                },
            ),
            self._external_table(
                "DeletedWorksTable",
                name="deleted_works",
                description="Copy of OpenAlex's cumulative deleted_ids.csv.gz, refreshed per load.",
                location=f"{research_url}/{research_scope.DELETED_IDS_PREFIX}",
                columns=[("work_id", "string"), ("deleted_date", "string")],
                formats=(
                    TEXT_INPUT,
                    TEXT_OUTPUT,
                    "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe",
                ),
                serde_parameters={"field.delim": ","},
                parameters={"classification": "csv", "skip.header.line.count": "1"},
            ),
        ]
        for table in tables:
            table.node.add_dependency(source_db)

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

    def _bucket(self, construct_id: str, key: str) -> s3.Bucket:
        bucket = s3.Bucket(
            self,
            construct_id,
            bucket_name=config.bucket_name(key, self.account, self.region),
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

    def _external_table(
        self,
        construct_id: str,
        *,
        name: str,
        description: str,
        location: str,
        columns: list[tuple[str, str]],
        formats: tuple[str, str, str],
        parameters: dict[str, str],
        partition_key: str | None = None,
        serde_parameters: dict[str, str] | None = None,
    ) -> glue.CfnTable:
        input_format, output_format, serde = formats
        return glue.CfnTable(
            self,
            construct_id,
            catalog_id=self.account,
            database_name=config.SOURCE_DATABASE,
            table_input=glue.CfnTable.TableInputProperty(
                name=name,
                description=description,
                table_type="EXTERNAL_TABLE",
                parameters={"EXTERNAL": "TRUE", **parameters},
                partition_keys=(
                    [glue.CfnTable.ColumnProperty(name=partition_key, type="string")]
                    if partition_key
                    else None
                ),
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    location=location,
                    input_format=input_format,
                    output_format=output_format,
                    serde_info=glue.CfnTable.SerdeInfoProperty(
                        serialization_library=serde, parameters=serde_parameters
                    ),
                    columns=[
                        glue.CfnTable.ColumnProperty(name=column, type=type_)
                        for column, type_ in columns
                    ],
                ),
            ),
        )
