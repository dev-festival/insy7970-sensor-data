const VALID_VIEWS = new Set(["review", "trends", "cluster", "drift"]);
const VALID_SCOPES = new Set(["all", "asset_tree", "equipment", "sensor"]);
const LEGACY_VIEWS = { snapshot: "review", trend: "trends" };
const FEATURE_DIMENSIONS = {
  x_accel: "x",
  y_vel: "y",
  z_vel: "z",
  temperature: "temperature",
};

export function createAppState() {
  return {
    context: null,
    equipmentTree: [],
    health: null,
    startDate: "",
    endDate: "",
    date: "",
    view: "review",
    scopeType: "all",
    scopeId: "",
    dimension: "x",
    metric: "rms_vel",
    equipmentSearch: "",
    expandedAssetTrees: new Set(),
    expandedEquipment: new Set(),
    scopeNotice: "",
  };
}

export function readRouteState(state, search = window.location.search) {
  const params = new URLSearchParams(search);
  const legacyView = LEGACY_VIEWS[params.get("view")];
  const view = legacyView || params.get("view") || state.view;
  const requestedScope = params.get("scope_type") || params.get("scope") || "";
  const scopeType = VALID_SCOPES.has(requestedScope)
    ? requestedScope
    : params.get("installation_point_id")
      ? "sensor"
      : params.get("equipment_id")
        ? "equipment"
        : "all";
  const legacyScopeId = {
    asset_tree: params.get("asset_tree_id"),
    equipment: params.get("equipment_id"),
    sensor: params.get("installation_point_id") || params.get("sensor_id"),
  }[scopeType];
  const legacyFeature = params.get("feature_space");
  const legacyDimension = FEATURE_DIMENSIONS[legacyFeature] || "";
  Object.assign(state, {
    startDate: params.get("start_date") || state.startDate,
    endDate: params.get("end_date") || state.endDate,
    date: params.get("date") || state.date,
    view: VALID_VIEWS.has(view) ? view : "review",
    scopeType,
    scopeId: params.get("scope_id") || legacyScopeId || "",
    dimension: params.get("dimension") || (
      legacyDimension === "temperature" ? "x" : legacyDimension
    ) || state.dimension,
    metric: params.get("metric") || (
      legacyDimension === "temperature" ? "temp_sensor" : state.metric
    ),
  });
  return state;
}

export function routeSearch(state) {
  const params = new URLSearchParams();
  params.set("view", state.view);
  params.set("start_date", state.startDate);
  params.set("end_date", state.endDate);
  params.set("scope_type", state.scopeType);
  if (state.scopeType !== "all" && state.scopeId) {
    params.set("scope_id", state.scopeId);
  }
  if (["review", "cluster"].includes(state.view)) {
    params.set("date", state.date);
    params.set("metric", state.metric);
    params.set("dimension", state.dimension);
  }
  return params;
}

export function writeRouteState(state, replace = false) {
  const nextUrl = `${window.location.pathname}?${routeSearch(state)}`;
  window.history[replace ? "replaceState" : "pushState"](null, "", nextUrl);
}

export function scopeSearchParams(state) {
  const params = new URLSearchParams();
  params.set("scope_type", state.scopeType);
  if (state.scopeType !== "all" && state.scopeId) {
    params.set("scope_id", state.scopeId);
  }
  return params;
}
