const currentPower = document.querySelector("#current-power");
const lifetimeEnergy = document.querySelector("#lifetime-energy");
const lastCapture = document.querySelector("#last-capture");
const powerDetail = document.querySelector("#power-detail");
const captureDetail = document.querySelector("#capture-detail");
const statusDot = document.querySelector("#status-dot");
const statusText = document.querySelector("#status-text");
const statusDetail = document.querySelector("#status-detail");
const collectorState = document.querySelector("#collector-state");
const radioLink = document.querySelector("#radio-link");
const radioHost = document.querySelector("#radio-host");
const radioRssi = document.querySelector("#radio-rssi");
const pollInterval = document.querySelector("#poll-interval");
const lastPoll = document.querySelector("#last-poll");
const pollResults = document.querySelector("#poll-results");
const powerLabel = document.querySelector("#power-label");
const rangePeak = document.querySelector("#range-peak");
const rangeGenerated = document.querySelector("#range-generated");
const sampleCount = document.querySelector("#sample-count");
const gapCount = document.querySelector("#gap-count");
const chartEmpty = document.querySelector("#chart-empty");
const chartHelp = document.querySelector("#chart-help");
const chartElement = document.querySelector("#history-chart");
const rangeButtons = [...document.querySelectorAll("[data-range]")];

const rangeLabels = {
  "1h": "1 hour",
  "6h": "6 hours",
  "24h": "24 hours",
  "7d": "7 days",
  "30d": "30 days",
  "1y": "1 year",
  all: "all time",
};
const knownRanges = new Set(Object.keys(rangeLabels));

let selectedRange = localStorage.getItem("solarHistoryRange") || "24h";
if (!knownRanges.has(selectedRange)) selectedRange = "24h";
let lastMeasurementSignature = "";
let historyRequestId = 0;
let zoomWindow = { start: 0, end: 100 };

const solarChart = window.echarts
  ? window.echarts.init(chartElement, null, { renderer: "canvas" })
  : null;

function hasNumber(value) {
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
}

function formatPower(value) {
  if (!hasNumber(value)) return "--";
  const watts = Math.max(0, Number(value));
  return watts >= 1000
    ? `${(watts / 1000).toFixed(2)} kW`
    : `${Math.round(watts)} W`;
}

function formatEnergy(value) {
  if (!hasNumber(value)) return "--";
  const wattHours = Number(value);
  if (wattHours >= 1_000_000) return `${(wattHours / 1_000_000).toFixed(2)} MWh`;
  if (wattHours >= 1000) return `${(wattHours / 1000).toFixed(2)} kWh`;
  return `${Math.round(wattHours)} Wh`;
}

function formatRelative(timestamp) {
  if (!hasNumber(timestamp)) return "--";
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - Number(timestamp)));
  if (seconds < 10) return "Now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function formatDuration(seconds) {
  if (seconds >= 86400) {
    const days = seconds / 86400;
    return `${days.toFixed(days >= 10 ? 0 : 1)} days`;
  }
  if (seconds >= 3600) {
    const hours = seconds / 3600;
    return `${hours.toFixed(hours >= 10 ? 0 : 1)} hours`;
  }
  return `${Math.round(seconds / 60)} minutes`;
}

