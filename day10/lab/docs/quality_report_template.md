# Quality report — Lab Day 10 (nhóm)

**run_id:** `demo-clean` (manifest: `artifacts/manifests/manifest_demo-clean.json`)
**Ngày:** 2026-06-10

---

## 1. Tóm tắt số liệu

| Chỉ số | Trước (baseline) | Sau (pipeline nhóm) | Ghi chú |
|--------|------------------|---------------------|---------|
| raw_records | 247 | 247 | export bẩn từ 5 nguồn hợp lệ + nhiều nguồn lỗi |
| cleaned_records | thiếu access_control_sop (≈25) | **31** | +6 chunk access_control_sop sau khi sửa allowlist |
| quarantine_records | — | **216** | chia theo `reason` (xem dưới) |
| coverage doc_id | 4/5 | **5/5** | thêm `access_control_sop` |
| Expectation halt? | **HALT** (baseline thiếu nguồn / stale) | **PASS** (exit 0) | E1–E10 đều pass |

**Quarantine theo reason:** `unknown_doc_id=109`, `duplicate_chunk_text=62`, `stale_hr_policy_effective_date=22`, `missing_chunk_text=9`, `stale_hr_2025_annual_leave=8`, `missing_effective_date=6`.

---

## 2. Before / after retrieval (bắt buộc)

> File: `artifacts/eval/after_inject_bad.csv` (before) vs `artifacts/eval/after_fix.csv` (after).

**Câu hỏi then chốt:** refund window (`q_refund_window` / `gq_d10_01`)
**Trước (inject `--no-refund-fix`):** `contains_expected=no`, `hits_forbidden=yes` (top-k còn "14 ngày làm việc").
**Sau (pipeline chuẩn):** `contains_expected=yes`, `hits_forbidden=no`, `top1_doc_expected=yes` (policy_refund_v4).

**HR version (`q_hr_annual_leave_under3` / `gq_d10_09`):**
**Trước:** top-k chứa "10 ngày phép năm (bản HR 2025)" → `hits_forbidden=yes`.
**Sau:** chỉ còn "12 ngày phép năm theo chính sách 2026" → `contains_expected=yes`, `hits_forbidden=no`.

---

## 3. Freshness & monitor

`python etl_pipeline.py freshness --manifest artifacts/manifests/manifest_<run_id>.json --dual`

- **publish** (run_timestamp): PASS nếu chạy ≤ 24h.
- **ingest** (max exported_at): FAIL trên CSV mẫu (snapshot nguồn cũ) — **kỳ vọng**; SLA của nhóm áp cho publish run, ingest dùng để cảnh báo dữ liệu nguồn cũ. Đổi qua `FRESHNESS_SLA_HOURS`.

---

## 4. Corruption inject (Sprint 3)

`python etl_pipeline.py run --run-id inject-bad --no-refund-fix --skip-validate`
→ bỏ rule fix refund: chunk "14 ngày làm việc" lọt vào embed. Phát hiện bằng:
- expectation E3 `refund_no_stale_14d_window` FAIL (halt) khi **không** `--skip-validate`;
- eval `hits_forbidden=yes` ở `q_refund_window`.

---

## 5. Hạn chế & việc chưa làm

- Query và document dùng chung embedding model (chưa tách task_type RETRIEVAL_QUERY/DOCUMENT).
- Chưa tự động alert thật (chỉ log) — để mở rộng Day 11.
- LLM-judge eval chưa bật (baseline keyword đủ cho grading).
