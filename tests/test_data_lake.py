from __future__ import annotations

from aws_cdk.assertions import Match

from infra import config


def _resources(template, type_):
    return template.find_resources(type_)


def test_every_bucket_is_private_encrypted_and_kept(data_lake_template):
    buckets = _resources(data_lake_template, "AWS::S3::Bucket")
    # One per domain plus Athena query results.
    assert len(buckets) == len(config.DOMAINS) + 1
    for bucket in buckets.values():
        props = bucket["Properties"]
        assert props["PublicAccessBlockConfiguration"] == {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }
        sse = props["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]
        assert sse["ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "AES256"
        assert bucket["DeletionPolicy"] == "Retain"


def test_every_bucket_denies_plain_http(data_lake_template):
    buckets = _resources(data_lake_template, "AWS::S3::Bucket")
    policies = _resources(data_lake_template, "AWS::S3::BucketPolicy").values()
    denied = []
    for policy in policies:
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            if statement.get("Condition") == {"Bool": {"aws:SecureTransport": "false"}}:
                assert statement["Effect"] == "Deny"
                denied.append(policy["Properties"]["Bucket"]["Ref"])
    assert sorted(denied) == sorted(buckets)


def test_query_results_and_landing_files_expire(data_lake_template):
    data_lake_template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "LifecycleConfiguration": {
                "Rules": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Id": "ExpireQueryResults",
                                "ExpirationInDays": config.ATHENA_RESULTS_RETENTION_DAYS,
                            }
                        )
                    ]
                )
            }
        },
    )
    data_lake_template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "LifecycleConfiguration": {
                "Rules": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Id": "ExpireLanding",
                                "Prefix": "landing/",
                                "ExpirationInDays": config.LANDING_RETENTION_DAYS,
                            }
                        )
                    ]
                )
            }
        },
    )


def test_one_glue_database_per_domain_and_one_for_the_source(data_lake_template):
    names = sorted(
        db["Properties"]["DatabaseInput"]["Name"]
        for db in _resources(data_lake_template, "AWS::Glue::Database").values()
    )
    assert names == sorted([*config.DOMAINS, config.SOURCE_DATABASE])


def test_athena_workgroup_caps_every_query(data_lake_template):
    data_lake_template.has_resource_properties(
        "AWS::Athena::WorkGroup",
        {
            "Name": config.PROJECT,
            "WorkGroupConfiguration": Match.object_like(
                {
                    "BytesScannedCutoffPerQuery": config.ATHENA_SCAN_LIMIT_BYTES,
                    # Without enforcement a client could override the cap.
                    "EnforceWorkGroupConfiguration": True,
                    "EngineVersion": {"SelectedEngineVersion": "Athena engine version 3"},
                }
            ),
        },
    )


def _table(template, name):
    (table,) = [
        t["Properties"]["TableInput"]
        for t in _resources(template, "AWS::Glue::Table").values()
        if t["Properties"]["TableInput"]["Name"] == name
    ]
    return table


def _works_table(template):
    return _table(template, "works")


def test_openalex_table_uses_partition_projection_over_the_public_bucket(data_lake_template):
    table = _works_table(data_lake_template)
    params = table["Parameters"]
    assert table["StorageDescriptor"]["Location"] == "s3://openalex/data/parquet/works/"
    assert params["projection.enabled"] == "true"
    assert params["projection.partition_date.type"] == "date"
    assert params["storage.location.template"] == (
        "s3://openalex/data/parquet/works/updated_date=${partition_date}/"
    )
    assert [key["Name"] for key in table["PartitionKeys"]] == ["partition_date"]


def test_openalex_table_exposes_no_abstracts_or_author_identities(data_lake_template):
    columns = {
        column["Name"]: column["Type"]
        for column in _works_table(data_lake_template)["StorageDescriptor"]["Columns"]
    }
    # Abstracts are about a third of the snapshot's bytes; never scan them.
    assert "abstract_inverted_index" not in columns
    # Authorships keep institutions only: no names, ORCIDs or raw strings.
    assert columns["authorships"] == (
        "array<struct<institutions:array<struct<id:string,country_code:string>>>>"
    )
    for name, type_ in columns.items():
        for personal in ("display_name:string,id:string,orcid", "raw_author_name", "orcid"):
            assert personal not in type_, name
    # The partition key must not repeat a data column.
    assert "partition_date" not in columns


def _joined(value):
    # Bucket names are CloudFormation references; render them as <ref>.
    if isinstance(value, str):
        return value
    return "".join(p if isinstance(p, str) else "<ref>" for p in value["Fn::Join"][1])


def test_api_feed_table_reads_one_partition_per_fetch_date(data_lake_template):
    table = _table(data_lake_template, "api_works")
    params = table["Parameters"]
    assert table["StorageDescriptor"]["SerdeInfo"]["SerializationLibrary"] == (
        "org.openx.data.jsonserde.JsonSerDe"
    )
    assert _joined(table["StorageDescriptor"]["Location"]) == "s3://<ref>/landing/openalex_api/"
    assert _joined(params["storage.location.template"]) == (
        "s3://<ref>/landing/openalex_api/fetch_date=${fetch_date}/"
    )
    assert [key["Name"] for key in table["PartitionKeys"]] == ["fetch_date"]
    columns = [c["Name"] for c in table["StorageDescriptor"]["Columns"]]
    assert not [c for c in columns if "author" in c or "orcid" in c]


def test_deleted_ids_table_skips_the_csv_header(data_lake_template):
    table = _table(data_lake_template, "deleted_works")
    assert table["Parameters"]["skip.header.line.count"] == "1"
    assert [c["Name"] for c in table["StorageDescriptor"]["Columns"]] == [
        "work_id",
        "deleted_date",
    ]


def test_failed_runs_leave_no_staging_data_behind(data_lake_template):
    data_lake_template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "LifecycleConfiguration": {
                "Rules": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Id": "ExpireStaging",
                                "Prefix": "staging/",
                                "ExpirationInDays": config.STAGING_RETENTION_DAYS,
                            }
                        )
                    ]
                )
            }
        },
    )
