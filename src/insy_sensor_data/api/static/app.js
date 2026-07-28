const DEFAULT_DIMENSIONS = ["x", "y", "z", "temperature"];
const DEFAULT_METRIC = "rms_vel";
const DEFAULT_K = "4";

const METRICS = {
  rms_vel: { label: "RMS Velocity", prefix: "rms_vel", axis: true, unit: "in/s" },
  rms_accel: { label: "RMS Acceleration", prefix: "rms_accel", axis: true, unit: "m/s²" },
  rms_pkpk: { label: "RMS Peak-to-Peak", prefix: "rms_pkpk", axis: true, unit: "source" },
  rms_cf: { label: "RMS Crest Factor", prefix: "rms_cf", axis: true, unit: "ratio" },
  temp_sensor: { label: "Sensor Temperature", prefix: "temp_sensor", axis: false, unit: "°F" },
  impact: { label: "Impact", prefix: "impact", axis: false, unit: "m/s²" },
};

const state = {
  artifacts: null,
  equipment: [],
  health: null,
  source: "",
  startDate: "",
  endDate: "",
  date: "",
  view: "snapshot",
  equipmentId: "",
  installationPointId: "",
  dimension: "x",
  metric: DEFAULT_METRIC,
  k: DEFAULT_K,
  equipmentSearch: "",
};

const elements = {
  healthStatus: document.querySelector("#health-status"),
  sourceSelect: document.querySelector("#source-select"),
  startDateSelect: document.querySelector("#start-date-select"),
  endDateSelect: document.querySelector("#end-date-select"),
  refreshButton: document.querySelector("#refresh-button"),
  equipmentSearch: document.querySelector("#equipment-search"),
  allEquipmentButton: document.querySelector("#all-equipment-button"),
  equipmentList: document.querySelector("#equipment-list"),
  allSensorsButton: document.querySelector("#all-sensors-button"),
  sensorList: document.querySelector("#sensor-list"),
  dateSelect: document.querySelector("#date-select"),
  metricSelect: document.querySelector("#metric-select"),
  dimensionSelect: document.querySelector("#dimension-select"),
  kSelect: document.querySelector("#k-select"),
  dateControl: document.querySelector("#date-control"),
  metricControl: document.querySelector("#metric-control"),
  dimensionControl: document.querySelector("#dimension-control"),
  kControl: document.querySelector("#k-control"),
  statusLine: document.querySelector("#status-line"),
  summaryGrid: document.querySelector("#summary-grid"),
  plot: document.querySelector("#plot"),
  tableHead: document.querySelector("#data-table-head"),
  tableBody: document.querySelector("#data-table-body"),
  tabs: Array.from(document.querySelectorAll(".tab")),
};

async function init() {
  readStateFromUrl();
  bindEvents();
  try {
    state.health = await fetchJson("/health");
    elements.healthStatus.textContent = [
      state.health.status.toUpperCase(),
      state.health.source_mode,
      state.health.data_dir,
    ].join(" | ");
  } catch (error) {
    elements.healthStatus.textContent = "Service health unavailable";
  }

  await loadArtifacts();
  updateControlsFromState();
  await renderActiveView();
}

function bindEvents() {
  elements.tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      updateState({ view: tab.dataset.view });
      renderActiveView();
    });
  });

  elements.sourceSelect.addEventListener("change", async () => {
    updateState({ source: elements.sourceSelect.value, equipmentId: "", installationPointId: "" }, false);
    normalizeState();
    updateUrlFromState();
    updateControlsFromState();
    await loadEquipment();
    await renderActiveView();
  });

  elements.startDateSelect.addEventListener("change", async () => {
    updateState({ startDate: elements.startDateSelect.value }, false);
    normalizeDateRange("start");
    updateUrlFromState();
    updateControlsFromState();
    await loadEquipment();
    await renderActiveView();
  });

  elements.endDateSelect.addEventListener("change", async () => {
    updateState({ endDate: elements.endDateSelect.value }, false);
    normalizeDateRange("end");
    updateUrlFromState();
    updateControlsFromState();
    await loadEquipment();
    await renderActiveView();
  });

  elements.dateSelect.addEventListener("change", () => {
    updateState({ date: elements.dateSelect.value });
    renderActiveView();
  });
  elements.metricSelect.addEventListener("change", () => {
    updateState({ metric: elements.metricSelect.value });
    renderActiveView();
  });
  elements.dimensionSelect.addEventListener("change", () => {
    updateState({ dimension: elements.dimensionSelect.value });
    renderActiveView();
  });
  elements.kSelect.addEventListener("change", () => {
    updateState({ k: elements.kSelect.value });
    renderActiveView();
  });

  elements.equipmentSearch.addEventListener("input", debounce(() => {
    state.equipmentSearch = elements.equipmentSearch.value;
    renderNavigator();
  }, 150));

  elements.allEquipmentButton.addEventListener("click", () => {
    updateState({ equipmentId: "", installationPointId: "" });
    renderNavigator();
    renderActiveView();
  });

  elements.allSensorsButton.addEventListener("click", () => {
    updateState({ installationPointId: "" });
    renderNavigator();
    renderActiveView();
  });

  elements.refreshButton.addEventListener("click", async () => {
    await loadArtifacts();
    await renderActiveView();
  });

  window.addEventListener("popstate", async () => {
    readStateFromUrl();
    normalizeState();
    updateControlsFromState();
    await loadEquipment();
    await renderActiveView();
  });
}

