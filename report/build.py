"""Build the static site: the report and the data product catalog.

The report reads the data products from DuckDB (CI, the committed samples) or
Athena (the daily run, as the product-reader role). The catalog comes from
dbt's manifest. Every chart has a table with the same numbers, so nothing
depends on the CDN or on hovering.

    python -m report.build --out site --manifest dbt/target/manifest.json
"""

from __future__ import annotations

import argparse
import csv
import html
import json
from datetime import UTC, date, datetime
from pathlib import Path

from report.catalog import load_manifest, products
from report.charts import SCRIPT
from report.products import ROOT, Products

REPO = "https://github.com/kaeldrin-gh/polymer-research-lakehouse"
FIRST_YEAR = 2010
SMALL_MULTIPLES = 8
# Topic shifts compare the last complete year with the year this many before.
TOPIC_YEARS_BACK = 10
# Topics below this share of the year's works in both years are left out.
TOPIC_MIN_SHARE = 0.02
# Before the pandemic and the energy crisis; compared on the same countries only,
# because a country's latest years can be missing from a release.
EMISSIONS_BASE_YEAR = 2019
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def european_countries() -> dict[str, str]:
    """ISO code -> name for the countries in the EEA release (the dbt seed)."""
    path = ROOT / "dbt" / "seeds" / "eea_country_codes.csv"
    with path.open(encoding="utf-8") as handle:
        return {row["country_code"]: row["country_name"] for row in csv.DictReader(handle)}


def _int(value) -> int:
    return int(float(value)) if value not in (None, "") else 0


def _float(value) -> float | None:
    return float(value) if value not in (None, "") else None


def fetch(source: Products, last_year: int) -> dict:
    codes = ", ".join(f"'{c}'" for c in european_countries())
    base_year = last_year - TOPIC_YEARS_BACK
    return {
        "works": source.query(
            "SELECT sum(CASE WHEN NOT is_xpac THEN 1 ELSE 0 END) AS works, "
            f"sum(CASE WHEN NOT is_xpac AND publication_year = {last_year} THEN 1 ELSE 0 END) "
            "AS works_last_year, "
            f"sum(CASE WHEN NOT is_xpac AND publication_year = {base_year} THEN 1 ELSE 0 END) "
            "AS works_base_year FROM research.works"
        ),
        "open_access": source.query(
            "SELECT publication_year, "
            f"sum(CASE WHEN country_code IN ({codes}) THEN works ELSE 0 END) AS europe_works, "
            f"sum(CASE WHEN country_code IN ({codes}) THEN open_access_works ELSE 0 END) "
            "AS europe_open_access, sum(works) AS all_works, "
            "sum(open_access_works) AS all_open_access FROM research.works_by_country_year "
            f"WHERE publication_year BETWEEN {FIRST_YEAR} AND {last_year} "
            "GROUP BY publication_year"
        ),
        "eea": source.query(
            "SELECT max(valid_from_version) AS version, max(reporting_year) AS last_year "
            "FROM sustainability.air_releases"
        ),
        "countries": source.query(
            "SELECT country_code, publication_year, works FROM research.works_by_country_year "
            f"WHERE publication_year BETWEEN {FIRST_YEAR} AND {last_year} "
            f"AND country_code IN ({codes})"
        ),
        "scatter": source.query(
            "SELECT * FROM products.research_vs_emissions "
            "WHERE year = (SELECT max(year) FROM products.research_vs_emissions)"
        ),
        "topics": source.query(
            "SELECT topic_name, publication_year, works, share_of_year FROM research.topic_trends "
            f"WHERE publication_year IN ({base_year}, {last_year})"
        ),
        "emissions": source.query(
            "SELECT reporting_year AS year, sum(chemical_co2_tonnes) AS chemical_co2_tonnes, "
            "sum(polymer_co2_tonnes) AS polymer_co2_tonnes, "
            "sum(polymer_facilities) AS polymer_facilities, "
            "count(DISTINCT country_code) AS countries "
            "FROM sustainability.chemical_sector_by_country_year GROUP BY reporting_year "
            "ORDER BY reporting_year"
        ),
        "country_emissions": source.query(
            "SELECT country_code, reporting_year, chemical_co2_tonnes "
            "FROM sustainability.chemical_sector_by_country_year "
            f"WHERE reporting_year IN ({EMISSIONS_BASE_YEAR}, "
            "(SELECT max(reporting_year) FROM sustainability.chemical_sector_by_country_year))"
        ),
    }


