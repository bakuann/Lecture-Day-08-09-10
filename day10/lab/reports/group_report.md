# Báo Cáo Nhóm — Lab Day 10: Data Pipeline & Data Observability

**Tên nhóm:** Team Day10 — Data Platform
**Thành viên:**

| Tên | Vai trò (Day 10) | Email |
|-----|------------------|-------|
| Huỳnh An Nghiệp | Ingestion / Raw Owner | anhuynh200319@gmail.com |


**Ngày nộp:** 2026-06-10
**Repo:** Lecture-Day-08-09-10/day10/lab
**Độ dài khuyến nghị:** 600–1000 từ

---

> Embedding dùng **Gemini API** (`text-embedding-004`) — không tải model local, nhẹ máy.
> Cấu hình: `.env` (`GEMINI_API_KEY`). Một lệnh chạy cả pipeline: `python etl_pipeline.py run`.

---

## 1. Pipeline tổng quan (150–200 từ)

Nguồn raw là `data/raw/policy_export_dirty.csv` (247 record) mô phỏng export bẩn từ nhiều hệ thống. Chuỗi end-to-end: **ingest → clean → validate → embed → serve**.

**Lệnh chạy một dòng:**

```bash
python etl_pipeline.py run
# eval:    python eval_retrieval.py --out artifacts/eval/after_fix.csv
# grading: python grading_run.py --out artifacts/eval/grading_run.jsonl
# dashboard: streamlit run dashboard.py
```

`run_id` lấy trong log dòng `run_id=...` và trong tên file manifest/cleaned/quarantine. Kết quả: **raw=247 → cleaned=31 → quarantine=216**, coverage **5/5** nguồn, expectation **PASS** (exit 0).

---

## 2. Cleaning & expectation (150–200 từ)

Baseline đã có allowlist, parse ngày ISO, HR-stale-theo-ngày, refund fix, dedup. Nhóm bổ sung **4 rule mới** + **4 expectation mới** (E7–E10), khai báo rõ **halt** vs **warn**.

### 2a. Bảng metric_impact (bắt buộc — chống trivial)

| Rule / Expectation mới | Trước (số liệu) | Sau / khi inject (số liệu) | Chứng cứ |
|------------------------|-----------------|----------------------------|----------|
| R-fix allowlist `access_control_sop` | 0 chunk access, coverage 4/5, gq_d10_10 fail | 6 chunk access, coverage 5/5, gq_d10_10 pass | `contracts/data_contract.yaml`, E7 log |
| R-N1 `strip_noise_markers` | nhiều chunk có `!!!` / ghi chú sync rác | 0 marker; dedup bắt thêm bản trùng | E8 `noisy_chunks=0` |
| R-N2 `collapse_repeated_text` | refund "làm việc làm việc", padding 5× | gộp còn 1 lần | diff cleaned CSV |
| R-N3 `quarantine_stale_hr_2025` | bản HR 2025 (10 ngày) lọt khi date ≥ cutoff | 8 chunk → quarantine `stale_hr_2025_annual_leave` | E6 `violations=0`, quarantine CSV |
| R-N4 `quarantine_unclear_flag` | chunk gắn cờ "Nội dung không rõ ràng" (trước: bóc cờ rồi giữ) | 8 chunk → quarantine `flagged_unclear_content`, không embed | quarantine CSV, E8 `noisy_chunks=0` |
| E7 `coverage_all_allowed_docs` (halt) | baseline có thể publish dù thiếu nguồn | HALT nếu thiếu doc allowlist | log expectation |
| E9 `no_duplicate_chunk_text` (halt) | — | `duplicate_rows=0` sau dedup | log |
| E10 `pydantic_schema_valid` (halt, **bonus**) | không validate schema | `valid=31/31 errors=0` | `quality/schema.py` |
| E8 `no_noise_markers` (**warn**) | marker rác | cảnh báo không chặn | log |

**Ví dụ expectation fail:** khi inject `--no-refund-fix`, E3 `refund_no_stale_14d_window` FAIL (halt, `violations=1`) → pipeline exit 2 (nếu không `--skip-validate`).

---

## 3. Before / after ảnh hưởng retrieval (200–250 từ)

**Kịch bản inject:** `python etl_pipeline.py run --run-id inject-bad --no-refund-fix --skip-validate` → embed dữ liệu xấu (refund 14 ngày lọt). Lưu eval xấu: `python eval_retrieval.py --out artifacts/eval/after_inject_bad.csv`. Sau đó chạy lại pipeline chuẩn (`python etl_pipeline.py run`) + `eval_retrieval.py --out artifacts/eval/after_fix.csv`.

**Kết quả định lượng:**
- `q_refund_window`: trước `contains_expected=no, hits_forbidden=yes` → sau `yes / no`, `top1_doc_expected=yes`.
- `q_hr_annual_leave_under3`: trước top-k còn "10 ngày phép năm" (`hits_forbidden=yes`) → sau chỉ "12 ngày phép năm 2026".
- Grading 10/10 `contains_expected=true`, `hits_forbidden=false`; `top1_doc_matches=true` cho gq_d10_09/10.

---

## 4. Freshness & monitoring (100–150 từ)

Đo **2 boundary**: *ingest* (max `exported_at` — tuổi snapshot nguồn) và *publish* (`run_timestamp` — tuổi lần chạy). SLA = 24h.
- **PASS** publish: pipeline vừa chạy.
- **FAIL** ingest trên CSV mẫu: snapshot nguồn cũ (cố ý) → cảnh báo dữ liệu nguồn lỗi thời, không chặn publish. Production sẽ siết cả hai. Lệnh: `python etl_pipeline.py freshness --manifest <...> --dual`.

---

## 5. Liên hệ Day 09 (50–100 từ)

Dữ liệu sau embed nằm ở collection **tách riêng** `day10_kb` (≠ Day 09) để cô lập thử nghiệm data-quality. Cùng `data/docs/` canonical; Day 09 có thể trỏ sang `day10_kb` để agent "đọc đúng version" (refund 7 ngày, HR 12 ngày 2026, access_control_sop) sau khi pipeline publish PASS.

---

## 6. Rủi ro còn lại & việc chưa làm

- Chưa tách task_type query/document cho Gemini embedding.
- Alert mới ở mức log; chưa nối webhook thật.
- LLM-judge eval để mở rộng (Distinction c) — chưa bật.
