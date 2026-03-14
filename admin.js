/* ═══════════════════════════════════════════════════
   VISIONGUARD AI — ADMIN DASHBOARD INTELLIGENCE
   ═══════════════════════════════════════════════════ */

// Dynamic Backend Routing Strategy
// Defaults to Localhost (8000), but uses production Render URL if hosted on Vercel
const isLocalhost = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
const BACKEND_URL = isLocalhost
  ? "http://127.0.0.1:8000"
  : "https://cctv-ai-backend-fig8.onrender.com"; // UPDATE THIS to your actual Render URL later


const dropZone = document.getElementById("drop-zone");
const fileInput = document.getElementById("video-upload");
const uploadProgress = document.getElementById("upload-progress");
const progressFill = document.getElementById("progress-fill");
const progressText = document.getElementById("progress-text");
const progressSize = document.getElementById("progress-size");
const startBtn = document.getElementById("start-analysis-btn");
const terminalLog = document.getElementById("terminal-log");
const aiBadge = document.getElementById("admin-ai-badge");
const insightsPanel = document.getElementById("insights-panel");

let currentFile = null;

// Helper: Add log to terminal
function addLog(message, type = "sys-msg") {
  const entry = document.createElement("div");
  entry.className = `log-entry ${type}`;

  const timestamp = new Date().toISOString().split('T')[1].split('.')[0];
  entry.innerText = `[${timestamp}] ${message}`;

  terminalLog.appendChild(entry);
  terminalLog.scrollTop = terminalLog.scrollHeight;
}

// ─── FILE UPLOAD UI HANDLERS ───

dropZone.addEventListener("click", () => fileInput.click());

dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropZone.classList.add("drag-over");
});

dropZone.addEventListener("dragleave", () => {
  dropZone.classList.remove("drag-over");
});

dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  if (e.dataTransfer.files.length) {
    handleFileSelection(e.dataTransfer.files[0]);
  }
});

fileInput.addEventListener("change", (e) => {
  if (e.target.files.length) {
    handleFileSelection(e.target.files[0]);
  }
});

function handleFileSelection(file) {
  const maxSize = 200 * 1024 * 1024; // 200MB
  if (file.size > maxSize) {
    addLog(`ERR: File ${file.name} exceeds 200MB limit.`, "danger-text");
    return;
  }

  currentFile = file;

  // Update Dropzone UI
  dropZone.querySelector("h3").innerText = file.name;
  dropZone.querySelector(".drop-hint").innerText = `SIZE: ${(file.size / (1024 * 1024)).toFixed(2)} MB / READY FOR TRANSFER`;
  dropZone.classList.add("file-selected");

  startBtn.disabled = false;
  addLog(`FILE ACQUIRED: ${file.name} | WAITING FOR USER EXECUTION`);
}


// ─── CLOCK TELEMETRY ───
function updateClock() {
  const now = new Date();
  document.getElementById("admin-timestamp").innerText = now.toISOString().replace("T", " ").substring(0, 19);
}
setInterval(updateClock, 1000);
updateClock();


// ─── API ORCHESTRATION ───

startBtn.addEventListener("click", async () => {
  if (!currentFile) return;
  startBtn.disabled = true;
  dropZone.style.display = "none";
  uploadProgress.classList.remove("hidden");

  aiBadge.classList.add("badge-active");
  aiBadge.innerText = "LINK ESTABLISHED";

  addLog("INITIALIZING INDEX UPLOAD PROTOCOL...");

  try {
    const { video_id, index_id } = await executeUploadAndIndex(currentFile);
    addLog(`INDEXING COMPLETE. VIDEO ID: ${video_id}`, "sys-success");
    addLog(`INDEX ID: ${index_id}`, "sys-success");

    addLog("DISPATCHING AI ANALYSIS JOB...");
    aiBadge.innerText = "ANALYZING...";

    const analysisResult = await executeAnalysis(video_id, index_id);
    addLog("ANALYSIS COMPLETE. DISPLAYING METRICS.", "sys-success");
    aiBadge.innerText = "ANALYSIS DONE";
    aiBadge.classList.remove("badge-active");

    renderResults(analysisResult);

  } catch (err) {
    addLog(`CRITICAL ERROR: ${err.message}`, "danger-text");
    aiBadge.innerText = "LINK FAILED";
    aiBadge.classList.remove("badge-active");
    startBtn.disabled = false;
    // reset UI
    dropZone.style.display = "flex";
    uploadProgress.classList.add("hidden");
  }
});