def _open_access(rows: list[dict]) -> list[dict]:
    def share(part, whole) -> float | None:
        return _int(part) / _int(whole) if _int(whole) else None

    years = [
        {
            "year": _int(r["publication_year"]),
            "europe": share(r["europe_open_access"], r["europe_works"]),
            "world": share(r["all_open_access"], r["all_works"]),
        }
        for r in rows
    ]
    return sorted(years, key=lambda y: y["year"])


def _topic_shift(rows: list[dict], base_year: int, last_year: int) -> list[dict]:
    """Each topic's share of the year's works then and now, biggest gain first."""
    by_topic: dict[str, dict[int, dict]] = {}
    for r in rows:
        by_topic.setdefault(r["topic_name"], {})[_int(r["publication_year"])] = r
    topics = []
    for name, years in by_topic.items():
        before, after = years.get(base_year, {}), years.get(last_year, {})
        share_before = _float(before.get("share_of_year")) or 0.0
        share = _float(after.get("share_of_year")) or 0.0
        if max(share_before, share) < TOPIC_MIN_SHARE:
            continue
        topics.append(
            {
                "topic_name": name,
                "works_before": _int(before.get("works")),
                "share_before": share_before,
                "works": _int(after.get("works")),
                "share": share,
                "change": share - share_before,
            }
        )
    return sorted(topics, key=lambda t: (-t["change"], t["topic_name"]))


def _like_for_like(rows: list[dict]) -> dict | None:
    """Chemical industry CO2 in the base year and the latest year, summed over
    the countries that reported a value in both."""
    by_year: dict[int, dict[str, float]] = {}
    for r in rows:
        tonnes = _float(r["chemical_co2_tonnes"])
        if tonnes:
            by_year.setdefault(_int(r["reporting_year"]), {})[r["country_code"]] = tonnes
    latest = max(by_year, default=None)
    if EMISSIONS_BASE_YEAR not in by_year or latest == EMISSIONS_BASE_YEAR:
        return None
    common = by_year[EMISSIONS_BASE_YEAR].keys() & by_year[latest].keys()
    before = sum(by_year[EMISSIONS_BASE_YEAR][c] for c in common)
    after = sum(by_year[latest][c] for c in common)
    if not before:
        return None
    return {
        "base_year": EMISSIONS_BASE_YEAR,
        "year": latest,
        "countries": len(common),
        "before": before,
        "after": after,
        "change": after / before - 1,
    }


