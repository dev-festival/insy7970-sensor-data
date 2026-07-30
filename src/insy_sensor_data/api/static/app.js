const AXIS_DIMENSIONS = ["x", "y", "z"];
const DEFAULT_DIMENSIONS = [...AXIS_DIMENSIONS, "temperature"];
const DEFAULT_FEATURE_SPACES = ["x_accel", "y_vel", "z_vel", "temperature"];
const DEFAULT_METRIC = "rms_vel";
const DEFAULT_K = "5";
const VALID_SCOPE_TYPES = new Set(["all", "asset_tree", "equipment", "sensor"]);
const FEATURE_SPACE_LABELS = {
  x_accel: "X Acceleration",
  y_vel: "Y Velocity",
  z_vel: "Z Velocity",
  temperature: "Temperature",
};

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
  featureSpace: "x_accel",
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
  metricCoverage: document.querySelector("#metric-coverage"),
  dimensionControl: document.querySelector("#dimension-control"),
  kControl: document.querySelector("#k-control"),
  statusLine: document.querySelector("#status-line"),
  snapshotReview: document.querySelector("#snapshot-review"),
  snapshotScroll: document.querySelector("#snapshot-scroll"),
  snapshotContext: document.querySelector("#snapshot-context"),
  snapshotTrendStatus: document.querySelector("#snapshot-trend-status"),
  snapshotTrendChart: document.querySelector("#snapshot-trend-chart"),
  snapshotClusterStatus: document.querySelector("#snapshot-cluster-status"),
  snapshotClusterChart: document.querySelector("#snapshot-cluster-chart"),
  snapshotEventsDetail: document.querySelector("#snapshot-events-detail"),
  snapshotEventsStatus: document.querySelector("#snapshot-events-status"),
  snapshotEventsHead: document.querySelector("#snapshot-events-head"),
  snapshotEventsBody: document.querySelector("#snapshot-events-body"),
  snapshotMeasurementsDetail: document.querySelector("#snapshot-measurements-detail"),
  snapshotMeasurementsStatus: document.querySelector("#snapshot-measurements-status"),
  snapshotMeasurementsHead: document.querySelector("#snapshot-measurements-head"),
  snapshotMeasurementsBody: document.querySelector("#snapshot-measurements-body"),
  snapshotDiagnosticsHead: document.querySelector("#snapshot-diagnostics-head"),
  snapshotDiagnosticsBody: document.querySelector("#snapshot-diagnostics-body"),
  summaryGrid: document.querySelector("#summary-grid"),
  reviewMain: document.querySelector(".review-main"),
  workspace: document.querySelector("#workspace"),
  plot: document.querySelector("#plot"),
  tableShell: document.querySelector("#table-shell"),
  tableHead: document.querySelector("#data-table-head"),
  tableBody: document.querySelector("#data-table-body"),
  tabs: Array.from(document.querySelectorAll(".tab")),
  viewPinned: document.querySelector("#view-pinned"),
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
    resetSnapshotDateToRangeEnd();
    updateUrlFromState();
    updateControlsFromState();
    await loadEquipmentTree();
    await renderActiveView();
  });

  elements.startDateSelect.addEventListener("change", async () => {
    updateState({ startDate: elements.startDateSelect.value }, false);
    normalizeDateRange("start");
    resetSnapshotDateToRangeEnd();
    updateUrlFromState();
    updateControlsFromState();
    await loadEquipmentTree();
    await renderActiveView();
  });

  elements.endDateSelect.addEventListener("change", async () => {
    updateState({ endDate: elements.endDateSelect.value }, false);
    normalizeDateRange("end");
    resetSnapshotDateToRangeEnd();
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
    if (clusterModelView()) {
      updateState({ featureSpace: elements.dimensionSelect.value });
    } else {
      updateState({ dimension: elements.dimensionSelect.value });
    }
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

  window.addEventListener("resize", debounce(() => {
    redrawCharts();
  }, 150));

  [
    elements.snapshotEventsDetail,
    elements.snapshotMeasurementsDetail,
  ].forEach((detail) => {
    detail?.addEventListener("toggle", () => {
      window.requestAnimationFrame(redrawSnapshotCharts);
    });
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

  const dimensions = ["snapshot", "trend"].includes(state.view) ? AXIS_DIMENSIONS : availableDimensions();
  if (!dimensions.includes(state.dimension)) {
    state.dimension = dimensions[0] || "x";
  }
  const featureSpaces = availableFeatureSpaces();
  if (!featureSpaces.includes(state.featureSpace)) {
    state.featureSpace = featureSpaces[0] || "x_accel";
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

function resetSnapshotDateToRangeEnd() {
  state.date = state.endDate || state.startDate || "";
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
  const modelView = clusterModelView();
  const dimensionOptions = modelView ? availableFeatureSpaces() : selectableDimensions();
  const dimensionLabel = elements.dimensionControl.querySelector("span");
  if (dimensionLabel) {
    dimensionLabel.textContent = modelView ? "Feature Space" : "Dimension";
  }
  setOptions(
    elements.dimensionSelect,
    dimensionOptions,
    (value) => modelView ? featureSpaceLabel(value) : value,
    modelView ? state.featureSpace : state.dimension,
  );
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
  elements.dateControl.hidden = state.view !== "cluster";
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
  if (normalizeViewParameters()) {
    updateUrlFromState(true);
  }
  updateControlsFromState();
  clearView();
  try {
    if (state.view === "snapshot") {
      await renderSnapshotReview();
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

async function renderSnapshotReview() {
  const params = snapshotReviewParams();
  const reviewDate = snapshotDate();
  const payload = await fetchJson(`/api/snapshot-review/${reviewDate}?${params}`);
  setStatus(`Snapshot review ${payload.source} ${payload.date} | ${payload.scope.label}`);
  renderSnapshotContext(payload);
  renderSnapshotTrendPanel(payload.trend || {});
  renderSnapshotClusterPanel(payload.cluster_context || {});
  renderSnapshotEventsPanel(payload.events || {});
  renderSnapshotMeasurements(payload.measurements || {}, payload.date);
  renderSnapshotDiagnostics(payload.trend?.coverage || {});
}

function renderSnapshotContext(payload) {
  const context = payload.context || {};
  elements.snapshotContext.replaceChildren();
  const title = document.createElement("div");
  title.className = "snapshot-title";
  const heading = document.createElement("h2");
  heading.textContent = context.equipment_name || context.label || payload.scope?.label || "Snapshot review";
  const subheading = document.createElement("p");
  subheading.textContent = [
    context.sensor_name || `${context.sensor_count || 0} sensors`,
    payload.date ? `Snapshot date ${payload.date}` : "",
  ].filter(Boolean).join(" | ");
  title.append(heading, subheading);

  const stats = document.createElement("div");
  stats.className = "snapshot-context-stats";
  [
    { label: "Asset Number", value: context.customer_asset_id || "n/a" },
    { label: "Rows", value: context.snapshot_row_count ?? 0 },
    { label: "Sensors", value: context.sensor_count ?? 0 },
    { label: "Scope", value: payload.scope?.type || "all" },
  ].forEach((item) => stats.append(snapshotStat(item.label, item.value)));
  elements.snapshotContext.append(title, stats);
}

function renderSnapshotTrendPanel(trend) {
  const coverage = trend.coverage || {};
  elements.snapshotTrendStatus.textContent = trend.status === "available"
    ? `${trend.row_count || 0} scoped rows | ${coverage.observed_value_count || 0} readings`
    : trend.message || "No trend data for this range";
  renderMetricCoverage(trend.status === "available" ? coverage : null);
  const field = trend.value_field || metricField(selectedMetric(), "mean");
  const traces = snapshotTrendTraces(trend, field);
  plotInto(
    elements.snapshotTrendChart,
    traces,
    {
      title: selectedMetric().label,
      xaxis: { title: "Date", range: [state.startDate, state.endDate] },
      yaxis: { title: selectedMetric().unit },
      onPointActivate: selectSnapshotDate,
    },
  );
}

function renderSnapshotClusterPanel(clusterContext) {
  const modelLabel = clusterContext.feature_space ? `${featureSpaceLabel(clusterContext.feature_space)} | ` : "";
  elements.snapshotClusterStatus.textContent = clusterContext.status === "available"
    ? `${clusterContext.row_count || 0} scoped points | ${modelLabel}k=${clusterContext.k}`
    : clusterContext.message || "No cluster artifact for this date";
  const selectedIds = new Set(clusterContext.selected_ids || []);
  const grouped = groupBy(clusterContext.points || [], "cluster");
  const traces = Object.entries(grouped).map(([cluster, rows]) => ({
    type: "scatter",
    mode: "markers",
    name: `Cluster ${cluster}`,
    x: rows.map((row) => numeric(row.pc1)),
    y: rows.map((row) => numeric(row.pc2)),
    text: rows.map((row) => row.installation_point_name || row.installation_point_id),
    marker: {
      size: rows.map((row) => selectedIds.has(String(row.installation_point_id || "")) ? 13 : 8),
      line: { width: rows.map((row) => selectedIds.has(String(row.installation_point_id || "")) ? 2 : 0), color: "#18202a" },
    },
  }));
  plotInto(
    elements.snapshotClusterChart,
    traces,
    { title: "Cluster PCA", xaxis: { title: "PC1" }, yaxis: { title: "PC2" } },
  );
}

function renderSnapshotEventsPanel(events) {
  const maximo = events.providers?.maximo || {};
  let suffix = "";
  if (maximo.status === "not_requested") {
    suffix = " | Maximo: select Asset Tree";
  } else if (maximo.status === "unavailable") {
    suffix = " | Maximo unavailable";
  } else if (["available", "partial"].includes(maximo.status)) {
    const warning = maximo.warning_count ? `, ${maximo.warning_count} assets skipped` : "";
    suffix = ` | Maximo: ${maximo.row_count || 0}${warning}`;
  }
  elements.snapshotEventsStatus.textContent = `${events.row_count || 0} scoped events${suffix}`;
  renderTableInto(
    elements.snapshotEventsHead,
    elements.snapshotEventsBody,
    events.rows || [],
    ["date", "source", "status", "type", "asset_number", "sensor_name", "event_id", "work_order", "title"],
  );
}

function renderSnapshotMeasurements(measurements, snapshotDate) {
  const selectedDate = measurements.snapshot_date || snapshotDate;
  elements.snapshotMeasurementsStatus.textContent = [
    `${measurements.row_count || 0} measurement rows`,
    selectedDate ? `Snapshot ${selectedDate}` : "",
  ].filter(Boolean).join(" | ");
  renderTableInto(
    elements.snapshotMeasurementsHead,
    elements.snapshotMeasurementsBody,
    measurements.rows || [],
    measurements.columns || [],
    { emptyText: "No data" },
  );
}

function renderSnapshotDiagnostics(coverage) {
  const rows = (coverage.sensors || []).map((sensor) => ({
    sensor_name: sensor.sensor_name || sensor.installation_point_id || "Unknown sensor",
    observed_days: sensor.observed_value_count || 0,
    expected_days: sensor.expected_value_count || 0,
    coverage: `${formatCoveragePercent(sensor.coverage_percent)}%`,
    missing_dates: (sensor.missing_dates || []).join(", ") || "None",
  }));
  renderTableInto(
    elements.snapshotDiagnosticsHead,
    elements.snapshotDiagnosticsBody,
    rows,
    ["sensor_name", "observed_days", "expected_days", "coverage", "missing_dates"],
  );
}

function renderMetricCoverage(coverage) {
  if (!elements.metricCoverage) {
    return;
  }
  const expected = numeric(coverage?.expected_value_count);
  const percent = numeric(coverage?.coverage_percent);
  if (expected === null || expected <= 0 || percent === null) {
    elements.metricCoverage.hidden = true;
    elements.metricCoverage.textContent = "";
    elements.metricCoverage.className = "metric-coverage";
    return;
  }
  const stateName = percent >= 80 ? "good" : percent >= 50 ? "partial" : "sparse";
  elements.metricCoverage.hidden = false;
  elements.metricCoverage.textContent = `${formatCoveragePercent(percent)}% coverage`;
  elements.metricCoverage.className = `metric-coverage is-${stateName}`;
}

function formatCoveragePercent(value) {
  const percent = numeric(value);
  if (percent === null) {
    return "0";
  }
  return Number.isInteger(percent) ? String(percent) : percent.toFixed(1);
}

async function renderTrend() {
  renderMetricCoverage(null);
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
    { label: "Input", value: payload.input || payload.input_mode || payload.metadata?.input_mode || "artifact" },
    { label: "Scope", value: scopeLabel() },
  ]);
  const aggregates = aggregateTrendRows(sensorRows, meanField);
  plotChart(
    [lineTrace(aggregates, meanField, metric.label, "#287271", { timeSeries: true })].filter(Boolean),
    {
      title: `${metric.label} Trend`,
      xaxis: { title: "Date", range: [state.startDate, state.endDate] },
      yaxis: { title: metric.unit },
    },
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
  const modelLabel = payload.feature_space ? featureSpaceLabel(payload.feature_space) : payload.dimension;
  setStatus(`Cluster ${payload.source} ${payload.date} ${modelLabel} k=${payload.k}`);
  renderSummary([
    { label: "Sensors", value: clusterRows.length },
    { label: "All Sensors", value: payload.row_count },
    { label: payload.feature_space ? "Feature Space" : "Dimension", value: modelLabel },
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
    setStatus(
      `Cluster ${payload.source} ${payload.date} ${modelLabel} | silhouette ${formatNumber(metricValues.silhouette_score.value)}`,
    );
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
  const modelLabel = payload.feature_space ? featureSpaceLabel(payload.feature_space) : payload.dimension;
  setStatus(`Cluster window ${payload.source} ${payload.start_date} to ${payload.end_date} ${modelLabel}`);
  renderSummary([
    { label: "Dates", value: metrics.date_count },
    { label: "Pairs", value: metrics.pair_count },
    { label: payload.feature_space ? "Feature Space" : "Dimension", value: modelLabel },
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
  const modelLabel = payload.feature_space ? featureSpaceLabel(payload.feature_space) : payload.dimension;
  setStatus(`Drift ${payload.source} ${payload.from_date} to ${payload.to_date} ${modelLabel || ""}`.trim());
  const rawRows = payload.aligned_rows?.length ? payload.aligned_rows : payload.raw_rows || [];
  const rows = filterRowsForScope(rawRows);
  renderSummary([
    { label: "Matched", value: rows.length },
    { label: "All Matched", value: aligned.matched_sensor_count || payload.metrics.matched_sensor_count },
    { label: payload.feature_space ? "Feature Space" : "Dimension", value: modelLabel },
    { label: "Raw Changes", value: payload.metrics.changed_sensor_count ?? payload.metrics.raw_label_changed_count },
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
  showSnapshotSurface(false);
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
    return `uv run sensor-data cluster registry build-grid --source ${state.source} --start-date ${state.date} --end-date ${state.date} --feature-spaces ${state.featureSpace} --ks ${state.k}`;
  }
  if (state.view === "drift") {
    return `uv run sensor-data cluster registry build-grid --source ${state.source} --start-date ${state.startDate} --end-date ${state.endDate} --feature-spaces ${state.featureSpace} --ks ${state.k}`;
  }
  if (state.view === "trend") {
    if (state.source === "api") {
      return `uv run sensor-data workflow api-range --start-date ${state.startDate} --end-date ${state.endDate} --facility 679 --raw-retention release --skip-cluster`;
    }
    return `uv run sensor-data workflow mock-range --start-date ${state.startDate} --end-date ${state.endDate} --skip-cluster`;
  }
  return `uv run sensor-data snapshot build --source ${state.source} --date ${state.date} --input sqlite`;
}

function snapshotReviewParams() {
  const params = new URLSearchParams();
  params.set("source", state.source);
  params.set("start_date", state.startDate);
  params.set("end_date", state.endDate);
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
  params.set("metric", state.metric);
  params.set("dimension", state.dimension);
  params.set("feature_space", state.featureSpace);
  params.set("k", state.k);
  return params;
}

function snapshotDate() {
  return state.date || state.endDate || state.startDate;
}

function selectSnapshotDate(selectedDate) {
  if (state.view !== "snapshot" || !datesInRange().includes(selectedDate) || state.date === selectedDate) {
    return;
  }
  updateState({ date: selectedDate });
  renderActiveView();
}

function scopedParams() {
  const params = new URLSearchParams();
  params.set("source", state.source);
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
  params.set("metric", state.metric);
  params.set("dimension", state.dimension);
  params.set("stat", "mean");
  return params;
}

function clusterParams() {
  const params = new URLSearchParams();
  params.set("source", state.source);
  params.set("date", state.date);
  params.set("dimension", featureSpaceDimension(state.featureSpace));
  params.set("feature_space", state.featureSpace);
  params.set("k", state.k);
  return params;
}

function clusterWindowParams() {
  const params = new URLSearchParams();
  params.set("source", state.source);
  params.set("start_date", state.startDate);
  params.set("end_date", state.endDate);
  params.set("dimension", featureSpaceDimension(state.featureSpace));
  params.set("feature_space", state.featureSpace);
  params.set("k", state.k);
  return params;
}

function driftParamsFromState() {
  const params = new URLSearchParams();
  params.set("source", state.source);
  params.set("from_date", state.startDate);
  params.set("to_date", state.endDate);
  params.set("dimension", featureSpaceDimension(state.featureSpace));
  params.set("feature_space", state.featureSpace);
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
  resetSnapshotPane();
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
  const rawDimension = params.get("dimension") || "";
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
    dimension: isFeatureSpace(rawDimension) ? state.dimension : rawDimension || state.dimension,
    featureSpace: params.get("feature_space") || (isFeatureSpace(rawDimension) ? rawDimension : state.featureSpace),
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
  params.set("feature_space", state.featureSpace);
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
    ...(state.artifacts?.cluster_models || [])
      .filter((row) => !source || row.source === source)
      .map((row) => row.date || row.source_date),
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

function selectableDimensions() {
  if (["snapshot", "trend"].includes(state.view)) {
    return AXIS_DIMENSIONS;
  }
  return availableDimensions();
}

function normalizeViewParameters() {
  if (["snapshot", "trend"].includes(state.view) && !AXIS_DIMENSIONS.includes(state.dimension)) {
    state.dimension = "x";
    return true;
  }
  return false;
}

function availableFeatureSpaces() {
  return (state.artifacts?.feature_spaces?.length ? state.artifacts.feature_spaces : DEFAULT_FEATURE_SPACES).slice().sort();
}

function clusterModelView() {
  return ["cluster", "drift"].includes(state.view);
}

function isFeatureSpace(value) {
  return Boolean(value && Object.prototype.hasOwnProperty.call(FEATURE_SPACE_LABELS, value));
}

function featureSpaceLabel(value) {
  return FEATURE_SPACE_LABELS[value] || value || "n/a";
}

function featureSpaceDimension(value) {
  if (value === "temperature") {
    return "temperature";
  }
  if (String(value || "").startsWith("x_")) {
    return "x";
  }
  if (String(value || "").startsWith("y_")) {
    return "y";
  }
  if (String(value || "").startsWith("z_")) {
    return "z";
  }
  return state.dimension || "x";
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
  renderTableInto(elements.tableHead, elements.tableBody, rows, columns);
}

function renderTableInto(head, body, rows, columns, options = {}) {
  const visibleRows = rows.slice(0, 100);
  const visibleColumns = columns.filter((column) => visibleRows.some((row) => row[column] !== undefined));
  const emptyText = options.emptyText ?? "";
  head.replaceChildren();
  body.replaceChildren();
  if (!visibleColumns.length) {
    body.append(tableMessageRow("No rows", columns.length || 1));
    return;
  }
  const headerRow = document.createElement("tr");
  visibleColumns.forEach((column) => {
    const th = document.createElement("th");
    th.textContent = columnLabel(column);
    headerRow.append(th);
  });
  head.append(headerRow);
  visibleRows.forEach((row) => {
    const tr = document.createElement("tr");
    visibleColumns.forEach((column) => {
      const td = document.createElement("td");
      td.textContent = formatCell(row[column], emptyText);
      tr.append(td);
    });
    body.append(tr);
  });
}

function plotChart(traces, layout) {
  plotInto(elements.plot, traces, layout);
}

function plotInto(element, traces, layout) {
  if (!window.SensorCharts) {
    element.textContent = "Chart renderer unavailable";
    return;
  }
  window.SensorCharts.render(element, traces, layout || {});
}

function redrawCharts() {
  if (!window.SensorCharts) {
    return;
  }
  [
    elements.plot,
    elements.snapshotTrendChart,
    elements.snapshotClusterChart,
  ].forEach((element) => window.SensorCharts.redraw(element));
}

function redrawSnapshotCharts() {
  if (!window.SensorCharts) {
    return;
  }
  [
    elements.snapshotTrendChart,
    elements.snapshotClusterChart,
  ].forEach((element) => window.SensorCharts.redraw(element));
}

function resetSnapshotPane() {
  if (elements.snapshotScroll) {
    elements.snapshotScroll.scrollTop = 0;
  }
  [
    elements.snapshotEventsDetail,
    elements.snapshotMeasurementsDetail,
  ].forEach((detail) => {
    if (detail) {
      detail.open = false;
    }
  });
}

function clearView() {
  setStatus("Loading...");
  showSnapshotSurface(state.view === "snapshot");
  renderSummary([]);
  elements.tableHead.replaceChildren();
  elements.tableBody.replaceChildren();
  if (window.SensorCharts) {
    window.SensorCharts.clear(elements.plot);
    window.SensorCharts.clear(elements.snapshotTrendChart);
    window.SensorCharts.clear(elements.snapshotClusterChart);
  }
  elements.plot.textContent = "";
  elements.snapshotContext.replaceChildren();
  elements.snapshotTrendStatus.textContent = "";
  elements.snapshotTrendChart.textContent = "";
  elements.snapshotClusterStatus.textContent = "";
  elements.snapshotClusterChart.textContent = "";
  elements.snapshotEventsStatus.textContent = "";
  elements.snapshotEventsHead.replaceChildren();
  elements.snapshotEventsBody.replaceChildren();
  elements.snapshotMeasurementsStatus.textContent = "";
  elements.snapshotMeasurementsHead.replaceChildren();
  elements.snapshotMeasurementsBody.replaceChildren();
  elements.snapshotDiagnosticsHead.replaceChildren();
  elements.snapshotDiagnosticsBody.replaceChildren();
  renderMetricCoverage(null);
}

function showSnapshotSurface(showSnapshot) {
  elements.reviewMain.classList.toggle("is-snapshot", showSnapshot);
  elements.viewPinned.classList.toggle("is-snapshot", showSnapshot);
  elements.snapshotContext.hidden = !showSnapshot;
  elements.statusLine.hidden = showSnapshot;
  elements.snapshotReview.hidden = !showSnapshot;
  elements.summaryGrid.hidden = showSnapshot;
  elements.workspace.hidden = showSnapshot;
  elements.tableShell.hidden = showSnapshot;
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

function lineTrace(rows, field, name, color, options = {}) {
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
    marker: {
      size: rows.map((row) => row.date === options.selectedDate ? 12 : 8),
      line: {
        width: rows.map((row) => row.date === options.selectedDate ? 2 : 0),
        color: "#18202a",
      },
    },
    timeSeries: options.timeSeries === true,
  };
}

function snapshotTrendTraces(trend, field) {
  if (trend.status !== "available") {
    return [];
  }
  const palette = ["#287271", "#5d7f9f", "#a64253", "#8a6f3d", "#59656f", "#3d8068"];
  if (["all", "asset_tree"].includes(state.scopeType) && trend.equipment_rows?.length) {
    const rows = aggregateTrendRows(trend.equipment_rows, field);
    const trace = lineTrace(rows, field, "Equipment average", "#287271", {
      selectedDate: state.date,
      timeSeries: true,
    });
    return trace ? [trace] : [];
  }
  const groups = groupBy(trend.sensor_rows || [], "installation_point_id");
  return Object.entries(groups)
    .slice(0, 12)
    .map(([installationId, rows], index) => {
      const chronologicalRows = rows.slice().sort((left, right) => String(left.date).localeCompare(String(right.date)));
      const label = chronologicalRows[0]?.installation_point_name || chronologicalRows[0]?.sensor_id || installationId;
      return lineTrace(chronologicalRows, field, label, palette[index % palette.length], {
        selectedDate: state.date,
        timeSeries: true,
      });
    })
    .filter(Boolean);
}

function snapshotStat(labelText, valueText) {
  const item = document.createElement("div");
  item.className = "snapshot-stat";
  const label = document.createElement("span");
  label.textContent = labelText;
  const value = document.createElement("strong");
  value.textContent = valueText ?? "n/a";
  item.append(label, value);
  return item;
}

function tableMessageRow(message, colspan) {
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = Math.max(colspan, 1);
  cell.textContent = message;
  row.append(cell);
  return row;
}

function columnLabel(column) {
  return column
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
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

function formatCell(value, emptyText = "") {
  if (value === null || value === undefined || value === "") {
    return emptyText;
  }
  const parsed = numeric(value);
  if (parsed !== null && String(value).length > 8) {
    return parsed.toFixed(4);
  }
  return value;
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
