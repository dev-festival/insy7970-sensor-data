async function loadHealth() {
  const status = document.querySelector("#health-status");

  try {
    const response = await fetch("/health");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const health = await response.json();
    status.textContent = `${health.status.toUpperCase()} | ${health.source_mode} | ${health.data_dir}`;
  } catch (error) {
    status.textContent = "Service health unavailable";
  }
}

loadHealth();
