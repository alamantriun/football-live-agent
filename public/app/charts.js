// First-party, synchronous charts: no animation is scheduled in either motion mode.
// Options: min/max (line axis), labels (observation labels), color/textColor/guideColor,
// and emptyMessage. Bar rows use { label, probability, color } in percentage units.
function prepare(canvas, options) {
  const context = canvas.getContext("2d");
  if (!context) return null;
  const bounds = canvas.getBoundingClientRect();
  const width = Math.max(1, bounds.width || 640);
  const height = Math.max(1, bounds.height || 240);
  const ratio = Number.isFinite(globalThis.devicePixelRatio)
    ? Math.max(1, globalThis.devicePixelRatio) : 1;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  context.font = "12px Inter, Arial, sans-serif";
  context.fillStyle = options.textColor || "#dbe2d5";
  const reducedMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  // Always paint the final frame; reduced-motion users never wait for transitions.
  return { context, width, height, reducedMotion };
}

function stateMessage(frame, message) {
  frame.context.textAlign = "center";
  frame.context.textBaseline = "middle";
  frame.context.fillText(String(message), frame.width / 2, frame.height / 2, frame.width);
}

export function clearChart(canvas, message = "Dato no disponible") {
  const frame = prepare(canvas, {});
  if (frame) stateMessage(frame, message);
}

export function drawLineChart(canvas, series = [], options = {}) {
  const frame = prepare(canvas, options);
  if (!frame) return;
  const { context, width, height } = frame;
  const values = series.flatMap(item => item.values).filter(Number.isFinite);
  if (!values.length) {
    stateMessage(frame, options.emptyMessage || "Dato no disponible");
    return;
  }
  let min = Number.isFinite(options.min) ? options.min : Math.min(...values);
  let max = Number.isFinite(options.max) ? options.max : Math.max(...values);
  if (max < min) [min, max] = [max, min];
  if (max === min) { min -= 1; max += 1; }
  const left = Math.min(48, width * 0.2);
  const right = Math.max(left + 1, width - 16);
  const top = 36;
  const bottom = Math.max(top + 1, height - 32);
  const count = Math.max(...series.map(item => item.values.length), 1);
  const x = index => count === 1 ? (left + right) / 2 : left + index * (right - left) / (count - 1);
  const y = value => bottom - (Math.max(min, Math.min(max, value)) - min) * (bottom - top) / (max - min);
  context.textBaseline = "middle";
  context.textAlign = "right";
  context.strokeStyle = options.guideColor || "rgba(255,255,255,0.16)";
  context.lineWidth = 1;
  for (let guide = 0; guide <= 4; guide++) {
    const value = min + (max - min) * guide / 4;
    context.beginPath();
    context.moveTo(left, y(value));
    context.lineTo(right, y(value));
    context.stroke();
    context.fillText(String(Number(value.toFixed(2))), left - 6, y(value));
  }
  const colors = ["#dfff54", "#ffd94d", "#ff9a85"];
  series.forEach((item, index) => {
    context.strokeStyle = item.color || colors[index % colors.length];
    context.lineWidth = 2;
    context.beginPath();
    let connected = false;
    item.values.forEach((value, point) => {
      if (!Number.isFinite(value)) { connected = false; return; }
      if (connected) context.lineTo(x(point), y(value));
      else context.moveTo(x(point), y(value));
      connected = true;
    });
    context.stroke();
    // A single observation is visible without implying a trend across gaps.
    context.fillStyle = item.color || colors[index % colors.length];
    item.values.forEach((value, point) => {
      if (!Number.isFinite(value)) return;
      context.beginPath();
      context.arc(x(point), y(value), 2, 0, Math.PI * 2);
      context.fill();
    });
    context.textAlign = "left";
    context.fillText(String(item.label || ""), left + index * (right - left) / series.length, 14, (right - left) / series.length);
  });
  context.fillStyle = options.textColor || "#dbe2d5";
  if (options.labels?.length) {
    context.textAlign = "left";
    context.fillText(String(options.labels[0] ?? ""), left, height - 12);
    context.textAlign = "right";
    context.fillText(String(options.labels[count - 1] ?? ""), right, height - 12);
  }
}

export function drawBarChart(canvas, rows = [], options = {}) {
  const frame = prepare(canvas, options);
  if (!frame) return;
  if (!rows.some(row => Number.isFinite(row.probability))) {
    stateMessage(frame, options.emptyMessage || "Dato no disponible");
    return;
  }
  const { context, width, height } = frame;
  const labelWidth = width * 0.3;
  const barWidth = width * 0.45;
  const rowHeight = height / Math.max(rows.length, 1);
  context.textBaseline = "middle";
  rows.forEach((row, index) => {
    const center = rowHeight * (index + 0.5);
    context.fillStyle = options.textColor || "#dbe2d5";
    context.textAlign = "left";
    context.fillText(String(row.label || ""), 0, center, Math.max(1, labelWidth - 8));
    if (Number.isFinite(row.probability)) {
      context.fillStyle = row.color || options.color || "#dfff54";
      context.fillRect(labelWidth, center - rowHeight * 0.2,
        barWidth * Math.max(0, Math.min(100, row.probability)) / 100, rowHeight * 0.4);
    }
    context.fillStyle = options.textColor || "#dbe2d5";
    context.textAlign = "right";
    context.fillText(Number.isFinite(row.probability) ? `${row.probability}%` : "Dato no disponible",
      width, center, Math.max(1, width - labelWidth - barWidth - 8));
  });
}
