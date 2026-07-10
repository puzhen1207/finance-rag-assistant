const $ = (id) => document.getElementById(id);

const pipeline = $("pipeline");
const answerCard = $("answerCard");
const sourcesList = $("sourcesList");
const fileInput = $("fileInput");
const fileState = $("fileState");
const fileStateTitle = $("fileStateTitle");
const fileStateDetail = $("fileStateDetail");
const fileStateBadge = $("fileStateBadge");
const clearFileBtn = $("clearFileBtn");
const workspace = $("workspace");
const viewLinks = Array.from(document.querySelectorAll("[data-view-link]"));

function currentViewFromHash() {
  const view = (window.location.hash || "#ask").replace("#", "");
  return ["ask", "ingest", "library", "trace"].includes(view) ? view : "ask";
}

function setView(view = currentViewFromHash()) {
  if (!workspace) return;
  workspace.className = `workspace view-${view}`;
  viewLinks.forEach((link) => {
    const active = link.dataset.viewLink === view;
    link.classList.toggle("active", active);
    link.setAttribute("aria-current", active ? "page" : "false");
  });
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (m) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[m]));
}

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function formatClock(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
}

function formatHistoryDay(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未知日期";
  const now = new Date();
  const start = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diff = Math.round((start(now) - start(date)) / 86400000);
  if (diff === 0) return "今天";
  if (diff === 1) return "昨天";
  return date.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
}

function formatBytes(bytes = 0) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / Math.pow(1024, index);
  return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}

function setFileState({ state = "empty", title = "未选择文件", detail = "选择文件后会显示文件名、大小和入库状态", badge = "waiting" } = {}) {
  if (!fileState) return;
  fileState.className = `file-state ${state}`;
  fileStateTitle.textContent = title;
  fileStateDetail.textContent = detail;
  fileStateBadge.textContent = badge;
  clearFileBtn.hidden = state === "empty";
  clearFileBtn.disabled = state === "uploading";
}

function renderSelectedFile(file, state = "ready", detailPrefix = "已选择，等待上传") {
  if (!file) {
    setFileState();
    return;
  }
  setFileState({
    state,
    title: file.name,
    detail: `${detailPrefix} · ${formatBytes(file.size)}`,
    badge: state === "done" ? "stored" : state === "uploading" ? "ingesting" : "ready",
  });
}

function renderPipeline(items = []) {
  pipeline.innerHTML = "";
  const safeItems = items.length
    ? items
    : [{ step: "等待入库或提问", status: "idle", detail: "RAG trace 会逐步显示在这里", metric: "idle" }];

  safeItems.forEach((item, index) => {
    const li = document.createElement("li");
    li.className = item.status === "done" ? "done" : item.status || "";
    li.innerHTML = `
      <span class="node">${String(index + 1).padStart(2, "0")}</span>
      <div class="trace-copy">
        <b>${escapeHtml(item.step)}</b>
        ${item.detail ? `<small>${escapeHtml(item.detail)}</small>` : ""}
      </div>
      <em>${escapeHtml(item.metric || "")}</em>
    `;
    pipeline.appendChild(li);
  });
  $("lastMetric").textContent = safeItems.at(-1)?.metric || "idle";
}

function renderMetrics(metrics = {}) {
  $("latencyMetric").textContent = metrics.total_seconds !== undefined ? `${metrics.total_seconds}s` : "-";
  $("hitMetric").textContent = metrics.hits !== undefined ? metrics.hits : "-";
  $("thresholdMetric").textContent = metrics.min_score !== undefined ? metrics.min_score : "-";
}

