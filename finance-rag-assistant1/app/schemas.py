from pydantic import BaseModel, Field


class Source(BaseModel):
    document_id: str
    title: str
    source: str
    chunk_id: str
    chunk_index: int | None = None
    score: float
    vector_score: float | None = None
    lexical_score: float | None = None
    rerank_score: float | None = None
    page: int | None = None
    text: str
    highlights: list[str] = Field(default_factory=list)


class AskRequest(BaseModel):
    question: str = Field(min_length=2)
    top_k: int | None = Field(default=None, ge=1, le=12)


class AskResponse(BaseModel):
    answer: str
    found: bool
    sources: list[Source]
    pipeline: list[dict]
    metrics: dict = Field(default_factory=dict)
    log_id: str | None = None


class QALog(BaseModel):
    log_id: str
    question: str
    answer: str
    found: bool
    source_count: int
    top_score: float | None = None
    sources: list[dict] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)
    pipeline: list[dict] = Field(default_factory=list)
    created_at: str


class DocumentSummary(BaseModel):
    document_id: str
    title: str
    source: str
    chunks: int
    created_at: str | None = None
    updated_at: str | None = None
    version: int = 1
    content_hash: str | None = None


class DocumentChunk(BaseModel):
    chunk_id: str
    document_id: str
    chunk_index: int
    title: str
    source: str
    page: int | None = None
    text: str
    text_length: int
    created_at: str | None = None


class DocumentChunksResponse(BaseModel):
    document: DocumentSummary
    chunks: list[DocumentChunk]


class StatusResponse(BaseModel):
    documents: list[DocumentSummary]
    chunks: int
    vector_database: dict
    embedding_model: dict
    qa_logs: dict = Field(default_factory=dict)


class IngestTaskStatus(BaseModel):
    task_id: str
    status: str
    kind: str
    title: str | None = None
    message: str | None = None
    progress: int = 0
    document: dict | None = None
    pipeline: list[dict] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)
    error: str | None = None
    created_at: str
    updated_at: str
