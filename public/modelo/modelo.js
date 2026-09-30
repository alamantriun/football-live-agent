const fields = {
  state: document.querySelector("[data-model-state]"),
  summary: document.querySelector("[data-model-summary]"),
  version: document.querySelector("[data-model-version]"),
  trainSize: document.querySelector("[data-train-size]"),
  validationSize: document.querySelector("[data-validation-size]"),
  lastDecision: document.querySelector("[data-last-decision]"),
  updatedAt: document.querySelector("[data-updated-at]"),
  brier: document.querySelector("[data-brier]"),
  logLoss: document.querySelector("[data-log-loss]"),
};

function setText(node, value) {
  node.textContent = value;
}

function formatMetric(value) {
  return Number.isFinite(Number(value)) ? Number(value).toFixed(3) : "Faltan datos verificados";
}

function formatDate(value) {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString("es-CO", { dateStyle: "medium", timeStyle: "short" }) : "Dato no disponible";
}

function renderModel(model) {
  const qualified = Number(model.train_size) >= 70 && Number(model.validation_size) >= 30;
  setText(fields.state, qualified ? "Modelo con muestra mínima verificada" : "Evidencia live todavía en recolección");
  setText(fields.summary, qualified ? "La versión publicada superó los mínimos de muestra; la promoción también exige mejorar Brier y log loss." : "El sistema conserva lecturas y resultados confirmados antes de evaluar una versión candidata.");
  setText(fields.version, model.version || "Faltan datos verificados");
  setText(fields.trainSize, Number.isFinite(Number(model.train_size)) ? `${model.train_size} partidos` : "Faltan datos verificados");
  setText(fields.validationSize, Number.isFinite(Number(model.validation_size)) ? `${model.validation_size} partidos` : "Faltan datos verificados");
  setText(fields.lastDecision, model.last_training_decision || "En espera de evidencia suficiente");
  setText(fields.updatedAt, formatDate(model.updated_at));
  setText(fields.brier, formatMetric(model.brier));
  setText(fields.logLoss, formatMetric(model.log_loss));
}

async function loadModelStatus() {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch("/api/model/status", { headers: { Accept: "application/json" }, signal: controller.signal });
    if (!response.ok) return;
    const envelope = await response.json();
    if (envelope && typeof envelope.model === "object" && envelope.model) renderModel(envelope.model);
  } catch {
    // The collecting state in the document is intentionally the safe fallback.
  } finally {
    window.clearTimeout(timeout);
  }
}

loadModelStatus();
