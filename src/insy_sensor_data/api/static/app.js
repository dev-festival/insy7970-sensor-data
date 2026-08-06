import { fetchJson } from "./api-client.js";
import {
  createAppState,
  readRouteState,
  scopeSearchParams,
  writeRouteState,
} from "./app-state.js";

const AXIS_DIMENSIONS = ["x", "y", "z"];
const DEFAULT_METRIC = "rms_vel";
const VALID_SCOPE_TYPES = new Set(["all", "asset_tree", "equipment", "sensor"]);
const FEATURE_SPACE_LABELS = {
  x_accel: "X Acceleration",
  y_vel: "Y Velocity",
  z_vel: "Z Velocity",
  temperature: "Temperature",
};

const state = createAppState();

const elements = {
  healthStatus: document.querySelector("#health-status"),
  syncStatus: document.querySelector("#sync-status"),
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
  dateControl: document.querySelector("#date-control"),
  metricControl: document.querySelector("#metric-control"),
  metricCoverage: document.querySelector("#metric-coverage"),
  dimensionControl: document.querySelector("#dimension-control"),
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
  readRouteState(state);
  bindEvents();
  await loadHealth();
  try {
    await loadContext();
    await loadEquipmentTree();
    await renderActiveView();
  } catch (error) {
    renderMissingState(error);
  }
}

async function loadHealth() {
  try {
    state.health = await fetchJson("/health");
    elements.healthStatus.textContent = `${state.health.status.toUpperCase()} · ${state.health.source_mode}`;
  } catch (_error) {
    elements.healthStatus.textContent = "Service health unavailable";
  }
}

async function loadContext() {
  setStatus("Loading context...");
  state.context = await fetchJson("/api/context");
  normalizeState();
  renderSynchronization();
  updateControlsFromState();
  setStatus("Ready");
}

function bindEvents() {
  elements.tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectView(tab.dataset.view));
    tab.addEventListener("keydown", (event) => {
      const keys = { ArrowLeft: -1, ArrowRight: 1 };
      let nextIndex = keys[event.key] === undefined ? null : index + keys[event.key];
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = elements.tabs.length - 1;
      if (nextIndex === null) return;
      event.preventDefault();
      const nextTab = elements.tabs[(nextIndex + elements.tabs.length) % elements.tabs.length];
      nextTab.focus();
      selectView(nextTab.dataset.view);
    });
  });

  elements.startDateSelect.addEventListener("change", async () => {
    state.startDate = elements.startDateSelect.value;
    normalizeDateRange("start");
    state.date = state.endDate;
    writeRouteState(state);
    updateControlsFromState();
    await loadEquipmentTree();
    await renderActiveView();
  });
  elements.endDateSelect.addEventListener("change", async () => {
    state.endDate = elements.endDateSelect.value;
    normalizeDateRange("end");
    state.date = state.endDate;
    writeRouteState(state);
    updateControlsFromState();
    await loadEquipmentTree();
    await renderActiveView();
  });
  elements.dateSelect.addEventListener("change", () => {
    state.date = elements.dateSelect.value;
    writeRouteState(state);
    renderActiveView();
  });
  elements.metricSelect.addEventListener("change", () => {
    state.metric = elements.metricSelect.value;
    normalizeMetricDimension();
    writeRouteState(state);
    renderActiveView();
  });
  elements.dimensionSelect.addEventListener("change", () => {
    state.dimension = elements.dimensionSelect.value;
    writeRouteState(state);
    renderActiveView();
  });
  elements.equipmentSearch.addEventListener("input", debounce(() => {
    state.equipmentSearch = elements.equipmentSearch.value;
    renderNavigator();
  }, 150));
  elements.allEquipmentButton.addEventListener("click", () => setScope("all", ""));
  elements.refreshButton.addEventListener("click", async () => {
    await loadContext();
    await loadEquipmentTree();
    await renderActiveView();
  });
  window.addEventListener("popstate", async () => {
    readRouteState(state);
    normalizeState();
    updateControlsFromState();
    await loadEquipmentTree();
    await renderActiveView();
  });
  window.addEventListener("resize", debounce(redrawCharts, 150));
  [elements.snapshotEventsDetail, elements.snapshotMeasurementsDetail].forEach((detail) => {
    detail?.addEventListener("toggle", () => window.requestAnimationFrame(redrawSnapshotCharts));
  });
}

function selectView(view) {
  if (view === state.view) return;
  state.view = view;
  writeRouteState(state);
  updateControlsFromState();
  renderActiveView();
}

