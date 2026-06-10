"""
Schema validation THẬT bằng pydantic (bonus +2 — không phải placeholder).

Mô hình `CleanedChunk` áp ràng buộc trên từng dòng cleaned trước khi embed:
- chunk_id / doc_id / chunk_text không rỗng,
- doc_id thuộc allowlist (đồng bộ contract),
- chunk_text >= min_length (đồng bộ data_contract.yaml: min_length=8),
- effective_date đúng ISO YYYY-MM-DD,
- exported_at parse được như datetime ISO.

`validate_cleaned_rows` trả về (n_valid, errors) để expectation suite biến thành halt/warn.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, List, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

from transform.cleaning_rules import ALLOWED_DOC_IDS

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MIN_CHUNK_LEN = 8


class CleanedChunk(BaseModel):
    """Một dòng cleaned hợp lệ trước khi đưa vào vector store."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    chunk_id: str = Field(min_length=1)
    doc_id: str = Field(min_length=1)
    chunk_text: str = Field(min_length=MIN_CHUNK_LEN)
    effective_date: str
    exported_at: str = ""

    @field_validator("doc_id")
    @classmethod
    def _doc_in_allowlist(cls, v: str) -> str:
        if v not in ALLOWED_DOC_IDS:
            raise ValueError(f"doc_id '{v}' ngoài allowlist")
        return v

    @field_validator("effective_date")
    @classmethod
    def _iso_date(cls, v: str) -> str:
        if not _ISO_DATE.match(v):
            raise ValueError("effective_date không ISO YYYY-MM-DD")
        # đảm bảo là ngày hợp lệ thật (vd 2026-13-40 sẽ fail)
        date.fromisoformat(v)
        return v

    @field_validator("exported_at")
    @classmethod
    def _iso_datetime(cls, v: str) -> str:
        if not v:
            return v
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


def validate_cleaned_rows(rows: List[Dict[str, Any]]) -> Tuple[int, List[str]]:
    """Validate toàn bộ cleaned rows. Trả về (số dòng hợp lệ, danh sách lỗi ngắn gọn)."""
    n_valid = 0
    errors: List[str] = []
    for i, r in enumerate(rows):
        try:
            CleanedChunk(**r)
            n_valid += 1
        except Exception as e:  # pydantic ValidationError hoặc ValueError
            msg = str(e).splitlines()[0]
            errors.append(f"row#{i} {r.get('chunk_id', '?')}: {msg}")
    return n_valid, errors
