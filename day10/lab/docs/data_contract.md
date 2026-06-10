# Data contract — Lab Day 10

> Source of truth: [`contracts/data_contract.yaml`](../contracts/data_contract.yaml). File này diễn giải.

---

## 1. Nguồn dữ liệu (source map)

| Nguồn | Phương thức ingest | Failure mode chính | Metric / alert |
|-------|-------------------|-------------------|----------------|
| `policy_refund_v4` | CSV export (CS DB) | chunk stale "14 ngày làm việc"; câu lặp 5× | E3 `refund_no_stale_14d_window` (halt) |
| `sla_p1_2026` | CSV export (Incident tool) | marker rác "!!!"/"Nội dung không rõ ràng"; ngày trống | E8 noise (warn), E4 length |
| `it_helpdesk_faq` | CSV export (Helpdesk KB) | chunk_text rỗng/space; duplicate | E9 dedup (halt) |
| `hr_leave_policy` | CSV export (HR portal) | xung đột version 2025 (10 ngày) vs 2026 (12 ngày) | E6 + rule R-N3 (halt) |
| `access_control_sop` | Markdown export (IT Security) | **bị baseline bỏ khỏi allowlist** | E7 coverage (halt) |
| `invalid_doc_*`, `legacy_*`, `security_policy`, `data_privacy_guideline` | export lỗi / nguồn chưa đăng ký | doc_id ngoài allowlist | quarantine `unknown_doc_id` |

---

## 2. Schema cleaned

| Cột | Kiểu | Bắt buộc | Ghi chú |
|-----|------|----------|---------|
| chunk_id | string | Có | `sha256(doc_id + text + seq)[:16]` — idempotent upsert key |
| doc_id | string | Có | phải ∈ `allowed_doc_ids` (pydantic validate) |
| chunk_text | string | Có | `min_length=8` (pydantic `CleanedChunk`) |
| effective_date | date | Có | ISO `YYYY-MM-DD`, ngày hợp lệ thật |
| exported_at | datetime | Có | ISO datetime, parse được |

Validate thật bằng pydantic: [`quality/schema.py`](../quality/schema.py) → expectation **E10 `pydantic_schema_valid`** (halt).

---

## 3. Quy tắc quarantine vs drop

- **Quarantine (giữ bằng chứng):** mọi record bị loại → ghi `reason` vào `artifacts/quarantine/quarantine_<run_id>.csv`. Không xoá vĩnh viễn → cho phép audit/merge lại.
- **Reason hiện có:** `unknown_doc_id`, `missing_effective_date`, `invalid_effective_date_format`, `stale_hr_policy_effective_date`, `stale_hr_2025_annual_leave`, `missing_chunk_text`, `duplicate_chunk_text`.
- **Ai approve merge lại:** owner nguồn (cột `owner` trong contract). Ví dụ thêm nguồn mới → IT Security xác nhận → thêm vào `allowed_doc_ids` → rerun.

---

## 4. Phiên bản & canonical

- **Refund:** source of truth = `policy_refund_v4` (v4 = 7 ngày làm việc). Chunk "14 ngày" là v3 stale → fix/halt.
- **HR leave:** bản 2026 (12 ngày phép năm) là canonical; bản 2025 (10 ngày phép năm / "bản HR 2025") bị cách ly theo **ngày** (cutoff) **và nội dung** (R-N3).
- **Cutoff version KHÔNG hard-code:** đọc từ `policy_versioning.hr_leave_min_effective_date` (contract) hoặc env `HR_LEAVE_MIN_EFFECTIVE_DATE`.
