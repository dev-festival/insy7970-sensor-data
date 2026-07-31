export async function fetchJson(path) {
  const response = await fetch(path);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : {};
  if (!response.ok) {
    const error = new Error(payload.detail || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return payload;
}
