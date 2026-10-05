const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const staticDir = path.join(__dirname, "../static");
const html = fs.readFileSync(path.join(staticDir, "index.html"), "utf8");
const source = fs.readFileSync(path.join(staticDir, "app.js"), "utf8");

function dashboard() {
  const createElement = () => ({
    textContent: "", className: "", hidden: false, children: [],
    append(...children) { this.children.push(...children); },
    classList: { toggle() {} },
  });
  const nodes = new Map([...html.matchAll(/id="([^"]+)"/g)].map((match) => [
    `#${match[1]}`, createElement(),
  ]));
  const context = vm.createContext({
    document: { createElement, querySelector: (id) => nodes.get(id) || null, querySelectorAll: () => [] },
    window: {}, localStorage: { getItem: () => null },
    fetch: async () => { throw new Error("Offline"); }, setInterval: () => {},
  });
  vm.runInContext(source, context);
  return { nodes, context, run: (code) => vm.runInContext(code, context) };
}

test("every queried DOM id exists in the actual page", () => {
  const { nodes } = dashboard();
  for (const match of source.matchAll(/document.querySelector\("(#[^"]+)"\)/g)) {
    assert.ok(nodes.has(match[1]), match[1]);
  }
});

test("missing values are not formatted as zero", () => {
  const { run } = dashboard();
  assert.equal(run("formatPower(null)"), "--");
  assert.equal(run("formatEnergy(undefined)"), "--");
  assert.equal(run("formatRelative(null)"), "--");
  assert.equal(run("formatPower(0)"), "0 W");
});

test("real zero readings stay while missing points and long gaps are distinct", () => {
  const { run } = dashboard();
  const result = run(`prepareHistory([
    {timestamp: 1, solar_w: 0}, {timestamp: 16, solar_w: null},
    {timestamp: 121, solar_w: 500}
  ], false, 15)`);
  assert.equal(result.validPoints.length, 2);
  assert.equal(result.gaps.length, 1);
  assert.equal(result.series[1][1], null);
});

test("two-minute power readings remain connected with a one-minute poll interval", () => {
  const { run } = dashboard();
  const result = run(`prepareHistory([
    {timestamp: 1000, solar_w: 390}, {timestamp: 1120.2, solar_w: 387},
    {timestamp: 1241, solar_w: 378}, {timestamp: 1361.5, solar_w: 368}
  ], false, 60)`);
  assert.equal(result.gaps.length, 0);
  assert.equal(result.series.length, 4);
  assert.ok(result.series.every((point) => point[1] !== null));
});

test("real outages remain blank and isolated valid readings retain visible symbols", () => {
  const { run } = dashboard();
  run(`var history = prepareHistory([
    {timestamp: 1000, solar_w: 0}, {timestamp: 1480, solar_w: 500},
    {timestamp: 1600, solar_w: 550}, {timestamp: 2080, solar_w: 600}
  ], false, 60)`);
  assert.equal(run("history.gaps.length"), 2);
  const symbols = run("history.series.map((point, index) => historySymbolSize(history.series, index))");
  assert.deepEqual(Array.from(symbols), [7, 0, 0, 0, 0, 7]);
  assert.equal(run("historySymbolSize([[1000, 0]], 0)"), 7);
});

test("missing cadence metadata uses the current cadence and aggregation keeps its wider gaps", () => {
  const { run } = dashboard();
  for (const interval of ["undefined", "null", "0", "-1", '"invalid"']) {
    assert.equal(run(`prepareHistory([
      {timestamp: 1, solar_w: 1}, {timestamp: 122, solar_w: 2}
    ], false, ${interval}).gaps.length`), 0);
  }
  const aggregated = run(`prepareHistory([
    {timestamp: 1000, solar_w: 1}, {timestamp: 2800, solar_w: 2},
    {timestamp: 4600, solar_w: 3}, {timestamp: 12000, solar_w: 4}
  ], true, 60)`);
  assert.equal(aggregated.gaps.length, 1);
  assert.equal(aggregated.gaps[0].seconds, 7400);
});

test("server failure preserves output but removes the live label", async () => {
  const { run, nodes } = dashboard();
  run(`renderLive({solar_w: 500, timestamp: Date.now()/1000,
    collector: {state: "live", requests: 1, responses: 1}})`);
  assert.equal(nodes.get("#power-label").textContent, "Producing now");
  await run("loadLive()");
  assert.equal(nodes.get("#current-power").textContent, "500 W");
  assert.equal(nodes.get("#power-label").textContent, "Last verified output");
  assert.equal(nodes.get("#status-text").textContent, "Dashboard unavailable");
});

test("nighttime standby is neutral and preserves the real reading and age", async () => {
  const { run, nodes } = dashboard();
  await Promise.resolve();
  run(`renderLive({solar_w: 12, timestamp: Date.now()/1000 - 10800,
    collector: {state: "standby", standby_until: Date.now()/1000 + 3600}})`);
  assert.equal(nodes.get("#current-power").textContent, "12 W");
  assert.equal(nodes.get("#power-label").textContent, "Last verified output");
  assert.equal(nodes.get("#last-capture").textContent, "3h ago");
  assert.equal(nodes.get("#status-text").textContent, "Nighttime standby");
  assert.equal(nodes.get("#status-dot").className, "dot standby");
  assert.match(nodes.get("#status-detail").textContent, /likely asleep; SMLIGHT online.*Sunrise/);
  run(`renderLive({solar_w: 12, timestamp: Date.now()/1000 - 10800,
    collector: {state: "stale", last_error: "Waiting for the inverter radio"}})`);
  assert.equal(nodes.get("#status-text").textContent, "Reading overdue");
  assert.equal(nodes.get("#status-detail").textContent, "Waiting for the inverter radio");
});

test("freshness follows the slower poll cadence without hiding old readings", () => {
  const { run, nodes } = dashboard();
  run(`renderLive({solar_w: 500, timestamp: Date.now()/1000 - 120,
    collector: {state: "live", interval_seconds: 60}})`);
  assert.equal(nodes.get("#power-label").textContent, "Producing now");
  assert.equal(nodes.get("#poll-interval").textContent, "60s");
  run(`renderLive({solar_w: 500, timestamp: Date.now()/1000 - 181,
    collector: {state: "stale", interval_seconds: 60}})`);
  assert.equal(nodes.get("#power-label").textContent, "Last verified output");
  assert.equal(nodes.get("#current-power").textContent, "500 W");
  run(`renderLive({solar_w: 500, timestamp: Date.now()/1000 - 46,
    collector: {state: "stale", interval_seconds: 15}})`);
  assert.equal(nodes.get("#power-label").textContent, "Last verified output");
});
