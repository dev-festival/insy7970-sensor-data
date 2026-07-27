const state = {
  artifacts: null,
  equipment: [],
  health: null,
  view: "snapshot",
};

const elements = {
  healthStatus: document.querySelector("#health-status"),
  sourceSelect: document.querySelector("#source-select"),
  dateSelect: document.querySelector("#date-select"),
  startDateSelect: document.querySelector("#start-date-select"),
  endDateSelect: document.querySelector("#end-date-select"),
  dimensionSelect: document.querySelector("#dimension-select"),
  kSelect: document.querySelector("#k-select"),
  equipmentSelect: document.querySelector("#equipment-select"),
  sensorInput: document.querySelector("#sensor-input"),
  refreshButton: document.querySelector("#refresh-button"),
  statusLine: document.querySelector("#status-line"),
  summaryGrid: document.querySelector("#summary-grid"),
  plot: document.querySelector("#plot"),
  tableHead: document.querySelector("#data-table-head"),
  tableBody: document.querySelector("#data-table-body"),
  tabs: Array.from(document.querySelectorAll(".tab")),
};

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

async function init() {
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
  await renderActiveView();
}

function bindEvents() {
  elements.tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      state.view = tab.dataset.view;
      elements.tabs.forEach((candidate) => {
        candidate.classList.toggle("is-active", candidate === tab);
      });
      renderActiveView();
    });
  });

  [
    elements.dateSelect,
    elements.startDateSelect,
    elements.endDateSelect,
    elements.dimensionSelect,
    elements.kSelect,
    elements.equipmentSelect,
  ].forEach((control) => {
    control.addEventListener("change", renderActiveView);
  });

  elements.sourceSelect.addEventListener("change", async () => {
    await loadEquipment();
    populateDateControls();
    await renderActiveView();
  });
  elements.sensorInput.addEventListener("input", debounce(renderActiveView, 250));
  elements.refreshButton.addEventListener("click", async () => {
    await loadArtifacts();
    await renderActiveView();
  });
}

async function loadArtifacts() {
  setStatus("Loading artifacts...");
  state.artifacts = await fetchJson("/api/artifacts");
  populateGlobalControls();
  await loadEquipment();
  setStatus("Ready");
}

async function loadEquipment() {
  const source = selectedSource();
  const params = new URLSearchParams();
  if (source) {
    params.set("source", source);
  }
  const payload = await fetchJson(`/api/equipment?${params}`);
  state.equipment = payload.rows || [];
  populateEquipmentControl();
}

function populateGlobalControls() {
  const preferredSource = selectedSource() || state.health?.source_mode || "mock";
  setOptions(elements.sourceSelect, state.artifacts.sources, (value) => value, preferredSource);
  setOptions(
    elements.dimensionSelect,
    state.artifacts.dimensions.length ? state.artifacts.dimensions : ["x", "y", "z", "temperature"],
    (value) => value,
    elements.dimensionSelect.value || "x",
  );
  setOptions(
    elements.kSelect,
    state.artifacts.ks.length ? state.artifacts.ks : [4],
    (value) => String(value),
    elements.kSelect.value || "4",
  );
  populateDateControls();
}

function populateDateControls() {
  const source = selectedSource();
  const snapshotDates = unique(
    (state.artifacts.snapshots || [])
      .filter((row) => !source || row.source === source)
      .map((row) => row.date),
  );
  const clusterDates = unique(
    (state.artifacts.clusters || [])
      .filter((row) => !source || row.source === source)
      .map((row) => row.date),
  );
  const dates = snapshotDates.length ? snapshotDates : clusterDates;
  const currentDate = elements.dateSelect.value || dates[dates.length - 1];
  setOptions(elements.dateSelect, dates, (value) => value, currentDate);

  const windowRanges = (state.artifacts.cluster_windows || []).filter((row) => !source || row.source === source);
  const trendRanges = (state.artifacts.trends || []).filter((row) => !source || row.source === source);
  const firstRange = windowRanges[windowRanges.length - 1] || trendRanges[trendRanges.length - 1];
  const startValue = elements.startDateSelect.value || firstRange?.start_date || dates[0];
  const endValue = elements.endDateSelect.value || firstRange?.end_date || dates[dates.length - 1];
  setOptions(elements.startDateSelect, dates, (value) => value, startValue);
  setOptions(elements.endDateSelect, dates, (value) => value, endValue);
}

function populateEquipmentControl() {
  const options = [
    { value: "", label: "All equipment" },
    ...state.equipment.map((row) => ({
      value: row.equipment_id,
      label: [row.equipment_id, row.equipment_name || row.customer_asset_id].filter(Boolean).join(" | "),
    })),
  ];
  setStructuredOptions(elements.equipmentSelect, options, elements.equipmentSelect.value);
}

function setOptions(select, values, labeler, selected) {
  const normalized = values.map((value) => String(value));
  select.replaceChildren();
  normalized.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = labeler(value);
    select.append(option);
  });
  if (normalized.includes(String(selected))) {
    select.value = String(selected);
  } else if (normalized.length) {
    select.value = normalized[normalized.length - 1];
  }
}

function setStructuredOptions(select, options, selected) {
  select.replaceChildren();
  options.forEach((row) => {
    const option = document.createElement("option");
    option.value = row.value;
    option.textContent = row.label;
    select.append(option);
  });
  if (options.some((row) => row.value === selected)) {
    select.value = selected;
  }
}

async function renderActiveView() {
  if (!state.artifacts) {
    return;
  }
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
    setStatus(error.message || "Unable to load the selected view");
    renderSummary([{ label: "State", value: "Missing artifact" }]);
  }
}

