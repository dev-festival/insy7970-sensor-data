(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const COLORS = ["#287271", "#5d7f9f", "#a64253", "#8a6f3d", "#59656f", "#3d8068", "#b7791f", "#4263eb"];

  function render(container, traces, layout) {
    if (!container) {
      return;
    }
    container.__sensorChart = {
      traces: Array.isArray(traces) ? traces : [],
      layout: layout || {},
    };
    draw(container);
  }

  function redraw(container) {
    if (container?.__sensorChart) {
      draw(container);
    }
  }

  function clear(container) {
    if (!container) {
      return;
    }
    delete container.__sensorChart;
    container.removeAttribute("data-chart-ready");
    container.replaceChildren();
  }

  function draw(container) {
    const chartState = container.__sensorChart || { traces: [], layout: {} };
    const layout = chartState.layout || {};
    const traces = prepareTraces(chartState.traces);
    container.replaceChildren();

    if (!traces.length) {
      container.append(emptyState(layout.emptyText || "No chartable rows"));
      container.removeAttribute("data-chart-ready");
      return;
    }

    const size = measuredSize(container);
    const margin = marginsFor(size.width);
    const bounds = {
      left: margin.left,
      top: margin.top,
      right: size.width - margin.right,
      bottom: size.height - margin.bottom,
    };
    bounds.width = Math.max(bounds.right - bounds.left, 10);
    bounds.height = Math.max(bounds.bottom - bounds.top, 10);

    const hasBars = traces.some((trace) => trace.type === "bar");
    const numericX = !hasBars && traces.every((trace) => trace.points.every((point) => point.xNumber !== null));
    const temporalX = numericX && traces.every(
      (trace) => trace.points.every((point) => point.xIsDate),
    );
    const categories = unique(traces.flatMap((trace) => trace.points.map((point) => point.xLabel)));
    const xDomain = numericX
      ? configuredDomain(layout.xaxis?.range) || paddedDomain(
        traces.flatMap((trace) => trace.points.map((point) => point.xNumber)),
        false,
        temporalX,
      )
      : null;
    const yDomain = configuredDomain(layout.yaxis?.range) || paddedDomain(
      traces.flatMap((trace) => trace.points.map((point) => point.y)).concat(hasBars ? [0] : []),
      hasBars,
    );
    const scales = {
      x: (point) => numericX
        ? linear(point.xNumber, xDomain[0], xDomain[1], bounds.left, bounds.right)
        : categorical(point.xLabel, categories, bounds.left, bounds.right),
      y: (value) => linear(value, yDomain[0], yDomain[1], bounds.bottom, bounds.top),
    };

    const svg = svgElement("svg", {
      class: "chart-svg",
      viewBox: `0 0 ${size.width} ${size.height}`,
      role: "img",
      "aria-label": layout.title || "Chart",
    });

    drawFrame(svg, bounds, layout, yDomain, xDomain, categories, numericX, temporalX, scales);
    drawSeries(svg, traces, bounds, scales, categories, hasBars, layout);
    drawLegend(svg, traces, bounds);
    drawTitle(svg, layout.title, bounds);

    container.append(svg);
    container.setAttribute("data-chart-ready", "true");
  }

  function prepareTraces(rawTraces) {
    return (rawTraces || [])
      .map((trace, traceIndex) => {
        const xValues = Array.isArray(trace.x) ? trace.x : [];
        const yValues = Array.isArray(trace.y) ? trace.y : [];
        const length = Math.max(xValues.length, yValues.length);
        const baseColor = trace.line?.color
          || colorAt(trace.marker?.color, 0)
          || COLORS[traceIndex % COLORS.length];
        const points = [];
        for (let index = 0; index < length; index += 1) {
          const y = toNumber(yValues[index]);
          if (y === null) {
            continue;
          }
          const xRaw = xValues[index] ?? index + 1;
          const xLabel = valueLabel(xRaw);
          const xDateNumber = isoDateNumber(xRaw);
          points.push({
            xRaw,
            xLabel,
            xNumber: xDateNumber ?? toNumber(xRaw),
            xIsDate: xDateNumber !== null,
            y,
            label: valueLabel(valueAt(trace.text, index)) || `${xLabel}: ${formatNumber(y)}`,
            color: colorAt(trace.marker?.color, index) || baseColor,
            size: markerSize(trace.marker?.size, index),
            outlineWidth: numberAt(trace.marker?.line?.width, index, 0),
            outlineColor: colorAt(trace.marker?.line?.color, index) || trace.marker?.line?.color || "#18202a",
          });
        }
        if (trace.timeSeries) {
          points.sort((left, right) => (left.xNumber ?? 0) - (right.xNumber ?? 0));
        }
        return {
          type: trace.type || "scatter",
          mode: trace.mode || "markers",
          name: trace.name || "",
          color: baseColor,
          points,
          timeSeries: trace.timeSeries === true,
        };
      })
      .filter((trace) => trace.points.length);
  }

  function drawFrame(svg, bounds, layout, yDomain, xDomain, categories, numericX, temporalX, scales) {
    const yTicks = ticks(yDomain[0], yDomain[1], 5);
    yTicks.forEach((tick) => {
      const y = scales.y(tick);
      svg.append(svgElement("line", {
        class: "chart-grid",
        x1: bounds.left,
        x2: bounds.right,
        y1: y,
        y2: y,
      }));
      const label = svgElement("text", {
        class: "chart-tick chart-tick-y",
        x: bounds.left - 8,
        y: y + 4,
        "text-anchor": "end",
      });
      label.textContent = formatNumber(tick);
      svg.append(label);
    });

    svg.append(svgElement("line", {
      class: "chart-axis",
      x1: bounds.left,
      x2: bounds.right,
      y1: bounds.bottom,
      y2: bounds.bottom,
    }));
    svg.append(svgElement("line", {
      class: "chart-axis",
      x1: bounds.left,
      x2: bounds.left,
      y1: bounds.top,
      y2: bounds.bottom,
    }));

    const xTicks = numericX ? ticks(xDomain[0], xDomain[1], 5) : categoricalTicks(categories, bounds.width);
    xTicks.forEach((tick) => {
      const x = numericX
        ? linear(tick, xDomain[0], xDomain[1], bounds.left, bounds.right)
        : categorical(tick, categories, bounds.left, bounds.right);
      svg.append(svgElement("line", {
        class: "chart-tick-mark",
        x1: x,
        x2: x,
        y1: bounds.bottom,
        y2: bounds.bottom + 4,
      }));
      const label = svgElement("text", {
        class: "chart-tick chart-tick-x",
        x,
        y: bounds.bottom + 18,
        "text-anchor": "middle",
      });
      label.textContent = numericX
        ? temporalX ? formatDateTick(tick) : formatNumber(tick)
        : shorten(axisLabel(tick), 18);
      svg.append(label);
    });

    const xTitle = layout.xaxis?.title;
    if (xTitle) {
      const label = svgElement("text", {
        class: "chart-axis-title",
        x: bounds.left + bounds.width / 2,
        y: bounds.bottom + 40,
        "text-anchor": "middle",
      });
      label.textContent = xTitle;
      svg.append(label);
    }

    const yTitle = layout.yaxis?.title;
    if (yTitle) {
      const label = svgElement("text", {
        class: "chart-axis-title",
        x: -(bounds.top + bounds.height / 2),
        y: 16,
        transform: "rotate(-90)",
        "text-anchor": "middle",
      });
      label.textContent = yTitle;
      svg.append(label);
    }
  }

  function drawSeries(svg, traces, bounds, scales, categories, hasBars, layout) {
    if (hasBars) {
      drawBars(svg, traces.filter((trace) => trace.type === "bar"), bounds, scales, categories, layout);
    }
    traces
      .filter((trace) => trace.type !== "bar")
      .forEach((trace) => drawScatter(svg, trace, scales, layout));
  }

  function drawBars(svg, traces, bounds, scales, categories, layout) {
    const baseline = clamp(scales.y(0), bounds.top, bounds.bottom);
    const groupWidth = Math.max(bounds.width / Math.max(categories.length, 1), 14);
    const barWidth = clamp((groupWidth * 0.62) / Math.max(traces.length, 1), 4, 34);
    traces.forEach((trace, traceIndex) => {
      trace.points.forEach((point) => {
        const center = scales.x(point);
        const offset = (traceIndex - (traces.length - 1) / 2) * barWidth;
        const valueY = scales.y(point.y);
        const rect = svgElement("rect", {
          class: "chart-bar",
          x: center + offset - barWidth / 2,
          y: Math.min(valueY, baseline),
          width: barWidth,
          height: Math.max(Math.abs(baseline - valueY), 1),
          rx: 2,
          fill: point.color,
          tabindex: "0",
        });
        rect.append(titleElement(pointTitle(trace, point, layout)));
        svg.append(rect);
      });
    });
  }

  function drawScatter(svg, trace, scales, layout) {
    const coordinates = trace.points.map((point) => ({
      point,
      x: scales.x(point),
      y: scales.y(point.y),
    }));

    if (trace.mode.includes("lines") && coordinates.length > 1) {
      if (trace.timeSeries) {
        drawTemporalSpline(svg, coordinates, trace);
      } else {
        svg.append(svgElement("polyline", {
          class: "chart-line",
          points: coordinates.map((point) => `${point.x},${point.y}`).join(" "),
          fill: "none",
          stroke: trace.color,
        }));
      }
    }

    if (trace.mode.includes("markers") || !trace.mode.includes("lines")) {
      coordinates.forEach(({ point, x, y }) => {
        const selected = point.outlineWidth > 1;
        const activatable = typeof layout.onPointActivate === "function";
        const circle = svgElement("circle", {
          class: selected ? "chart-point is-selected" : "chart-point",
          cx: x,
          cy: y,
          r: point.size / 2,
          fill: point.color || trace.color,
          stroke: selected ? point.outlineColor : "#ffffff",
          "stroke-width": selected ? point.outlineWidth : 1,
          tabindex: "0",
        });
        circle.append(titleElement(pointTitle(trace, point, layout)));
        if (activatable) {
          circle.setAttribute("role", "button");
          circle.setAttribute("aria-label", `Select snapshot date ${point.xLabel}`);
          const activate = () => layout.onPointActivate(point.xLabel);
          circle.addEventListener("click", activate);
          circle.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              activate();
            }
          });
        }
        svg.append(circle);
      });
    }
  }

  function drawTemporalSpline(svg, coordinates, trace) {
    const tangents = monotoneTangents(coordinates);
    for (let index = 1; index < coordinates.length; index += 1) {
      const previous = coordinates[index - 1];
      const current = coordinates[index];
      const gap = current.point.xNumber - previous.point.xNumber > 24 * 60 * 60 * 1000;
      svg.append(svgElement("path", {
        class: gap ? "chart-line chart-line-gap" : "chart-line",
        d: splineSegmentPath(previous, current, tangents[index - 1], tangents[index]),
        fill: "none",
        stroke: trace.color,
      }));
    }
  }

  function monotoneTangents(coordinates) {
    if (coordinates.length < 2) {
      return [0];
    }
    const widths = [];
    const slopes = [];
    for (let index = 0; index < coordinates.length - 1; index += 1) {
      const width = coordinates[index + 1].x - coordinates[index].x;
      widths.push(width);
      slopes.push(width > 0 ? (coordinates[index + 1].y - coordinates[index].y) / width : 0);
    }
    const tangents = Array(coordinates.length).fill(0);
    tangents[0] = slopes[0];
    tangents[tangents.length - 1] = slopes[slopes.length - 1];
    for (let index = 1; index < tangents.length - 1; index += 1) {
      const leftSlope = slopes[index - 1];
      const rightSlope = slopes[index];
      if (leftSlope === 0 || rightSlope === 0 || leftSlope * rightSlope < 0) {
        tangents[index] = 0;
        continue;
      }
      const leftWidth = widths[index - 1];
      const rightWidth = widths[index];
      const firstWeight = 2 * rightWidth + leftWidth;
      const secondWeight = rightWidth + 2 * leftWidth;
      tangents[index] = (firstWeight + secondWeight) / (
        firstWeight / leftSlope + secondWeight / rightSlope
      );
    }
    slopes.forEach((slope, index) => {
      if (slope === 0) {
        tangents[index] = 0;
        tangents[index + 1] = 0;
        return;
      }
      const leftRatio = tangents[index] / slope;
      const rightRatio = tangents[index + 1] / slope;
      const sum = leftRatio ** 2 + rightRatio ** 2;
      if (sum > 9) {
        const scale = 3 / Math.sqrt(sum);
        tangents[index] = scale * leftRatio * slope;
        tangents[index + 1] = scale * rightRatio * slope;
      }
    });
    return tangents;
  }

  function splineSegmentPath(from, to, fromTangent, toTangent) {
    const width = to.x - from.x;
    if (width <= 0) {
      return `M ${from.x} ${from.y} L ${to.x} ${to.y}`;
    }
    const controlWidth = width / 3;
    return [
      `M ${from.x} ${from.y}`,
      `C ${from.x + controlWidth} ${from.y + fromTangent * controlWidth}`,
      `${to.x - controlWidth} ${to.y - toTangent * controlWidth}`,
      `${to.x} ${to.y}`,
    ].join(" ");
  }

  function drawLegend(svg, traces, bounds) {
    if (traces.length <= 1) {
      return;
    }
    const visible = traces.slice(0, 7);
    let x = bounds.left;
    const y = bounds.top - 12;
    visible.forEach((trace) => {
      const labelText = shorten(trace.name || "Series", 15);
      const estimatedWidth = 18 + labelText.length * 6.2;
      if (x + estimatedWidth > bounds.right) {
        return;
      }
      const group = svgElement("g", { class: "chart-legend-item" });
      group.append(svgElement("circle", {
        cx: x + 5,
        cy: y - 4,
        r: 4,
        fill: trace.color,
      }));
      const label = svgElement("text", {
        class: "chart-legend-text",
        x: x + 14,
        y,
      });
      label.textContent = labelText;
      group.append(label);
      svg.append(group);
      x += estimatedWidth + 12;
    });
    if (traces.length > visible.length && x + 44 <= bounds.right) {
      const more = svgElement("text", {
        class: "chart-legend-text",
        x,
        y,
      });
      more.textContent = `+${traces.length - visible.length}`;
      svg.append(more);
    }
  }

  function drawTitle(svg, title, bounds) {
    if (!title) {
      return;
    }
    const label = svgElement("text", {
      class: "chart-title",
      x: bounds.left,
      y: 20,
    });
    label.textContent = shorten(title, 48);
    svg.append(label);
  }

  function measuredSize(container) {
    const rect = container.getBoundingClientRect();
    return {
      width: Math.max(Math.round(rect.width || 760), 320),
      height: Math.max(Math.round(rect.height || 320), 220),
    };
  }

  function marginsFor(width) {
    if (width < 520) {
      return { top: 42, right: 12, bottom: 50, left: 46 };
    }
    return { top: 48, right: 22, bottom: 54, left: 58 };
  }

  function configuredDomain(range) {
    if (!Array.isArray(range) || range.length !== 2) {
      return null;
    }
    const values = range.map((value) => isoDateNumber(value) ?? toNumber(value));
    if (values.some((value) => value === null) || values[0] === values[1]) {
      return null;
    }
    return values[0] < values[1] ? values : [values[1], values[0]];
  }

  function paddedDomain(values, includeZero, temporal = false) {
    const numericValues = values.map(toNumber).filter((value) => value !== null);
    if (includeZero) {
      numericValues.push(0);
    }
    if (!numericValues.length) {
      return [0, 1];
    }
    let min = Math.min(...numericValues);
    let max = Math.max(...numericValues);
    if (includeZero) {
      min = Math.min(min, 0);
      max = Math.max(max, 0);
    }
    if (min === max) {
      const pad = temporal ? 12 * 60 * 60 * 1000 : Math.abs(min || 1) * 0.2;
      return [min - pad, max + pad];
    }
    const pad = (max - min) * 0.08;
    return [min - pad, max + pad];
  }

  function ticks(min, max, count) {
    if (count <= 1 || min === max) {
      return [min];
    }
    const step = (max - min) / (count - 1);
    return Array.from({ length: count }, (_, index) => min + step * index);
  }

  function categoricalTicks(categories, width) {
    const maxTicks = width < 420 ? 4 : 7;
    const step = Math.max(1, Math.ceil(categories.length / maxTicks));
    return categories.filter((_, index) => index % step === 0);
  }

  function linear(value, sourceMin, sourceMax, targetMin, targetMax) {
    if (sourceMin === sourceMax) {
      return targetMin + (targetMax - targetMin) / 2;
    }
    const fraction = (value - sourceMin) / (sourceMax - sourceMin);
    return targetMin + fraction * (targetMax - targetMin);
  }

  function categorical(value, categories, left, right) {
    if (categories.length <= 1) {
      return left + (right - left) / 2;
    }
    const index = Math.max(categories.indexOf(value), 0);
    return left + (index / (categories.length - 1)) * (right - left);
  }

  function markerSize(value, index) {
    return clamp(numberAt(value, index, 8), 5, 15);
  }

  function colorAt(value, index) {
    if (Array.isArray(value)) {
      return value[index] || "";
    }
    return typeof value === "string" ? value : "";
  }

  function numberAt(value, index, fallback) {
    const raw = Array.isArray(value) ? value[index] : value;
    const number = toNumber(raw);
    return number === null ? fallback : number;
  }

  function valueAt(value, index) {
    return Array.isArray(value) ? value[index] : value;
  }

  function toNumber(value) {
    if (value === null || value === undefined || value === "") {
      return null;
    }
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function isoDateNumber(value) {
    if (typeof value !== "string") {
      return null;
    }
    const match = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) {
      return null;
    }
    return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  }

  function formatDateTick(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return formatNumber(value);
    }
    const month = String(date.getUTCMonth() + 1).padStart(2, "0");
    const day = String(date.getUTCDate()).padStart(2, "0");
    return `${month}/${day}`;
  }

  function valueLabel(value) {
    if (value === null || value === undefined) {
      return "";
    }
    return String(value);
  }

  function pointTitle(trace, point, layout) {
    const pieces = [
      trace.name,
      point.label,
      layout.xaxis?.title ? `${layout.xaxis.title}: ${point.xLabel}` : point.xLabel,
      layout.yaxis?.title ? `${layout.yaxis.title}: ${formatNumber(point.y)}` : formatNumber(point.y),
    ];
    return unique(pieces.filter(Boolean)).join(" | ");
  }

  function formatNumber(value) {
    const number = toNumber(value);
    if (number === null) {
      return "n/a";
    }
    if (Math.abs(number) >= 1000) {
      return number.toLocaleString(undefined, { maximumFractionDigits: 0 });
    }
    if (Math.abs(number) >= 10) {
      return number.toLocaleString(undefined, { maximumFractionDigits: 1 });
    }
    return number.toLocaleString(undefined, { maximumFractionDigits: 3 });
  }

  function axisLabel(value) {
    const label = valueLabel(value);
    const range = label.match(/^(\d{4})-(\d{2})-(\d{2}) to (\d{4})-(\d{2})-(\d{2})$/);
    if (range) {
      return `${range[2]}/${range[3]}-${range[5]}/${range[6]}`;
    }
    const date = label.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (date) {
      return `${date[2]}/${date[3]}`;
    }
    return label;
  }

  function shorten(value, maxLength) {
    const label = valueLabel(value);
    if (label.length <= maxLength) {
      return label;
    }
    return `${label.slice(0, Math.max(maxLength - 3, 1))}...`;
  }

  function unique(values) {
    const seen = new Set();
    return values.filter((value) => {
      const key = valueLabel(value);
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });
  }

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }

  function emptyState(message) {
    const empty = document.createElement("div");
    empty.className = "chart-empty";
    empty.textContent = message;
    return empty;
  }

  function svgElement(name, attributes) {
    const element = document.createElementNS(SVG_NS, name);
    Object.entries(attributes || {}).forEach(([key, value]) => {
      element.setAttribute(key, String(value));
    });
    return element;
  }

  function titleElement(text) {
    const title = svgElement("title");
    title.textContent = text;
    return title;
  }

  window.SensorCharts = {
    clear,
    redraw,
    render,
  };
}());