async function loadArtifacts() {
  setStatus("Loading artifacts...");
  state.artifacts = await fetchJson("/api/artifacts");
  normalizeState();
  updateControlsFromState();
  await loadEquipment();
  setStatus("Ready");
}

async function loadEquipment() {
  const params = new URLSearchParams();
  params.set("source", state.source);
  if (state.startDate) {
    params.set("start_date", state.startDate);
  }
  if (state.endDate) {
    params.set("end_date", state.endDate);
  }
  const payload = await fetchJson(`/api/equipment?${params}`);
  state.equipment = payload.rows || [];
  if (state.equipmentId && !state.equipment.some((row) => row.equipment_id === state.equipmentId)) {
    state.equipmentId = "";
    state.installationPointId = "";
    updateUrlFromState();
  }
  if (state.installationPointId && !selectedSensorIds().includes(state.installationPointId)) {
    state.installationPointId = "";
    updateUrlFromState();
  }
  renderNavigator();
}

function normalizeState() {
  const availableSources = state.artifacts?.sources || [];
  const preferredSource = state.source || state.health?.source_mode || "mock";
  state.source = availableSources.includes(preferredSource) ? preferredSource : availableSources[0] || preferredSource;

  const dates = availableDates();
  if (!state.startDate || !dates.includes(state.startDate)) {
    state.startDate = dates[0] || "";
  }
  if (!state.endDate || !dates.includes(state.endDate)) {
    state.endDate = dates[dates.length - 1] || state.startDate;
  }
  normalizeDateRange("end");

  const rangeDates = datesInRange();
  if (!state.date || !rangeDates.includes(state.date)) {
    state.date = rangeDates[rangeDates.length - 1] || state.endDate || state.startDate;
  }

  const dimensions = availableDimensions();
  if (!dimensions.includes(state.dimension)) {
    state.dimension = dimensions[0] || "x";
  }
  const ks = availableKs().map(String);
  if (!ks.includes(String(state.k))) {
    state.k = ks.includes(DEFAULT_K) ? DEFAULT_K : ks[0] || DEFAULT_K;
  }
  if (!METRICS[state.metric]) {
    state.metric = DEFAULT_METRIC;
  }
  if (!["snapshot", "trend", "cluster", "drift"].includes(state.view)) {
    state.view = "snapshot";
  }
  updateUrlFromState(true);
}

function normalizeDateRange(changedEdge) {
  const dates = availableDates();
  if (!dates.length) {
    return;
  }
  const startIndex = dates.indexOf(state.startDate);
  const endIndex = dates.indexOf(state.endDate);
  if (startIndex === -1 || endIndex === -1) {
    state.startDate = dates[0];
    state.endDate = dates[dates.length - 1];
    return;
  }
  if (startIndex <= endIndex) {
    return;
  }
  if (changedEdge === "start") {
    state.endDate = state.startDate;
  } else {
    state.startDate = state.endDate;
  }
}