async function executeUploadAndIndex(file) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("index_name_prefix", "vision-guard");

  // Mocking XHR for accurate Upload Progress bar reading
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) {
        const percent = Math.round((e.loaded / e.total) * 100);
        progressFill.style.width = `${percent}%`;
        progressText.innerText = `TRANSFERRING DATA... ${percent}%`;
        progressSize.innerText = `${(e.loaded / 1024 / 1024).toFixed(1)} / ${(e.total / 1024 / 1024).toFixed(1)} MB`;
      }
    });

    xhr.onload = async () => {
      if (xhr.status === 202) {
        const response = JSON.parse(xhr.responseText);
        addLog(`UPLOAD COMPLETE. JOB_ID: ${response.job_id}`);
        addLog(`POLLING FOR TWELVE LABS CLOUD INDEXING...`);
        progressText.innerText = "INDEXING (AWAITING SERVER...)";

        try {
          const result = await pollIndexJob(response.job_id);
          resolve(result);
        } catch (pollErr) {
          reject(pollErr);
        }
      } else {
        addLog(`UPLOAD FAILED (STATUS ${xhr.status}): ${xhr.responseText}`, "danger-text");
        reject(new Error("File upload failed"));
      }
    };

    xhr.onerror = () => reject(new Error("Network Error during upload"));

    xhr.open("POST", `${BACKEND_URL}/warehouse-monitoring/index-jobs`);
    xhr.send(formData);
  });
}

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function pollIndexJob(jobId) {
  const url = `${BACKEND_URL}/warehouse-monitoring/index-jobs/${jobId}`;

  while (true) {
    await delay(3000); // Poll every 3 seconds
    const res = await fetch(url);
    if (!res.ok) throw new Error("Index polling failed");

    const data = await res.json();

    if (data.status === "failed") throw new Error(`Indexing Failed: ${data.error?.message || "Unknown error"}`);

    if (data.status === "completed") {
      if (data.result.ready_for_search && data.result.completion_basis === "indexed_asset_ready") {
        return {
          index_id: data.result.index_id,
          video_id: data.result.video_id
        };
      }
    }
    // Still running/queued
    addLog(`[INDEX JOB] STATUS: ${data.status.toUpperCase()}`);
  }
}

