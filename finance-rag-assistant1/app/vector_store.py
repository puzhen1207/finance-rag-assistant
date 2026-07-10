from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .schemas import DocumentChunk, DocumentChunksResponse, DocumentSummary, QALog, Source


@dataclass
class VectorWriteSummary:
    document_id: str
    title: str
    source: str
    chunks: int
    vectors_inserted: int
    dimension: int
    database_path: str


class SQLiteVectorStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source TEXT NOT NULL,
                    chunks INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    source TEXT NOT NULL,
                    page INTEGER,
                    text TEXT NOT NULL,
                    vector BLOB NOT NULL,
                    dimension INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(document_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS qa_logs (
                    log_id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    found INTEGER NOT NULL,
                    source_count INTEGER NOT NULL,
                    top_score REAL,
                    sources_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    pipeline_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_dimension ON chunks(dimension)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_qa_logs_created_at ON qa_logs(created_at)")
            self._ensure_column(conn, "documents", "updated_at", "TEXT")
            self._ensure_column(conn, "documents", "version", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(conn, "documents", "content_hash", "TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(content_hash)")

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def add_document(self, document: dict, chunks: list[dict], vectors: np.ndarray) -> VectorWriteSummary:
        created_at = datetime.now(timezone.utc).isoformat()
        dimension = int(vectors.shape[1])
        with self._connect() as conn:
            version_row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM documents WHERE source = ?",
                (document["source"],),
            ).fetchone()
        version = int(version_row["version"] or 1)
        rows = []
        for chunk, vector in zip(chunks, vectors):
            rows.append(
                (
                    chunk["chunk_id"],
                    chunk["document_id"],
                    chunk["chunk_index"],
                    chunk["title"],
                    chunk["source"],
                    chunk["page"],
                    chunk["text"],
                    np.asarray(vector, dtype=np.float32).tobytes(),
                    dimension,
                    created_at,
                )
            )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO documents(
                    document_id, title, source, chunks, created_at, updated_at, version, content_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document["document_id"],
                    document["title"],
                    document["source"],
                    len(chunks),
                    created_at,
                    created_at,
                    version,
                    document.get("content_hash"),
                ),
            )
            conn.executemany(
                """
                INSERT OR REPLACE INTO chunks(
                    chunk_id, document_id, chunk_index, title, source, page,
                    text, vector, dimension, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return VectorWriteSummary(
            document_id=document["document_id"],
            title=document["title"],
            source=document["source"],
            chunks=len(chunks),
            vectors_inserted=len(rows),
            dimension=dimension,
            database_path=str(self.path),
        )

    def search(self, query_vector: np.ndarray, top_k: int, min_score: float, query_text: str = "") -> list[Source]:
        query = np.asarray(query_vector, dtype=np.float32)
        if query.ndim == 2:
            query = query[0]
        dimension = int(query.shape[0])
        query_terms = self._terms(query_text)

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT chunk_id, document_id, chunk_index, title, source, page, text, vector, dimension
                FROM chunks
                WHERE dimension = ?
                """,
                (dimension,),
            ).fetchall()

        scored: list[tuple[float, float, float, sqlite3.Row]] = []
        for row in rows:
            vector = np.frombuffer(row["vector"], dtype=np.float32)
            vector_score = float(np.dot(query, vector))
            lexical_score = self._lexical_score(query_terms, row["text"])
            weighted_score = (0.82 * vector_score) + (0.18 * lexical_score)
            rerank_score = round(max(vector_score, weighted_score), 6)
            if rerank_score >= min_score:
                scored.append((rerank_score, vector_score, lexical_score, row))

        scored.sort(key=lambda item: item[0], reverse=True)
        results = []
        for score, vector_score, lexical_score, row in scored[:top_k]:
            results.append(
                Source(
                    document_id=row["document_id"],
                    title=row["title"],
                    source=row["source"],
                    chunk_id=row["chunk_id"],
                    chunk_index=row["chunk_index"],
                    score=round(score, 4),
                    vector_score=round(vector_score, 4),
                    lexical_score=round(lexical_score, 4),
                    rerank_score=round(score, 4),
                    page=row["page"],
                    text=row["text"],
                    highlights=self._highlights(query_terms, row["text"]),
                )
            )
        return results

    def _terms(self, text: str) -> list[str]:
        terms = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
        return [term for term in terms if len(term) > 1]

    def _lexical_score(self, query_terms: list[str], text: str) -> float:
        if not query_terms:
            return 0.0
        lowered = text.lower()
        unique_terms = list(dict.fromkeys(query_terms))
        hits = sum(1 for term in unique_terms if term in lowered)
        coverage = hits / max(len(unique_terms), 1)
        density = min(sum(lowered.count(term) for term in unique_terms) / 12, 1.0)
        return round((0.78 * coverage) + (0.22 * density), 6)

    def _highlights(self, query_terms: list[str], text: str, limit: int = 4) -> list[str]:
        highlights = []
        lowered = text.lower()
        for term in dict.fromkeys(query_terms):
            index = lowered.find(term)
            if index < 0:
                continue
            start = max(0, index - 70)
            end = min(len(text), index + len(term) + 120)
            snippet = text[start:end].strip()
            if snippet and snippet not in highlights:
                highlights.append(snippet)
            if len(highlights) >= limit:
                break
        return highlights

    def delete_document(self, document_id: str) -> bool:
        with self._connect() as conn:
            document = conn.execute("SELECT document_id FROM documents WHERE document_id = ?", (document_id,)).fetchone()
            if document is None:
                return False
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            conn.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
        return True

    def rebuild_document_vectors(self, document_id: str, vectors: np.ndarray) -> dict | None:
        updated_at = datetime.now(timezone.utc).isoformat()
        dimension = int(vectors.shape[1])
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT chunk_id
                FROM chunks
                WHERE document_id = ?
                ORDER BY chunk_index ASC
                """,
                (document_id,),
            ).fetchall()
            if not rows:
                return None
            payload = [
                (np.asarray(vector, dtype=np.float32).tobytes(), dimension, updated_at, row["chunk_id"])
                for row, vector in zip(rows, vectors)
            ]
            conn.executemany(
                "UPDATE chunks SET vector = ?, dimension = ?, created_at = ? WHERE chunk_id = ?",
                payload,
            )
            conn.execute("UPDATE documents SET updated_at = ? WHERE document_id = ?", (updated_at, document_id))
        return {"chunks": len(rows), "dimension": dimension, "updated_at": updated_at}

    def add_qa_log(
        self,
        *,
        question: str,
        answer: str,
        found: bool,
        sources: list[Source],
        metrics: dict,
        pipeline: list[dict],
    ) -> str:
        log_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        source_payload = [
            {
                "document_id": source.document_id,
                "title": source.title,
                "source": source.source,
                "chunk_id": source.chunk_id,
                "chunk_index": source.chunk_index,
                "score": source.score,
                "vector_score": source.vector_score,
                "lexical_score": source.lexical_score,
                "rerank_score": source.rerank_score,
                "page": source.page,
                "text": source.text,
                "highlights": source.highlights,
            }
            for source in sources
        ]
        top_score = source_payload[0]["score"] if source_payload else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO qa_logs(
                    log_id, question, answer, found, source_count, top_score,
                    sources_json, metrics_json, pipeline_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log_id,
                    question,
                    answer,
                    int(found),
                    len(source_payload),
                    top_score,
                    json.dumps(source_payload, ensure_ascii=False),
                    json.dumps(metrics, ensure_ascii=False),
                    json.dumps(pipeline, ensure_ascii=False),
                    created_at,
                ),
            )
        return log_id

    def qa_history(self, limit: int = 20) -> list[QALog]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT log_id, question, answer, found, source_count, top_score,
                       sources_json, metrics_json, pipeline_json, created_at
                FROM qa_logs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        history = []
        for row in rows:
            history.append(
                QALog(
                    log_id=row["log_id"],
                    question=row["question"],
                    answer=row["answer"],
                    found=bool(row["found"]),
                    source_count=row["source_count"],
                    top_score=row["top_score"],
                    sources=json.loads(row["sources_json"]),
                    metrics=json.loads(row["metrics_json"]),
                    pipeline=json.loads(row["pipeline_json"]),
                    created_at=row["created_at"],
                )
            )
        return history

    def document_chunks(self, document_id: str) -> DocumentChunksResponse | None:
        with self._connect() as conn:
            document_row = conn.execute(
                """
                SELECT document_id, title, source, chunks, created_at, updated_at, version, content_hash
                FROM documents
                WHERE document_id = ?
                """,
                (document_id,),
            ).fetchone()
            if document_row is None:
                return None

            chunk_rows = conn.execute(
                """
                SELECT chunk_id, document_id, chunk_index, title, source, page, text, created_at
                FROM chunks
                WHERE document_id = ?
                ORDER BY chunk_index ASC
                """,
                (document_id,),
            ).fetchall()

        return DocumentChunksResponse(
            document=DocumentSummary(**dict(document_row)),
            chunks=[
                DocumentChunk(
                    chunk_id=row["chunk_id"],
                    document_id=row["document_id"],
                    chunk_index=row["chunk_index"],
                    title=row["title"],
                    source=row["source"],
                    page=row["page"],
                    text=row["text"],
                    text_length=len(row["text"]),
                    created_at=row["created_at"],
                )
                for row in chunk_rows
            ],
        )

    def status(self) -> tuple[list[DocumentSummary], int, dict, dict]:
        with self._connect() as conn:
            doc_rows = conn.execute(
                """
                SELECT document_id, title, source, chunks, created_at, updated_at, version, content_hash
                FROM documents
                ORDER BY created_at DESC
                """
            ).fetchall()
            chunk_count = conn.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"]
            qa_count = conn.execute("SELECT COUNT(*) AS count FROM qa_logs").fetchone()["count"]
            latest_qa = conn.execute("SELECT created_at FROM qa_logs ORDER BY created_at DESC LIMIT 1").fetchone()
            dims = conn.execute(
                """
                SELECT dimension, COUNT(*) AS count
                FROM chunks
                GROUP BY dimension
                ORDER BY count DESC
                """
            ).fetchall()
        docs = [DocumentSummary(**dict(row)) for row in doc_rows]
        vector_database = {
            "path": str(self.path),
            "backend": "sqlite",
            "collections": 1,
            "vectors": int(chunk_count),
            "dimensions": [{"dimension": row["dimension"], "count": row["count"]} for row in dims],
        }
        qa_logs = {
            "count": int(qa_count),
            "latest_at": latest_qa["created_at"] if latest_qa else None,
        }
        return docs, int(chunk_count), vector_database, qa_logs
