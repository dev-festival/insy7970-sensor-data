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

async function loadArtifactStatus() {
  const rawDates = document.querySelector("#raw-dates");
  const snapshotStatus = document.querySelector("#snapshot-status");
  const trendStatus = document.querySelector("#trend-status");

  try {
    const response = await fetch("/api/dates");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const dates = await response.json();
    rawDates.textContent = dates.raw_waites.length ? dates.raw_waites.join(", ") : "none";

    if (dates.snapshots.length) {
      const latestSnapshot = dates.snapshots[dates.snapshots.length - 1];
      const snapshotResponse = await fetch(`/api/snapshots/${latestSnapshot}`);
      const snapshot = await snapshotResponse.json();
      snapshotStatus.textContent = `${latestSnapshot} | ${snapshot.rows.length} sensors`;
    } else {
      snapshotStatus.textContent = "none";
    }

    if (dates.trends.length) {
      const latestTrend = dates.trends[dates.trends.length - 1];
      const params = new URLSearchParams(latestTrend);
      const trendResponse = await fetch(`/api/trends?${params}`);
      const trend = await trendResponse.json();
      trendStatus.textContent = `${latestTrend.start_date} to ${latestTrend.end_date} | ${trend.sensor_rows.length} sensor rows`;
    } else {
      trendStatus.textContent = "none";
    }
  } catch (error) {
    rawDates.textContent = "unavailable";
    snapshotStatus.textContent = "unavailable";
    trendStatus.textContent = "unavailable";
  }
}

loadHealth();
loadArtifactStatus();
