from __future__ import annotations

import json
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from time import perf_counter

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .document_loader import SUPPORTED_SUFFIXES, load_file, load_url
from .llm import generate_grounded_answer, is_refusal_answer
from .rag import RagStore, build_extractive_answer
from .schemas import AskRequest, AskResponse, DocumentChunksResponse, IngestTaskStatus, QALog, StatusResponse


app = FastAPI(title=settings.app_name)
store = RagStore(settings.data_dir)
tasks: dict[str, dict] = {}
tasks_lock = RLock()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

public_dir = Path(__file__).parent / "public"
app.mount("/static", StaticFiles(directory=public_dir), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(public_dir / "index.html")


@app.get("/documents/{document_id}/chunks")
def chunks_page(document_id: str) -> FileResponse:
    return FileResponse(public_dir / "chunks.html")


@app.get("/api/status", response_model=StatusResponse)
def status() -> StatusResponse:
    docs, chunks, vector_database, embedding_model, qa_logs = store.status()
    return StatusResponse(
        documents=docs,
        chunks=chunks,
        vector_database=vector_database,
        embedding_model=embedding_model,
        qa_logs=qa_logs,
    )


@app.get("/api/history", response_model=list[QALog])
def history(limit: int = Query(default=20, ge=1, le=100)) -> list[QALog]:
    return store.qa_history(limit)


@app.get("/api/tasks/{task_id}", response_model=IngestTaskStatus)
def task_status(task_id: str) -> IngestTaskStatus:
    with tasks_lock:
        task = tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return IngestTaskStatus(**task)


@app.get("/api/documents/{document_id}/chunks", response_model=DocumentChunksResponse)
def document_chunks(document_id: str) -> DocumentChunksResponse:
    result = store.document_chunks(document_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return result


@app.delete("/api/documents/{document_id}")
def delete_document(document_id: str):
    deleted = store.delete_document(document_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"ok": True, "document_id": document_id}


@app.post("/api/documents/{document_id}/rebuild")
def rebuild_document(document_id: str):
    result = store.rebuild_document(document_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Document not found")
    summary, metrics = result
    return {"ok": True, "document_id": document_id, "summary": summary, "metrics": metrics, "pipeline": _rebuild_pipeline(metrics)}


@app.post("/api/upload")
async def upload(background_tasks: BackgroundTasks, file: UploadFile = File(...), title: str | None = Form(default=None)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}. Supported: {sorted(SUPPORTED_SUFFIXES)}")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)
    task_id = _create_task("file", title or Path(file.filename or "document").stem)
    background_tasks.add_task(_run_file_ingest, task_id, tmp_path, suffix, title, file.filename)
    return {"ok": True, "task_id": task_id, "status": "queued"}


@app.post("/api/ingest-url")
def ingest_url(payload: dict, background_tasks: BackgroundTasks):
    url = (payload.get("url") or "").strip()
    title = (payload.get("title") or "").strip() or None
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")
    task_id = _create_task("url", title or url)
    background_tasks.add_task(_run_url_ingest, task_id, url, title)
    return {"ok": True, "task_id": task_id, "status": "queued"}


@app.post("/api/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    sources, metrics = store.search(request.question, request.top_k)
    pipeline = [
        {"step": "接收问题", "status": "done", "detail": request.question, "metric": "ready"},
        {
            "step": "BGE-M3 查询向量化",
            "status": "done",
            "detail": f"生成 {metrics['query_vector_dimension']} 维查询向量，用时 {metrics['query_embedding_seconds']}s",
            "metric": f"{metrics['query_vector_dimension']} dim",
        },
        {
            "step": "向量数据库检索",
            "status": "done",
            "detail": f"top_k={metrics['top_k']}，阈值={metrics['min_score']}，命中 {metrics['hits']} 个片段",
            "metric": f"{metrics['hits']} hits",
        },
    ]
    if not sources:
        pipeline.append({"step": "证据不足拒答", "status": "done", "detail": "没有检索到高于阈值的片段", "metric": "blocked"})
        answer = "知识库中没有检索到足够相关的内容，无法回答该问题。"
        log_id = store.add_qa_log(
            question=request.question,
            answer=answer,
            found=False,
            sources=[],
            metrics=metrics,
            pipeline=pipeline,
        )
        return AskResponse(
            answer=answer,
            found=False,
            sources=[],
            pipeline=pipeline,
            metrics=metrics,
            log_id=log_id,
        )

    llm_result = await generate_grounded_answer(request.question, sources)
    if llm_result.answer and not is_refusal_answer(llm_result.answer):
        answer = llm_result.answer
        metrics["generation_mode"] = "llm_grounded"
        metrics["llm_model"] = settings.llm_model
        pipeline.append({"step": "大模型生成", "status": "done", "detail": "已将用户问题和命中片段一起发送给 LLM，生成完整的受约束答案", "metric": settings.llm_model})
    else:
        answer = build_extractive_answer(request.question, sources)
        metrics["generation_mode"] = "extractive_fallback"
        metrics["llm_status"] = llm_result.status
        if llm_result.answer and is_refusal_answer(llm_result.answer):
            metrics["llm_refusal_overridden"] = True
            pipeline.append({"step": "修正过度拒答", "status": "done", "detail": "已检索到相关片段，但 LLM 返回了拒答模板；系统改用命中片段整理答案", "metric": "corrected"})
        elif llm_result.error:
            metrics["llm_error"] = llm_result.error
            pipeline.append({"step": "大模型生成失败", "status": "done", "detail": f"LLM 调用失败，已使用抽取式兜底。原因：{llm_result.error}", "metric": "fallback"})
        else:
            pipeline.append({"step": "抽取式回答", "status": "done", "detail": "未配置 LLM API，直接从命中片段抽取答案", "metric": "extract"})
    pipeline.append({"step": "输出引用", "status": "done", "detail": "返回来源、页码、分数和原文片段", "metric": "cited"})

    log_id = store.add_qa_log(
        question=request.question,
        answer=answer,
        found=True,
        sources=sources,
        metrics=metrics,
        pipeline=pipeline,
    )
    return AskResponse(answer=answer, found=True, sources=sources, pipeline=pipeline, metrics=metrics, log_id=log_id)


@app.get("/api/seed-sources")
def seed_sources():
    path = Path(__file__).resolve().parents[1] / "data" / "seed_sources.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _create_task(kind: str, title: str | None = None) -> str:
    task_id = str(uuid.uuid4())
    now = _now()
    with tasks_lock:
        tasks[task_id] = {
            "task_id": task_id,
            "status": "queued",
            "kind": kind,
            "title": title,
            "message": "等待入库任务开始",
            "progress": 5,
            "document": None,
            "pipeline": [],
            "metrics": {},
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
    return task_id


def _update_task(task_id: str, **kwargs) -> None:
    with tasks_lock:
        task = tasks.get(task_id)
        if task is None:
            return
        task.update(kwargs)
        task["updated_at"] = _now()


def _run_file_ingest(task_id: str, tmp_path: Path, suffix: str, title: str | None, filename: str | None) -> None:
    try:
        _update_task(task_id, status="running", message="正在解析文件", progress=18)
        parse_started = perf_counter()
        doc = load_file(tmp_path, title=title or Path(filename or "document").stem, source=filename)
        parse_seconds = perf_counter() - parse_started
        _update_task(task_id, message="正在切块并生成向量", progress=48)
        summary, chunks, metrics = store.add_document(doc)
        metrics["parse_seconds"] = round(parse_seconds, 3)
        pipeline = _ingest_pipeline(metrics)
        _update_task(
            task_id,
            status="done",
            message="入库完成",
            progress=100,
            document=summary,
            metrics=metrics,
            pipeline=pipeline,
        )
    except Exception as exc:
        _update_task(task_id, status="failed", message="入库失败", progress=100, error=str(exc), pipeline=[
            {"step": "入库失败", "status": "done", "detail": str(exc), "metric": "error"}
        ])
    finally:
        tmp_path.unlink(missing_ok=True)


def _run_url_ingest(task_id: str, url: str, title: str | None) -> None:
    try:
        _update_task(task_id, status="running", message="正在读取 URL", progress=18)
        parse_started = perf_counter()
        doc = load_url(url, title=title)
        parse_seconds = perf_counter() - parse_started
        _update_task(task_id, message="正在切块并生成向量", progress=48)
        summary, chunks, metrics = store.add_document(doc)
        metrics["parse_seconds"] = round(parse_seconds, 3)
        pipeline = _ingest_pipeline(metrics)
        _update_task(
            task_id,
            status="done",
            message="入库完成",
            progress=100,
            document=summary,
            metrics=metrics,
            pipeline=pipeline,
        )
    except Exception as exc:
        _update_task(task_id, status="failed", message="入库失败", progress=100, error=str(exc), pipeline=[
            {"step": "入库失败", "status": "done", "detail": str(exc), "metric": "error"}
        ])


def _ingest_pipeline(metrics: dict) -> list[dict]:
    vector_db = metrics["vector_database"]
    return [
        {
            "step": "读取资料",
            "status": "done",
            "detail": f"解析 {metrics['pages_read']} 个页面/文档单元，用时 {metrics.get('parse_seconds', 0)}s",
            "metric": f"{metrics['pages_read']} pages",
        },
        {
            "step": "知识切块",
            "status": "done",
            "detail": "按 900 字窗口、160 字重叠切块，保留来源和页码",
            "metric": f"{metrics['chunks_created']} chunks",
        },
        {
            "step": "BGE-M3 向量化",
            "status": "done",
            "detail": f"生成 {metrics['vectors_generated']} 条 {metrics['vector_dimension']} 维向量，用时 {metrics['embedding_seconds']}s",
            "metric": f"{metrics['vector_dimension']} dim",
        },
        {
            "step": "写入向量数据库",
            "status": "done",
            "detail": f"SQLite 向量库新增 {vector_db['vectors_inserted']} 条记录：{vector_db['database_path']}",
            "metric": f"{vector_db['vectors_inserted']} vectors",
        },
    ]


def _rebuild_pipeline(metrics: dict) -> list[dict]:
    return [
        {
            "step": "读取已有切块",
            "status": "done",
            "detail": f"读取 {metrics['chunks_rebuilt']} 个已入库切块",
            "metric": f"{metrics['chunks_rebuilt']} chunks",
        },
        {
            "step": "重新生成向量",
            "status": "done",
            "detail": f"生成 {metrics['vectors_generated']} 条 {metrics['vector_dimension']} 维向量",
            "metric": f"{metrics['vector_dimension']} dim",
        },
        {
            "step": "覆盖写入索引",
            "status": "done",
            "detail": f"更新完成，用时 {metrics['total_seconds']}s",
            "metric": "rebuilt",
        },
    ]
