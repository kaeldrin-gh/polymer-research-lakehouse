"""Check the latest run of every pipeline state machine.

The scheduled loads run on AWS, so nothing in GitHub shows a failed run by
itself. This reads the newest run of each watched state machine and reports
the ones that failed, timed out or were aborted, and the ones that have not
run for longer than their schedule allows (a schedule that stopped firing).
The `watch` workflow turns a report into a GitHub issue; no email.

    python -m monitor.watch --report watch-report.md

The report is written only when something needs attention. It names state
machines, run names, statuses and error codes; never an ARN, which would
carry the account ID into a public issue.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime

from infra.config import GITHUB_REPOSITORY, WATCHED_STATE_MACHINES

FAILED = ("FAILED", "TIMED_OUT", "ABORTED")
RUNBOOK = f"https://github.com/{GITHUB_REPOSITORY}/blob/main/docs/operations.md#when-the-run-watch-opens-an-issue"


def problems(latest: dict[str, dict | None], now: datetime, limits: dict[str, int]) -> list[str]:
    """One line per state machine that needs attention.

    `latest` maps each watched name prefix to its newest run (name, status,
    startDate, error) or None when it has never run or was not found."""
    found = []
    for prefix, hours in limits.items():
        run = latest.get(prefix)
        if run is None:
            found.append(f"**{prefix}**: no run found.")
            continue
        started = f"{run['startDate']:%Y-%m-%d %H:%M} UTC"
        if run["status"] in FAILED:
            error = f", error `{run['error']}`" if run.get("error") else ""
            found.append(
                f"**{prefix}**: run `{run['name']}` {run['status']}{error}, started {started}."
            )
        elif run["status"] != "RUNNING" and (now - run["startDate"]).total_seconds() > hours * 3600:
            found.append(
                f"**{prefix}**: last run started {started}, more than {hours} h ago. "
                "Is its schedule still enabled?"
            )
    return found


def summary(latest: dict[str, dict | None]) -> list[str]:
    """One line per state machine for the workflow log: its newest run."""
    lines = []
    for prefix, run in latest.items():
        if run is None:
            lines.append(f"{prefix}: no run found")
        else:
            lines.append(
                f"{prefix}: {run['status']}, run {run['name']}, "
                f"started {run['startDate']:%Y-%m-%d %H:%M} UTC"
            )
    return lines


def latest_runs(client, prefixes) -> dict[str, dict | None]:
    machines = {}
    for page in client.get_paginator("list_state_machines").paginate():
        for machine in page["stateMachines"]:
            for prefix in prefixes:
                if machine["name"].startswith(prefix):
                    machines[prefix] = machine["stateMachineArn"]
    latest: dict[str, dict | None] = {}
    for prefix in prefixes:
        if prefix not in machines:
            latest[prefix] = None
            continue
        runs = client.list_executions(stateMachineArn=machines[prefix], maxResults=1)["executions"]
        if not runs:
            latest[prefix] = None
            continue
        run = runs[0]
        error = None
        if run["status"] in FAILED:
            error = client.describe_execution(executionArn=run["executionArn"]).get("error")
        latest[prefix] = {
            "name": run["name"],
            "status": run["status"],
            "startDate": run["startDate"].astimezone(UTC),
            "error": error,
        }
    return latest


def report(found: list[str], now: datetime) -> str:
    lines = "\n".join(f"- {line}" for line in found)
    return (
        f"The run watch found pipeline runs that need attention ({now:%Y-%m-%d %H:%M} UTC):\n\n"
        f"{lines}\n\n"
        f"What to do: [when the run watch opens an issue]({RUNBOOK}).\n"
    )


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m monitor.watch")
    parser.add_argument("--report", default="watch-report.md", help="where to write the report")
    args = parser.parse_args(argv)

    import boto3

    now = datetime.now(UTC)
    client = boto3.client("stepfunctions", region_name="us-east-1")
    latest = latest_runs(client, WATCHED_STATE_MACHINES)
    for line in summary(latest):
        print(line)
    found = problems(latest, now, WATCHED_STATE_MACHINES)
    for line in found:
        print(line)
    if found:
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write(report(found, now))
    else:
        print(f"All {len(WATCHED_STATE_MACHINES)} state machines ran on schedule.")
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"problems={'yes' if found else 'no'}\n")


if __name__ == "__main__":
    main()