function formatTooltipTime(milliseconds) {
  return new Date(milliseconds).toLocaleString([], {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
}

function collectorLabel(state) {
  if (state === "live") return "Polling";
  if (state === "standby") return "Nighttime standby";
  if (state === "connecting") return "Connecting";
  if (state === "discovering") return "Finding inverter";
  if (state === "waiting") return "Waiting for inverter";
  if (state === "conflict") return "Radio conflict";
  if (state === "stale") return "Reading overdue";
  return "Offline";
}

const telemetryFields = [
  ["inverter_dc", "Temperature & operating state", [
    ["cabinet_c", "Cabinet temperature", "\u00b0C", 1],
    ["operating_state", "Operating state"],
    ["dc_voltage_v", "DC voltage", "V", 1],
    ["dc_current_a", "DC current", "A", 3],
    ["dc_power_w", "DC input power", "W", 0],
    ["fault_bits", "Standard event flags 1", "bits"],
    ["event_bits_2", "Standard event flags 2", "bits"],
  ]],
  ["inverter_ac", "Inverter AC output", [
    ["inverter_ac_w", "AC output power", "W", 0],
    ["ac_voltage_v", "Line-to-line voltage", "V", 1],
    ["ac_current_a", "AC current", "A", 3],
    ["frequency_hz", "Grid frequency", "Hz", 2],
    ["inverter_lifetime_wh", "Inverter lifetime counter", "energy"],
  ]],
  ["meter_ac", "Export meter", [
    ["meter_real_w", "Real power", "W", 1],
    ["meter_voltage_v", "Line-to-line voltage", "V", 2],
    ["meter_current_a", "AC current", "A", 4],
    ["meter_frequency_hz", "Frequency", "Hz", 2],
    ["apparent_va", "Apparent power", "VA", 1],
    ["reactive_var", "Reactive power", "var", 2],
    ["power_factor_pct", "Power factor", "%", 2],
  ]],
  ["identity", "Device", [
    ["manufacturer", "Manufacturer"], ["model", "Model"],
    ["serial", "Serial number"], ["firmware", "Reported version"],
    ["options", "Device options"],
  ]],
];

const telemetryElements = new Map();
for (const [key, title, fields] of telemetryFields) {
  const section = document.createElement("section");
  const heading = document.createElement("h3");
  heading.textContent = title;
  const age = document.createElement("p");
  age.className = "telemetry-age";
  const list = document.createElement("dl");
  const outputs = new Map();
  for (const [name, label] of fields) {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    term.textContent = label;
    const value = document.createElement("dd");
    value.textContent = "--";
    if (name === "inverter_lifetime_wh") term.title = "Separate inverter counter; production history uses the export meter.";
    if (name.includes("bits")) term.title = "Raw SunSpec event bitmask. Zero means no flags set in this field.";
    row.append(term, value);
    list.append(row);
    outputs.set(name, value);
  }
  section.append(heading, age, list);
  document.querySelector("#telemetry-groups").append(section);
  telemetryElements.set(key, { age, outputs });
}

function renderTelemetry(telemetry = {}, standby = false, staleAfterSeconds = 480) {
  const freshnessSeconds = hasNumber(staleAfterSeconds) && Number(staleAfterSeconds) > 0
    ? Number(staleAfterSeconds) : 480;
  for (const [key, , fields] of telemetryFields) {
    const group = telemetry[key] || {};
    const { age, outputs } = telemetryElements.get(key);
    const overdue = group.stale || !hasNumber(group.observed_at)
      || Date.now() / 1000 - group.observed_at > freshnessSeconds;
    age.textContent = hasNumber(group.observed_at)
      ? `${overdue ? "Last verified" : "Updated"} ${formatRelative(group.observed_at)}`
      : "Awaiting reading";
    age.classList.toggle("stale", overdue && !standby);
    age.title = hasNumber(group.observed_at) ? new Date(group.observed_at * 1000).toLocaleString() : "";
    for (const [name, , unit, precision] of fields) {
      const value = group.values?.[name];
      let text = "Unavailable";
      if (value !== undefined && value !== null && value !== "") {
        if (unit === "bits") text = Number(value) === 0 ? "None set" : `0x${Number(value).toString(16).padStart(8, "0")}`;
        else if (unit === "energy") text = formatEnergy(value);
        else if (unit && hasNumber(value)) text = `${Number(value).toFixed(precision)} ${unit}`;
        else if (!unit) text = String(value);
      }
      outputs.get(name).textContent = text;
    }
  }
}

function renderLive(data) {
  const collector = data.collector || {};
  const state = collector.state || "disconnected";
  const standby = state === "standby";
  renderTelemetry(collector.telemetry, standby, collector.telemetry_stale_after_seconds);
  const timestamp = hasNumber(data.timestamp) ? Number(data.timestamp) : null;

  currentPower.textContent = formatPower(data.solar_w);
  lifetimeEnergy.textContent = formatEnergy(data.solar?.lifetime_wh);
  lastCapture.textContent = formatRelative(timestamp);
  captureDetail.textContent = timestamp !== null
    ? new Date(timestamp * 1000).toLocaleString()
    : "No reading received";
  const freshnessSeconds = hasNumber(collector.interval_seconds) && Number(collector.interval_seconds) > 0
    ? Number(collector.interval_seconds) * 3 : 180;
  const fresh = timestamp !== null && Date.now() / 1000 - timestamp <= freshnessSeconds;
  powerLabel.textContent = fresh ? "Producing now" : "Last verified output";

  statusDot.className = `dot ${state}`;
  collectorState.textContent = collectorLabel(state);
  statusText.textContent = state === "live" ? "Receiving solar readings" : collectorLabel(state);
  statusDetail.textContent = standby
    ? `Inverter likely asleep; SMLIGHT online.${hasNumber(collector.standby_until)
      ? ` Sunrise ${new Date(collector.standby_until * 1000).toLocaleTimeString([], {hour: "numeric", minute: "2-digit"})}.` : ""}`
    : collector.last_error || (fresh
    ? "Verified inverter response"
    : timestamp !== null ? "Last verified reading is preserved" : "Awaiting inverter reading");

  powerDetail.textContent = timestamp !== null
    ? `Verified ${formatRelative(timestamp)}`
    : "Last verified inverter reading";
  radioLink.textContent = collector.channel
    ? `Channel ${collector.channel}`
    : "Channel 14";
  radioHost.textContent = collector.host || "Not configured";
  radioRssi.textContent = hasNumber(collector.rssi) ? `${collector.rssi} dBm` : "--";
  pollInterval.textContent = hasNumber(collector.interval_seconds) ? `${collector.interval_seconds}s` : "--";
  lastPoll.textContent = formatRelative(collector.last_poll_at);
  pollResults.textContent = hasNumber(collector.requests)
    ? `${collector.responses || 0} / ${collector.requests}` : "--";

  const signature = [
    data.timestamp,
    data.solar_w,
    data.solar?.lifetime_wh,
  ].join(":");
  if (lastMeasurementSignature && signature !== lastMeasurementSignature) {
    loadHistory();
  }
  lastMeasurementSignature = signature;
}

async function loadLive() {
  try {
    const response = await fetch("api/live");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderLive(await response.json());
  } catch (error) {
    powerLabel.textContent = "Last verified output";
    collectorState.textContent = "Unknown";
    statusDot.className = "dot disconnected";
    statusText.textContent = "Dashboard unavailable";
    statusDetail.textContent = error.message;
  }
}

function median(values) {
  if (!values.length) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2
    ? sorted[middle]
    : (sorted[middle - 1] + sorted[middle]) / 2;
}

function prepareHistory(points, downsampled, pollIntervalSeconds = 60) {
  const validPoints = (points || [])
    .filter(
      (point) =>
        hasNumber(point.timestamp) && hasNumber(point.solar_w),
    )
    .map((point) => ({
      timestamp: Number(point.timestamp),
      solar_w: Math.max(0, Number(point.solar_w)),
    }))
    .sort((left, right) => left.timestamp - right.timestamp);

  const intervals = validPoints
    .slice(1)
    .map((point, index) => point.timestamp - validPoints[index].timestamp)
    .filter((seconds) => seconds > 0);
  const typicalInterval = median(intervals);
  // Power is read every other query. Allow one more query interval for jitter;
  // do not infer the normal cadence from sparse readings separated by outages.
  const pollInterval = hasNumber(pollIntervalSeconds) && Number(pollIntervalSeconds) > 0
    ? Number(pollIntervalSeconds) : 60;
  const readingGapThreshold = Math.max(90, pollInterval * 3);
  const gapThreshold = downsampled
    ? Math.max(30 * 60, typicalInterval * 3, readingGapThreshold)
    : readingGapThreshold;
  const gaps = [];
  const series = [];

  validPoints.forEach((point, index) => {
    const previous = validPoints[index - 1];
    if (previous) {
      const interval = point.timestamp - previous.timestamp;
      if (interval > gapThreshold) {
        gaps.push({
          start: previous.timestamp,
          end: point.timestamp,
          seconds: interval,
        });
        series.push([point.timestamp * 1000 - 1, null]);
      }
    }
    series.push([point.timestamp * 1000, point.solar_w]);
  });

  return { validPoints, series, gaps, gapThreshold };
}

function historySymbolSize(series, index) {
  if (series[index][1] === null) return 0;
  const previous = series[index - 1];
  const next = series[index + 1];
  return (!previous || previous[1] === null) && (!next || next[1] === null) ? 7 : 0;
}

function renderChart(prepared) {
  if (!solarChart) {
    chartEmpty.hidden = false;
    chartEmpty.textContent = "Chart library unavailable";
    return;
  }

  const { validPoints, series, gaps } = prepared;
  const gapBands = gaps.map((gap) => [
    { xAxis: gap.start * 1000 },
    { xAxis: gap.end * 1000 },
  ]);

  solarChart.setOption(
    {
      animationDuration: 250,
      aria: {
        enabled: true,
        description: `Solar production for ${rangeLabels[selectedRange]}. Missing capture intervals are blank.`,
      },
      grid: { left: 66, right: 24, top: 46, bottom: 84 },
      toolbox: {
        top: 0,
        right: 4,
        itemSize: 17,
        iconStyle: { borderColor: "#9ba7b2" },
        emphasis: { iconStyle: { borderColor: "#f5f7f9" } },
        feature: {
          dataZoom: {
            yAxisIndex: "none",
            title: { zoom: "Select range", back: "Zoom back" },
          },
          restore: { title: "Reset zoom" },
          saveAsImage: {
            title: "Save chart",
            name: `solar-production-${selectedRange}`,
            backgroundColor: "#171c21",
            pixelRatio: 2,
          },
        },
      },
      tooltip: {
        trigger: "axis",
        confine: true,
        backgroundColor: "rgba(14, 17, 20, 0.96)",
        borderColor: "#303842",
        textStyle: { color: "#f5f7f9" },
        axisPointer: {
          type: "cross",
          lineStyle: { color: "#64c7d9", type: "dashed" },
          label: { backgroundColor: "#303842" },
        },
        formatter(parameters) {
          const items = Array.isArray(parameters) ? parameters : [parameters];
          const reading = items.find(
            (item) =>
              Array.isArray(item.value) &&
              hasNumber(item.value[1]),
          );
          if (!reading) return "No verified reading";
          return [
            `<strong>${formatTooltipTime(Number(reading.value[0]))}</strong>`,
            `${reading.marker}Production&nbsp;&nbsp;<strong>${formatPower(reading.value[1])}</strong>`,
          ].join("<br>");
        },
      },
      xAxis: {
        type: "time",
        boundaryGap: false,
        axisLine: { lineStyle: { color: "#303842" } },
        axisTick: { lineStyle: { color: "#303842" } },
        axisLabel: { color: "#9ba7b2", hideOverlap: true },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        min: 0,
        max(value) {
          return Math.max(1000, Math.ceil(value.max / 1000) * 1000);
        },
        axisLabel: {
          color: "#9ba7b2",
          formatter(value) {
            return value >= 1000
              ? `${(value / 1000).toFixed(1)} kW`
              : `${value} W`;
          },
        },
        splitLine: { lineStyle: { color: "#303842" } },
      },
      dataZoom: [
        {
          type: "inside",
          xAxisIndex: 0,
          filterMode: "none",
          start: zoomWindow.start,
          end: zoomWindow.end,
          zoomOnMouseWheel: true,
          moveOnMouseMove: true,
          moveOnMouseWheel: false,
          preventDefaultMouseMove: true,
        },
        {
          type: "slider",
          xAxisIndex: 0,
          filterMode: "none",
          start: zoomWindow.start,
          end: zoomWindow.end,
          bottom: 12,
          height: 25,
          borderColor: "#303842",
          backgroundColor: "#0e1114",
          fillerColor: "rgba(255, 209, 102, 0.16)",
          dataBackground: {
            lineStyle: { color: "#9ba7b2", opacity: 0.55 },
            areaStyle: { color: "#9ba7b2", opacity: 0.08 },
          },
          selectedDataBackground: {
            lineStyle: { color: "#ffd166" },
            areaStyle: { color: "#ffd166", opacity: 0.14 },
          },
          handleStyle: { color: "#f5f7f9", borderColor: "#303842" },
          moveHandleStyle: { color: "#9ba7b2" },
          textStyle: { color: "#9ba7b2" },
          brushSelect: true,
        },
      ],
      series: [
        {
          name: "Production",
          type: "line",
          data: series,
          connectNulls: false,
          smooth: false,
          showSymbol: true,
          symbol: "circle",
          symbolSize: (_value, parameters) => historySymbolSize(series, parameters.dataIndex),
          lineStyle: { color: "#ffd166", width: 2.5 },
          itemStyle: { color: "#ffd166" },
          emphasis: { focus: "series", scale: 1.4 },
          areaStyle: {
            color: new window.echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: "rgba(255, 209, 102, 0.30)" },
              { offset: 1, color: "rgba(255, 209, 102, 0.02)" },
            ]),
          },
          markArea: {
            silent: true,
            label: { show: false },
            itemStyle: { color: "rgba(155, 167, 178, 0.055)" },
            data: gapBands,
          },
        },
      ],
    },
    { notMerge: true },
  );

  if (!validPoints.length) solarChart.clear();
}

