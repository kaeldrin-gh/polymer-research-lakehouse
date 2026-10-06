from __future__ import annotations

from report import build, catalog

# Athena returns every value as a string; DuckDB returns numbers.
RAW = {
    "works": [{"works": "785851", "works_last_year": "30126"}],
    "eea": [{"version": "16", "last_year": "2024"}],
    "countries": [
        {"country_code": "DE", "publication_year": "2024", "works": "650"},
        {"country_code": "DE", "publication_year": "2025", "works": "709"},
        {"country_code": "FR", "publication_year": "2025", "works": 969},
        {"country_code": "NL", "publication_year": "2025", "works": 120},
    ],
    "scatter": [
        {
            "country_code": "NL",
            "year": "2024",
            "polymer_works": "190",
            "polymer_facilities": "6",
            "polymer_co2_tonnes": "3642000.0",
            "chemical_co2_tonnes": "13200000.0",
            "sdg_tagged_share": None,
        },
        {
            "country_code": "DE",
            "year": "2024",
            "polymer_works": "622",
            "polymer_facilities": "12",
            "polymer_co2_tonnes": None,
            "chemical_co2_tonnes": "18856000.0",
            "sdg_tagged_share": "0.39",
        },
    ],
    "topics": [{"topic_name": "Composites", "works": "5336", "share_of_year": "0.1774"}],
    "emissions": [
        {
            "year": "2024",
            "chemical_co2_tonnes": "73745580.4",
            "polymer_co2_tonnes": None,
            "polymer_facilities": "59",
        }
    ],
}


def test_shape_converts_strings_and_orders_by_size():
    payload = build.shape(RAW, 2025)
    assert payload["overview"] == {
        "works": 785851,
        "works_last_year": 30126,
        "eea_version": 16,
        "eea_last_year": 2024,
    }
    # Largest producer first, named from the EEA country list.
    assert [(c["code"], c["name"], c["last"]) for c in payload["countries"]] == [
        ("FR", "France", 969),
        ("DE", "Germany", 709),
        ("NL", "Netherlands", 120),
    ]
    assert [y["year"] for y in payload["countries"][1]["years"]] == [2024, 2025]
    assert [r["country_code"] for r in payload["scatter"]] == ["DE", "NL"]
    assert payload["scatter"][0]["polymer_co2_tonnes"] is None
    assert payload["scatter_year"] == 2024
    # A missing (confidential) total is drawn as zero, not dropped.
    assert payload["emissions"][0]["polymer_co2_tonnes"] == 0.0


def test_render_escapes_text_and_keeps_tables_for_every_chart():
    payload = build.shape(RAW, 2025)
    entries = [
        {
            "name": "products.research_vs_emissions",
            "kind": "model",
            "domain": "shared",
            "owner": "data office",
            "freshness_sla_hours": None,
            "description": "Research <next to> emissions",
            "contract": True,
            "tests": 1,
            "built_from": [],
            "upstream_products": ["research.works_by_country_year"],
            "columns": [{"name": "year", "type": "bigint", "description": ""}],
        }
    ]
    page = build.render(payload, entries)
    assert "Research &lt;next to&gt; emissions" in page
    assert page.count("<details><summary>Table:") == 4
    assert "built from <code>research.works_by_country_year</code>" in page
    # The embedded JSON cannot close its script element early.
    data = page.split('<script id="report-data" type="application/json">', 1)[1]
    assert "</" not in data.split("</script>", 1)[0]


def _node(name, resource_type="model", depends=(), meta=None, schema="research", **extra):
    return {
        "name": name,
        "alias": name,
        "schema": schema,
        "resource_type": resource_type,
        "description": f"{name} description",
        "meta": meta or {},
        "config": {"meta": {}, "contract": {"enforced": resource_type == "model"}},
        "depends_on": {"nodes": list(depends)},
        "columns": {"id": {"name": "id", "data_type": "string", "description": "key"}},
        **extra,
    }


def test_catalog_lists_products_with_their_nearest_upstream_products():
    product = {"data_product": True, "domain": "research", "owner": "team"}
    manifest = {
        "sources": {"source.p.research.works": _node("works", "source", meta=product)},
        "nodes": {
            "model.p.stg": _node("stg", depends=["source.p.research.works"]),
            "model.p.by_year": _node("by_year", depends=["model.p.stg"], meta=product),
            "test.p.unique": _node("unique", "test", depends=["model.p.by_year"]),
            "test.p.not_null": _node("not_null", "test", depends=["model.p.by_year"]),
        },
    }
    entries = {e["name"]: e for e in catalog.products(manifest)}
    # Staging is not a product; the source and the mart are.
    assert list(entries) == ["research.works", "research.by_year"]
    by_year = entries["research.by_year"]
    assert by_year["upstream_products"] == ["research.works"]
    assert by_year["built_from"] == ["research.stg"]
    assert by_year["tests"] == 2
    assert by_year["contract"] is True
    assert entries["research.works"]["upstream_products"] == []
