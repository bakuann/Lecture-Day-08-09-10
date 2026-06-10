"""
Expectation suite đơn giản (không bắt buộc Great Expectations).

Baseline E1–E6. Nhóm thêm ≥2 expectation mới — ở đây thêm 4 (E7–E10),
phân biệt rõ warn vs halt:

  E7  coverage_all_allowed_docs   (halt) : đủ 5 nguồn allowlist trong cleaned
                                           (đặc biệt access_control_sop → gq_d10_10).
  E8  no_noise_markers            (warn) : không còn "!!!"/"Nội dung không rõ ràng" sau clean.
  E9  no_duplicate_chunk_text     (halt) : không còn chunk_text trùng (xác nhận dedup).
  E10 pydantic_schema_valid       (halt) : validate THẬT bằng pydantic (bonus +2).

Mỗi expectation mới đều có metric_impact đo được (xem reports/group_report.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from transform.cleaning_rules import ALLOWED_DOC_IDS


@dataclass
class ExpectationResult:
    name: str
    passed: bool
    severity: str  # "warn" | "halt"
    detail: str


def _norm(s: str) -> str:
    return " ".join((s or "").strip().split()).lower()


def run_expectations(cleaned_rows: List[Dict[str, Any]]) -> Tuple[List[ExpectationResult], bool]:
    """
    Trả về (results, should_halt).

    should_halt = True nếu có bất kỳ expectation severity halt nào fail.
    """
    results: List[ExpectationResult] = []

    # E1: có ít nhất 1 dòng sau clean
    ok = len(cleaned_rows) >= 1
    results.append(ExpectationResult("min_one_row", ok, "halt", f"cleaned_rows={len(cleaned_rows)}"))

    # E2: không doc_id rỗng
    bad_doc = [r for r in cleaned_rows if not (r.get("doc_id") or "").strip()]
    results.append(
        ExpectationResult("no_empty_doc_id", len(bad_doc) == 0, "halt", f"empty_doc_id_count={len(bad_doc)}")
    )

    # E3: policy refund không được chứa cửa sổ sai 14 ngày (sau khi đã fix)
    bad_refund = [
        r
        for r in cleaned_rows
        if r.get("doc_id") == "policy_refund_v4" and "14 ngày làm việc" in (r.get("chunk_text") or "")
    ]
    results.append(
        ExpectationResult("refund_no_stale_14d_window", len(bad_refund) == 0, "halt", f"violations={len(bad_refund)}")
    )

    # E4: chunk_text đủ dài
    short = [r for r in cleaned_rows if len((r.get("chunk_text") or "")) < 8]
    results.append(ExpectationResult("chunk_min_length_8", len(short) == 0, "warn", f"short_chunks={len(short)}"))

    # E5: effective_date đúng định dạng ISO sau clean (phát hiện parser lỏng)
    iso_bad = [
        r for r in cleaned_rows if not re.match(r"^\d{4}-\d{2}-\d{2}$", (r.get("effective_date") or "").strip())
    ]
    results.append(
        ExpectationResult("effective_date_iso_yyyy_mm_dd", len(iso_bad) == 0, "halt", f"non_iso_rows={len(iso_bad)}")
    )

    # E6: không còn marker phép năm cũ 10 ngày trên doc HR (conflict version sau clean)
    bad_hr_annual = [
        r
        for r in cleaned_rows
        if r.get("doc_id") == "hr_leave_policy" and "10 ngày phép năm" in (r.get("chunk_text") or "")
    ]
    results.append(
        ExpectationResult("hr_leave_no_stale_10d_annual", len(bad_hr_annual) == 0, "halt", f"violations={len(bad_hr_annual)}")
    )

    # === Expectation mới của nhóm (E7–E10) ===

    # E7 (halt): đủ coverage tất cả nguồn allowlist — bắt lỗi "thiếu nguồn" như access_control_sop.
    present = {(r.get("doc_id") or "").strip() for r in cleaned_rows}
    missing = sorted(set(ALLOWED_DOC_IDS) - present)
    results.append(
        ExpectationResult(
            "coverage_all_allowed_docs",
            len(missing) == 0,
            "halt",
            f"missing_docs={missing or 'none'} present={len(present)}/{len(ALLOWED_DOC_IDS)}",
        )
    )

    # E8 (warn): không còn marker rác trong cleaned (chất lượng, không chặn pipeline).
    noisy = [
        r
        for r in cleaned_rows
        if ("!!!" in (r.get("chunk_text") or ""))
        or ("nội dung không rõ ràng" in (r.get("chunk_text") or "").lower())
    ]
    results.append(ExpectationResult("no_noise_markers", len(noisy) == 0, "warn", f"noisy_chunks={len(noisy)}"))

    # E9 (halt): không còn chunk_text trùng (xác nhận dedup hoạt động).
    seen: set[str] = set()
    dups = 0
    for r in cleaned_rows:
        k = _norm(r.get("chunk_text") or "")
        if k in seen:
            dups += 1
        else:
            seen.add(k)
    results.append(ExpectationResult("no_duplicate_chunk_text", dups == 0, "halt", f"duplicate_rows={dups}"))

    # E10 (halt): validate schema THẬT bằng pydantic (bonus +2).
    try:
        from quality.schema import validate_cleaned_rows

        n_valid, errs = validate_cleaned_rows(cleaned_rows)
        ok_schema = len(errs) == 0
        sample = ("; " + errs[0]) if errs else ""
        results.append(
            ExpectationResult(
                "pydantic_schema_valid",
                ok_schema,
                "halt",
                f"valid={n_valid}/{len(cleaned_rows)} errors={len(errs)}{sample}",
            )
        )
    except Exception as e:
        results.append(ExpectationResult("pydantic_schema_valid", False, "warn", f"validator_error={e}"))

    halt = any(not r.passed and r.severity == "halt" for r in results)
    return results, halt
