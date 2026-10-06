"""The data product catalog, from dbt's manifest.

Every product is described where it is built: owner, domain and freshness SLA
in `meta`, the contract in the column types, the checks as dbt tests. This
turns those into one entry per product, with what it is built from.
"""

from __future__ import annotations

import json
from pathlib import Path


def _meta(node: dict) -> dict:
    return {**node.get("meta", {}), **node.get("config", {}).get("meta", {})}


def _name(node: dict) -> str:
    if node["resource_type"] == "source":
        return f"{node['schema']}.{node['name']}"
    return f"{node['schema']}.{node.get('alias') or node['name']}"


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def products(manifest: dict) -> list[dict]:
    """One entry per data product (model or source with meta.data_product)."""
    nodes = {**manifest["nodes"], **manifest["sources"]}
    tests: dict[str, int] = {}
    for node in manifest["nodes"].values():
        if node["resource_type"] == "test":
            for parent in node["depends_on"]["nodes"]:
                tests[parent] = tests.get(parent, 0) + 1

    entries = []
    for unique_id, node in nodes.items():
        meta = _meta(node)
        if node["resource_type"] not in ("model", "source") or not meta.get("data_product"):
            continue
        parents = node.get("depends_on", {}).get("nodes", [])
        entries.append(
            {
                "name": _name(node),
                "kind": node["resource_type"],
                "domain": meta.get("domain"),
                "owner": meta.get("owner"),
                "freshness_sla_hours": meta.get("freshness_sla_hours"),
                "description": " ".join((node.get("description") or "").split()),
                "contract": bool(node.get("config", {}).get("contract", {}).get("enforced")),
                "tests": tests.get(unique_id, 0),
                "built_from": sorted(_name(nodes[p]) for p in parents if p in nodes),
                "upstream_products": _upstream_products(unique_id, nodes),
                "columns": [
                    {
                        "name": c["name"],
                        "type": c.get("data_type"),
                        "description": " ".join((c.get("description") or "").split()),
                    }
                    for c in node.get("columns", {}).values()
                ],
            }
        )
    # Domain by domain; within one, the loaded table first, then what is built
    # from it.
    order = {"research": 0, "sustainability": 1, "shared": 2}
    kinds = {"source": 0, "model": 1}
    return sorted(entries, key=lambda e: (order.get(e["domain"], 9), kinds[e["kind"]], e["name"]))


def _upstream_products(unique_id: str, nodes: dict) -> list[str]:
    """The nearest data products upstream: the lineage a consumer cares about."""
    found, seen = set(), set()
    stack = list(nodes[unique_id].get("depends_on", {}).get("nodes", []))
    while stack:
        current = stack.pop()
        if current in seen or current not in nodes:
            continue
        seen.add(current)
        if _meta(nodes[current]).get("data_product"):
            found.add(_name(nodes[current]))
        else:
            stack.extend(nodes[current].get("depends_on", {}).get("nodes", []))
    return sorted(found)