def findings(payload: dict) -> list[dict]:
    """The page's headline takeaways, computed from the same numbers as the
    charts, so they stay true as the data refreshes. A finding whose inputs
    are missing (as in the CI samples) is left out."""
    o, last_year = payload["overview"], payload["last_year"]
    base_year = last_year - TOPIC_YEARS_BACK
    found = []
    if o["works_base_year"] and o["works_last_year"]:
        change = o["works_last_year"] / o["works_base_year"] - 1
        found.append(
            {
                "value": f"{'↑' if change >= 0 else '↓'} {abs(change):.0%}",
                "text": f"{'more' if change >= 0 else 'fewer'} polymer and plastics works "
                f"published in {last_year} than in {base_year}: {o['works_last_year']:,} "
                f"against {o['works_base_year']:,}.",
            }
        )
    oa = {y["year"]: y for y in payload["open_access"]}
    first, last = oa.get(payload["first_year"], {}), oa.get(last_year, {})
    if first.get("europe") is not None and last.get("europe") is not None:
        direction = "up" if last["europe"] >= first["europe"] else "down"
        world = f"; {_pct(last['world'])} worldwide" if last.get("world") is not None else ""
        found.append(
            {
                "value": _pct(last["europe"]),
                "text": f"of the polymer works from European countries in {last_year} are open "
                f"access, {direction} from {_pct(first['europe'])} in "
                f"{payload['first_year']}{world}.",
            }
        )
    rising = [t for t in payload["topic_shift"] if t["change"] > 0]
    if rising:
        t = rising[0]
        found.append(
            {
                "value": f"+{t['change'] * 100:.1f} pp",
                "text": f"{t['topic_name']}: the fastest-growing topic, from "
                f"{_pct(t['share_before'])} to {_pct(t['share'])} of the year's works since "
                f"{base_year}.",
            }
        )
    e = payload["like_for_like"]
    if e:
        found.append(
            {
                "value": f"{'↓' if e['change'] < 0 else '↑'} {abs(e['change']):.0%}",
                "text": f"{'less' if e['change'] < 0 else 'more'} CO2 released to air by the "
                f"chemical industry in {e['year']} than in {e['base_year']}, in the "
                f"{e['countries']} countries that reported both years.",
            }
        )
    return found


def shape(raw: dict, last_year: int) -> dict:
    """Turn product rows into the page payload. Pure, so it is tested without
    a warehouse; Athena returns strings, DuckDB numbers, both are converted."""
    names = european_countries()
    by_country: dict[str, list[dict]] = {}
    for row in raw["countries"]:
        by_country.setdefault(row["country_code"], []).append(
            {"year": _int(row["publication_year"]), "works": _int(row["works"])}
        )
    countries = []
    for code, years in by_country.items():
        years.sort(key=lambda y: y["year"])
        last = next((y["works"] for y in years if y["year"] == last_year), 0)
        countries.append(
            {"code": code, "name": names.get(code, code), "years": years, "last": last}
        )
    countries.sort(key=lambda c: (-c["last"], c["name"]))

    scatter = [
        {
            "country_code": r["country_code"],
            "polymer_works": _int(r["polymer_works"]),
            "polymer_facilities": _int(r["polymer_facilities"]),
            "polymer_co2_tonnes": _float(r["polymer_co2_tonnes"]),
            "chemical_co2_tonnes": _float(r["chemical_co2_tonnes"]),
            "sdg_tagged_share": _float(r["sdg_tagged_share"]),
        }
        for r in raw["scatter"]
    ]
    scatter.sort(key=lambda r: -r["polymer_works"])
    works, eea = raw["works"][0], raw["eea"][0]
    return {
        "first_year": FIRST_YEAR,
        "last_year": last_year,
        "overview": {
            "works": _int(works["works"]),
            "works_last_year": _int(works["works_last_year"]),
            "works_base_year": _int(works["works_base_year"]),
            "eea_version": _int(eea["version"]),
            "eea_last_year": _int(eea["last_year"]),
        },
        "countries": countries[:SMALL_MULTIPLES],
        "open_access": _open_access(raw["open_access"]),
        "scatter_year": _int(raw["scatter"][0]["year"]) if raw["scatter"] else None,
        "scatter": scatter,
        "topic_base_year": last_year - TOPIC_YEARS_BACK,
        "topic_shift": _topic_shift(raw["topics"], last_year - TOPIC_YEARS_BACK, last_year),
        "emissions": [
            {
                "year": _int(r["year"]),
                "chemical_co2_tonnes": _float(r["chemical_co2_tonnes"]) or 0.0,
                "polymer_co2_tonnes": _float(r["polymer_co2_tonnes"]) or 0.0,
                "polymer_facilities": _int(r["polymer_facilities"]),
                "countries": _int(r["countries"]),
            }
            for r in raw["emissions"]
        ],
        "like_for_like": _like_for_like(raw["country_emissions"]),
    }


def _json_script(payload: dict) -> str:
    # "</" would end the <script> element early; the JSON is identical once parsed.
    return json.dumps(payload, separators=(",", ":"), default=str).replace("</", "<\\/")