async function loadEquipmentTree() {
  if (!availableDates().length) {
    state.equipmentTree = [];
    renderNavigator();
    return;
  }
  const params = new URLSearchParams({
    start_date: state.startDate,
    end_date: state.endDate,
  });
  const payload = await fetchJson(`/api/equipment-tree?${params}`);
  state.equipmentTree = payload.asset_trees || [];
  const changed = normalizeScopeAgainstTree();
  expandSelectedScope();
  if (changed) writeRouteState(state, true);
  renderNavigator();
}

function normalizeState() {
  const dates = availableDates();
  if (!state.startDate || !dates.includes(state.startDate)) state.startDate = dates[0] || "";
  if (!state.endDate || !dates.includes(state.endDate)) state.endDate = dates.at(-1) || state.startDate;
  normalizeDateRange("end");
  const rangeDates = datesInRange();
  if (!state.date || !rangeDates.includes(state.date)) state.date = rangeDates.at(-1) || "";
  if (!metricRows().some((row) => row.key === state.metric)) state.metric = DEFAULT_METRIC;
  normalizeMetricDimension();
  if (!VALID_SCOPE_TYPES.has(state.scopeType)) setScopeState("all", "");
  writeRouteState(state, true);
}

function normalizeDateRange(changedEdge) {
  const dates = availableDates();
  const startIndex = dates.indexOf(state.startDate);
  const endIndex = dates.indexOf(state.endDate);
  if (startIndex < 0 || endIndex < 0 || startIndex <= endIndex) return;
  if (changedEdge === "start") state.endDate = state.startDate;
  else state.startDate = state.endDate;
}

function normalizeMetricDimension() {
  if (!selectedMetric().axis) state.dimension = "x";
  if (!AXIS_DIMENSIONS.includes(state.dimension)) state.dimension = "x";
}

function updateControlsFromState() {
  setOptions(elements.startDateSelect, availableDates(), (value) => value, state.startDate);
  setOptions(elements.endDateSelect, availableDates(), (value) => value, state.endDate);
  setOptions(elements.dateSelect, datesInRange(), (value) => value, state.date);
  setOptions(
    elements.metricSelect,
    metricRows().map((metric) => ({ value: metric.key, label: metric.label })),
    (row) => row.label,
    state.metric,
  );
  setOptions(elements.dimensionSelect, AXIS_DIMENSIONS, (value) => value.toUpperCase(), state.dimension);
  elements.equipmentSearch.value = state.equipmentSearch;
  elements.tabs.forEach((tab) => {
    const active = tab.dataset.view === state.view;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
    tab.tabIndex = active ? 0 : -1;
  });
  elements.dateControl.hidden = !["review", "cluster"].includes(state.view);
  elements.metricControl.hidden = !["review", "trends", "cluster"].includes(state.view);
  elements.dimensionControl.hidden = !(
    ["review", "trends", "cluster"].includes(state.view) && selectedMetric().axis
  );
}