function updateControlsFromState() {
  setOptions(elements.sourceSelect, state.artifacts?.sources || [state.source], (value) => value, state.source);
  setOptions(elements.startDateSelect, availableDates(), (value) => value, state.startDate);
  setOptions(elements.endDateSelect, availableDates(), (value) => value, state.endDate);
  setOptions(elements.dateSelect, datesInRange(), (value) => value, state.date);
  setOptions(
    elements.metricSelect,
    Object.entries(METRICS).map(([value, metric]) => ({ value, label: metric.label })),
    (row) => row.label,
    state.metric,
  );
  setOptions(elements.dimensionSelect, availableDimensions(), (value) => value, state.dimension);
  setOptions(elements.kSelect, availableKs(), (value) => String(value), state.k);
  elements.equipmentSearch.value = state.equipmentSearch;
  updateTabState();
  updateViewControls();
}

function updateTabState() {
  elements.tabs.forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.view === state.view);
  });
}

function updateViewControls() {
  const metricNeedsAxis = METRICS[state.metric]?.axis;
  elements.dateControl.hidden = !["snapshot", "cluster"].includes(state.view);
  elements.metricControl.hidden = !["snapshot", "trend"].includes(state.view);
  elements.dimensionControl.hidden = !(
    ["cluster", "drift"].includes(state.view)
    || (["snapshot", "trend"].includes(state.view) && metricNeedsAxis)
  );
  elements.kControl.hidden = !["cluster", "drift"].includes(state.view);
}

function renderNavigator() {
  const rows = filteredEquipment();
  elements.allEquipmentButton.classList.toggle("is-active", !state.equipmentId);
  elements.allSensorsButton.classList.toggle("is-active", !state.installationPointId);

  elements.equipmentList.replaceChildren();
  if (!rows.length) {
    elements.equipmentList.append(emptyBlock("No equipment in context"));
  } else {
    rows.forEach((row) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "equipment-row";
      button.classList.toggle("is-active", row.equipment_id === state.equipmentId);
      button.dataset.equipmentId = row.equipment_id;
      button.innerHTML = `
        <strong>${escapeHtml(row.equipment_id || "unknown")}</strong>
        <span>${escapeHtml(row.equipment_name || row.customer_asset_id || "")}</span>
        <small>${row.sensor_count} sensors | ${row.first_date || ""} to ${row.last_date || ""}</small>
      `;
      button.addEventListener("click", () => {
        const nextEquipmentId = row.equipment_id === state.equipmentId ? "" : row.equipment_id;
        updateState({ equipmentId: nextEquipmentId, installationPointId: "" });
        renderNavigator();
        renderActiveView();
      });
      elements.equipmentList.append(button);
    });
  }

  elements.sensorList.replaceChildren();
  const sensorIds = selectedSensorIds();
  if (!state.equipmentId) {
    elements.sensorList.append(emptyBlock("Select equipment"));
  } else if (!sensorIds.length) {
    elements.sensorList.append(emptyBlock("No sensors"));
  } else {
    sensorIds.forEach((sensorId) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "sensor-row";
      button.classList.toggle("is-active", sensorId === state.installationPointId);
      button.textContent = sensorId;
      button.addEventListener("click", () => {
        updateState({ installationPointId: sensorId === state.installationPointId ? "" : sensorId });
        renderNavigator();
        renderActiveView();
      });
      elements.sensorList.append(button);
    });
  }
}

async function renderActiveView() {
  if (!state.artifacts) {
    return;
  }
  updateControlsFromState();
  clearView();
  try {
    if (state.view === "snapshot") {
      await renderSnapshot();
    } else if (state.view === "trend") {
      await renderTrend();
    } else if (state.view === "cluster") {
      await renderCluster();
    } else {
      await renderDrift();
    }
  } catch (error) {
    renderMissingState(error);
  }
}

async function renderSnapshot() {
  const params = scopedParams();
  const payload = await fetchJson(`/api/snapshots/${state.date}?${params}`);
  const metric = selectedMetric();
  const yField = metricField(metric, "mean");
  setStatus(`Snapshot ${payload.source} ${payload.date}`);
  renderSummary([
    { label: "Sensors", value: payload.filtered_row_count },
    { label: "All Rows", value: payload.row_count },
    { label: "Metric", value: metric.label },
    { label: "Scope", value: scopeLabel() },
  ]);
  const rows = payload.rows || [];
  const plotted = rows
    .filter((row) => numeric(row[yField]) !== null)
    .sort((left, right) => numeric(right[yField]) - numeric(left[yField]))
    .slice(0, 60);
  plotChart(
    [
      {
        type: "bar",
        x: plotted.map((row) => row.installation_point_id),
        y: plotted.map((row) => numeric(row[yField])),
        marker: { color: "#287271" },
        hovertext: plotted.map((row) => row.equipment_name || row.equipment_id),
      },
    ],
    { title: `Snapshot ${metric.label}`, xaxis: { title: "Sensor" }, yaxis: { title: metric.unit } },
  );
  renderTable(rows, [
    "installation_point_id",
    "equipment_id",
    "equipment_name",
    "sensor_id",
    "customer_asset_id",
    yField,
    metricField(metric, "max"),
    metricField(metric, "min"),
  ]);
}

