const DEFAULT_DIMENSIONS = ["x", "y", "z", "temperature"];
const DEFAULT_METRIC = "rms_vel";
const DEFAULT_K = "4";
const VALID_SCOPE_TYPES = new Set(["all", "asset_tree", "equipment", "sensor"]);

const METRICS = {
  rms_vel: { label: "RMS Velocity", prefix: "rms_vel", axis: true, unit: "in/s" },
  rms_accel: { label: "RMS Acceleration", prefix: "rms_accel", axis: true, unit: "m/s2" },
  rms_pkpk: { label: "RMS Peak-to-Peak", prefix: "rms_pkpk", axis: true, unit: "source" },
  rms_cf: { label: "RMS Crest Factor", prefix: "rms_cf", axis: true, unit: "ratio" },
  temp_sensor: { label: "Sensor Temperature", prefix: "temp_sensor", axis: false, unit: "deg F" },
  impact: { label: "Impact", prefix: "impact", axis: false, unit: "m/s2" },
};

const state = {
  artifacts: null,
  equipmentTree: [],
  health: null,
  source: "",
  startDate: "",
  endDate: "",
  date: "",
  view: "snapshot",
  scopeType: "all",
  assetTreeId: "",
  equipmentId: "",
  installationPointId: "",
  sensorId: "",
  dimension: "x",
  metric: DEFAULT_METRIC,
  k: DEFAULT_K,
  equipmentSearch: "",
  expandedAssetTrees: new Set(),
  expandedEquipment: new Set(),
  scopeNotice: "",
};

const elements = {
  healthStatus: document.querySelector("#health-status"),
  sourceSelect: document.querySelector("#source-select"),
  startDateSelect: document.querySelector("#start-date-select"),
  endDateSelect: document.querySelector("#end-date-select"),
  refreshButton: document.querySelector("#refresh-button"),
  equipmentSearch: document.querySelector("#equipment-search"),
  allEquipmentButton: document.querySelector("#all-equipment-button"),
  scopeStatus: document.querySelector("#scope-status"),
  equipmentTree: document.querySelector("#equipment-tree"),
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
    updateState({ source: elements.sourceSelect.value }, false);
    normalizeState();
    updateUrlFromState();
    updateControlsFromState();
    await loadEquipmentTree();
    await renderActiveView();
  });

  elements.startDateSelect.addEventListener("change", async () => {
    updateState({ startDate: elements.startDateSelect.value }, false);
    normalizeDateRange("start");
    updateUrlFromState();
    updateControlsFromState();
    await loadEquipmentTree();
    await renderActiveView();
  });

  elements.endDateSelect.addEventListener("change", async () => {
    updateState({ endDate: elements.endDateSelect.value }, false);
    normalizeDateRange("end");
    updateUrlFromState();
    updateControlsFromState();
    await loadEquipmentTree();
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
    setScope({ scopeType: "all" });
  });

  elements.refreshButton.addEventListener("click", async () => {
    await loadArtifacts();
    await renderActiveView();
  });

  window.addEventListener("popstate", async () => {
    readStateFromUrl();
    normalizeState();
    updateControlsFromState();
    await loadEquipmentTree();
    await renderActiveView();
  });
}

async function loadArtifacts() {
  setStatus("Loading artifacts...");
  state.artifacts = await fetchJson("/api/artifacts");
  normalizeState();
  updateControlsFromState();
  await loadEquipmentTree();
  setStatus("Ready");
}