def _table(headers: list[str], rows: list[list], left: int = 1) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>" for row in rows
    )
    return f"<table class='left-{left}'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _mt(tonnes: float | None) -> str:
    return "–" if tonnes is None else f"{tonnes / 1e6:.2f}"


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value * 100:.0f}%"


def _age(hours: float) -> str:
    if hours < 1:
        return "under an hour"
    if hours < 48:
        return f"{hours:.0f} h"
    return f"{hours / 24:.0f} days"


def _freshness(entry: dict, updated: datetime | None, built_at: datetime) -> str | None:
    """Last refresh against the product's freshness target, as text and icon."""
    sla = entry["freshness_sla_hours"]
    if updated is None:
        return f"fresh within {sla} h" if sla else None
    hours = (built_at - updated).total_seconds() / 3600
    refreshed = f"refreshed {_age(hours)} ago"
    if not sla:
        return refreshed
    if hours <= sla:
        return f"<span class='ok'>✓ {refreshed}</span>, target {sla} h"
    return f"<span class='late'>✗ late: {refreshed}</span>, target {sla} h"


def _catalog_html(entries: list[dict], updated: dict[str, datetime], built_at: datetime) -> str:
    cards = []
    for e in entries:
        facts = [
            f"<span class='pill'>{html.escape(e['domain'] or '')}</span>",
            f"owner: {html.escape(e['owner'] or '–')}",
        ]
        freshness = _freshness(e, updated.get(e["name"]), built_at)
        if freshness:
            facts.append(freshness)
        facts.append("contract enforced" if e["contract"] else "loaded by the pipeline")
        facts.append(f"{e['tests']} test{'' if e['tests'] == 1 else 's'}")
        lineage = (
            "built from "
            + ", ".join(f"<code>{html.escape(u)}</code>" for u in e["upstream_products"])
            if e["upstream_products"]
            else "loaded from the source"
        )
        columns = _table(
            ["Column", "Type", "Description"],
            [[c["name"], c["type"] or "", c["description"]] for c in e["columns"]],
            left=3,
        )
        cards.append(
            f"<div class='product'><h3><code>{html.escape(e['name'])}</code></h3>"
            f"<div class='facts'>{' · '.join(facts)}</div>"
            f"<p>{html.escape(e['description'])}</p><p class='lineage'>{lineage}</p>"
            f"<details><summary>Columns</summary>{columns}</details></div>"
        )
    return "".join(cards)


