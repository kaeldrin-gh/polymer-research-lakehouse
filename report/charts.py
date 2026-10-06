"""Client-side charts (Observable Plot, rendered in the browser).

The page embeds its numbers as JSON; this script draws them as SVG with hover
tooltips, in the page's light or dark tokens. Kept as a plain string so the
braces need no escaping.
"""

PLOT_MODULE = "https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6/+esm"

SCRIPT = """
import * as Plot from "__PLOT_MODULE__";

const data = JSON.parse(document.getElementById("report-data").textContent);

function tokens() {
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  return {
    surface: v("--surface"), ink: v("--text-primary"), secondary: v("--text-secondary"),
    muted: v("--text-muted"), grid: v("--grid"), baseline: v("--baseline"),
    s1: v("--series-1"), s2: v("--series-2"), warning: v("--warning"),
  };
}

const fmt = (n) => (n == null ? "–" : Math.round(n).toLocaleString("en-US"));
const mt = (n) => (n == null ? "–" : `${(n / 1e6).toFixed(2)} Mt`);
const pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);

function frame(t, width, height, extra = {}) {
  return {
    width, height, marginLeft: 48, marginRight: 16, marginTop: 16, marginBottom: 32,
    style: { background: "transparent", color: t.secondary, fontSize: "12px",
             fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif" },
    ...extra,
  };
}

// One small chart per country, all on the same y scale so heights compare.
function countries(t, el) {
  const max = Math.max(...data.countries.flatMap((c) => c.years.map((y) => y.works)), 1);
  const cells = data.countries.map((c) => {
    const box = document.createElement("div");
    box.className = "mini";
    const title = document.createElement("div");
    title.className = "mini-title";
    title.textContent = `${c.name} · ${fmt(c.last)} in ${data.last_year}`;
    box.append(title);
    box.append(Plot.plot(frame(t, 210, 120, {
      marginLeft: 36, marginBottom: 22, marginTop: 8,
      x: { label: null, tickFormat: (d) => `${d}`, ticks: [data.first_year, data.last_year] },
      y: { label: null, domain: [0, max], ticks: 3, grid: true },
      marks: [
        Plot.areaY(c.years, { x: "year", y: "works", fill: t.s1, fillOpacity: 0.18 }),
        Plot.lineY(c.years, { x: "year", y: "works", stroke: t.s1, strokeWidth: 2 }),
        Plot.ruleY([0], { stroke: t.baseline }),
        Plot.tip(c.years, Plot.pointerX({ x: "year", y: "works",
          title: (d) => `${c.name} ${d.year}\\n${fmt(d.works)} polymer works` })),
      ],
    })));
    return box;
  });
  el.replaceChildren(...cells);
}

function scatter(t, width) {
  const rows = data.scatter.filter((d) => d.polymer_works > 0 && d.polymer_co2_tonnes > 0);
  return Plot.plot(frame(t, width, 380, {
    marginLeft: 56, marginBottom: 40, marginRight: 48, marginTop: 28,
    // Inset, so dots and labels at the extremes stay inside the frame.
    x: { type: "log", label: "polymer works (log scale)", grid: true, inset: 18, ticks: 5,
         tickFormat: "~s" },
    y: { type: "log", label: "polymer plants' CO2, tonnes (log scale)", grid: true, inset: 18,
         ticks: 4, tickFormat: "~s" },
    marks: [
      Plot.dot(rows, { x: "polymer_works", y: "polymer_co2_tonnes", r: 6, fill: t.s1,
                       stroke: t.surface, strokeWidth: 2 }),
      Plot.text(rows, { x: "polymer_works", y: "polymer_co2_tonnes", text: "country_code",
                        dx: 9, textAnchor: "start", fill: t.ink }),
      Plot.tip(rows, Plot.pointer({ x: "polymer_works", y: "polymer_co2_tonnes",
        title: (d) => `${d.country_code} ${data.scatter_year}\\n${fmt(d.polymer_works)} polymer `
          + `works\\n${fmt(d.polymer_facilities)} polymer plants, ${mt(d.polymer_co2_tonnes)} CO2` })),
    ],
  }));
}

// Europe against every country; the world line is the muted reference.
function openaccess(t, width) {
  const rows = data.open_access.flatMap((d) => [
    { year: d.year, series: "Europe", share: d.europe },
    { year: d.year, series: "all countries", share: d.world },
  ]).filter((d) => d.share != null);
  const last = data.open_access[data.open_access.length - 1];
  return Plot.plot(frame(t, width, 280, {
    marginRight: 104,
    x: { label: null, tickFormat: (d) => `${d}` },
    y: { label: null, domain: [0, 1], grid: true, ticks: 5, tickFormat: pct },
    color: { domain: ["Europe", "all countries"], range: [t.s1, t.muted] },
    marks: [
      Plot.lineY(rows, { x: "year", y: "share", stroke: "series", strokeWidth: 2 }),
      Plot.ruleY([0], { stroke: t.baseline }),
      Plot.text([last], { x: "year", y: "europe", dx: 8, textAnchor: "start", fill: t.ink,
                          text: (d) => `Europe ${pct(d.europe)}` }),
      Plot.text([last], { x: "year", y: "world", dx: 8, textAnchor: "start", fill: t.secondary,
                          text: (d) => `all ${pct(d.world)}` }),
      Plot.tip(data.open_access, Plot.pointerX({ x: "year", y: "europe",
        title: (d) => `${d.year}\\nEurope ${pct(d.europe)} open access\\n`
          + `all countries ${pct(d.world)}` })),
    ],
  }));
}

// Dumbbells: each topic's share then (ring) and now (dot), biggest gain first.
function topics(t, width) {
  const rows = data.topic_shift;
  const max = Math.max(...rows.flatMap((d) => [d.share, d.share_before]), 0.01);
  const pp = (d) => `${d.change >= 0 ? "+" : "−"}${Math.abs(d.change * 100).toFixed(1)} pp`;
  const marginLeft = Math.min(300, width * 0.45);
  return Plot.plot(frame(t, width, 36 + rows.length * 28, {
    marginLeft, marginRight: 72, marginBottom: 24,
    x: { grid: true, label: null, domain: [0, max * 1.05], ticks: 5, tickFormat: pct },
    y: { domain: rows.map((d) => d.topic_name), label: null, axis: null },
    marks: [
      // Long names are cut with an ellipsis on narrow screens; the tip has them in full.
      Plot.axisY({ tickSize: 0, label: null, textOverflow: "ellipsis",
                   lineWidth: (marginLeft - 12) / 13 }),
      Plot.link(rows, { x1: "share_before", x2: "share", y1: "topic_name", y2: "topic_name",
                        stroke: t.baseline, strokeWidth: 2 }),
      Plot.dot(rows, { x: "share_before", y: "topic_name", r: 5, fill: t.surface,
                       stroke: t.muted, strokeWidth: 2 }),
      Plot.dot(rows, { x: "share", y: "topic_name", r: 5, fill: t.s1, stroke: t.surface,
                       strokeWidth: 2 }),
      Plot.text(rows, { y: "topic_name", frameAnchor: "right", dx: 64, textAnchor: "end",
                        fill: t.ink, text: pp }),
      Plot.tip(rows, Plot.pointerY({ x: "share", y: "topic_name",
        title: (d) => `${d.topic_name}\\n${data.topic_base_year}: ${pct(d.share_before)} `
          + `(${fmt(d.works_before)} works)\\n${data.last_year}: ${pct(d.share)} `
          + `(${fmt(d.works)} works)\\n${pp(d)}` })),
    ],
  }));
}

function emissions(t, width) {
  const rows = data.emissions.flatMap((d) => [
    { year: d.year, series: "chemical industry", tonnes: d.chemical_co2_tonnes },
    { year: d.year, series: "polymer plants", tonnes: d.polymer_co2_tonnes },
  ]);
  const last = data.emissions[data.emissions.length - 1];
  return Plot.plot(frame(t, width, 300, {
    marginLeft: 56, marginRight: 120, marginTop: 28,
    x: { label: null, tickFormat: (d) => `${d}` },
    y: { label: "CO2, million tonnes", grid: true, tickFormat: (d) => (d / 1e6).toFixed(0) },
    color: { domain: ["chemical industry", "polymer plants"], range: [t.s1, t.s2] },
    marks: [
      Plot.lineY(rows, { x: "year", y: "tonnes", stroke: "series", strokeWidth: 2 }),
      Plot.ruleY([0], { stroke: t.baseline }),
      Plot.text([last], { x: "year", y: "chemical_co2_tonnes", text: () => "chemical industry",
                          dx: 8, textAnchor: "start", fill: t.ink }),
      Plot.text([last], { x: "year", y: "polymer_co2_tonnes", text: () => "polymer plants",
                          dx: 8, textAnchor: "start", fill: t.ink }),
      Plot.tip(data.emissions, Plot.pointerX({ x: "year", y: "chemical_co2_tonnes",
        title: (d) => `${d.year}\\nchemical industry ${mt(d.chemical_co2_tonnes)}\\n`
          + `polymer plants ${mt(d.polymer_co2_tonnes)} (${fmt(d.polymer_facilities)} plants)` })),
    ],
  }));
}

// One cell per country and year; a gap keeps its own colour instead of a value.
function coverage(t, width) {
  const rows = data.coverage;
  const names = [...new Set(rows.map((d) => d.name))];
  const years = [...new Set(rows.map((d) => d.year))].sort();
  const statuses = ["reported", "missing", "left", "not yet reporting"];
  const status = { reported: "reported", missing: "missing from this release",
                   left: "stopped reporting", "not yet reporting": "not yet reporting" };
  return Plot.plot(frame(t, width, 24 + names.length * 18, {
    marginLeft: Math.min(130, width * 0.3), marginBottom: 24, marginTop: 4,
    x: { label: null, type: "band", padding: 0.1, tickFormat: (d) => `${d}`,
         ticks: width < 640 ? years.filter((y) => y % 4 === 0) : years },
    y: { label: null, domain: names, tickSize: 0, padding: 0.1 },
    color: { domain: statuses, range: [t.s1, t.warning, t.muted, "transparent"] },
    marks: [
      Plot.cell(rows, { x: "year", y: "name", fill: "status", rx: 2 }),
      // "Not yet reporting" is an outline only: no data was expected.
      Plot.cell(rows.filter((d) => d.status === "not yet reporting"),
                { x: "year", y: "name", fill: "none", stroke: t.baseline, rx: 2 }),
      Plot.tip(rows, Plot.pointer({ x: "year", y: "name",
        title: (d) => `${d.name} ${d.year}\\n${status[d.status]}`
          + (d.facilities ? `: ${fmt(d.facilities)} facilities` : "") })),
    ],
  }));
}

function render() {
  const t = tokens();
  const el = (id) => document.getElementById(id);
  if (el("chart-countries")) countries(t, el("chart-countries"));
  const draw = { openaccess, scatter, topics, emissions, coverage };
  for (const [name, fn] of Object.entries(draw)) {
    const node = el(`chart-${name}`);
    if (node) node.replaceChildren(fn(t, Math.max(node.clientWidth, 300)));
  }
}

render();
let pending;
addEventListener("resize", () => { clearTimeout(pending); pending = setTimeout(render, 150); });
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
""".replace("__PLOT_MODULE__", PLOT_MODULE)