async function loadEquipmentTree() {
  const params = new URLSearchParams();
  params.set("source", state.source);
  if (state.startDate) {
    params.set("start_date", state.startDate);
  }
  if (state.endDate) {
    params.set("end_date", state.endDate);
  }
  const payload = await fetchJson(`/api/equipment-tree?${params}`);
  state.equipmentTree = payload.asset_trees || [];
  const changed = normalizeScopeAgainstTree();
  expandSelectedScope();
  if (changed) {
    updateUrlFromState(true);
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
  if (!VALID_SCOPE_TYPES.has(state.scopeType)) {
    resetScope();
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
  const trees = filteredEquipmentTree();
  elements.allEquipmentButton.classList.toggle("is-active", state.scopeType === "all");
  elements.scopeStatus.textContent = state.scopeNotice || `Scope: ${scopeLabel()}`;
  elements.equipmentTree.replaceChildren();

  if (!trees.length) {
    elements.equipmentTree.append(emptyBlock("No equipment in context"));
    return;
  }

  trees.forEach((assetTree) => {
    const group = document.createElement("div");
    group.className = "tree-group";
    const assetExpanded = isAssetExpanded(assetTree);
    group.append(
      createTreeRow({
        level: "asset",
        active: state.scopeType === "asset_tree" && state.assetTreeId === assetTree.asset_tree_id,
        expanded: assetExpanded,
        hasChildren: Boolean(assetTree.equipment?.length),
        label: assetTree.asset_tree_name || `Asset Tree ${assetTree.asset_tree_id}`,
        title: [
          assetTree.asset_tree_name,
          assetTree.asset_tree_id ? `Asset Tree ${assetTree.asset_tree_id}` : "",
          assetTree.asset_tree_path,
        ].filter(Boolean).join(" | "),
        detail: `${assetTree.equipment_count || 0} equipment | ${assetTree.sensor_count || 0} sensors`,
        onToggle: () => toggleAsset(assetTree.asset_tree_id),
        onSelect: () => setScope({
          scopeType: "asset_tree",
          assetTreeId: assetTree.asset_tree_id,
        }),
      }),
    );

    if (assetExpanded) {
      (assetTree.equipment || []).forEach((equipment) => {
        const equipmentExpanded = isEquipmentExpanded(assetTree, equipment);
        group.append(
          createTreeRow({
            level: "equipment",
            active: state.scopeType === "equipment" && state.equipmentId === equipment.equipment_id,
            expanded: equipmentExpanded,
            hasChildren: Boolean(equipment.sensors?.length),
            label: compactEquipmentLabel(equipment.equipment_name) || `Equipment ${equipment.equipment_id}`,
            title: [
              equipment.equipment_name,
              equipment.equipment_id ? `Equipment ${equipment.equipment_id}` : "",
              equipment.customer_asset_id,
              dateRangeLabel(equipment),
            ].filter(Boolean).join(" | "),
            detail: [
              equipment.customer_asset_id,
              `${equipment.sensor_count || 0} sensors`,
            ].filter(Boolean).join(" | "),
            onToggle: () => toggleEquipment(equipment.equipment_id),
            onSelect: () => setScope({
              scopeType: "equipment",
              assetTreeId: assetTree.asset_tree_id,
              equipmentId: equipment.equipment_id,
            }),
          }),
        );

        if (equipmentExpanded) {
          (equipment.sensors || []).forEach((sensor) => {
            group.append(
              createTreeRow({
                level: "sensor",
                active: state.scopeType === "sensor"
                  && state.installationPointId === sensor.installation_point_id,
                expanded: false,
                hasChildren: false,
                label: sensor.installation_point_name
                  || `Sensor ${sensor.installation_point_id}`,
                title: [
                  sensor.installation_point_name,
                  sensor.installation_point_id ? `Installation Point ${sensor.installation_point_id}` : "",
                  sensor.sensor_id ? `Sensor ${sensor.sensor_id}` : "",
                  sensor.customer_asset_id,
                  dateRangeLabel(sensor),
                ].filter(Boolean).join(" | "),
                onSelect: () => setScope({
                  scopeType: "sensor",
                  assetTreeId: assetTree.asset_tree_id,
                  equipmentId: equipment.equipment_id,
                  installationPointId: sensor.installation_point_id,
                  sensorId: sensor.sensor_id || "",
                }),
              }),
            );
          });
        }
      });
    }
    elements.equipmentTree.append(group);
  });
}

function createTreeRow(options) {
  const row = document.createElement("div");
  row.className = `tree-row is-${options.level}`;
  row.classList.toggle("is-active", options.active);
  row.setAttribute("role", "treeitem");
  row.setAttribute("aria-selected", options.active ? "true" : "false");

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "tree-toggle";
  if (options.hasChildren) {
    toggle.textContent = options.expanded ? "-" : "+";
    toggle.setAttribute("aria-label", `${options.expanded ? "Collapse" : "Expand"} ${options.label}`);
    toggle.addEventListener("click", options.onToggle);
  } else {
    toggle.textContent = "";
    toggle.classList.add("is-placeholder");
    toggle.tabIndex = -1;
    toggle.setAttribute("aria-hidden", "true");
  }

  const select = document.createElement("button");
  select.type = "button";
  select.className = "tree-select";
  select.title = options.title || options.label || "";
  select.addEventListener("click", options.onSelect);

  const label = document.createElement("span");
  label.className = "tree-label";
  const strong = document.createElement("strong");
  strong.textContent = options.label || "Unnamed";
  label.append(strong);
  if (options.secondary) {
    const secondary = document.createElement("span");
    secondary.textContent = options.secondary;
    label.append(secondary);
  }
  if (options.detail) {
    const detail = document.createElement("small");
    detail.textContent = options.detail;
    label.append(detail);
  }
  select.append(label);
  row.append(toggle, select);
  return row;
}

function compactEquipmentLabel(label = "") {
  const parts = String(label).split(" - ");
  if (parts.length > 1) {
    return parts.slice(1).join(" - ").trim();
  }
  return label;
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
  const rows = filterRowsForScope(payload.rows || []);
  setStatus(`Snapshot ${payload.source} ${payload.date}`);
  renderSummary([
    { label: "Sensors", value: rows.length },
    { label: "All Rows", value: payload.row_count },
    { label: "Metric", value: metric.label },
    { label: "Scope", value: scopeLabel() },
  ]);
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
  const sensorRows = filterRowsForScope(payload.sensor_rows || []);
  const equipmentRows = filterRowsForScope(payload.equipment_rows || []);
  setStatus(`Trend ${payload.source} ${payload.start_date} to ${payload.end_date}`);
  renderSummary([
    { label: "Sensor Rows", value: sensorRows.length },
    { label: "Equipment Rows", value: equipmentRows.length },
    { label: "Metric", value: metric.label },
    { label: "Scope", value: scopeLabel() },
  ]);
  const aggregates = aggregateTrendRows(sensorRows, meanField);
  plotChart(
    [lineTrace(aggregates, meanField, metric.label, "#287271")].filter(Boolean),
    { title: `${metric.label} Trend`, xaxis: { title: "Date" }, yaxis: { title: metric.unit } },
  );
  renderTable(sensorRows, [
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
  const clusterRows = scopeClusterRows(payload.rows || []);
  setStatus(`Cluster ${payload.source} ${payload.date} ${payload.dimension} k=${payload.k}`);
  renderSummary([
    { label: "Sensors", value: clusterRows.length },
    { label: "All Sensors", value: payload.row_count },
    { label: "Inertia", value: formatNumber(metrics.kmeans?.inertia) },
    { label: "Scope", value: scopeLabel() },
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
  renderTable(clusterRows, [
    "installation_point_id",
    "equipment_id",
    "equipment_name",
    "cluster",
    "distance_to_centroid",
    ...featureColumns,
  ]);
  if (metricValues.silhouette_score?.value !== undefined) {
    setStatus(`Cluster ${payload.source} ${payload.date} | silhouette ${formatNumber(metricValues.silhouette_score.value)}`);
  }
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
    { label: "Scope", value: scopeLabel() },
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
  const rawRows = payload.aligned_rows?.length ? payload.aligned_rows : payload.raw_rows || [];
  const rows = filterRowsForScope(rawRows);
  renderSummary([
    { label: "Matched", value: rows.length },
    { label: "All Matched", value: aligned.matched_sensor_count || payload.metrics.matched_sensor_count },
    { label: "Raw Changes", value: payload.metrics.changed_sensor_count },
    { label: "Scope", value: scopeLabel() },
  ]);
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
    { label: "Scope", value: scopeLabel() },
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
  if (state.scopeType === "equipment" && state.equipmentId) {
    params.set("equipment_id", state.equipmentId);
  }
  if (state.scopeType === "sensor" && state.installationPointId) {
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

function setScope(scope) {
  state.scopeNotice = "";
  state.scopeType = scope.scopeType || "all";
  state.assetTreeId = scope.assetTreeId || "";
  state.equipmentId = scope.equipmentId || "";
  state.installationPointId = scope.installationPointId || "";
  state.sensorId = scope.sensorId || "";
  if (state.scopeType === "all") {
    resetScope();
  }
  normalizeScopeAgainstTree();
  expandSelectedScope();
  updateUrlFromState();
  updateControlsFromState();
  renderNavigator();
  renderActiveView();
}

function resetScope() {
  state.scopeType = "all";
  state.assetTreeId = "";
  state.equipmentId = "";
  state.installationPointId = "";
  state.sensorId = "";
}

function readStateFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const scope = params.get("scope");
  const legacyInstallationId = params.get("installation_point_id") || "";
  const legacyEquipmentId = params.get("equipment_id") || "";
  const scopeType = VALID_SCOPE_TYPES.has(scope) ? scope : legacyInstallationId ? "sensor" : legacyEquipmentId ? "equipment" : "all";
  Object.assign(state, {
    source: params.get("source") || state.source,
    startDate: params.get("start_date") || state.startDate,
    endDate: params.get("end_date") || state.endDate,
    date: params.get("date") || state.date,
    view: params.get("view") || state.view,
    scopeType,
    assetTreeId: params.get("asset_tree_id") || "",
    equipmentId: params.get("equipment_id") || legacyEquipmentId,
    installationPointId: params.get("installation_point_id") || legacyInstallationId,
    sensorId: params.get("sensor_id") || "",
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
  params.set("scope", state.scopeType);
  if (state.assetTreeId) {
    params.set("asset_tree_id", state.assetTreeId);
  }
  if (state.equipmentId) {
    params.set("equipment_id", state.equipmentId);
  }
  if (state.installationPointId) {
    params.set("installation_point_id", state.installationPointId);
  }
  if (state.sensorId) {
    params.set("sensor_id", state.sensorId);
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

function filteredEquipmentTree() {
  const needle = state.equipmentSearch.trim().toLowerCase();
  if (!needle) {
    return state.equipmentTree;
  }
  return state.equipmentTree
    .map((assetTree) => {
      const assetMatches = textMatches(needle, [
        assetTree.asset_tree_id,
        assetTree.asset_tree_name,
        assetTree.asset_tree_path,
      ]);
      const equipment = (assetTree.equipment || [])
        .map((row) => {
          const equipmentMatches = textMatches(needle, [
            row.equipment_id,
            row.equipment_name,
            row.customer_asset_id,
          ]);
          const sensors = (row.sensors || []).filter((sensor) => textMatches(needle, [
            sensor.installation_point_id,
            sensor.installation_point_name,
            sensor.sensor_id,
            sensor.customer_asset_id,
          ]));
          if (assetMatches || equipmentMatches) {
            return row;
          }
          return sensors.length ? { ...row, sensors, sensor_count: sensors.length } : null;
        })
        .filter(Boolean);
      if (assetMatches || equipment.length) {
        return {
          ...assetTree,
          equipment,
          equipment_count: equipment.length,
          sensor_count: equipment.reduce((sum, row) => sum + (row.sensors?.length || 0), 0),
        };
      }
      return null;
    })
    .filter(Boolean);
}

function textMatches(needle, values) {
  return values
    .filter((value) => value !== null && value !== undefined)
    .join(" ")
    .toLowerCase()
    .includes(needle);
}

function normalizeScopeAgainstTree() {
  if (state.scopeType === "all") {
    resetScope();
    return false;
  }
  const resolved = resolveScope();
  if (!resolved) {
    resetScope();
    state.scopeNotice = "Selected scope is no longer in context; showing all equipment.";
    return true;
  }
  const changed = [
    ["assetTreeId", resolved.assetTreeId],
    ["equipmentId", resolved.equipmentId],
    ["installationPointId", resolved.installationPointId],
    ["sensorId", resolved.sensorId],
  ].some(([key, value]) => state[key] !== (value || ""));
  state.assetTreeId = resolved.assetTreeId || "";
  state.equipmentId = resolved.equipmentId || "";
  state.installationPointId = resolved.installationPointId || "";
  state.sensorId = resolved.sensorId || "";
  return changed;
}

function resolveScope() {
  if (state.scopeType === "asset_tree") {
    const tree = findAssetTree(state.assetTreeId);
    return tree ? { assetTreeId: tree.asset_tree_id } : null;
  }
  if (state.scopeType === "equipment") {
    const found = findEquipment(state.equipmentId);
    return found ? {
      assetTreeId: found.assetTree.asset_tree_id,
      equipmentId: found.equipment.equipment_id,
    } : null;
  }
  if (state.scopeType === "sensor") {
    const found = findSensor(state.installationPointId, state.sensorId);
    return found ? {
      assetTreeId: found.assetTree.asset_tree_id,
      equipmentId: found.equipment.equipment_id,
      installationPointId: found.sensor.installation_point_id,
      sensorId: found.sensor.sensor_id || "",
    } : null;
  }
  return null;
}

function findAssetTree(assetTreeId) {
  return state.equipmentTree.find((tree) => tree.asset_tree_id === assetTreeId) || null;
}

function findEquipment(equipmentId) {
  for (const assetTree of state.equipmentTree) {
    const equipment = (assetTree.equipment || []).find((row) => row.equipment_id === equipmentId);
    if (equipment) {
      return { assetTree, equipment };
    }
  }
  return null;
}

function findSensor(installationPointId, sensorId = "") {
  for (const assetTree of state.equipmentTree) {
    for (const equipment of assetTree.equipment || []) {
      const sensor = (equipment.sensors || []).find((row) => (
        (installationPointId && row.installation_point_id === installationPointId)
        || (sensorId && row.sensor_id === sensorId)
      ));
      if (sensor) {
        return { assetTree, equipment, sensor };
      }
    }
  }
  return null;
}

function expandSelectedScope() {
  if (state.assetTreeId) {
    state.expandedAssetTrees.add(state.assetTreeId);
  }
  if (state.equipmentId) {
    state.expandedEquipment.add(state.equipmentId);
  }
}

function isAssetExpanded(assetTree) {
  return Boolean(
    state.equipmentSearch
    || state.expandedAssetTrees.has(assetTree.asset_tree_id)
  );
}

function isEquipmentExpanded(assetTree, equipment) {
  return Boolean(
    state.equipmentSearch
    || state.expandedEquipment.has(equipment.equipment_id)
  );
}

function toggleAsset(assetTreeId) {
  toggleSet(state.expandedAssetTrees, assetTreeId);
  renderNavigator();
}

function toggleEquipment(equipmentId) {
  toggleSet(state.expandedEquipment, equipmentId);
  renderNavigator();
}

function toggleSet(set, value) {
  if (set.has(value)) {
    set.delete(value);
  } else {
    set.add(value);
  }
}

function scopeLabel() {
  if (state.scopeType === "asset_tree") {
    const tree = findAssetTree(state.assetTreeId);
    return tree?.asset_tree_name || `Asset Tree ${state.assetTreeId}`;
  }
  if (state.scopeType === "equipment") {
    const found = findEquipment(state.equipmentId);
    return found?.equipment.equipment_name || `Equipment ${state.equipmentId}`;
  }
  if (state.scopeType === "sensor") {
    const found = findSensor(state.installationPointId, state.sensorId);
    return found?.sensor.installation_point_name || `Sensor ${state.installationPointId || state.sensorId}`;
  }
  return "All equipment";
}

function filterRowsForScope(rows) {
  if (state.scopeType === "all") {
    return rows;
  }
  return rows.filter((row) => rowInScope(row));
}

function scopeClusterRows(rows) {
  return filterRowsForScope(rows);
}

function selectedPoint(row) {
  return state.scopeType !== "all" && rowInScope(row);
}

function rowInScope(row) {
  const equipmentId = String(row.equipment_id || "");
  const installationPointId = String(row.installation_point_id || "");
  if (state.scopeType === "asset_tree") {
    const tree = findAssetTree(state.assetTreeId);
    if (!tree) {
      return false;
    }
    return treeIncludesRow(tree, equipmentId, installationPointId);
  }
  if (state.scopeType === "equipment") {
    return equipmentId === state.equipmentId || equipmentIncludesInstallation(state.equipmentId, installationPointId);
  }
  if (state.scopeType === "sensor") {
    if (installationPointId) {
      return installationPointId === state.installationPointId;
    }
    return equipmentId === state.equipmentId;
  }
  return true;
}

function treeIncludesRow(tree, equipmentId, installationPointId) {
  return (tree.equipment || []).some((equipment) => (
    equipment.equipment_id === equipmentId
    || (equipment.sensors || []).some((sensor) => sensor.installation_point_id === installationPointId)
  ));
}

function equipmentIncludesInstallation(equipmentId, installationPointId) {
  const found = findEquipment(equipmentId);
  return Boolean(
    found && (found.equipment.sensors || []).some((sensor) => sensor.installation_point_id === installationPointId),
  );
}

function dateRangeLabel(row) {
  if (row.first_date && row.last_date && row.first_date !== row.last_date) {
    return `${row.first_date} to ${row.last_date}`;
  }
  return row.first_date || row.last_date || "";
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