async function renderTrend() {
  const params = scopedParams();
  params.set("start_date", state.startDate);
  params.set("end_date", state.endDate);
  const payload = await fetchJson(`/api/trends?${params}`);
  const metric = selectedMetric();
  const meanField = metricField(metric, "mean");
  setStatus(`Trend ${payload.source} ${payload.start_date} to ${payload.end_date}`);
  renderSummary([
    { label: "Sensor Rows", value: payload.filtered_sensor_row_count },
    { label: "Equipment Rows", value: payload.filtered_equipment_row_count },
    { label: "Metric", value: metric.label },
    { label: "Scope", value: scopeLabel() },
  ]);
  const aggregates = aggregateTrendRows(payload.sensor_rows || [], meanField);
  plotChart(
    [lineTrace(aggregates, meanField, metric.label, "#287271")].filter(Boolean),
    { title: `${metric.label} Trend`, xaxis: { title: "Date" }, yaxis: { title: metric.unit } },
  );
  renderTable(payload.sensor_rows || [], [
    "date",
    "installation_point_id",
    "equipment_id",
    "equipment_name",
    "customer_asset_id",
    meanField,
    metricField(metric, "max"),
    metricField(metric, "min"),
  ]);
}

async function renderCluster() {
  const params = clusterParams();
  const payload = await fetchJson(`/api/clusters?${params}`);
  const metrics = payload.metrics || {};
  const metricValues = metrics.metrics || {};
  setStatus(`Cluster ${payload.source} ${payload.date} ${payload.dimension} k=${payload.k}`);
  renderSummary([
    { label: "Sensors", value: payload.row_count },
    { label: "Features", value: metrics.feature_count },
    { label: "Inertia", value: formatNumber(metrics.kmeans?.inertia) },
    { label: "Silhouette", value: formatNumber(metricValues.silhouette_score?.value) },
  ]);
  const grouped = groupBy(payload.pca_rows || [], "cluster");
  const traces = Object.entries(grouped).map(([cluster, rows]) => ({
    type: "scatter",
    mode: "markers",
    name: `Cluster ${cluster}`,
    x: rows.map((row) => numeric(row.pc1)),
    y: rows.map((row) => numeric(row.pc2)),
    text: rows.map((row) => `${row.installation_point_id} | ${row.equipment_name || row.equipment_id}`),
    marker: {
      size: rows.map((row) => selectedPoint(row) ? 13 : 8),
      line: { width: rows.map((row) => selectedPoint(row) ? 2 : 0), color: "#18202a" },
    },
  }));
  plotChart(traces, { title: "Cluster PCA", xaxis: { title: "PC1" }, yaxis: { title: "PC2" } });
  const featureColumns = (metrics.features || []).slice(0, 4);
  renderTable(scopeClusterRows(payload.rows || []), [
    "installation_point_id",
    "equipment_id",
    "equipment_name",
    "cluster",
    "distance_to_centroid",
    ...featureColumns,
  ]);
}

async function renderDrift() {
  const params = clusterWindowParams();
  try {
    const payload = await fetchJson(`/api/cluster-windows?${params}`);
    renderClusterWindow(payload);
  } catch (error) {
    if (error.status !== 404) {
      throw error;
    }
    const driftParams = driftParamsFromState();
    const payload = await fetchJson(`/api/drift?${driftParams}`);
    renderDriftPair(payload);
  }
}