def render(
    payload: dict,
    catalog: list[dict],
    built_at: datetime | None = None,
    updated: dict[str, datetime] | None = None,
) -> str:
    built_at = built_at or datetime.now(UTC)
    updated = updated or {}
    o, last_year = payload["overview"], payload["last_year"]
    base_year = payload["topic_base_year"]
    finding_cards = "".join(
        f"<div class='finding'><div class='num'>{html.escape(f['value'])}</div>"
        f"<p>{html.escape(f['text'])}</p></div>"
        for f in findings(payload)
    )
    findings_html = (
        f"<h2>What the data shows</h2><div class='findings'>{finding_cards}</div>"
        if finding_cards
        else ""
    )
    tiles = [
        (f"{o['works']:,}", "polymer and plastics works (OpenAlex)"),
        (f"{o['works_last_year']:,}", f"published in {last_year}"),
        (f"v{o['eea_version']}", f"EEA industrial release (data to {o['eea_last_year']})"),
        (f"{len(catalog)}", "data products"),
    ]
    cards = "".join(
        f"<div class='card'><div class='num'>{v}</div><div class='lbl'>{html.escape(k)}</div></div>"
        for v, k in tiles
    )
    country_rows = [
        [c["name"], *[f"{y['works']:,}" for y in c["years"] if y["year"] >= last_year - 4]]
        for c in payload["countries"]
    ]
    recent_years = [str(y) for y in range(last_year - 4, last_year + 1)]
    scatter_rows = [
        [
            r["country_code"],
            f"{r['polymer_works']:,}",
            f"{r['polymer_facilities']:,}",
            _mt(r["polymer_co2_tonnes"]),
            _mt(r["chemical_co2_tonnes"]),
        ]
        for r in payload["scatter"]
    ]
    open_access_rows = [
        [y["year"], _pct(y["europe"]), _pct(y["world"])] for y in payload["open_access"]
    ]
    topic_rows = [
        [
            t["topic_name"],
            _pct(t["share_before"]),
            _pct(t["share"]),
            f"{t['change'] * 100:+.1f}",
            f"{t['works']:,}",
        ]
        for t in payload["topic_shift"]
    ]
    emission_rows = [
        [
            e["year"],
            _mt(e["chemical_co2_tonnes"]),
            _mt(e["polymer_co2_tonnes"]),
            e["polymer_facilities"],
            e["countries"],
        ]
        for e in payload["emissions"]
    ]
    coverage = ""
    if payload["emissions"]:
        first, last = payload["emissions"][0], payload["emissions"][-1]
        e = payload["like_for_like"]
        coverage = (
            f" Not every country appears in every year ({first['countries']} in "
            f"{first['year']}, {last['countries']} in {last['year']}): the United Kingdom left "
            "the EU after 2019, and a country's latest years can be missing from a release "
            "until it reports them, so the totals are not like for like"
            + (
                f". Compared on the same {e['countries']} countries, the chemical industry's "
                f"CO2 {'fell' if e['change'] < 0 else 'rose'} {abs(e['change']):.0%} from "
                f"{e['base_year']} to {e['year']}."
                if e
                else "."
            )
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Polymer research and emissions</title>
<meta name="description" content="Polymer and plastics research (OpenAlex) next to the emissions of polymer plants and the chemical industry (EEA), built by a small data mesh on AWS.">
<style>{PAGE_STYLE}</style>
</head>
<body>
<header>
  <h1>Polymer research and emissions</h1>
  <p class="sub">Polymer and plastics research from <a href="https://openalex.org">OpenAlex</a>
  (CC0) next to industrial releases to air from the European Environment Agency's
  industrial reporting (CC BY 4.0 © EEA). Built from the data products of the
  <a href="{REPO}">polymer-research-lakehouse</a>; read as its Lake Formation-governed
  product-reader role. Built {built_at:%d %b %Y %H:%M} UTC.</p>
</header>
<div class="cards">{cards}</div>
{findings_html}

<h2>Polymer research in Europe's largest producers</h2>
<p class="sub">Polymer and plastics works per year with at least one author at an
institution in the country, {payload["first_year"]} to {last_year}. Same scale in every
panel. A work with authors in several countries counts for each; OpenAlex's xpac works
are left out, as on openalex.org.</p>
<div class="chart"><div id="chart-countries" class="minis" role="img"
  aria-label="Polymer works per year for the European countries with the most works"></div></div>
<details><summary>Table: works per country, {recent_years[0]} to {last_year}</summary>
{_table(["Country", *recent_years], country_rows)}</details>

<h2>Open access</h2>
<p class="sub">Share of polymer and plastics works that are free to read, per publication
year: authors at institutions in the European countries of the EEA's industrial reporting,
and in every country. A work with authors in several countries counts for each.</p>
<div class="chart">
  <div class="legend">
    <span class="key"><span class="swatch" style="background:var(--series-1)"></span>Europe</span>
    <span class="key"><span class="swatch" style="background:var(--text-muted)"></span>all countries</span>
  </div>
  <div id="chart-openaccess" class="plot" role="img"
    aria-label="Open access share of polymer works per year, Europe and worldwide"></div>
</div>
<details><summary>Table: open access share per year</summary>
{_table(["Year", "Europe", "All countries"], open_access_rows)}</details>

<h2>Research next to polymer plants' emissions, {payload["scatter_year"]}</h2>
<p class="sub">Each dot is a country: polymer works against the CO2 its polymer
production plants (E-PRTR activity 4(a)(viii)) released to air. Both axes are
logarithmic. Only countries whose polymer plants reported CO2 appear. Descriptive only:
it puts two facts side by side, it does not link them.</p>
<div class="chart"><div id="chart-scatter" class="plot" role="img"
  aria-label="Polymer works against polymer plants' CO2 per country"></div></div>
<details><summary>Table: research and emissions per country</summary>
{_table(["Country", "Polymer works", "Polymer plants", "Plants' CO2 (Mt)", "Chemical industry CO2 (Mt)"], scatter_rows)}</details>

<h2>How polymer research shifted, {base_year} to {last_year}</h2>
<p class="sub">Each OpenAlex topic's share of the year's polymer works, in {base_year} and
{last_year}, for topics with at least {TOPIC_MIN_SHARE:.0%} in either year; the biggest gain
first. The label is the change in percentage points.</p>
<div class="chart">
  <div class="legend">
    <span class="key"><span class="ring"></span>{base_year}</span>
    <span class="key"><span class="ring filled"></span>{last_year}</span>
  </div>
  <div id="chart-topics" class="plot" role="img"
    aria-label="Topic shares of polymer works, {base_year} and {last_year}"></div>
</div>
<details><summary>Table: topic shares</summary>
{_table(["Topic", str(base_year), str(last_year), "Change (pp)", f"Works {last_year}"], topic_rows)}</details>

<h2>Chemical industry releases of CO2 to air</h2>
<p class="sub">All reporting countries, current EEA release. Polymer plants are part of
the chemical industry; values withheld as confidential are not counted.{coverage}</p>
<div class="chart">
  <div class="legend">
    <span class="key"><span class="swatch" style="background:var(--series-1)"></span>chemical industry</span>
    <span class="key"><span class="swatch" style="background:var(--series-2)"></span>polymer plants</span>
  </div>
  <div id="chart-emissions" class="plot" role="img" aria-label="Chemical industry and polymer plants CO2 per year"></div>
</div>
<details><summary>Table: CO2 per year</summary>
{_table(["Year", "Chemical industry (Mt)", "Polymer plants (Mt)", "Polymer plants", "Countries"], emission_rows)}</details>

<h2 id="catalog">Data product catalog</h2>
<p class="sub">Every data product of the mesh, generated from the dbt project: who owns
it, when it was last refreshed against its freshness target, its enforced contract and
tests, and the products it is built from. Full model documentation and lineage graph:
<a href="docs/">dbt docs</a>.</p>
<div class="products">{_catalog_html(catalog, updated, built_at)}</div>

<h2>About</h2>
<ul class="notes">
  <li>How the data is loaded, modeled, governed and tested:
  <a href="{REPO}#readme">README</a> · <a href="{REPO}/blob/main/docs/design.md">design</a>.</li>
  <li>Polymer works: OpenAlex subfield Polymers and Plastics. Emissions: the EEA's
  industrial reporting under the IED and E-PRTR, releases to air.</li>
</ul>
<noscript><p>The charts need JavaScript; every number is in the tables.</p></noscript>
<script id="report-data" type="application/json">{_json_script(payload)}</script>
<script type="module">{SCRIPT}</script>
</body>
</html>
"""


PAGE_STYLE = """
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --border: rgba(11, 11, 11, 0.10);
  --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #898781;
  --grid: #e1e0d9; --baseline: #c3c2b7;
  --series-1: #2a78d6; --series-2: #eb6834;
  --good: #1f7a3d; --critical: #c62828;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --border: rgba(255, 255, 255, 0.10);
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #898781;
    --grid: #2c2c2a; --baseline: #383835;
    --series-1: #3987e5; --series-2: #d95926;
    --good: #4cc27a; --critical: #ff6b6b;
  }
}
* { box-sizing: border-box; }
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; max-width: 1100px;
       margin: 0 auto; padding: 2rem 16px 3rem; background: var(--page);
       color: var(--text-primary); line-height: 1.45; }
