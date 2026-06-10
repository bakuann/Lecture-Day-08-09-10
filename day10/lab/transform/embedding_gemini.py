"""
Embedding adapter — Gemini API (KHÔNG tải model local, nhẹ máy).

Cung cấp một EmbeddingFunction tương thích ChromaDB dùng `google-genai`:
- batch nhiều chunk / lần gọi,
- cache theo nội dung (artifacts/cache/embeddings) để rerun không tốn quota,
- retry nhẹ khi gặp lỗi tạm thời.

Cấu hình qua .env:
  GEMINI_API_KEY=...                  (bắt buộc)
  EMBEDDING_MODEL=text-embedding-004  (mặc định; có thể gemini-embedding-001)
  EMBEDDING_DIM=                       (tuỳ chọn, chỉ cho gemini-embedding-001)
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import List, Sequence

ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = ROOT / "artifacts" / "cache" / "embeddings"

_DEFAULT_MODEL = "gemini-embedding-001"
_BATCH = 32


def _normalize_model(name: str) -> str:
    name = (name or _DEFAULT_MODEL).strip()
    # SentenceTransformers cũ (all-MiniLM…) hoặc model không khả dụng → fallback Gemini.
    if name.startswith("all-") or name == "text-embedding-004":
        return _DEFAULT_MODEL
    return name


class GeminiEmbeddingFunction:
    """EmbeddingFunction tương thích ChromaDB (callable: Documents -> Embeddings)."""

    def __init__(self, model_name: str | None = None, api_key: str | None = None) -> None:
        self._model = _normalize_model(model_name or os.environ.get("EMBEDDING_MODEL", _DEFAULT_MODEL))
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        dim = os.environ.get("EMBEDDING_DIM", "").strip()
        self._dim = int(dim) if dim.isdigit() else None
        self._client = None
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # --- ChromaDB EmbeddingFunction protocol -------------------------------
    @staticmethod
    def name() -> str:
        return "gemini"

    def get_config(self) -> dict:
        return {"model_name": self._model}

    @classmethod
    def build_from_config(cls, config: dict) -> "GeminiEmbeddingFunction":
        return cls(model_name=config.get("model_name"))

    def _ensure_client(self):
        if self._client is None:
            if not self._api_key:
                raise RuntimeError(
                    "Thiếu GEMINI_API_KEY. Thêm vào .env: GEMINI_API_KEY=<key của bạn> "
                    "(lấy tại https://aistudio.google.com/apikey)."
                )
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def _cache_path(self, text: str) -> Path:
        h = hashlib.sha256(f"{self._model}|{self._dim}|{text}".encode("utf-8")).hexdigest()
        return _CACHE_DIR / f"{h}.json"

    def _embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        from google.genai import types

        client = self._ensure_client()
        cfg = None
        if self._dim:
            cfg = types.EmbedContentConfig(output_dimensionality=self._dim)

        last_err: Exception | None = None
        for attempt in range(4):
            try:
                resp = client.models.embed_content(
                    model=self._model,
                    contents=list(texts),
                    config=cfg,
                )
                return [list(e.values) for e in resp.embeddings]
            except Exception as e:  # transient (rate limit / network)
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"Gemini embed_content thất bại sau retry: {last_err}")

    def __call__(self, input: Sequence[str]) -> List[List[float]]:  # noqa: A002 (Chroma dùng tên 'input')
        texts = [str(t) for t in input]
        out: List[List[float] | None] = [None] * len(texts)

        # 1) lấy từ cache
        misses: List[int] = []
        for i, t in enumerate(texts):
            cp = self._cache_path(t)
            if cp.is_file():
                try:
                    out[i] = json.loads(cp.read_text(encoding="utf-8"))
                    continue
                except Exception:
                    pass
            misses.append(i)

        # 2) gọi API theo batch cho phần thiếu
        for start in range(0, len(misses), _BATCH):
            idxs = misses[start : start + _BATCH]
            vecs = self._embed_batch([texts[i] for i in idxs])
            for i, v in zip(idxs, vecs):
                out[i] = v
                try:
                    self._cache_path(texts[i]).write_text(json.dumps(v), encoding="utf-8")
                except Exception:
                    pass

        return [v if v is not None else [] for v in out]

    # ChromaDB >=1.x gọi embed_documents / embed_query tách riêng cho upsert vs query.
    def embed_documents(self, input: Sequence[str]) -> List[List[float]]:  # noqa: A002
        return self(input)

    def embed_query(self, input: Sequence[str]) -> List[List[float]]:  # noqa: A002
        return self(input)


def get_embedding_function(model_name: str | None = None) -> GeminiEmbeddingFunction:
    return GeminiEmbeddingFunction(model_name=model_name)