async function refreshStatus() {
  const res = await fetch("/api/status");
  const data = await res.json();
  const dims = data.vector_database?.dimensions || [];
  const primaryDim = dims[0]?.dimension || data.embedding_model?.dimension || "-";

  $("docCount").textContent = data.documents.length;
  $("chunkCount").textContent = data.chunks;
  $("vectorCount").textContent = data.vector_database?.vectors || 0;
  $("qaCount").textContent = data.qa_logs?.count || 0;
  $("dimensionCount").textContent = primaryDim;
  $("docTag").textContent = `${data.documents.length} docs`;
  $("dbPath").textContent = data.vector_database?.path || "storage/vector_store.sqlite3";

  const loaded = Boolean(data.embedding_model?.loaded);
  $("modelDot").className = loaded ? "status-dot ok" : "status-dot";
  $("modelStatus").textContent = loaded ? `BGE-M3 已加载，${data.embedding_model.dimension} 维` : "BGE-M3 等待加载";
  $("modelPath").textContent = loaded ? "本地嵌入模型已就绪" : "等待首次入库或提问时加载本地嵌入模型";

  $("docList").innerHTML = data.documents.length
    ? data.documents.map((d) => `
      <article class="doc" data-doc-id="${escapeHtml(d.document_id)}">
        <div class="doc-main" data-doc-href="/documents/${encodeURIComponent(d.document_id)}/chunks" tabindex="0" role="link" aria-label="打开 ${escapeHtml(d.title)} 的分块详情页">
          <b>${escapeHtml(d.title)}</b>
          <small>${escapeHtml(d.source)}</small>
          <span>${d.chunks} chunks · v${d.version || 1}${d.updated_at ? ` · ${formatTime(d.updated_at)}` : ""}</span>
        </div>
        <div class="doc-actions">
          <button class="ghost small" data-open-doc="${escapeHtml(d.document_id)}" type="button">分块</button>
          <button class="ghost small" data-rebuild-doc="${escapeHtml(d.document_id)}" type="button">重建</button>
          <button class="ghost small danger" data-delete-doc="${escapeHtml(d.document_id)}" type="button">删除</button>
        </div>
      </article>
    `).join("")
    : `<div class="empty-note">还没有资料入库。</div>`;

  document.querySelectorAll("[data-doc-href]").forEach((card) => {
    const openChunks = () => {
      window.location.href = card.dataset.docHref;
    };
    card.addEventListener("click", openChunks);
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openChunks();
      }
    });
  });
  document.querySelectorAll("[data-open-doc]").forEach((btn) => {
    btn.addEventListener("click", () => {
      window.location.href = `/documents/${encodeURIComponent(btn.dataset.openDoc)}/chunks`;
    });
  });
  document.querySelectorAll("[data-delete-doc]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const docId = btn.dataset.deleteDoc;
      if (!confirm("确定删除这份资料及其全部分块和向量吗？")) return;
      btn.disabled = true;
      try {
        const res = await fetch(`/api/documents/${encodeURIComponent(docId)}`, { method: "DELETE" });
        if (!res.ok) {
          const data = await res.json();
          throw new Error(data.detail || "删除失败");
        }
        await refreshStatus();
      } catch (error) {
        alert(error.message || "删除失败");
      } finally {
        btn.disabled = false;
      }
    });
  });
  document.querySelectorAll("[data-rebuild-doc]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const docId = btn.dataset.rebuildDoc;
      if (!confirm("确定重新生成这份资料的向量索引吗？")) return;
      btn.disabled = true;
      renderPipeline([{ step: "重建索引", status: "pending", detail: "正在重新生成该资料的向量", metric: "rebuild" }]);
      try {
        const res = await fetch(`/api/documents/${encodeURIComponent(docId)}/rebuild`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "重建失败");
        renderPipeline(data.pipeline || []);
        renderMetrics(data.metrics || {});
        await refreshStatus();
      } catch (error) {
        renderPipeline([{ step: "重建失败", status: "done", detail: error.message || "未知错误", metric: "error" }]);
      } finally {
        btn.disabled = false;
      }
    });
  });
}

