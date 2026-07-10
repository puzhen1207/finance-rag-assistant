const $ = (id) => document.getElementById(id);

let allChunks = [];
let filteredChunks = [];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (m) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[m]));
}

function getDocumentId() {
  const match = window.location.pathname.match(/^\/documents\/([^/]+)\/chunks\/?$/);
  return match ? decodeURIComponent(match[1]) : "";
}

function renderError(message) {
  $("chunkFilterState").textContent = "failed";
  $("chunkList").innerHTML = `<div class="empty-note">${escapeHtml(message)}</div>`;
}

async function loadChunks() {
  const documentId = getDocumentId();
  if (!documentId) {
    renderError("缺少 document_id，无法读取分块。");
    return;
  }

  try {
    const res = await fetch(`/api/documents/${encodeURIComponent(documentId)}/chunks`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "分块读取失败");

    allChunks = data.chunks || [];
    filteredChunks = allChunks;
    document.title = `${data.document?.title || "Document"} - Chunks`;
    $("chunkPageTitle").textContent = data.document?.title || "文档分块详情";
    $("chunkDocSource").textContent = data.document?.source || "-";
    $("chunkDocId").textContent = data.document?.document_id || documentId;
    $("chunkTotal").textContent = allChunks.length;
    $("chunkChars").textContent = allChunks.reduce((sum, chunk) => sum + (chunk.text_length || 0), 0);
    renderChunks();
  } catch (error) {
    renderError(error.message || "分块读取失败");
  }
}

function renderChunks() {
  const keyword = $("chunkSearchInput").value.trim().toLowerCase();
  filteredChunks = keyword
    ? allChunks.filter((chunk) => String(chunk.text || "").toLowerCase().includes(keyword))
    : allChunks;

  $("chunkShown").textContent = filteredChunks.length;
  $("chunkIndexCount").textContent = filteredChunks.length;
  $("chunkFilterState").textContent = keyword ? `filtered · ${filteredChunks.length}` : `${filteredChunks.length} chunks`;

  $("chunkIndexList").innerHTML = filteredChunks.length
    ? filteredChunks.map((chunk, idx) => `
      <a href="#chunk-${chunk.chunk_index}" class="${idx === 0 ? "active" : ""}">
        <b>${String((chunk.chunk_index ?? idx) + 1).padStart(3, "0")}</b>
        <span>${chunk.page ? `page ${chunk.page}` : "no page"}</span>
      </a>
    `).join("")
    : `<div class="empty-note">没有匹配的分块。</div>`;

  $("chunkList").innerHTML = filteredChunks.length
    ? filteredChunks.map((chunk, idx) => `
      <details id="chunk-${chunk.chunk_index}" class="chunk-card chunk-card-rich" ${idx === 0 ? "open" : ""}>
        <summary>
          <span>Chunk ${String((chunk.chunk_index ?? idx) + 1).padStart(3, "0")}</span>
          <em>${chunk.page ? `page ${chunk.page}` : "no page"} · ${chunk.text_length} chars</em>
        </summary>
        <pre>${escapeHtml(chunk.text)}</pre>
        <small>${escapeHtml(chunk.chunk_id)}</small>
      </details>
    `).join("")
    : `<div class="empty-note">没有匹配的分块。</div>`;
}

$("chunkSearchInput").addEventListener("input", renderChunks);

$("expandAllBtn").addEventListener("click", () => {
  document.querySelectorAll(".chunk-card").forEach((item) => {
    item.open = true;
  });
});

$("collapseAllBtn").addEventListener("click", () => {
  document.querySelectorAll(".chunk-card").forEach((item) => {
    item.open = false;
  });
});

loadChunks();