function renderHistory(data) {
  const prepared = prepareHistory(data.points, data.downsampled, data.poll_interval_seconds);
  rangePeak.textContent = formatPower(data.peak_w);
  rangeGenerated.textContent = formatEnergy(data.generated_wh);
  sampleCount.textContent = Number(data.sample_count || 0).toLocaleString();
  gapCount.textContent = prepared.gaps.length
    ? prepared.gaps.length.toLocaleString()
    : "None";
  gapCount.title = `Intervals longer than ${formatDuration(prepared.gapThreshold)}`;

  chartEmpty.textContent = data.sample_count
    ? `Only one reading in ${rangeLabels[selectedRange]}`
    : `No verified readings in ${rangeLabels[selectedRange]}`;
  chartEmpty.hidden = prepared.validPoints.length >= 1;

  rangeGenerated.title = data.energy_first_at && data.energy_last_at
    ? `Meter difference: ${formatTooltipTime(data.energy_first_at * 1000)} to ${formatTooltipTime(data.energy_last_at * 1000)}`
    : "Requires two lifetime-meter readings";
  const notes = [];
  if (prepared.gaps.length) {
    notes.unshift(
      `${prepared.gaps.length.toLocaleString()} capture gaps over ${formatDuration(prepared.gapThreshold)} are left blank.`,
    );
  }
  if (data.downsampled) {
    notes.unshift(
      `Showing ${Number(data.point_count).toLocaleString()} aggregated chart points from ${Number(data.sample_count).toLocaleString()} readings.`,
    );
  }
  chartHelp.textContent = notes.join(" ");
  chartHelp.hidden = !notes.length;
  renderChart(prepared);
}