function renderClusterWindow(payload) {
  const metrics = payload.metrics || {};
  setStatus(`Cluster window ${payload.source} ${payload.start_date} to ${payload.end_date}`);
  renderSummary([
    { label: "Dates", value: metrics.date_count },
    { label: "Pairs", value: metrics.pair_count },
    { label: "Warnings", value: metrics.warning_count },
    { label: "Dimension", value: payload.dimension },
  ]);
  const rows = payload.aligned_drift_rows || [];
  plotChart(
    [
      {
        type: "bar",
        x: rows.map((row) => `${row.from_date} to ${row.to_date}`),
        y: rows.map((row) => numeric(row.aligned_changed_ratio)),
        marker: { color: "#a64253" },
      },
    ],
    { title: "Aligned drift ratio", xaxis: { title: "Date pair" }, yaxis: { title: "Ratio" } },
  );
  renderTable(rows, [
    "from_date",
    "to_date",
    "matched_sensor_count",
    "raw_label_changed_count",
    "aligned_changed_count",
    "aligned_changed_ratio",
    "warning_count",
    "interpretation",
  ]);
}

function renderDriftPair(payload) {
  const aligned = payload.aligned_metrics || {};
  setStatus(`Drift ${payload.source} ${payload.from_date} to ${payload.to_date}`);
  renderSummary([
    { label: "Matched", value: aligned.matched_sensor_count || payload.metrics.matched_sensor_count },
    { label: "Aligned Changes", value: aligned.aligned_changed_count || "n/a" },
    { label: "Raw Changes", value: payload.metrics.changed_sensor_count },
    { label: "Dimension", value: payload.dimension },
  ]);
  const rows = payload.aligned_rows?.length ? payload.aligned_rows : payload.raw_rows || [];
  const clusterField = payload.aligned_rows?.length ? "aligned_changed" : "changed";
  const counts = countValues(rows, clusterField);
  plotChart(
    [
      {
        type: "bar",
        x: Object.keys(counts),
        y: Object.values(counts),
        marker: { color: ["#287271", "#a64253"] },
      },
    ],
    { title: "Drift counts", xaxis: { title: clusterField }, yaxis: { title: "Sensors" } },
  );
  renderTable(rows, [
    "installation_point_id",
    "equipment_id",
    "equipment_name",
    "from_cluster",
    "to_cluster",
    "raw_label_changed",
    "aligned_changed",
    "changed",
  ]);
}

function renderMissingState(error) {
  const command = commandHint();
  setStatus(error.message || "Missing artifact");
  renderSummary([
    { label: "State", value: error.status === 404 ? "Missing artifact" : "Unavailable" },
    { label: "Source", value: state.source },
    { label: "Range", value: `${state.startDate} to ${state.endDate}` },
    { label: "View", value: state.view },
  ]);
  elements.plot.innerHTML = `
    <div class="missing-state">
      <strong>${escapeHtml(error.message || "Unable to load this view")}</strong>
      ${command ? `<code>${escapeHtml(command)}</code>` : ""}
    </div>
  `;
}

function commandHint() {
  if (state.view === "cluster") {
    return `uv run sensor-data cluster run --source ${state.source} --date ${state.date} --dimension ${state.dimension} --k ${state.k}`;
  }
  if (state.view === "drift") {
    return `uv run sensor-data cluster window --source ${state.source} --start-date ${state.startDate} --end-date ${state.endDate} --dimension ${state.dimension} --k ${state.k}`;
  }
  if (state.view === "trend") {
    return `uv run sensor-data trend build --source ${state.source} --start-date ${state.startDate} --end-date ${state.endDate} --input sqlite`;
  }
  return `uv run sensor-data snapshot build --source ${state.source} --date ${state.date} --input sqlite`;
}

function scopedParams() {
  const params = new URLSearchParams();
  params.set("source", state.source);
  if (state.equipmentId) {
    params.set("equipment_id", state.equipmentId);
  }
  if (state.installationPointId) {
    params.set("installation_point_id", state.installationPointId);
  }
  return params;
}

function clusterParams() {
  const params = new URLSearchParams();
  params.set("source", state.source);
  params.set("date", state.date);
  params.set("dimension", state.dimension);
  params.set("k", state.k);
  return params;
}

function clusterWindowParams() {
  const params = new URLSearchParams();
  params.set("source", state.source);
  params.set("start_date", state.startDate);
  params.set("end_date", state.endDate);
  params.set("dimension", state.dimension);
  params.set("k", state.k);
  return params;
}

function driftParamsFromState() {
  const params = new URLSearchParams();
  params.set("source", state.source);
  params.set("from_date", state.startDate);
  params.set("to_date", state.endDate);
  params.set("dimension", state.dimension);
  params.set("k", state.k);
  return params;
}