async function refreshHistory() {
  const res = await fetch("/api/history?limit=40");
  const history = await res.json();
  const groups = history.reduce((acc, item) => {
    const key = formatHistoryDay(item.created_at);
    if (!acc.has(key)) acc.set(key, []);
    acc.get(key).push(item);
    return acc;
  }, new Map());
  $("historyCount").textContent = `${groups.size || 0} 天 · ${history.length} 条`;
  $("historyList").innerHTML = history.length
    ? Array.from(groups.entries()).map(([day, items], index) => `
      <details class="history-day" ${index === 0 ? "open" : ""}>
        <summary>
          <span>${escapeHtml(day)}</span>
          <em>${items.length} 条</em>
        </summary>
        <div class="history-day-list">
          ${items.map((item) => {
            const mode = item.metrics?.generation_mode === "llm_grounded" ? "大模型" : item.found ? "命中" : "拒答";
            const preview = String(item.answer || "").replace(/\s+/g, " ").slice(0, 96);
            return `
              <article class="history-item">
                <div class="history-line">
                  <time>${formatClock(item.created_at)}</time>
                  <b>${escapeHtml(item.question)}</b>
                  <span>${escapeHtml(mode)} · ${item.source_count} 源</span>
                </div>
                <p>${escapeHtml(preview)}${String(item.answer || "").length > 96 ? "..." : ""}</p>
                <small>${item.top_score !== null && item.top_score !== undefined ? `top ${item.top_score}` : "无相似度"}</small>
              </article>
            `;
          }).join("")}
        </div>
      </details>
    `).join("")
    : `<div class="empty-note">还没有问答记录。</div>`;
}

$("refreshBtn").addEventListener("click", async () => {
  await refreshStatus();
  await refreshHistory();
});

viewLinks.forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    const view = link.dataset.viewLink || "ask";
    history.replaceState(null, "", `#${view}`);
    setView(view);
  });
});

window.addEventListener("hashchange", () => setView());

$("uploadForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = fileInput.files[0];
  if (!file) return alert("请选择文件");
  renderSelectedFile(file, "uploading", "正在上传并解析入库");
  const form = new FormData();
  form.append("file", file);
  form.append("title", $("titleInput").value);
  const result = await runIngest(() => fetch("/api/upload", { method: "POST", body: form }));
  if (result?.ok) {
    renderSelectedFile(file, "done", "已完成入库");
  } else {
    setFileState({
      state: "error",
      title: file.name,
      detail: result?.detail || "入库失败，请检查文件格式或服务日志",
      badge: "failed",
    });
  }
});

fileInput.addEventListener("change", () => {
  renderSelectedFile(fileInput.files[0]);
});

clearFileBtn.addEventListener("click", () => {
  fileInput.value = "";
  setFileState();
});

