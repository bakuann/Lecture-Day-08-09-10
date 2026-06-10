"""
Cleaning rules — raw export → cleaned rows + quarantine.

Baseline gồm các failure mode mở rộng (allowlist doc_id, parse ngày, HR stale version).
Nhóm thêm ≥3 rule mới: mỗi rule phải ghi `metric_impact` (xem README — chống trivial).

--- Mở rộng của nhóm (Day 10) ---
Allowlist:
  + Thêm `access_control_sop` (nguồn hợp lệ thứ 5 bị baseline bỏ sót → fix gq_d10_10).
  + Allowlist & cutoff version đọc từ `contracts/data_contract.yaml` / env (KHÔNG hard-code
    một ngày cố định trong code → đáp ứng Distinction (d): rule-versioning).

Rule mới (đánh số R-N*, đều có metric_impact đo được — xem reports/group_report.md):
  R-N1 strip_noise_markers      : bóc marker rác định dạng ("!!!", ghi chú sync)
                                  → chuẩn hoá text, lộ thêm bản trùng cho dedup.
  R-N2 collapse_repeated_text   : gộp cụm/câu lặp ("làm việc làm việc", câu padding lặp 5×).
  R-N3 quarantine_stale_hr_2025 : cách ly chunk HR mang bản 2025 ("10 ngày phép năm" /
                                  "bản HR 2025") mà rule theo-ngày bỏ lọt (chunk 2025 nhưng
                                  effective_date >= cutoff) → fix gq_d10_09.
  R-N4 quarantine_unclear_flag  : cách ly chunk bị gắn cờ low-trust ("Nội dung không rõ ràng")
                                  → KHÔNG tin, KHÔNG embed (thay vì bóc cờ rồi giữ).
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
_CONTRACT_PATH = ROOT / "contracts" / "data_contract.yaml"

# Allowlist & cutoff version mặc định (fallback nếu không đọc được contract/env).
_DEFAULT_ALLOWED_DOC_IDS = (
    "policy_refund_v4",
    "sla_p1_2026",
    "it_helpdesk_faq",
    "hr_leave_policy",
    "access_control_sop",  # R-fix: nguồn hợp lệ thứ 5 baseline bỏ sót (gq_d10_10).
)
_DEFAULT_HR_CUTOFF = "2026-01-01"


def _load_contract_config() -> Tuple[frozenset, str]:
    """
    Đọc allowlist + HR cutoff từ contract YAML (ưu tiên) rồi env, rồi default.
    Versioning KHÔNG hard-code trong code (Distinction (d) / rule-versioning).
    """
    allowed = set(_DEFAULT_ALLOWED_DOC_IDS)
    cutoff = _DEFAULT_HR_CUTOFF
    try:
        import yaml  # pyyaml đã có trong requirements

        if _CONTRACT_PATH.is_file():
            data = yaml.safe_load(_CONTRACT_PATH.read_text(encoding="utf-8")) or {}
            doc_ids = data.get("allowed_doc_ids")
            if isinstance(doc_ids, list) and doc_ids:
                allowed = {str(x).strip() for x in doc_ids if str(x).strip()}
            pv = data.get("policy_versioning") or {}
            c = pv.get("hr_leave_min_effective_date")
            if c:
                cutoff = str(c).strip()
    except Exception:
        # Contract lỗi → vẫn chạy với default (fail-open có kiểm soát).
        pass

    # Env override (tiện cho inject / A-B test versioning).
    cutoff = os.environ.get("HR_LEAVE_MIN_EFFECTIVE_DATE", cutoff).strip() or _DEFAULT_HR_CUTOFF
    return frozenset(allowed), cutoff


ALLOWED_DOC_IDS, HR_LEAVE_MIN_EFFECTIVE_DATE = _load_contract_config()

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DMY_SLASH = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")

# R-N1: marker rác đầu chunk (low-signal/noise đánh dấu trong export bẩn).
# Lưu ý: "Nội dung không rõ ràng" KHÔNG còn ở đây — nó được R-N4 CÁCH LY (xem dưới).
_NOISE_PREFIXES = ("!!!",)

# R-N4: cờ "không xác định / không rõ ràng" — dữ liệu low-trust → CÁCH LY, không embed.
_UNCLEAR_FLAGS = ("nội dung không rõ ràng",)
# R-N1: ghi chú/đuôi rác bám vào chunk (không phải nội dung chính sách).
_NOISE_SUFFIX_PATTERNS = (
    re.compile(r"\s*Chú ý:\s*effective_date không đồng nhất giữa các nguồn\.?\s*$"),
    re.compile(r"\s*Nguồn:\s*export tự động từ hệ thống CRM\.?\s*$"),
    re.compile(r"\s*Nội dung có thể bị trùng do sync lại dữ liệu\.?\s*$"),
    re.compile(r"\s*Ghi chú:\s*bản sync cũ policy-v3 — lỗi migration\.?\s*$"),
)
# R-N2: cụm từ bị lặp liền kề do lỗi export.
_REPEAT_PHRASES = ("làm việc làm việc",)


def _norm_text(s: str) -> str:
    return " ".join((s or "").strip().split()).lower()


def _stable_chunk_id(doc_id: str, chunk_text: str, seq: int) -> str:
    h = hashlib.sha256(f"{doc_id}|{chunk_text}|{seq}".encode("utf-8")).hexdigest()[:16]
    return f"{doc_id}_{seq}_{h}"


def _normalize_effective_date(raw: str) -> Tuple[str, str]:
    """
    Trả về (iso_date, error_reason).
    iso_date rỗng nếu không parse được.
    """
    s = (raw or "").strip()
    if not s:
        return "", "empty_effective_date"
    if _ISO_DATE.match(s):
        return s, ""
    m = _DMY_SLASH.match(s)
    if m:
        dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
        return f"{yyyy}-{mm}-{dd}", ""
    return "", "invalid_effective_date_format"


def _is_flagged_unclear(text: str) -> bool:
    """R-N4: text bị gắn cờ low-trust ('Nội dung không rõ ràng') → True để cách ly."""
    low = (text or "").lower()
    return any(flag in low for flag in _UNCLEAR_FLAGS)


def _strip_noise_markers(text: str) -> str:
    """R-N1: bóc prefix/suffix rác, giữ nguyên phần nội dung chính sách."""
    t = (text or "").strip()
    changed = True
    while changed:
        changed = False
        for pref in _NOISE_PREFIXES:
            if t.startswith(pref):
                t = t[len(pref):].strip()
                changed = True
    for pat in _NOISE_SUFFIX_PATTERNS:
        t = pat.sub("", t).strip()
    return t


def _collapse_repeated_text(text: str) -> str:
    """
    R-N2: gộp lặp do export lỗi.
    - Cụm liền kề ("làm việc làm việc" → "làm việc").
    - Câu lặp nguyên văn nhiều lần (padding 5×) → giữ 1 lần.
    """
    t = text or ""
    for phrase in _REPEAT_PHRASES:
        words = phrase.split(" ")
        half = " ".join(words[: len(words) // 2])
        if half:
            pat = re.compile(r"(?:" + re.escape(half) + r"\s*){2,}")
            t = pat.sub(half + " ", t)
    # gộp câu padding lặp nguyên văn
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", t) if p.strip()]
    if len(parts) >= 2:
        seen: set[str] = set()
        uniq: List[str] = []
        for p in parts:
            k = p.lower()
            if k in seen:
                continue
            seen.add(k)
            uniq.append(p)
        if len(uniq) < len(parts):
            t = " ".join(uniq)
    return re.sub(r"\s+", " ", t).strip()


def _is_stale_hr_2025(doc_id: str, text: str) -> bool:
    """
    R-N3: chunk HR mang nội dung bản 2025 (xung đột version).
    Bắt theo NỘI DUNG nên chặn cả chunk có effective_date >= cutoff nhưng text vẫn là 2025.
    Lưu ý: KHÔNG đụng "10 ngày/năm" của nghỉ ốm (chỉ chặn cụm 'phép năm').
    """
    if doc_id != "hr_leave_policy":
        return False
    low = (text or "").lower()
    return ("10 ngày phép năm" in low) or ("bản hr 2025" in low)


def load_raw_csv(path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({k: (v or "").strip() for k, v in r.items()})
    return rows


def clean_rows(
    rows: List[Dict[str, str]],
    *,
    apply_refund_window_fix: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Trả về (cleaned, quarantine).

    Baseline (mở rộng theo narrative Day 10):
    1) Quarantine: doc_id không thuộc allowlist (export lạ / catalog sai).
    2) Chuẩn hoá effective_date sang YYYY-MM-DD; quarantine nếu không parse được.
    3) Quarantine: chunk hr_leave_policy có effective_date < cutoff (bản HR cũ theo NGÀY).
    4) Quarantine: chunk_text rỗng hoặc effective_date rỗng sau chuẩn hoá.
    5) Loại trùng nội dung chunk_text (giữ bản đầu).
    6) Fix stale refund: policy_refund_v4 chứa '14 ngày làm việc' → 7 ngày.

    Mở rộng nhóm:
    R-N1 strip_noise_markers      (trước dedup/stale-check để chuẩn hoá text).
    R-N2 collapse_repeated_text   (gộp cụm/câu lặp).
    R-N3 quarantine_stale_hr_2025 (cách ly bản HR 2025 theo NỘI DUNG).
    """
    quarantine: List[Dict[str, Any]] = []
    seen_text: set[str] = set()
    cleaned: List[Dict[str, Any]] = []
    seq = 0

    for raw in rows:
        doc_id = raw.get("doc_id", "")
        text = raw.get("chunk_text", "")
        eff_raw = raw.get("effective_date", "")
        exported_at = raw.get("exported_at", "")

        if doc_id not in ALLOWED_DOC_IDS:
            quarantine.append({**raw, "reason": "unknown_doc_id"})
            continue

        eff_norm, eff_err = _normalize_effective_date(eff_raw)
        if eff_err == "empty_effective_date":
            quarantine.append({**raw, "reason": "missing_effective_date"})
            continue
        if eff_err == "invalid_effective_date_format":
            quarantine.append({**raw, "reason": eff_err, "effective_date_raw": eff_raw})
            continue

        # R-N4: chunk bị gắn cờ "không xác định / không rõ ràng" → CÁCH LY (low-trust,
        # không tin, không embed). Check trên text GỐC trước khi chuẩn hoá.
        if _is_flagged_unclear(text):
            quarantine.append({**raw, "reason": "flagged_unclear_content"})
            continue

        # R-N1 + R-N2: chuẩn hoá nội dung TRƯỚC khi check stale/empty/dedup.
        text = _strip_noise_markers(text)
        text = _collapse_repeated_text(text)

        if doc_id == "hr_leave_policy" and eff_norm < HR_LEAVE_MIN_EFFECTIVE_DATE:
            quarantine.append(
                {
                    **raw,
                    "reason": "stale_hr_policy_effective_date",
                    "effective_date_normalized": eff_norm,
                }
            )
            continue

        # R-N3: cách ly bản HR 2025 theo nội dung (bắt cả chunk có ngày >= cutoff).
        if _is_stale_hr_2025(doc_id, text):
            quarantine.append(
                {
                    **raw,
                    "reason": "stale_hr_2025_annual_leave",
                    "effective_date_normalized": eff_norm,
                }
            )
            continue

        if not text:
            quarantine.append({**raw, "reason": "missing_chunk_text"})
            continue

        key = _norm_text(text)
        if key in seen_text:
            quarantine.append({**raw, "reason": "duplicate_chunk_text"})
            continue
        seen_text.add(key)

        fixed_text = text
        if apply_refund_window_fix and doc_id == "policy_refund_v4":
            if "14 ngày làm việc" in fixed_text:
                fixed_text = fixed_text.replace(
                    "14 ngày làm việc",
                    "7 ngày làm việc",
                )
                fixed_text += " [cleaned: stale_refund_window]"

        seq += 1
        cleaned.append(
            {
                "chunk_id": _stable_chunk_id(doc_id, fixed_text, seq),
                "doc_id": doc_id,
                "chunk_text": fixed_text,
                "effective_date": eff_norm,
                "exported_at": exported_at or "",
            }
        )

    return cleaned, quarantine


def write_cleaned_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("chunk_id,doc_id,chunk_text,effective_date,exported_at\n", encoding="utf-8")
        return
    fieldnames = ["chunk_id", "doc_id", "chunk_text", "effective_date", "exported_at"]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def write_quarantine_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("chunk_id,doc_id,chunk_text,effective_date,exported_at,reason\n", encoding="utf-8")
        return
    keys: List[str] = []
    seen_k: set[str] = set()
    for r in rows:
        for k in r.keys():
            if k not in seen_k:
                seen_k.add(k)
                keys.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore", restval="")
        w.writeheader()
        for r in rows:
            w.writerow(r)