function updateState(patch, updateUrl = true) {
  Object.assign(state, patch);
  if (updateUrl) {
    updateUrlFromState();
  }
  updateControlsFromState();
}

function readStateFromUrl() {
  const params = new URLSearchParams(window.location.search);
  Object.assign(state, {
    source: params.get("source") || state.source,
    startDate: params.get("start_date") || state.startDate,
    endDate: params.get("end_date") || state.endDate,
    date: params.get("date") || state.date,
    view: params.get("view") || state.view,
    equipmentId: params.get("equipment_id") || "",
    installationPointId: params.get("installation_point_id") || "",
    dimension: params.get("dimension") || state.dimension,
    metric: params.get("metric") || state.metric,
    k: params.get("k") || state.k,
  });
}

function updateUrlFromState(replace = false) {
  const params = new URLSearchParams();
  params.set("source", state.source);
  params.set("start_date", state.startDate);
  params.set("end_date", state.endDate);
  params.set("date", state.date);
  params.set("view", state.view);
  if (state.equipmentId) {
    params.set("equipment_id", state.equipmentId);
  }
  if (state.installationPointId) {
    params.set("installation_point_id", state.installationPointId);
  }
  params.set("dimension", state.dimension);
  params.set("metric", state.metric);
  params.set("k", state.k);
  const nextUrl = `${window.location.pathname}?${params}`;
  if (replace) {
    window.history.replaceState(null, "", nextUrl);
  } else {
    window.history.pushState(null, "", nextUrl);
  }
}

async function fetchJson(path) {
  const response = await fetch(path);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : {};
  if (!response.ok) {
    const detail = payload.detail || `HTTP ${response.status}`;
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return payload;
}

function availableDates() {
  const source = state.source;
  return unique([
    ...(state.artifacts?.snapshots || [])
      .filter((row) => !source || row.source === source)
      .map((row) => row.date),
    ...(state.artifacts?.clusters || [])
      .filter((row) => !source || row.source === source)
      .map((row) => row.date),
  ]);
}

function datesInRange() {
  const dates = availableDates();
  if (!state.startDate || !state.endDate) {
    return dates;
  }
  return dates.filter((date) => date >= state.startDate && date <= state.endDate);
}

function availableDimensions() {
  return (state.artifacts?.dimensions?.length ? state.artifacts.dimensions : DEFAULT_DIMENSIONS).slice().sort();
}

function availableKs() {
  return state.artifacts?.ks?.length ? state.artifacts.ks : [DEFAULT_K];
}

function filteredEquipment() {
  const needle = state.equipmentSearch.trim().toLowerCase();
  if (!needle) {
    return state.equipment;
  }
  return state.equipment.filter((row) => (
    [
      row.equipment_id,
      row.equipment_name,
      row.customer_asset_id,
      ...(row.installation_point_ids || []),
    ]
      .join(" ")
      .toLowerCase()
      .includes(needle)
  ));
}

function selectedEquipment() {
  return state.equipment.find((row) => row.equipment_id === state.equipmentId) || null;
}

function selectedSensorIds() {
  return selectedEquipment()?.installation_point_ids || [];
}

function selectedMetric() {
  return METRICS[state.metric] || METRICS[DEFAULT_METRIC];
}

function metricField(metric, stat) {
  if (metric.axis) {
    return `${metric.prefix}_${stat}_${state.dimension}`;
  }
  return `${metric.prefix}_${stat}`;
}

function scopeLabel() {
  if (state.installationPointId) {
    return `Sensor ${state.installationPointId}`;
  }
  if (state.equipmentId) {
    return `Equipment ${state.equipmentId}`;
  }
  return "All equipment";
}

function scopeClusterRows(rows) {
  if (!state.equipmentId && !state.installationPointId) {
    return rows;
  }
  return rows.filter((row) => selectedPoint(row));
}

function selectedPoint(row) {
  if (state.installationPointId && row.installation_point_id === state.installationPointId) {
    return true;
  }
  if (state.equipmentId && row.equipment_id === state.equipmentId) {
    return true;
  }
  return false;
}

function renderSummary(items) {
  elements.summaryGrid.replaceChildren();
  items.forEach((item) => {
    const card = document.createElement("div");
    card.className = "summary-item";
    const label = document.createElement("span");
    label.textContent = item.label;
    const value = document.createElement("strong");
    value.textContent = item.value ?? "n/a";
    card.append(label, value);
    elements.summaryGrid.append(card);
  });
}

function renderTable(rows, columns) {
  const visibleRows = rows.slice(0, 100);
  const visibleColumns = columns.filter((column) => visibleRows.some((row) => row[column] !== undefined));
  elements.tableHead.replaceChildren();
  elements.tableBody.replaceChildren();
  if (!visibleColumns.length) {
    return;
  }
  const headerRow = document.createElement("tr");
  visibleColumns.forEach((column) => {
    const th = document.createElement("th");
    th.textContent = column;
    headerRow.append(th);
  });
  elements.tableHead.append(headerRow);
  visibleRows.forEach((row) => {
    const tr = document.createElement("tr");
    visibleColumns.forEach((column) => {
      const td = document.createElement("td");
      td.textContent = formatCell(row[column]);
      tr.append(td);
    });
    elements.tableBody.append(tr);
  });
}

function plotChart(traces, layout) {
  if (!window.Plotly) {
    elements.plot.textContent = "Chart library unavailable";
    return;
  }
  if (!traces.length) {
    elements.plot.textContent = "No chartable rows";
    return;
  }
  Plotly.react(elements.plot, traces, {
    margin: { t: 48, r: 20, b: 52, l: 56 },
    paper_bgcolor: "#ffffff",
    plot_bgcolor: "#ffffff",
    font: { family: "Inter, Segoe UI, sans-serif", size: 12, color: "#18202a" },
    title: { text: layout.title, font: { size: 15 } },
    xaxis: layout.xaxis || {},
    yaxis: layout.yaxis || {},
    legend: { orientation: "h" },
  }, {
    displayModeBar: false,
    responsive: true,
  });
}

function clearView() {
  setStatus("Loading...");
  renderSummary([]);
  elements.tableHead.replaceChildren();
  elements.tableBody.replaceChildren();
  if (window.Plotly) {
    Plotly.purge(elements.plot);
  }
  elements.plot.textContent = "";
}

function setStatus(message) {
  elements.statusLine.textContent = message;
}

function aggregateTrendRows(rows, field) {
  const byDate = groupBy(rows, "date");
  return Object.entries(byDate)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([date, dateRows]) => ({ date, [field]: average(dateRows, field) }));
}

