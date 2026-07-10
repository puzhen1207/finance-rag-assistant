from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import numpy as np

from .config import settings


@dataclass
class EmbeddingStatus:
    provider: str
    model_path: str
    loaded: bool
    dimension: int | None
    device: str
    last_error: str | None = None


class LocalBgeM3Embedder:
    def __init__(self, model_path: Path, device: str = "cpu"):
        self.model_path = model_path
        self.device = device
        self._model = None
        self.dimension: int | None = None
        self.last_error: str | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def status(self) -> EmbeddingStatus:
        return EmbeddingStatus(
            provider="sentence-transformers",
            model_path=str(self.model_path),
            loaded=self.loaded,
            dimension=self.dimension,
            device=self.device,
            last_error=self.last_error,
        )

    def load(self) -> float:
        if self._model is not None:
            return 0.0
        started = perf_counter()
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                str(self.model_path),
                device=self.device,
                local_files_only=True,
            )
            probe = self._model.encode(
                ["embedding dimension probe"],
                normalize_embeddings=True,
                convert_to_numpy=True,
            )
            self.dimension = int(probe.shape[1])
            self.last_error = None
            return perf_counter() - started
        except Exception as exc:
            self.last_error = str(exc)
            self._model = None
            raise

    def encode(self, texts: list[str], *, is_query: bool = False) -> np.ndarray:
        self.load()
        prepared = [self._prepare_text(text, is_query=is_query) for text in texts]
        vectors = self._model.encode(
            prepared,
            batch_size=settings.embedding_batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim == 1:
            vectors = vectors.reshape(1, -1)
        self.dimension = int(vectors.shape[1])
        return vectors

    def _prepare_text(self, text: str, *, is_query: bool) -> str:
        text = text.strip()
        if is_query and settings.query_instruction and not text.startswith(settings.query_instruction):
            return settings.query_instruction + text
        return text