h1 { font-size: 1.6rem; margin: 0; }
h2 { font-size: 1.1rem; margin-top: 2.6rem; padding-bottom: 6px;
     border-bottom: 1px solid var(--grid); }
h3 { font-size: 0.98rem; margin: 0 0 4px; }
.sub { color: var(--text-secondary); font-size: 0.9rem; margin: 0.35rem 0 0.8rem; }
a { color: var(--series-1); }
code { font-size: 0.86em; }
table { border-collapse: collapse; margin-top: 8px; font-variant-numeric: tabular-nums;
        display: block; overflow-x: auto; max-width: 100%; }
td, th { border-bottom: 1px solid var(--grid); padding: 5px 12px; font-size: 0.88rem;
         text-align: right; white-space: nowrap; }
th { color: var(--text-secondary); font-weight: 600; }
th:nth-child(1), td:nth-child(1) { text-align: left; }
.left-3 th, .left-3 td { text-align: left; white-space: normal; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
         gap: 12px; margin-top: 16px; }
.card, .product, .chart { background: var(--surface); border: 1px solid var(--border);
                          border-radius: 10px; }
.card { padding: 12px 16px; }
.num { font-size: 1.4rem; font-weight: 600; }
.lbl { color: var(--text-secondary); font-size: 0.82rem; margin-top: 2px; }
.chart { padding: 14px 16px 8px; margin: 14px 0 0; }
.plot { width: 100%; min-height: 60px; }
.minis { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
         gap: 8px 16px; }
