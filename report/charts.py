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
    s1: v("--series-1"), s2: v("--series-2"),
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

function topics(t, width) {
  const rows = data.topics;
  return Plot.plot(frame(t, width, 28 + rows.length * 26, {
    marginLeft: Math.min(300, width * 0.45), marginRight: 56, marginBottom: 24,
    x: { grid: true, label: null, nice: true },
    y: { domain: rows.map((d) => d.topic_name), label: null, tickSize: 0 },
    marks: [
      Plot.barX(rows, { x: "works", y: "topic_name", fill: t.s1, insetTop: 4, insetBottom: 4,
                        rx: 2 }),
      Plot.ruleX([0], { stroke: t.baseline }),
      Plot.text(rows, { x: "works", y: "topic_name", dx: 6, textAnchor: "start", fill: t.ink,
                        text: (d) => pct(d.share_of_year) }),
      Plot.tip(rows, Plot.pointerY({ x: "works", y: "topic_name",
        title: (d) => `${d.topic_name}\\n${fmt(d.works)} works in ${data.last_year}, `
          + `${pct(d.share_of_year)} of the year` })),
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
    marginLeft: 56, marginRight: 120,
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

function render() {
  const t = tokens();
  const el = (id) => document.getElementById(id);
  if (el("chart-countries")) countries(t, el("chart-countries"));
  const draw = { scatter, topics, emissions };
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