async function executeAnalysis(videoId, indexId) {
  const res = await fetch(`${BACKEND_URL}/warehouse-monitoring/analysis-jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ index_id: indexId, video_id: videoId })
  });

  if (!res.ok) throw new Error("Failed to start analysis job");
  const data = await res.json();

  addLog(`ANALYSIS ORCHESTRATED. JOB_ID: ${data.job_id}`);

  return pollAnalysisJob(data.job_id);
}
//test comment
async function pollAnalysisJob(jobId) {
  const url = `${BACKEND_URL}/warehouse-monitoring/analysis-jobs/${jobId}`;
  let retryCount = 0;
  const MAX_RETRIES = 5;

  while (true) {
    await delay(6000); // Poll every 6s for analysis (it's slower)
    try {
      const res = await fetch(url);
      if (!res.ok) {
        retryCount++;
        addLog(`[AI JOB] POLL RETRY ${retryCount}/${MAX_RETRIES} (HTTP ${res.status})`, "danger-text");
        if (retryCount >= MAX_RETRIES) throw new Error(`Analysis polling failed after ${MAX_RETRIES} retries (HTTP ${res.status})`);
        continue;
      }
      retryCount = 0; // reset on success

      const data = await res.json();

      if (data.status === "failed") throw new Error(`Analysis Failed: ${data.error?.message || "Unknown error"}`);

      if (data.status === "completed") {
        return data.result;
      }

      addLog(`[AI JOB] STATUS: ${data.status.toUpperCase()}`);
    } catch (err) {
      if (err.message.includes("Analysis")) throw err; // Re-throw analysis errors
      retryCount++;
      addLog(`[AI JOB] NETWORK RETRY ${retryCount}/${MAX_RETRIES}`, "danger-text");
      if (retryCount >= MAX_RETRIES) throw new Error("Analysis polling failed — network error");
    }
  }
}

// ─── RENDERING RESULTS ───

function renderResults(result) {
  // Hide upload grid, show insights panel
  document.getElementById("upload-grid").style.display = "none";
  insightsPanel.classList.remove("hidden");

  // ── PDF metadata ──
  const now = new Date();
  document.getElementById("pdf-date").innerText = now.toLocaleDateString("en-GB", {
    day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit"
  });
  document.getElementById("analysis-time").innerText = now.toLocaleTimeString();

  // ── 1. Bag Unloading ──
  const bagCount = result.bag_unloading?.estimated_total_bags_unloaded ?? 0;
  const bagConf = (result.bag_unloading?.count_confidence ?? "N/A").toUpperCase();
  document.getElementById("bag-count").innerText = bagCount;
  document.getElementById("bag-confidence").innerText = bagConf;
  // PDF KPIs
  document.getElementById("pdf-bag-count").innerText = bagCount;
  document.getElementById("pdf-bag-confidence").innerText = `Confidence: ${bagConf}`;

  // ── 2. Productivity ──
  const wp = result.worker_productivity;
  if (wp) {
    const workerCount = wp.observed_worker_count ?? 0;
    document.getElementById("worker-count").innerText = workerCount;
    document.getElementById("pdf-worker-count").innerText = workerCount;

    const workerList = document.getElementById("worker-list");
    const pdfTbody = document.getElementById("pdf-worker-tbody");
    workerList.innerHTML = "";
    pdfTbody.innerHTML = "";

    const labels = [], activeData = [], idleData = [];
    let totalScore = 0;

    (wp.workers || []).forEach(worker => {
      const score = (worker.productivity_score * 100).toFixed(1);
      totalScore += worker.productivity_score;

      // Dark UI card
      const wCard = document.createElement("div");
      wCard.className = "worker-entry";
      wCard.innerHTML = `
        <div class="worker-header">
           <strong>${worker.worker_tag.toUpperCase()}</strong>
           <span>${score}% SCORE</span>
        </div>
        <div class="worker-bar">
          <div class="active-bar" style="width: ${score}%"></div>
        </div>
        <div style="font-size: 0.6rem; color: var(--color-faint); margin-top: 3px;">
           ACTIVE: ${worker.active_seconds_estimate}s | IDLE: ${worker.idle_seconds_estimate}s
        </div>
      `;
      workerList.appendChild(wCard);

      // PDF table row
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${worker.worker_tag.toUpperCase()}</td>
        <td>${worker.active_seconds_estimate}s</td>
        <td>${worker.idle_seconds_estimate}s</td>
        <td class="pdf-score-cell">${score}%</td>
      `;
      pdfTbody.appendChild(tr);

      labels.push(worker.worker_tag.toUpperCase());
      activeData.push(worker.active_seconds_estimate);
      idleData.push(worker.idle_seconds_estimate);
    });

    // Avg productivity KPI
    const avg = wp.workers?.length ? ((totalScore / wp.workers.length) * 100).toFixed(1) : "—";
    document.getElementById("pdf-avg-productivity").innerText = `${avg}%`;

    renderPdfChart(labels, activeData, idleData);
  }

  // ── 3. Theft / Suspicious Activity ──
  const td = result.theft_detection;
  if (td) {
    const theftCard = document.getElementById("theft-card");
    const theftStatus = document.getElementById("theft-status");
    const incList = document.getElementById("incident-list");
    const pdfIncidents = document.getElementById("pdf-incidents");
    const incidentKpi = document.getElementById("pdf-incident-kpi");
    incList.innerHTML = "";

    if (td.theft_detected) {
      theftCard.style.borderColor = "var(--color-danger)";
      theftCard.style.boxShadow = "inset 0 0 20px rgba(255, 51, 51, 0.2)";
      theftStatus.innerText = "INCIDENTS DETECTED";
      theftStatus.classList.add("danger-text");

      const count = (td.incidents || []).length;
      document.getElementById("pdf-theft-count").innerText = count;
      incidentKpi.classList.add("pdf-kpi-alert");
      pdfIncidents.innerHTML = "";

      (td.incidents || []).forEach(inc => {
        // Dark UI
        const iDiv = document.createElement("div");
        iDiv.className = "incident-entry";
        iDiv.innerHTML = `
          <div class="incident-tag">${inc.worker_tag.toUpperCase()} <span>[${inc.start_sec}s - ${inc.end_sec}s]</span></div>
          <div class="incident-desc">${inc.item_description || "Unknown Item"}</div>
          <div class="incident-reason">${inc.reason || ""}</div>
        `;
        incList.appendChild(iDiv);

        // PDF entry
        const printLog = document.createElement("div");
        printLog.className = "print-log-entry danger";
        printLog.innerHTML = `<strong>${inc.worker_tag.toUpperCase()}</strong> &nbsp;[${inc.start_sec}s – ${inc.end_sec}s] &nbsp;|&nbsp; ${inc.item_description} &nbsp;|&nbsp; <em>${inc.reason}</em>`;
        pdfIncidents.appendChild(printLog);
      });
    } else {
      theftStatus.innerText = "NO SUSPICIOUS ACTIVITY";
      theftStatus.style.color = "var(--color-accent)";
      document.getElementById("pdf-theft-count").innerText = "0";
    }
  }
}

// ─── PDF CHART ───

function renderPdfChart(labels, activeData, idleData) {
  const ctx = document.getElementById("pdfWorkerChart").getContext("2d");

  if (window.pdfChart instanceof Chart) {
    window.pdfChart.destroy();
  }

  window.pdfChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Active (s)",
          data: activeData,
          backgroundColor: "#00C875",
          borderRadius: 4,
          barPercentage: 0.55,
        },
        {
          label: "Idle (s)",
          data: idleData,
          backgroundColor: "#FB7185",
          borderRadius: 4,
          barPercentage: 0.55,
        }
      ]
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: "bottom",
          labels: { font: { family: "Inter", size: 11 }, padding: 12 }
        }
      },
      scales: {
        x: { stacked: false, grid: { color: "#F1F5F9" }, ticks: { font: { size: 10 } } },
        y: { grid: { display: false }, ticks: { font: { family: "Inter", size: 11 } } }
      }
    }
  });
}

// ─── DOWNLOAD PDF ───
document.getElementById("download-report-btn").addEventListener("click", () => {
  window.print();
});
