from __future__ import annotations

from datetime import UTC, datetime, timedelta

from report import build, catalog

# Athena returns every value as a string; DuckDB returns numbers.
RAW = {
    "works": [{"works": "785851", "works_last_year": "30126", "works_base_year": "25385"}],
    "eea": [{"version": "16", "last_year": "2024"}],
    "countries": [
        {"country_code": "DE", "publication_year": "2024", "works": "650"},
        {"country_code": "DE", "publication_year": "2025", "works": "709"},
        {"country_code": "FR", "publication_year": "2025", "works": 969},
        {"country_code": "NL", "publication_year": "2025", "works": 120},
    ],
    "open_access": [
        {
            "publication_year": "2025",
            "europe_works": "1000",
            "europe_open_access": "696",
            "all_works": "2000",
            "all_open_access": "1026",
        },
        {
            "publication_year": "2010",
            "europe_works": "1000",
            "europe_open_access": "229",
            "all_works": "0",
            "all_open_access": "0",
        },
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
    "topics": [
        {
            "topic_name": "Composites",
            "publication_year": "2015",
            "works": "2826",
            "share_of_year": "0.1114",
        },
        {
            "topic_name": "Composites",
            "publication_year": "2025",
            "works": "5336",
            "share_of_year": "0.1774",
        },
        {
            "topic_name": "Textiles",
            "publication_year": "2015",
            "works": "4281",
            "share_of_year": "0.1687",
        },
        {
            "topic_name": "Textiles",
            "publication_year": "2025",
            "works": "3093",
            "share_of_year": "0.1028",
        },
        # New since the base year: compared against zero.
        {
            "topic_name": "Recycling",
            "publication_year": "2025",
            "works": "900",
            "share_of_year": "0.03",
        },
        # Below the share threshold in both years: left out.
        {
            "topic_name": "Niche",
            "publication_year": "2015",
            "works": "10",
            "share_of_year": "0.001",
        },
        {
            "topic_name": "Niche",
            "publication_year": "2025",
            "works": "30",
            "share_of_year": "0.019",
        },
    ],
    "emissions": [
        {
            "year": "2024",
            "chemical_co2_tonnes": "73745580.4",
            "polymer_co2_tonnes": None,
            "polymer_facilities": "59",
            "countries": "22",
        }
    ],
    "country_emissions": [
        {"country_code": "DE", "reporting_year": "2019", "chemical_co2_tonnes": "20000000"},
        {"country_code": "DE", "reporting_year": "2024", "chemical_co2_tonnes": "15000000"},
        {"country_code": "FR", "reporting_year": "2019", "chemical_co2_tonnes": "10000000"},
        {"country_code": "FR", "reporting_year": "2024", "chemical_co2_tonnes": "9000000"},
        # Reported only in the base year: not compared.
        {"country_code": "GB", "reporting_year": "2019", "chemical_co2_tonnes": "8000000"},
        # Confidential in the latest year: not compared.
        {"country_code": "NL", "reporting_year": "2019", "chemical_co2_tonnes": "5000000"},
        {"country_code": "NL", "reporting_year": "2024", "chemical_co2_tonnes": None},
    ],
    "coverage": [
        {
            "country_code": "DE",
            "reporting_year": "2023",
            "facilities": "1168",
            "status": "reported",
        },
        {"country_code": "CZ", "reporting_year": "2018", "facilities": "0", "status": "missing"},
        {"country_code": "CZ", "reporting_year": "2023", "facilities": "0", "status": "missing"},
        {"country_code": "CZ", "reporting_year": "2024", "facilities": "0", "status": "missing"},
        {"country_code": "CZ", "reporting_year": "2022", "facilities": "611", "status": "reported"},
        {"country_code": "GB", "reporting_year": "2020", "facilities": "0", "status": "left"},
        {"country_code": "GB", "reporting_year": "2021", "facilities": "0", "status": "left"},
        {
            "country_code": "RS",
            "reporting_year": "2007",
            "facilities": "0",
            "status": "not yet reporting",
        },
    ],
}

PRODUCT = {
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


def test_shape_converts_strings_and_orders_by_size():
    payload = build.shape(RAW, 2025)
    assert payload["overview"] == {
        "works": 785851,
        "works_last_year": 30126,
        "works_base_year": 25385,
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
    assert payload["emissions"][0]["countries"] == 22


def test_open_access_is_a_share_per_year_and_none_without_works():
    assert build.shape(RAW, 2025)["open_access"] == [
        {"year": 2010, "europe": 0.229, "world": None},
        {"year": 2025, "europe": 0.696, "world": 0.513},
    ]


def test_topic_shift_orders_by_gain_and_drops_small_topics():
    payload = build.shape(RAW, 2025)
    shift = {t["topic_name"]: t for t in payload["topic_shift"]}
    assert list(shift) == ["Composites", "Recycling", "Textiles"]
    assert round(shift["Composites"]["change"], 4) == 0.066
    assert (shift["Recycling"]["share_before"], shift["Recycling"]["works_before"]) == (0.0, 0)
    assert payload["topic_base_year"] == 2015


def test_like_for_like_compares_only_countries_reported_in_both_years():
    e = build.shape(RAW, 2025)["like_for_like"]
    assert (e["base_year"], e["year"], e["countries"]) == (2019, 2024, 2)
    assert (e["before"], e["after"]) == (30_000_000.0, 24_000_000.0)
    assert round(e["change"], 2) == -0.2


def test_coverage_is_ordered_by_country_name_and_gaps_are_spans():
    payload = build.shape(RAW, 2025)
    assert [(c["name"], c["year"]) for c in payload["coverage"][:3]] == [
        ("Czechia", 2018),
        ("Czechia", 2022),
        ("Czechia", 2023),
    ]
    assert payload["coverage"][1]["facilities"] == 611
    # Missing years read as ranges; a country that left is listed apart, and
    # one that has not started reporting yet is no gap.
    assert build.coverage_gaps(payload["coverage"]) == [
        {"name": "Czechia", "missing": "2018, 2023–2024", "left": ""},
        {"name": "United Kingdom", "missing": "", "left": "2020–2021"},
    ]


def test_findings_state_the_numbers_behind_them():
    found = build.findings(build.shape(RAW, 2025))
    assert [f["value"] for f in found] == ["↑ 19%", "70%", "+6.6 pp", "↓ 20%"]
    assert found[0]["text"].startswith("more polymer and plastics works published in 2025")
    assert "up from 23% in 2010; 51% worldwide" in found[1]["text"]
    assert found[2]["text"].startswith("Composites: the fastest-growing topic, from 11% to 18%")
    assert found[3]["text"].endswith("in the 2 countries that reported both years.")


def test_findings_skip_what_the_data_cannot_support():
    raw = {
        **RAW,
        "works": [{"works": "3000", "works_last_year": "300", "works_base_year": "0"}],
        "open_access": [],
        "topics": [],
        "country_emissions": [],
        "coverage": [],
    }
    payload = build.shape(raw, 2025)
    assert build.findings(payload) == []
    # No findings, no empty section.
    assert "What the data shows" not in build.render(payload, [])


def test_freshness_is_checked_against_the_target():
    built = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)
    entry = {"freshness_sla_hours": 48}
    assert build._freshness(entry, built - timedelta(hours=2), built) == (
        "<span class='ok'>✓ refreshed 2 h ago</span>, target 48 h"
    )
    assert build._freshness(entry, built - timedelta(days=3), built) == (
        "<span class='late'>✗ late: refreshed 3 days ago</span>, target 48 h"
    )
    # Without a target, only the age; without a time (the samples), only the target.
    no_target = {"freshness_sla_hours": None}
    assert build._freshness(no_target, built, built) == "refreshed under an hour ago"
    assert build._freshness(entry, None, built) == "fresh within 48 h"
    assert build._freshness(no_target, None, built) is None


def test_render_escapes_text_and_keeps_tables_for_every_chart():
    built = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)
    updated = {PRODUCT["name"]: built - timedelta(hours=2)}
    page = build.render(build.shape(RAW, 2025), [PRODUCT], built, updated)
    assert "Research &lt;next to&gt; emissions" in page
    assert page.count("<details><summary>Table:") == 6
    assert "built from <code>research.works_by_country_year</code>" in page
    assert "· 1 test</div>" in page
    assert "refreshed 2 h ago" in page
    assert "What the data shows" in page
    # The emissions chart states its like-for-like comparison itself.
    assert (
        "Compared on the same 2 countries, the chemical industry's CO2 fell 20% from 2019 "
        "to 2024." in page
    )
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