async function loadHistory() {
  const requestId = ++historyRequestId;
  if (solarChart) {
    solarChart.showLoading("default", {
      text: "Loading production history",
      color: "#ffd166",
      textColor: "#9ba7b2",
      maskColor: "rgba(23, 28, 33, 0.72)",
    });
  }

  try {
    const response = await fetch(`api/history?range=${selectedRange}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (requestId !== historyRequestId) return;
    if (solarChart) solarChart.hideLoading();
    renderHistory(data);
  } catch (error) {
    if (requestId !== historyRequestId) return;
    if (solarChart) {
      solarChart.hideLoading();
      solarChart.clear();
    }
    chartEmpty.hidden = false;
    chartEmpty.textContent = `History unavailable: ${error.message}`;
  }
}

function updateRangeButtons() {
  rangeButtons.forEach((button) => {
    const active = button.dataset.range === selectedRange;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

rangeButtons.forEach((button) => {
  button.addEventListener("click", () => {
    if (button.dataset.range === selectedRange) return;
    selectedRange = button.dataset.range;
    zoomWindow = { start: 0, end: 100 };
    localStorage.setItem("solarHistoryRange", selectedRange);
    updateRangeButtons();
    loadHistory();
  });
});

if (solarChart) {
  solarChart.on("datazoom", (event) => {
    const detail = event.batch?.[0] || event;
    if (Number.isFinite(detail.start) && Number.isFinite(detail.end)) {
      zoomWindow = { start: detail.start, end: detail.end };
    }
  });
  if (window.ResizeObserver) {
    new ResizeObserver(() => solarChart.resize()).observe(chartElement);
  } else {
    window.addEventListener("resize", () => solarChart.resize());
  }
}

updateRangeButtons();
loadLive();
loadHistory();
setInterval(loadLive, 10_000);
