"""
Luồng ingest từ TÀI LIỆU GỐC (.txt) — bước chunking thật.

Khác với `policy_export_dirty.csv` (đã chunk sẵn từ upstream), module này đọc
`data/docs/*.txt`, tự cắt thành chunk và xuất ra CSV cùng schema để đưa vào
pipeline clean → validate → embed hiện có.

Chiến lược chunking:
  1) Tách header (Title/Source/Department/Effective Date/Access) → lấy `effective_date`.
  2) Tách body theo các section `=== ... ===`.
  3) Trong mỗi section: cắt sentence-aware, gói tham lam tới ~chunk_size ký tự,
     overlap 1 câu để giữ ngữ cảnh giữa các chunk.

Dùng:
  python -m transform.chunk_docs --docs data/docs --out data/raw/docs_chunked.csv
hoặc qua entrypoint:
  python etl_pipeline.py chunk-docs
"""

from __future__ import annotations

import argparse
import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent

_EFF_RE = re.compile(r"Effective Date:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})")
_SECTION_RE = re.compile(r"^=+\s*(.+?)\s*=+\s*$", re.MULTILINE)
_META_PREFIXES = ("Source:", "Department:", "Effective Date:", "Access:", "Ghi chú:")

DEFAULT_CHUNK_SIZE = 360
DEFAULT_OVERLAP_SENTENCES = 1
MIN_CHUNK_LEN = 8


def _extract_effective_date(text: str) -> str:
    m = _EFF_RE.search(text)
    if m:
        return m.group(1)
    # fallback: ngày hôm nay (vẫn ISO hợp lệ) nếu file thiếu header
    return datetime.now(timezone.utc).date().isoformat()


def _strip_header(text: str) -> str:
    """Bỏ dòng tiêu đề + metadata ở đầu file (trước section đầu tiên)."""
    lines = text.splitlines()
    body: List[str] = []
    started = False
    for i, ln in enumerate(lines):
        if not started:
            # bắt đầu lấy nội dung từ section đầu tiên
            if ln.strip().startswith("==="):
                started = True
            else:
                continue
        body.append(ln)
    if not started:  # file không có section → bỏ dòng metadata + title
        body = [
            ln for ln in lines
            if ln.strip() and not ln.strip().startswith(_META_PREFIXES)
        ][1:]  # bỏ luôn dòng title đầu
    return "\n".join(body)


def _split_sentences(text: str) -> List[str]:
    units: List[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("==="):
            continue
        for s in re.split(r"(?<=[.!?])\s+", line):
            s = s.strip()
            if s:
                units.append(s)
    return units


def _pack(units: List[str], size: int, overlap: int) -> List[str]:
    chunks: List[str] = []
    cur: List[str] = []
    cur_len = 0
    for u in units:
        if cur and cur_len + 1 + len(u) > size:
            chunks.append(" ".join(cur))
            cur = cur[-overlap:] if overlap > 0 else []          # overlap theo câu
            cur_len = sum(len(x) for x in cur) + max(0, len(cur) - 1)
        cur.append(u)
        cur_len += (1 if cur_len else 0) + len(u)
    if cur:
        chunks.append(" ".join(cur))
    return [c for c in chunks if len(c) >= MIN_CHUNK_LEN]


def chunk_one_doc(path: Path, *, chunk_size: int, overlap: int) -> List[Dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    doc_id = path.stem
    eff = _extract_effective_date(raw)
    body = _strip_header(raw)

    # tách theo section; mỗi section gói chunk riêng để không trộn ngữ cảnh
    sections = _SECTION_RE.split(body)
    # split trả về [trước_section, title1, content1, title2, content2, ...]
    blocks: List[str] = []
    if len(sections) == 1:
        blocks = [sections[0]]
    else:
        rest = sections[1:]
        for i in range(0, len(rest), 2):
            title = rest[i].strip()
            content = rest[i + 1] if i + 1 < len(rest) else ""
            blocks.append((title + ". " + content) if title else content)

    rows: List[Dict[str, Any]] = []
    exported = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for block in blocks:
        for chunk in _pack(_split_sentences(block), chunk_size, overlap):
            rows.append(
                {
                    "chunk_id": "",  # cleaning sẽ sinh chunk_id ổn định
                    "doc_id": doc_id,
                    "chunk_text": chunk,
                    "effective_date": eff,
                    "exported_at": exported,
                }
            )
    return rows


def chunk_documents(
    docs_dir: Path,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP_SENTENCES,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted(docs_dir.glob("*.txt")):
        rows.extend(chunk_one_doc(path, chunk_size=chunk_size, overlap=overlap))
    return rows


def write_chunks_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["chunk_id", "doc_id", "chunk_text", "effective_date", "exported_at"]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def main() -> int:
    p = argparse.ArgumentParser(description="Chunk data/docs/*.txt → raw CSV")
    p.add_argument("--docs", default=str(ROOT / "data" / "docs"))
    p.add_argument("--out", default=str(ROOT / "data" / "raw" / "docs_chunked.csv"))
    p.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    p.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP_SENTENCES)
    args = p.parse_args()

    rows = chunk_documents(Path(args.docs), chunk_size=args.chunk_size, overlap=args.overlap)
    write_chunks_csv(Path(args.out), rows)
    by_doc: Dict[str, int] = {}
    for r in rows:
        by_doc[r["doc_id"]] = by_doc.get(r["doc_id"], 0) + 1
    print(f"chunked {len(rows)} chunks từ {len(by_doc)} docs → {args.out}")
    for d, n in sorted(by_doc.items()):
        print(f"  {d}: {n} chunks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