$("urlForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = $("urlInput").value.trim();
  if (!url) return alert("请输入 URL");
  await runIngest(() => fetch("/api/ingest-url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  }));
});

async function runIngest(requestFactory) {
  renderMetrics();
  renderPipeline([
    { step: "读取资料", status: "pending", detail: "正在解析文件或 URL", metric: "read" },
    { step: "知识切块", status: "pending", detail: "等待文本解析完成", metric: "chunk" },
    { step: "BGE-M3 向量化", status: "pending", detail: "等待切块输入", metric: "embed" },
    { step: "写入向量数据库", status: "pending", detail: "等待向量生成", metric: "store" },
  ]);
  let res;
  let data;
  try {
    res = await requestFactory();
    data = await res.json();
  } catch (error) {
    const detail = error?.message || "请求失败";
    renderPipeline([{ step: "入库失败", status: "done", detail, metric: "error" }]);
    return { ok: false, detail };
  }
  if (!res.ok) {
    renderPipeline([{ step: "入库失败", status: "done", detail: data.detail || "未知错误", metric: "error" }]);
    return { ok: false, detail: data.detail || "未知错误", data };
  }
  if (data.task_id) {
    return pollIngestTask(data.task_id);
  }
  renderPipeline(data.pipeline || []);
  renderMetrics(data.metrics || {});
  await refreshStatus();
  return { ok: true, data };
}

async function pollIngestTask(taskId) {
  for (let i = 0; i < 240; i += 1) {
    const res = await fetch(`/api/tasks/${encodeURIComponent(taskId)}`);
    const task = await res.json();
    if (!res.ok) {
      renderPipeline([{ step: "入库失败", status: "done", detail: task.detail || "任务不存在", metric: "error" }]);
      return { ok: false, detail: task.detail || "任务不存在", data: task };
    }
    renderPipeline(task.pipeline?.length ? task.pipeline : [
      { step: "创建入库任务", status: "done", detail: task.title || task.task_id, metric: task.kind },
      { step: "后台处理", status: "pending", detail: task.message || task.status, metric: `${task.progress || 0}%` },
    ]);
    $("lastMetric").textContent = `${task.status} · ${task.progress || 0}%`;
    if (fileInput.files[0] && task.status !== "done" && task.status !== "failed") {
      renderSelectedFile(fileInput.files[0], "uploading", `${task.message || "正在入库"} · ${task.progress || 0}%`);
    }
    if (task.status === "done") {
      renderPipeline(task.pipeline || []);
      renderMetrics(task.metrics || {});
      await refreshStatus();
      return { ok: true, data: task };
    }
    if (task.status === "failed") {
      renderPipeline(task.pipeline || [{ step: "入库失败", status: "done", detail: task.error || "未知错误", metric: "error" }]);
      return { ok: false, detail: task.error || "入库失败", data: task };
    }
    await new Promise((resolve) => setTimeout(resolve, 900));
  }
  return { ok: false, detail: "入库任务超时" };
}

$("askBtn").addEventListener("click", async () => {
  const question = $("questionInput").value.trim();
  if (!question) return alert("请输入问题");
  $("askBtn").disabled = true;
  answerCard.className = "answer-card empty";
  answerCard.textContent = "正在向量化问题并检索知识库...";
  sourcesList.innerHTML = "";
  $("sourceCount").textContent = "0 hits";
  renderPipeline([{ step: "接收问题", status: "pending", detail: question, metric: "query" }]);
  try {
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const data = await res.json();
    renderPipeline(data.pipeline || []);
    renderMetrics(data.metrics || {});
    answerCard.className = data.found ? "answer-card" : "answer-card not-found";
    answerCard.textContent = data.answer || "没有返回答案";
    renderSources(data.sources || []);
    await refreshStatus();
    await refreshHistory();
  } finally {
    $("askBtn").disabled = false;
  }
});

function renderSources(sources) {
  $("sourceCount").textContent = `${sources.length} hits`;
  sourcesList.innerHTML = sources.length
    ? sources.map((s, idx) => `
      <article class="source">
        <div>
          <b>[${idx + 1}] ${escapeHtml(s.title)}</b>
          <span>final ${escapeHtml(s.score)}${s.vector_score !== null && s.vector_score !== undefined ? ` · vector ${escapeHtml(s.vector_score)}` : ""}${s.lexical_score !== null && s.lexical_score !== undefined ? ` · text ${escapeHtml(s.lexical_score)}` : ""}</span>
        </div>
        <small>${escapeHtml(s.source)}${s.page ? ` · page ${s.page}` : ""}${s.chunk_index !== null && s.chunk_index !== undefined ? ` · chunk ${s.chunk_index}` : ""}</small>
        ${s.highlights?.length ? `<ul class="highlight-list">${s.highlights.map((h) => `<li>${escapeHtml(h)}</li>`).join("")}</ul>` : ""}
        <p>${escapeHtml(s.text.slice(0, 620))}${s.text.length > 620 ? "..." : ""}</p>
      </article>
    `).join("")
    : `
      <div class="empty-state evidence-empty">
        <div class="empty-state-mark">0</div>
        <div>
          <b>等待检索结果</b>
          <p>发起问题后，这里会展示命中的资料片段、页码、分块编号和相似度分数。</p>
        </div>
      </div>
    `;
}

$("loadSeedsBtn").addEventListener("click", async () => {
  const res = await fetch("/api/seed-sources");
  const data = await res.json();
  $("seedList").innerHTML = data.sources.map((item) => `
    <article class="seed-item">
      <b>${escapeHtml(item.title)}</b>
      <small>${escapeHtml(item.publisher)}</small>
      <button class="ghost small" data-url="${escapeHtml(item.url)}">导入</button>
    </article>
  `).join("");
  document.querySelectorAll("[data-url]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      $("urlInput").value = btn.dataset.url;
      await runIngest(() => fetch("/api/ingest-url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: btn.dataset.url }),
      }));
    });
  });
});

refreshStatus();
refreshHistory();
renderPipeline();
renderMetrics();
setView();
