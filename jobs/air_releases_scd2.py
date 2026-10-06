"""Glue job: apply one EEA release to sustainability.air_releases (Iceberg).

The Lambda that landed the release also planned the SQL
(sustainability.scd2_sql, unit-tested against DuckDB); this job only reads
the landing CSV as the view `incoming`, runs the statements, and checks the
result. Runs on Glue 5 (Spark 3.5) with the Iceberg catalog configured in the
job's arguments (infra/stacks/sustainability_pipeline.py).
"""

import sys

from awsglue.utils import getResolvedOptions
from pyspark.sql import SparkSession
from pyspark.sql.types import DoubleType, IntegerType, StringType, StructField, StructType

STATEMENT_SEPARATOR = "\n;\n"  # sustainability.scd2_sql.STATEMENT_SEPARATOR

LANDING_SCHEMA = StructType(
    [
        StructField("facility_id", StringType(), nullable=False),
        StructField("reporting_year", IntegerType(), nullable=False),
        StructField("pollutant", StringType(), nullable=False),
        StructField("country_name", StringType()),
        StructField("sector_code", StringType()),
        StructField("sector_name", StringType()),
        StructField("annex_activity", StringType()),
        StructField("releases_kg", DoubleType()),
        StructField("confidentiality_reason", StringType()),
    ]
)


def main() -> None:
    args = getResolvedOptions(sys.argv, ["landing_path", "statements", "target"])
    spark = SparkSession.builder.getOrCreate()

    incoming = (
        spark.read.option("header", "true")
        .option("mode", "FAILFAST")
        .schema(LANDING_SCHEMA)
        .csv(args["landing_path"])
    )
    incoming.createOrReplaceTempView("incoming")
    expected = incoming.count()

    for statement in args["statements"].split(STATEMENT_SEPARATOR):
        spark.sql(statement)

    # After a release is applied, its rows are exactly the current rows.
    current = spark.sql(
        f"SELECT count(*) AS n FROM {args['target']} WHERE valid_to_version IS NULL"
    ).first()["n"]
    if current != expected:
        raise RuntimeError(f"{current} current rows after the load, expected {expected}")
    print(f"applied release: {expected} current rows")


if __name__ == "__main__":
    main()