async function renderSnapshot() {
  const date = requiredValue(elements.dateSelect, "date");
  const params = filteredParams();
  const payload = await fetchJson(`/api/snapshots/${date}?${params}`);
  setStatus(`Snapshot ${payload.source} ${payload.date}`);
  renderSummary([
    { label: "Sensors", value: payload.filtered_row_count },
    { label: "All rows", value: payload.row_count },
    { label: "Source", value: payload.source },
    { label: "Date", value: payload.date },
  ]);
  const rows = payload.rows || [];
  const yField = "rms_vel_mean_x";
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
    { title: "Snapshot RMS velocity X", xaxis: { title: "Sensor" }, yaxis: { title: "in/s" } },
  );
  renderTable(rows, [
    "installation_point_id",
    "equipment_id",
    "equipment_name",
    "sensor_id",
    "customer_asset_id",
    "rms_vel_mean_x",
    "rms_vel_mean_y",
    "rms_vel_mean_z",
    "temp_sensor_mean",
    "impact_mean",
  ]);
}

async function renderTrend() {
  const params = filteredParams();
  params.set("start_date", requiredValue(elements.startDateSelect, "start date"));
  params.set("end_date", requiredValue(elements.endDateSelect, "end date"));
  const payload = await fetchJson(`/api/trends?${params}`);
  setStatus(`Trend ${payload.source} ${payload.start_date} to ${payload.end_date}`);
  renderSummary([
    { label: "Sensor rows", value: payload.filtered_sensor_row_count },
    { label: "Equipment rows", value: payload.filtered_equipment_row_count },
    { label: "Skipped dates", value: (payload.metadata.skipped_dates || []).length },
    { label: "Source", value: payload.source },
  ]);
  const aggregates = aggregateTrendRows(payload.sensor_rows || []);
  plotChart(
    [
      lineTrace(aggregates, "rms_vel_mean_x", "RMS X", "#287271"),
      lineTrace(aggregates, "rms_vel_mean_y", "RMS Y", "#a64253"),
      lineTrace(aggregates, "rms_vel_mean_z", "RMS Z", "#7a5c99"),
      lineTrace(aggregates, "temp_sensor_mean", "Temp", "#d88929"),
    ].filter(Boolean),
    { title: "Trend averages", xaxis: { title: "Date" }, yaxis: { title: "Value" } },
  );
  renderTable(payload.sensor_rows || [], [
    "date",
    "installation_point_id",
    "equipment_id",
    "equipment_name",
    "customer_asset_id",
    "rms_vel_mean_x",
    "rms_vel_mean_y",
    "rms_vel_mean_z",
    "temp_sensor_mean",
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
    marker: { size: 8 },
  }));
  plotChart(traces, { title: "Cluster PCA", xaxis: { title: "PC1" }, yaxis: { title: "PC2" } });
  const featureColumns = (metrics.features || []).slice(0, 4);
  renderTable(payload.rows || [], [
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
    const driftParams = driftParamsFromControls();
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
    { label: "Aligned changes", value: aligned.aligned_changed_count || "n/a" },
    { label: "Raw changes", value: payload.metrics.changed_sensor_count },
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

function filteredParams() {
  const params = new URLSearchParams();
  params.set("source", selectedSource());
  if (elements.equipmentSelect.value) {
    params.set("equipment_id", elements.equipmentSelect.value);
  }
  const sensor = elements.sensorInput.value.trim();
  if (sensor) {
    params.set("installation_point_id", sensor);
  }
  return params;
}

function clusterParams() {
  const params = new URLSearchParams();
  params.set("source", selectedSource());
  params.set("date", requiredValue(elements.dateSelect, "date"));
  params.set("dimension", selectedDimension());
  params.set("k", selectedK());
  return params;
}

function clusterWindowParams() {
  const params = new URLSearchParams();
  params.set("source", selectedSource());
  params.set("start_date", requiredValue(elements.startDateSelect, "start date"));
  params.set("end_date", requiredValue(elements.endDateSelect, "end date"));
  params.set("dimension", selectedDimension());
  params.set("k", selectedK());
  return params;
}

function driftParamsFromControls() {
  const params = new URLSearchParams();
  params.set("source", selectedSource());
  params.set("from_date", requiredValue(elements.startDateSelect, "start date"));
  params.set("to_date", requiredValue(elements.endDateSelect, "end date"));
  params.set("dimension", selectedDimension());
  params.set("k", selectedK());
  return params;
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

function aggregateTrendRows(rows) {
  const byDate = groupBy(rows, "date");
  return Object.entries(byDate)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([date, dateRows]) => ({
      date,
      rms_vel_mean_x: average(dateRows, "rms_vel_mean_x"),
      rms_vel_mean_y: average(dateRows, "rms_vel_mean_y"),
      rms_vel_mean_z: average(dateRows, "rms_vel_mean_z"),
      temp_sensor_mean: average(dateRows, "temp_sensor_mean"),
    }));
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

function selectedSource() {
  return elements.sourceSelect.value || state.health?.source_mode || "mock";
}

function selectedDimension() {
  return elements.dimensionSelect.value || "x";
}

function selectedK() {
  return elements.kSelect.value || "4";
}

function requiredValue(select, label) {
  if (!select.value) {
    throw new Error(`Select a ${label}`);
  }
  return select.value;
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

function debounce(callback, delay) {
  let timeout;
  return () => {
    clearTimeout(timeout);
    timeout = setTimeout(callback, delay);
  };
}

init();
