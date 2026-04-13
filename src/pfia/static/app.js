const state = {
  sessionId: null,
  jobId: null,
  pollTimer: null,
};

const uploadForm = document.getElementById("upload-form");
const uploadInput = document.getElementById("upload-input");
const uploadResult = document.getElementById("upload-result");
const runDemoButton = document.getElementById("run-demo");
const queueState = document.getElementById("queue-state");
const sessionStatus = document.getElementById("session-status");
const sessionMeta = document.getElementById("session-meta");
const summaryMetrics = document.getElementById("summary-metrics");
const clustersEl = document.getElementById("clusters");
const reportOutput = document.getElementById("report-output");
const eventsEl = document.getElementById("events");
const chatForm = document.getElementById("chat-form");
const chatQuestion = document.getElementById("chat-question");
const chatAnswer = document.getElementById("chat-answer");

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const [file] = uploadInput.files;
  if (!file) {
    setInlineMessage("Choose a CSV or JSON file first.", true);
    return;
  }
  await submitFile(file);
});

runDemoButton.addEventListener("click", async () => {
  runDemoButton.disabled = true;
  try {
    const response = await fetch("/api/demo/sample-file");
    if (!response.ok) {
      throw new Error("Could not load the demo dataset.");
    }
    const blob = await response.blob();
    const file = new File([blob], "mobile_app_reviews.csv", { type: "text/csv" });
    await submitFile(file);
  } catch (error) {
    setInlineMessage(error.message, true);
  } finally {
    runDemoButton.disabled = false;
  }
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.sessionId) {
    renderAnswerError("Start a session first.");
    return;
  }
  const question = chatQuestion.value.trim();
  if (question.length < 3) {
    renderAnswerError("Question must contain at least 3 characters.");
    return;
  }
  try {
    const response = await fetch(`/api/sessions/${state.sessionId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.message || payload.detail || "Q&A request failed.");
    }
    renderAnswer(payload);
  } catch (error) {
    renderAnswerError(error.message);
  }
});

async function submitFile(file) {
  queueState.textContent = "Submitting";
  const formData = new FormData();
  formData.append("file", file);
  try {
    const response = await fetch("/api/sessions/upload", { method: "POST", body: formData });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.message || payload.detail || "Upload failed.");
    }
    state.sessionId = payload.session_id;
    state.jobId = payload.job_id;
    setInlineMessage(`Queued session ${payload.session_id}.`, false);
    await loadSession();
    startPolling();
  } catch (error) {
    setInlineMessage(error.message, true);
    queueState.textContent = "Idle";
  }
}

function startPolling() {
  stopPolling();
  state.pollTimer = window.setInterval(loadSession, 2000);
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function loadSession() {
  if (!state.sessionId) return;
  try {
    const response = await fetch(`/api/sessions/${state.sessionId}`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.message || payload.detail || "Could not load session.");
    }
    renderSession(payload);
    const status = payload.session.status;
    if (["COMPLETED", "DEGRADED_COMPLETED", "FAILED"].includes(status)) {
      stopPolling();
    }
  } catch (error) {
    stopPolling();
    setInlineMessage(error.message, true);
  }
}

function setInlineMessage(message, isError) {
  uploadResult.classList.remove("hidden");
  uploadResult.textContent = message;
  uploadResult.style.background = isError ? "rgba(196, 69, 54, 0.12)" : "rgba(0, 109, 119, 0.08)";
  uploadResult.style.color = isError ? "var(--alert)" : "var(--accent-strong)";
}

function renderSession(payload) {
  const { session, job, preprocessing_summary: summary, clusters, alerts, report, events } = payload;
  const failed = session.status === "FAILED";
  queueState.textContent = job.status;
  sessionStatus.textContent = session.status;
  sessionStatus.className = `badge ${failed ? "status-failed" : session.status.includes("COMPLETED") ? "status-completed" : ""}`;

  sessionMeta.innerHTML = `
    <div><strong>Session:</strong> <code>${session.session_id}</code></div>
    <div><strong>Job:</strong> <code>${job.job_id}</code></div>
    <div><strong>Current stage:</strong> ${job.stage}</div>
    <div><strong>Failure code:</strong> ${session.failure_code || "n/a"}</div>
  `;

  summaryMetrics.innerHTML = summary
    ? [
        metric("Reviews kept", summary.kept_records),
        metric("Duplicates", summary.duplicate_records),
        metric("Quarantined", summary.quarantined_records),
        metric("Clusters", clusters.length),
      ].join("")
    : "";

  clustersEl.innerHTML = clusters.length
    ? clusters.slice(0, 10).map((cluster) => renderCluster(cluster, alerts)).join("")
    : `<div class="cluster-card muted">Clusters will appear here after analysis.</div>`;

  reportOutput.textContent = report?.markdown || "The report will appear here after the job completes.";
  eventsEl.innerHTML = events.length ? events.map(renderEvent).join("") : `<div class="event muted">No events yet.</div>`;
}

function renderCluster(cluster, alerts) {
  const alert = alerts.find((item) => item.cluster_id === cluster.cluster_id && !item.insufficient_history);
  return `
    <article class="cluster-card">
      <div class="cluster-header">
        <div>
          <div class="cluster-id">${cluster.cluster_id}</div>
          <h3>${cluster.label}</h3>
        </div>
        <div class="badge">${cluster.priority_score.toFixed(2)}</div>
      </div>
      <div class="cluster-summary">${cluster.summary}</div>
      <div class="muted">Reviews: ${cluster.size} · Sentiment: ${cluster.sentiment_score.toFixed(2)} · Trend: ${cluster.trend_delta.toFixed(2)}</div>
      <div class="tool-trace">
        ${(cluster.keywords || []).map((keyword) => `<span class="chip">${keyword}</span>`).join("")}
      </div>
      ${alert ? `<div class="inline-message">Alert: ${alert.reason}</div>` : ""}
    </article>
  `;
}

function renderEvent(item) {
  return `
    <div class="event">
      <div class="event-header">
        <strong>${item.stage}</strong>
        <span class="event-level">${item.level}</span>
      </div>
      <div>${item.message}</div>
      <div class="muted">${item.created_at}</div>
    </div>
  `;
}

function renderAnswer(payload) {
  chatAnswer.classList.remove("hidden");
  chatAnswer.innerHTML = `
    <div class="answer-block">
      <div class="eyebrow">Answer</div>
      <div class="answer-pre">${payload.answer}</div>
    </div>
    <div class="answer-block">
      <div class="eyebrow">Tool Trace</div>
      <div class="tool-trace">${payload.tool_trace
        .map((item) => `<span class="chip">${item.tool}: ${item.output_summary}</span>`)
        .join("")}</div>
    </div>
  `;
}

function renderAnswerError(message) {
  chatAnswer.classList.remove("hidden");
  chatAnswer.innerHTML = `<div class="answer-block">${message}</div>`;
}

function metric(label, value) {
  return `
    <div class="metric">
      <span class="metric-label">${label}</span>
      <span class="metric-value">${value}</span>
    </div>
  `;
}