.mini-title { font-size: 0.85rem; color: var(--text-secondary); }
.legend { display: flex; gap: 18px; flex-wrap: wrap; margin: 0 0 6px;
          color: var(--text-secondary); font-size: 0.85rem; }
.key { display: inline-flex; align-items: center; gap: 6px; }
.swatch { display: inline-block; width: 14px; height: 12px; border-radius: 2px; }
.ring { display: inline-block; width: 11px; height: 11px; border-radius: 50%;
        border: 2px solid var(--text-muted); }
.ring.filled { background: var(--series-1); border-color: var(--series-1); }
.findings { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 12px; margin-top: 14px; }
.finding { background: var(--surface); border: 1px solid var(--border);
           border-left: 3px solid var(--series-1); border-radius: 10px; padding: 12px 16px; }
.finding p { margin: 4px 0 0; color: var(--text-secondary); font-size: 0.88rem; }
.ok { color: var(--good); }
.late { color: var(--critical); font-weight: 600; }
.products { display: grid; gap: 12px; }
.product { padding: 12px 16px; }
.product p { margin: 6px 0; font-size: 0.9rem; }
.facts { color: var(--text-secondary); font-size: 0.84rem; }
.lineage { color: var(--text-secondary); }
.pill { border: 1px solid var(--border); border-radius: 999px; padding: 0 8px; }
details { margin-top: 8px; color: var(--text-secondary); font-size: 0.88rem; }
.notes { color: var(--text-secondary); font-size: 0.9rem; padding-left: 1.2rem; }
.notes li { margin: 0.35rem 0; }
noscript p { color: var(--text-secondary); }
"""


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m report.build")
    parser.add_argument("--out", default="site", help="output directory")
    parser.add_argument("--manifest", default=str(ROOT / "dbt" / "target" / "manifest.json"))
    parser.add_argument("--as-of", default=None, help="date the report is built for (ISO)")
    args = parser.parse_args(argv)
    as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.now(UTC).date()
    last_year = as_of.year - 1  # the last complete publication year
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    source = Products()
    payload = shape(fetch(source, last_year), last_year)
    catalog = products(load_manifest(Path(args.manifest)))
    updated = source.updated_at([e["name"] for e in catalog])
    built_at = datetime.now(UTC)
    (out / "index.html").write_text(render(payload, catalog, built_at, updated), encoding="utf-8")
    (out / "report.json").write_text(
        json.dumps(
            {"report": payload, "catalog": catalog, "updated": updated}, default=str, indent=1
        ),
        encoding="utf-8",
    )
    print(
        f"wrote {out / 'index.html'}: {len(catalog)} products, {len(payload['countries'])} countries"
    )


if __name__ == "__main__":
    main()
