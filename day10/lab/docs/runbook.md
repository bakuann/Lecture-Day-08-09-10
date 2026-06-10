# Runbook — Lab Day 10 (incident tối giản)

> Thứ tự debug (slide Day 10): **Freshness/version → Volume & errors → Schema & contract → Lineage/run_id → model/prompt**

---

## Symptom

> Agent/eval trả lời **"14 ngày"** thay vì 7 ngày cho refund, hoặc **"10 ngày phép năm"** thay vì 12 ngày cho HR 2026; hoặc câu về Level 4 Admin Access **không tìm thấy** (`access_control_sop` vắng mặt).

---

## Detection

- `python etl_pipeline.py run` trả **exit 2** + log `PIPELINE_HALT` (expectation halt fail).
- `eval_retrieval.py` cho `hits_forbidden=yes` (top-k còn chunk stale).
- `grading_run.jsonl`: `contains_expected=false` hoặc `top1_doc_matches=false`.
- `freshness_dual_boundary` = FAIL ở boundary ingest hoặc publish.

---

## Diagnosis

| Bước | Việc làm | Kết quả mong đợi |
|------|----------|------------------|
| 1 | Mở `artifacts/manifests/manifest_<run_id>.json` | đối chiếu `raw/cleaned/quarantine_records`, `run_id`, `latest_exported_at` |
| 2 | Mở `artifacts/quarantine/quarantine_<run_id>.csv`, lọc theo `reason` | thấy `unknown_doc_id` (nguồn thiếu allowlist), `stale_hr_2025_annual_leave`, `duplicate_chunk_text` |
| 3 | Xem log `expectation[...] FAIL (halt)` | biết rule nào chặn (vd `coverage_all_allowed_docs` → thiếu nguồn) |
| 4 | `python eval_retrieval.py --out artifacts/eval/after.csv` | `hits_forbidden=no`, `top1_doc_expected=yes` |

---

## Mitigation

- **Thiếu nguồn (access_control_sop):** thêm doc_id vào `allowed_doc_ids` trong `contracts/data_contract.yaml` → rerun.
- **Stale refund 14 ngày:** đảm bảo chạy **không** `--no-refund-fix`; rule E3 halt sẽ chặn publish nếu còn.
- **HR version conflict:** cutoff `hr_leave_min_effective_date` + rule nội dung R-N3 cách ly bản 2025. Rollback embed = rerun pipeline chuẩn (upsert + prune ghi đè snapshot cũ).
- Tạm thời treo banner "data stale" nếu freshness publish FAIL trong giờ cao điểm.

---

## Prevention

- Expectation **halt**: E3 refund, E6/R-N3 HR, E7 coverage, E9 dedup, E10 pydantic-schema.
- Expectation **warn**: E4 length, E8 noise-marker (cảnh báo không chặn).
- Freshness 2 boundary + alert `#data-quality`.
- **SLA freshness:** áp cho **publish run** (pipeline chạy ≤ 24h). Snapshot nguồn (ingest) trên CSV mẫu cố tình cũ → ingest-boundary FAIL là **kỳ vọng** trong lab; production sẽ siết cả hai. Đổi qua `FRESHNESS_SLA_HOURS`.
- Owner mỗi nguồn ghi ở contract → nối guardrail sang Day 11.
