from __future__ import annotations

import re
import uuid
import hashlib
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from .config import settings
from .document_loader import LoadedDocument, chunk_document
from .embedding import LocalBgeM3Embedder
from .schemas import DocumentChunksResponse, DocumentSummary, QALog, Source
from .vector_store import SQLiteVectorStore


class RagStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.embedder = LocalBgeM3Embedder(settings.embedding_model_path, settings.embedding_device)
        self.vector_store = SQLiteVectorStore(settings.vector_db_path)

    def add_document(self, doc: LoadedDocument) -> tuple[dict, list[dict], dict]:
        started = perf_counter()
        document_id = str(uuid.uuid4())
        chunks = []
        for i, chunk in enumerate(chunk_document(doc)):
            chunks.append(
                {
                    "chunk_id": f"{document_id}:{i}",
                    "document_id": document_id,
                    "chunk_index": i,
                    "title": doc.title,
                    "source": doc.source,
                    "page": chunk["page"],
                    "text": chunk["text"],
                }
            )
        if not chunks:
            raise ValueError("No readable text was extracted from the document.")

        load_seconds = self.embedder.load()
        embed_started = perf_counter()
        vectors = self.embedder.encode([chunk["text"] for chunk in chunks])
        embed_seconds = perf_counter() - embed_started

        document = {
            "document_id": document_id,
            "title": doc.title,
            "source": doc.source,
            "chunks": len(chunks),
            "content_hash": hashlib.sha256("\n".join(chunk["text"] for chunk in chunks).encode("utf-8")).hexdigest(),
        }
        write_started = perf_counter()
        write_summary = self.vector_store.add_document(document, chunks, vectors)
        write_seconds = perf_counter() - write_started

        metrics = {
            "document_id": document_id,
            "pages_read": len(doc.pages),
            "chunks_created": len(chunks),
            "vectors_generated": int(vectors.shape[0]),
            "vector_dimension": int(vectors.shape[1]),
            "embedding_model_loaded": self.embedder.loaded,
            "embedding_load_seconds": round(load_seconds, 3),
            "embedding_seconds": round(embed_seconds, 3),
            "vector_write_seconds": round(write_seconds, 3),
            "total_seconds": round(perf_counter() - started, 3),
            "vector_database": asdict(write_summary),
        }
        return asdict(write_summary), chunks, metrics

    def search(self, question: str, top_k: int | None = None) -> tuple[list[Source], dict]:
        started = perf_counter()
        limit = top_k or settings.top_k
        load_seconds = self.embedder.load()
        embed_started = perf_counter()
        query_vector = self.embedder.encode([question], is_query=True)
        embed_seconds = perf_counter() - embed_started
        search_started = perf_counter()
        sources = self.vector_store.search(query_vector, limit, settings.min_score, question)
        search_seconds = perf_counter() - search_started
        metrics = {
            "top_k": limit,
            "min_score": settings.min_score,
            "query_vector_dimension": int(query_vector.shape[1]),
            "hits": len(sources),
            "embedding_load_seconds": round(load_seconds, 3),
            "query_embedding_seconds": round(embed_seconds, 3),
            "vector_search_seconds": round(search_seconds, 3),
            "retrieval_mode": "hybrid_vector_lexical",
            "total_seconds": round(perf_counter() - started, 3),
        }
        return sources, metrics

    def delete_document(self, document_id: str) -> bool:
        return self.vector_store.delete_document(document_id)

    def rebuild_document(self, document_id: str) -> tuple[dict, dict] | None:
        detail = self.vector_store.document_chunks(document_id)
        if detail is None or not detail.chunks:
            return None
        started = perf_counter()
        load_seconds = self.embedder.load()
        embed_started = perf_counter()
        vectors = self.embedder.encode([chunk.text for chunk in detail.chunks])
        embed_seconds = perf_counter() - embed_started
        write_started = perf_counter()
        write_summary = self.vector_store.rebuild_document_vectors(document_id, vectors)
        if write_summary is None:
            return None
        metrics = {
            "document_id": document_id,
            "chunks_rebuilt": write_summary["chunks"],
            "vectors_generated": int(vectors.shape[0]),
            "vector_dimension": int(vectors.shape[1]),
            "embedding_load_seconds": round(load_seconds, 3),
            "embedding_seconds": round(embed_seconds, 3),
            "vector_write_seconds": round(perf_counter() - write_started, 3),
            "total_seconds": round(perf_counter() - started, 3),
            "updated_at": write_summary["updated_at"],
        }
        return write_summary, metrics

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
        return self.vector_store.add_qa_log(
            question=question,
            answer=answer,
            found=found,
            sources=sources,
            metrics=metrics,
            pipeline=pipeline,
        )

    def qa_history(self, limit: int = 20) -> list[QALog]:
        return self.vector_store.qa_history(limit)

    def document_chunks(self, document_id: str) -> DocumentChunksResponse | None:
        return self.vector_store.document_chunks(document_id)

    def status(self) -> tuple[list[DocumentSummary], int, dict, dict, dict]:
        docs, chunks, vector_database, qa_logs = self.vector_store.status()
        return docs, chunks, vector_database, asdict(self.embedder.status()), qa_logs


def build_extractive_answer(question: str, sources: list[Source]) -> str:
    if not sources:
        return "知识库中没有检索到足够相关的内容，无法回答该问题。"

    sentences = []
    question_terms = {t.lower() for t in re.findall(r"[\w\u4e00-\u9fff]+", question) if len(t) > 1}
    for source in sources:
        parts = re.split(r"(?<=[。！？!?])\s+|\n+", source.text)
        scored = []
        for part in parts:
            terms = {t.lower() for t in re.findall(r"[\w\u4e00-\u9fff]+", part) if len(t) > 1}
            scored.append((len(question_terms & terms), len(part), part.strip()))
        for _, _, sentence in sorted(scored, reverse=True)[:2]:
            if sentence and sentence not in sentences:
                sentences.append(sentence)
            if len(sentences) >= 5:
                break
        if len(sentences) >= 5:
            break

    cited = []
    for i, sentence in enumerate(sentences[:5], start=1):
        cited.append(f"{i}. {sentence}")
    return "以下内容仅基于已检索到的知识库片段：\n" + "\n".join(cited)