function lineTrace(rows, field, name, color) {
  if (!rows.some((row) => numeric(row[field]) !== null)) {
    return null;
  }
  return {
    type: "scatter",
    mode: "lines+markers",
    name,
    x: rows.map((row) => row.date),
    y: rows.map((row) => numeric(row[field])),
    line: { color },
  };
}

function setOptions(select, values, labeler, selected) {
  const rows = values.map((value) => (
    typeof value === "object" ? value : { value: String(value), label: labeler(value) }
  ));
  select.replaceChildren();
  rows.forEach((row) => {
    const option = document.createElement("option");
    option.value = String(row.value);
    option.textContent = row.label ?? labeler(row.value);
    select.append(option);
  });
  if (rows.some((row) => String(row.value) === String(selected))) {
    select.value = String(selected);
  } else if (rows.length) {
    select.value = String(rows[0].value);
  }
}

function emptyBlock(text) {
  const node = document.createElement("p");
  node.className = "empty-block";
  node.textContent = text;
  return node;
}

function unique(values) {
  return Array.from(new Set(values.filter(Boolean))).sort();
}

function groupBy(rows, field) {
  return rows.reduce((groups, row) => {
    const key = row[field] || "";
    groups[key] = groups[key] || [];
    groups[key].push(row);
    return groups;
  }, {});
}

function average(rows, field) {
  const values = rows.map((row) => numeric(row[field])).filter((value) => value !== null);
  if (!values.length) {
    return null;
  }
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function countValues(rows, field) {
  return rows.reduce((counts, row) => {
    const key = row[field] || "unknown";
    counts[key] = (counts[key] || 0) + 1;
    return counts;
  }, {});
}

function numeric(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatNumber(value) {
  const parsed = numeric(value);
  return parsed === null ? "n/a" : parsed.toFixed(3);
}

function formatCell(value) {
  const parsed = numeric(value);
  if (parsed !== null && String(value).length > 8) {
    return parsed.toFixed(4);
  }
  return value ?? "";
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function debounce(callback, delay) {
  let timeout;
  return () => {
    clearTimeout(timeout);
    timeout = setTimeout(callback, delay);
  };
}

init();