function renderSynchronization() {
  const sync = state.context?.synchronization || {};
  const revision = sync.data_revision?.snapshot_revision;
  const revisionLabel = revision ? ` · rev ${shortRevision(revision)}` : "";
  const timestamp = sync.last_synchronized_at ? formatTimestamp(sync.last_synchronized_at) : "never";
  const status = String(sync.status || "unknown").replace(/_/g, " ");
  elements.syncStatus.textContent = `${status} · synced ${timestamp}${revisionLabel}`;
  elements.syncStatus.dataset.status = sync.status || "unknown";
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
    group.append(createTreeRow({
      level: "asset",
      active: state.scopeType === "asset_tree" && state.scopeId === assetTree.asset_tree_id,
      expanded: assetExpanded,
      hasChildren: Boolean(assetTree.equipment?.length),
      label: assetTree.asset_tree_name || `Asset Tree ${assetTree.asset_tree_id}`,
      title: [assetTree.asset_tree_name, assetTree.asset_tree_id, assetTree.asset_tree_path].filter(Boolean).join(" | "),
      detail: `${assetTree.equipment_count || 0} equipment | ${assetTree.sensor_count || 0} sensors`,
      onToggle: () => toggleSetAndRender(state.expandedAssetTrees, assetTree.asset_tree_id),
      onSelect: () => setScope("asset_tree", assetTree.asset_tree_id),
    }));
    if (assetExpanded) {
      (assetTree.equipment || []).forEach((equipment) => {
        const equipmentExpanded = isEquipmentExpanded(equipment);
        group.append(createTreeRow({
          level: "equipment",
          active: state.scopeType === "equipment" && state.scopeId === equipment.equipment_id,
          expanded: equipmentExpanded,
          hasChildren: Boolean(equipment.sensors?.length),
          label: compactEquipmentLabel(equipment.equipment_name) || `Equipment ${equipment.equipment_id}`,
          title: [equipment.equipment_name, equipment.equipment_id, equipment.customer_asset_id, dateRangeLabel(equipment)].filter(Boolean).join(" | "),
          detail: [equipment.customer_asset_id, `${equipment.sensor_count || 0} sensors`].filter(Boolean).join(" | "),
          onToggle: () => toggleSetAndRender(state.expandedEquipment, equipment.equipment_id),
          onSelect: () => setScope("equipment", equipment.equipment_id),
        }));
        if (equipmentExpanded) {
          (equipment.sensors || []).forEach((sensor) => group.append(createTreeRow({
            level: "sensor",
            active: state.scopeType === "sensor" && state.scopeId === sensor.installation_point_id,
            expanded: false,
            hasChildren: false,
            label: sensor.installation_point_name || `Sensor ${sensor.installation_point_id}`,
            title: [sensor.installation_point_name, sensor.installation_point_id, sensor.sensor_id, sensor.customer_asset_id, dateRangeLabel(sensor)].filter(Boolean).join(" | "),
            onSelect: () => setScope("sensor", sensor.installation_point_id),
          })));
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
  if (options.hasChildren) row.setAttribute("aria-expanded", options.expanded ? "true" : "false");
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "tree-toggle";
  if (options.hasChildren) {
    toggle.textContent = options.expanded ? "−" : "+";
    toggle.setAttribute("aria-label", `${options.expanded ? "Collapse" : "Expand"} ${options.label}`);
    toggle.addEventListener("click", options.onToggle);
  } else {
    toggle.classList.add("is-placeholder");
    toggle.tabIndex = -1;
    toggle.setAttribute("aria-hidden", "true");
  }
  const select = document.createElement("button");
  select.type = "button";
  select.className = "tree-select";
  select.title = options.title || options.label;
  select.setAttribute("aria-label", `Select ${options.label}`);
  select.addEventListener("click", options.onSelect);
  const label = document.createElement("span");
  label.className = "tree-label";
  const strong = document.createElement("strong");
  strong.textContent = options.label || "Unnamed";
  label.append(strong);
  if (options.detail) {
    const detail = document.createElement("small");
    detail.textContent = options.detail;
    label.append(detail);
  }
  select.append(label);
  row.append(toggle, select);
  return row;
}

async function renderActiveView() {
  if (!state.context) return;
  updateControlsFromState();
  clearView();
  if (!availableDates().length) {
    renderMissingState(new Error("No synchronized snapshot dates are available."), "Not synchronized");
    return;
  }
  try {
    if (state.view === "review") await renderSnapshotReview();
    else if (state.view === "trends") await renderTrend();
    else if (state.view === "cluster") await renderCluster();
    else await renderDrift();
  } catch (error) {
    renderMissingState(error);
  }
}

async function renderSnapshotReview() {
  const params = scopeSearchParams(state);
  params.set("start_date", state.startDate);
  params.set("end_date", state.endDate);
  params.set("metric", state.metric);
  params.set("dimension", state.dimension);
  const payload = await fetchJson(`/api/snapshot-review/${state.date}?${params}`);
  setStatus(`Review ${payload.date} · ${payload.scope.label}`);
  renderSnapshotContext(payload);
  renderSnapshotTrendPanel(payload.trend || {});
  await renderSnapshotClusterPanel(payload.cluster_context || {});
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
  heading.textContent = context.equipment_name || context.label || payload.scope?.label || "Review";
  const subheading = document.createElement("p");
  subheading.textContent = [
    context.sensor_name || `${context.sensor_count || 0} sensors`,
    payload.date ? `Snapshot ${payload.date}` : "",
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
  const metric = selectedMetric();
  const field = trend.value_field || metricField(metric, "mean");
  plotInto(elements.snapshotTrendChart, snapshotTrendTraces(trend, field), {
    title: metric.label,
    xaxis: { title: "Date", range: [state.startDate, state.endDate] },
    yaxis: { title: metric.unit },
    onPointActivate: selectSnapshotDate,
  });
}

async function renderSnapshotClusterPanel(clusterContext) {
  const modelLabel = clusterContext.feature_space ? featureSpaceLabel(clusterContext.feature_space) : "";
  const readiness = readinessForDate(state.date);
  const missingMessage = readiness && !readiness.registered_model_ready
    ? "Model pending for this date"
    : clusterContext.message || "No cluster data for this date";
  const chartLayout = {
    title: "Sensor positions in the PCA cluster cloud",
    xaxis: { title: "PC1" },
    yaxis: { title: "PC2" },
    emptyText: "PCA projection unavailable",
  };
  if (clusterContext.status !== "available") {
    elements.snapshotClusterStatus.textContent = missingMessage;
    plotInto(elements.snapshotClusterChart, [], chartLayout);
    return;
  }

  elements.snapshotClusterStatus.textContent = "Loading PCA projection...";
  try {
    const allProjectionPromise = fetchJson(clusterExplorerUrl("all"));
    const selectedProjectionPromise = state.scopeType === "all"
      ? allProjectionPromise
      : fetchJson(clusterExplorerUrl(state.scopeType, state.scopeId));
    const [allProjection, selectedProjection] = await Promise.all([
      allProjectionPromise,
      selectedProjectionPromise,
    ]);
    const allRows = allProjection.pca_rows || [];
    const selectedRows = selectedProjection.pca_rows || [];
    const selectedCount = selectedProjection.row_count ?? clusterContext.row_count ?? selectedRows.length;
    const allCount = allProjection.all_row_count ?? allRows.length;
    const variance = allProjection.metrics?.pca?.explained_variance_ratio || [];
    chartLayout.xaxis.title = pcaAxisTitle("PC1", variance[0]);
    chartLayout.yaxis.title = pcaAxisTitle("PC2", variance[1]);
    elements.snapshotClusterStatus.textContent = [
      `${selectedCount} selected of ${allCount} sensors`,
      modelLabel,
      `k=${clusterContext.k}`,
    ].filter(Boolean).join(" | ");
    plotInto(
      elements.snapshotClusterChart,
      snapshotClusterTraces(allRows, selectedRows),
      chartLayout,
    );
  } catch (error) {
    elements.snapshotClusterStatus.textContent = `${clusterContext.row_count || 0} scoped sensors | PCA projection unavailable`;
    plotInto(elements.snapshotClusterChart, [], {
      ...chartLayout,
      emptyText: error?.message || "PCA projection unavailable",
    });
  }
}

function renderSnapshotEventsPanel(events) {
  const maximo = events.providers?.maximo || {};
  let suffix = "";
  if (maximo.status === "not_requested") suffix = " | Maximo: select Asset Tree";
  else if (maximo.status === "unavailable") suffix = " | Maximo unavailable";
  else if (["available", "partial"].includes(maximo.status)) {
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

async function renderTrend() {
  renderMetricCoverage(null);
  const params = scopeSearchParams(state);
  params.set("start_date", state.startDate);
  params.set("end_date", state.endDate);
  params.set("metric", state.metric);
  params.set("dimension", state.dimension);
  params.set("stat", "mean");
  const payload = await fetchJson(`/api/trends?${params}`);
  const metric = selectedMetric();
  const meanField = metricField(metric, "mean");
  const sensorRows = payload.sensor_rows || [];
  setStatus(`Fleet Trends ${payload.start_date} to ${payload.end_date}`);
  renderSummary([
    { label: "Scoped Rows", value: payload.sensor_row_count || 0 },
    { label: "Detail Rows", value: sensorRows.length },
    { label: "Series", value: payload.series_count || 0 },
    { label: "Metric", value: metric.label },
    { label: "Scope", value: scopeLabel() },
  ]);
  plotChart(fleetTrendTraces(payload.series || [], meanField), {
    title: `${metric.label} Trend · average with min–max range`,
    xaxis: { title: "Date", range: [state.startDate, state.endDate] },
    yaxis: { title: metric.unit },
  });
  renderTable(sensorRows, [
    "date", "installation_point_id", "equipment_id", "equipment_name",
    "customer_asset_id", meanField, metricField(metric, "max"), metricField(metric, "min"),
  ]);
}

async function renderCluster() {
  const params = scopeSearchParams(state);
  params.set("date", state.date);
  params.set("metric", state.metric);
  params.set("dimension", state.dimension);
  const payload = await fetchJson(`/api/cluster-explorer?${params}`);
  const metrics = payload.metrics || {};
  const metricValues = metrics.metrics || {};
  const modelLabel = featureSpaceLabel(payload.feature_space);
  setStatus(`Cluster ${payload.date} · ${modelLabel}`);
  renderSummary([
    { label: "Scoped Sensors", value: payload.row_count },
    { label: "All Sensors", value: payload.all_row_count },
    { label: "Feature Space", value: modelLabel },
    { label: "Inertia", value: formatNumber(metrics.kmeans?.inertia) },
    { label: "Scope", value: payload.scope?.label || scopeLabel() },
  ]);
  const grouped = groupBy(payload.pca_rows || [], "cluster");
  const traces = Object.entries(grouped).map(([cluster, rows]) => ({
    type: "scatter",
    mode: "markers",
    name: `Cluster ${cluster}`,
    x: rows.map((row) => numeric(row.pc1)),
    y: rows.map((row) => numeric(row.pc2)),
    text: rows.map((row) => `${row.installation_point_id} | ${row.equipment_name || row.equipment_id}`),
    marker: { size: 9 },
  }));
  plotChart(traces, { title: "Cluster PCA", xaxis: { title: "PC1" }, yaxis: { title: "PC2" } });
  const featureColumns = (metrics.features || []).slice(0, 4);
  renderTable(payload.rows || [], [
    "installation_point_id", "equipment_id", "equipment_name", "cluster",
    "distance_to_centroid", ...featureColumns,
  ]);
  if (metricValues.silhouette_score?.value !== undefined) {
    setStatus(`Cluster ${payload.date} · ${modelLabel} · silhouette ${formatNumber(metricValues.silhouette_score.value)}`);
  }
}

async function renderDrift() {
  const params = scopeSearchParams(state);
  params.set("start_date", state.startDate);
  params.set("end_date", state.endDate);
  const payload = await fetchJson(`/api/drift-overview?${params}`);
  const summary = payload.summary || {};
  setStatus(`Drift ${payload.start_date} to ${payload.end_date} · ${payload.status}`);
  renderSummary([
    { label: "Feature Spaces", value: summary.feature_space_count || 0 },
    { label: "Complete Pairs", value: summary.complete_pair_count || 0 },
    { label: "Model Gaps", value: summary.missing_pair_count || 0 },
    { label: "Warnings", value: summary.warning_count || 0 },
    { label: "Scope", value: payload.scope?.label || scopeLabel() },
  ]);
  const grouped = groupBy(payload.pairs || [], "feature_space");
  const colors = ["#287271", "#5d7f9f", "#a64253", "#8a6f3d"];
  const traces = Object.entries(grouped).map(([featureSpace, rows], index) => {
    const ordered = rows.slice().sort((left, right) => String(left.to_date).localeCompare(String(right.to_date)));
    const color = colors[index % colors.length];
    return {
      type: "scatter",
      mode: "lines+markers",
      name: featureSpaceLabel(featureSpace),
      x: ordered.map((row) => row.to_date),
      y: ordered.map((row) => {
        const ratio = numeric(row.aligned_changed_ratio);
        return ratio === null ? null : ratio * 100;
      }),
      text: ordered.map((row) => (
        `${row.from_date} → ${row.to_date} | `
        + `${row.aligned_changed_count || 0} of ${row.matched_sensor_count || 0} sensors changed | `
        + (row.interpretation || "")
      )),
      line: { color },
      marker: {
        size: ordered.map((row) => row.warning_count ? 10 : 8),
        line: {
          width: ordered.map((row) => row.warning_count ? 2 : 0),
          color: "#8a6f3d",
        },
      },
      timeSeries: true,
    };
  });
  plotChart(traces, {
    title: "Aligned cluster movement over time",
    xaxis: { title: "To date" },
    yaxis: { title: "Matched sensors changing cluster (%)", range: [0, 100] },
  });
  const gapRows = (payload.gaps || []).map((row) => ({
    ...row,
    interpretation: row.reason,
    matched_sensor_count: "gap",
  }));
  renderTable([...(payload.pairs || []), ...gapRows], [
    "feature_space_label", "from_date", "to_date", "status", "matched_sensor_count",
    "raw_label_changed_count", "aligned_changed_count", "aligned_changed_ratio",
    "warning_count", "interpretation",
  ]);
}

function renderMissingState(error, forcedState = "") {
  showReviewSurface(false);
  const message = error?.message || "Unable to load this view";
  let stateLabel = forcedState || "Unavailable";
  if (error?.status === 404 && (state.view === "cluster" || /model|cluster/i.test(message))) {
    stateLabel = "Model pending";
  } else if (error?.status === 503) {
    stateLabel = "Provider unavailable";
  }
  setStatus(`${stateLabel}: ${message}`);
  renderSummary([
    { label: "State", value: stateLabel },
    { label: "Range", value: `${state.startDate} to ${state.endDate}` },
    { label: "Scope", value: scopeLabel() },
  ]);
  const panel = document.createElement("div");
  panel.className = "missing-state";
  const heading = document.createElement("strong");
  heading.textContent = stateLabel;
  const detail = document.createElement("p");
  detail.textContent = message;
  const retry = document.createElement("button");
  retry.type = "button";
  retry.textContent = "Retry";
  retry.addEventListener("click", renderActiveView);
  panel.append(heading, detail, retry);
  elements.plot.replaceChildren(panel);
}

function setScope(scopeType, scopeId) {
  setScopeState(scopeType, scopeId);
  state.scopeNotice = "";
  normalizeScopeAgainstTree();
  expandSelectedScope();
  writeRouteState(state);
  updateControlsFromState();
  renderNavigator();
  resetSnapshotPane();
  renderActiveView();
}

function setScopeState(scopeType, scopeId) {
  state.scopeType = scopeType || "all";
  state.scopeId = state.scopeType === "all" ? "" : String(scopeId || "");
}

function normalizeScopeAgainstTree() {
  if (state.scopeType === "all") {
    state.scopeId = "";
    return false;
  }
  if (resolveScope()) return false;
  setScopeState("all", "");
  state.scopeNotice = "Selected scope is no longer in context; showing all equipment.";
  return true;
}

function resolveScope() {
  if (state.scopeType === "asset_tree") return findAssetTree(state.scopeId);
  if (state.scopeType === "equipment") return findEquipment(state.scopeId);
  if (state.scopeType === "sensor") return findSensor(state.scopeId);
  return null;
}

function findAssetTree(id) {
  return state.equipmentTree.find((tree) => tree.asset_tree_id === id) || null;
}

function findEquipment(id) {
  for (const assetTree of state.equipmentTree) {
    const equipment = (assetTree.equipment || []).find((row) => row.equipment_id === id);
    if (equipment) return { assetTree, equipment };
  }
  return null;
}

function findSensor(id) {
  for (const assetTree of state.equipmentTree) {
    for (const equipment of assetTree.equipment || []) {
      const sensor = (equipment.sensors || []).find((row) => (
        row.installation_point_id === id || row.sensor_id === id
      ));
      if (sensor) return { assetTree, equipment, sensor };
    }
  }
  return null;
}

function expandSelectedScope() {
  const resolved = resolveScope();
  if (state.scopeType === "asset_tree" && resolved) state.expandedAssetTrees.add(resolved.asset_tree_id);
  if (state.scopeType === "equipment" && resolved) {
    state.expandedAssetTrees.add(resolved.assetTree.asset_tree_id);
    state.expandedEquipment.add(resolved.equipment.equipment_id);
  }
  if (state.scopeType === "sensor" && resolved) {
    state.expandedAssetTrees.add(resolved.assetTree.asset_tree_id);
    state.expandedEquipment.add(resolved.equipment.equipment_id);
  }
}

function scopeLabel() {
  const resolved = resolveScope();
  if (state.scopeType === "asset_tree") return resolved?.asset_tree_name || `Asset Tree ${state.scopeId}`;
  if (state.scopeType === "equipment") return resolved?.equipment.equipment_name || `Equipment ${state.scopeId}`;
  if (state.scopeType === "sensor") return resolved?.sensor.installation_point_name || `Sensor ${state.scopeId}`;
  return "All equipment";
}

function filteredEquipmentTree() {
  const needle = state.equipmentSearch.trim().toLowerCase();
  if (!needle) return state.equipmentTree;
  return state.equipmentTree.map((assetTree) => {
    const assetMatches = textMatches(needle, [assetTree.asset_tree_id, assetTree.asset_tree_name, assetTree.asset_tree_path]);
    const equipment = (assetTree.equipment || []).map((row) => {
      const equipmentMatches = textMatches(needle, [row.equipment_id, row.equipment_name, row.customer_asset_id]);
      const sensors = (row.sensors || []).filter((sensor) => textMatches(needle, [
        sensor.installation_point_id, sensor.installation_point_name, sensor.sensor_id, sensor.customer_asset_id,
      ]));
      if (assetMatches || equipmentMatches) return row;
      return sensors.length ? { ...row, sensors, sensor_count: sensors.length } : null;
    }).filter(Boolean);
    if (!assetMatches && !equipment.length) return null;
    return {
      ...assetTree,
      equipment,
      equipment_count: equipment.length,
      sensor_count: equipment.reduce((sum, row) => sum + (row.sensors?.length || 0), 0),
    };
  }).filter(Boolean);
}

function textMatches(needle, values) {
  return values.filter((value) => value !== null && value !== undefined).join(" ").toLowerCase().includes(needle);
}

function toggleSetAndRender(set, value) {
  if (set.has(value)) set.delete(value);
  else set.add(value);
  renderNavigator();
}

function isAssetExpanded(assetTree) {
  return Boolean(state.equipmentSearch || state.expandedAssetTrees.has(assetTree.asset_tree_id));
}

function isEquipmentExpanded(equipment) {
  return Boolean(state.equipmentSearch || state.expandedEquipment.has(equipment.equipment_id));
}

function availableDates() {
  return unique((state.context?.dates || []).filter((row) => row.snapshot_ready).map((row) => row.date));
}

function datesInRange() {
  return availableDates().filter((date) => date >= state.startDate && date <= state.endDate);
}

function readinessForDate(date) {
  return (state.context?.dates || []).find((row) => row.date === date) || null;
}

function metricRows() {
  return state.context?.metrics || [];
}

function selectedMetric() {
  const metric = metricRows().find((row) => row.key === state.metric);
  return metric ? { ...metric, prefix: metric.key } : {
    key: DEFAULT_METRIC,
    label: "RMS Velocity",
    prefix: DEFAULT_METRIC,
    axis: true,
    unit: "in/s",
  };
}

function metricField(metric, stat) {
  return metric.axis ? `${metric.prefix}_${stat}_${state.dimension}` : `${metric.prefix}_${stat}`;
}

function selectSnapshotDate(selectedDate) {
  if (state.view !== "review" || !datesInRange().includes(selectedDate) || state.date === selectedDate) return;
  state.date = selectedDate;
  writeRouteState(state);
  updateControlsFromState();
  renderActiveView();
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
      td.textContent = formatCell(row[column], options.emptyText ?? "");
      tr.append(td);
    });
    body.append(tr);
  });
}

function clearView() {
  setStatus("Loading...");
  showReviewSurface(state.view === "review");
  renderSummary([]);
  elements.tableHead.replaceChildren();
  elements.tableBody.replaceChildren();
  if (window.SensorCharts) {
    [elements.plot, elements.snapshotTrendChart, elements.snapshotClusterChart]
      .forEach((element) => window.SensorCharts.clear(element));
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

function showReviewSurface(showReview) {
  elements.reviewMain.classList.toggle("is-snapshot", showReview);
  elements.viewPinned.classList.toggle("is-snapshot", showReview);
  elements.snapshotContext.hidden = !showReview;
  elements.statusLine.hidden = showReview;
  elements.snapshotReview.hidden = !showReview;
  elements.summaryGrid.hidden = showReview;
  elements.workspace.hidden = showReview;
  elements.tableShell.hidden = showReview;
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
  if (!window.SensorCharts) return;
  [elements.plot, elements.snapshotTrendChart, elements.snapshotClusterChart]
    .forEach((element) => window.SensorCharts.redraw(element));
}

function redrawSnapshotCharts() {
  if (!window.SensorCharts) return;
  [elements.snapshotTrendChart, elements.snapshotClusterChart]
    .forEach((element) => window.SensorCharts.redraw(element));
}

function resetSnapshotPane() {
  if (elements.snapshotScroll) elements.snapshotScroll.scrollTop = 0;
  [elements.snapshotEventsDetail, elements.snapshotMeasurementsDetail].forEach((detail) => {
    if (detail) detail.open = false;
  });
}

function snapshotTrendTraces(trend, field) {
  if (trend.status !== "available") return [];
  return trendSeriesTraces(trend.series || [], field, state.date);
}

function clusterExplorerUrl(scopeType, scopeId = "") {
  const params = new URLSearchParams();
  params.set("date", state.date);
  params.set("metric", state.metric);
  params.set("dimension", state.dimension);
  params.set("scope_type", scopeType);
  if (scopeType !== "all" && scopeId) params.set("scope_id", scopeId);
  return `/api/cluster-explorer?${params}`;
}

function snapshotClusterTraces(allRows, selectedRows) {
  const validRows = (rows) => rows.filter((row) => (
    numeric(row.pc1) !== null && numeric(row.pc2) !== null
  ));
  const all = validRows(allRows);
  const selected = validRows(selectedRows);
  const traces = [];
  if (all.length) traces.push(clusterPcaTrace("All sensors", all, "#c1c8d1", false));
  if (selected.length) traces.push(clusterPcaTrace("Selected view", selected, "#287271", true));
  return traces;
}

function clusterPcaTrace(name, rows, color, selected) {
  return {
    type: "scatter",
    mode: "markers",
    name,
    x: rows.map((row) => numeric(row.pc1)),
    y: rows.map((row) => numeric(row.pc2)),
    text: rows.map(clusterPcaPointLabel),
    marker: {
      color,
      size: selected ? 11 : 7,
      line: {
        color: selected ? "#153c3a" : "#aeb7c2",
        width: selected ? 2 : 1,
      },
    },
  };
}

function clusterPcaPointLabel(row) {
  const sensor = row.installation_point_name || row.installation_point_id || row.sensor_id || "Unknown sensor";
  const equipment = row.equipment_name || row.equipment_id;
  const cluster = row.cluster === null || row.cluster === undefined ? "" : `Cluster ${row.cluster}`;
  return [sensor, equipment, cluster].filter(Boolean).join(" | ");
}

function pcaAxisTitle(axis, explainedVariance) {
  const ratio = numeric(explainedVariance);
  return ratio === null ? axis : `${axis} (${(ratio * 100).toFixed(1)}% variance)`;
}

function trendSeriesTraces(series, field, selectedDate = "") {
  const palette = ["#287271", "#5d7f9f", "#a64253", "#8a6f3d", "#59656f", "#3d8068"];
  return series.slice(0, 12).map((item, index) => {
    const rows = (item.rows || []).slice().sort((left, right) => String(left.date).localeCompare(String(right.date)));
    return lineTrace(rows, field, item.label || item.id || `Series ${index + 1}`, palette[index % palette.length], {
      selectedDate,
      timeSeries: true,
    });
  }).filter(Boolean);
}

function fleetTrendTraces(series, field) {
  const palette = ["#287271", "#5d7f9f", "#a64253", "#8a6f3d", "#59656f", "#3d8068"];
  return series.slice(0, 12).map((item, index) => {
    const rows = (item.rows || []).slice().sort((left, right) => String(left.date).localeCompare(String(right.date)));
    return lineTrace(rows, field, item.label || item.id || `Series ${index + 1}`, palette[index % palette.length], {
      bandLowerField: "range_min",
      bandUpperField: "range_max",
      timeSeries: true,
    });
  }).filter(Boolean);
}

function lineTrace(rows, field, name, color, options = {}) {
  if (!rows.some((row) => numeric(row[field]) !== null)) return null;
  const trace = {
    type: "scatter",
    mode: "lines+markers",
    name,
    x: rows.map((row) => row.date),
    y: rows.map((row) => numeric(row[field])),
    line: { color },
    marker: {
      size: rows.map((row) => row.date === options.selectedDate ? 12 : 8),
      line: { width: rows.map((row) => row.date === options.selectedDate ? 2 : 0), color: "#18202a" },
    },
    timeSeries: options.timeSeries === true,
  };
  if (options.bandLowerField && options.bandUpperField) {
    trace.band = {
      lower: rows.map((row) => numeric(row[options.bandLowerField])),
      upper: rows.map((row) => numeric(row[options.bandUpperField])),
    };
  }
  return trace;
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

function setOptions(select, values, labeler, selected) {
  const rows = values.map((value) => typeof value === "object" ? value : { value: String(value), label: labeler(value) });
  select.replaceChildren();
  rows.forEach((row) => {
    const option = document.createElement("option");
    option.value = String(row.value);
    option.textContent = row.label ?? labeler(row.value);
    select.append(option);
  });
  if (rows.some((row) => String(row.value) === String(selected))) select.value = String(selected);
  else if (rows.length) select.value = String(rows[0].value);
}

function dateRangeLabel(row) {
  if (row.first_date && row.last_date && row.first_date !== row.last_date) return `${row.first_date} to ${row.last_date}`;
  return row.first_date || row.last_date || "";
}

function compactEquipmentLabel(label = "") {
  const parts = String(label).split(" - ");
  return parts.length > 1 ? parts.slice(1).join(" - ").trim() : label;
}

function featureSpaceLabel(value) {
  return FEATURE_SPACE_LABELS[value] || value || "n/a";
}

function formatCoveragePercent(value) {
  const percent = numeric(value);
  if (percent === null) return "0";
  return Number.isInteger(percent) ? String(percent) : percent.toFixed(1);
}

function formatTimestamp(value) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? String(value) : parsed.toLocaleString();
}

function shortRevision(value) {
  const text = String(value);
  return text.length > 18 ? `${text.slice(0, 18)}…` : text;
}

function columnLabel(column) {
  return column.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatCell(value, emptyText = "") {
  if (value === null || value === undefined || value === "") return emptyText;
  const parsed = numeric(value);
  if (parsed !== null && String(value).length > 8) return parsed.toFixed(4);
  return value;
}

function numeric(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatNumber(value) {
  const parsed = numeric(value);
  return parsed === null ? "n/a" : parsed.toFixed(3);
}

function groupBy(rows, field) {
  return rows.reduce((groups, row) => {
    const key = row[field] || "";
    groups[key] = groups[key] || [];
    groups[key].push(row);
    return groups;
  }, {});
}

function unique(values) {
  return Array.from(new Set(values.filter(Boolean))).sort();
}

function emptyBlock(text) {
  const node = document.createElement("p");
  node.className = "empty-block";
  node.textContent = text;
  return node;
}

function setStatus(message) {
  elements.statusLine.textContent = message;
}

function debounce(callback, delay) {
  let timeout;
  return (...args) => {
    clearTimeout(timeout);
    timeout = setTimeout(() => callback(...args), delay);
  };
}

init();
