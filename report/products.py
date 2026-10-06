"""Read the data products for the report, from DuckDB (CI, the committed
samples) or Athena (the daily run, as the Lake Formation-governed
product-reader role, so the report sees exactly what that role may see)."""

from __future__ import annotations

import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKGROUP = "polymer-research-lakehouse"


class Products:
    def __init__(self) -> None:
        if os.getenv("WAREHOUSE", "duckdb") == "athena":
            import boto3

            self.client = boto3.client("athena", region_name="us-east-1")
            self.run = self._athena
        else:
            import duckdb

            path = os.getenv("DUCKDB_PATH", str(ROOT / "dbt" / "target" / "lakehouse.duckdb"))
            self.conn = duckdb.connect(path, read_only=True)
            self.run = self._duckdb

    def query(self, sql: str) -> list[dict]:
        return self.run(sql)

    def _duckdb(self, sql: str) -> list[dict]:
        cursor = self.conn.execute(sql)
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def _athena(self, sql: str) -> list[dict]:
        qid = self.client.start_query_execution(QueryString=sql, WorkGroup=WORKGROUP)[
            "QueryExecutionId"
        ]
        while True:
            status = self.client.get_query_execution(QueryExecutionId=qid)["QueryExecution"][
                "Status"
            ]
            if status["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
                break
            time.sleep(1)
        if status["State"] != "SUCCEEDED":
            raise RuntimeError(f"Athena query failed: {status.get('StateChangeReason')}\n{sql}")
        rows, names = [], None
        for page in self.client.get_paginator("get_query_results").paginate(QueryExecutionId=qid):
            for row in page["ResultSet"]["Rows"]:
                values = [c.get("VarCharValue") for c in row["Data"]]
                if names is None:
                    names = values
                    continue
                rows.append(dict(zip(names, values, strict=True)))
        return rows
