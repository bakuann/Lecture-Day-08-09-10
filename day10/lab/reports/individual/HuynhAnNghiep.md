# Báo Cáo Cá Nhân — Lab Day 10: Data Pipeline & Observability

**Họ và tên:** Huỳnh An Nghiệp
**Vai trò:** Monitoring / Docs Owner
**Ngày nộp:** 2026-06-10
**Độ dài yêu cầu:** 400–650 từ

---

## 1. Tôi phụ trách phần nào? (80–120 từ)

**File / module:**

- `monitoring/freshness_check.py` — thêm hàm `check_dual_boundary_freshness()` đo freshness ở **2 boundary** (ingest snapshot + publish run).
- `etl_pipeline.py` — wire log `freshness_dual_boundary=...` và thêm cờ `freshness --dual`.
- `dashboard.py` — tab Freshness + KPI cards.
- Docs: `docs/runbook.md`, `docs/pipeline_architecture.md` (sơ đồ Mermaid), `docs/data_contract.md`.

**Kết nối với thành viên khác:** đọc manifest do Embed Owner sinh (`run_timestamp`, `latest_exported_at`); dùng allowlist/cutoff do Cleaning Owner khai trong contract.

**Bằng chứng:** commit sửa `freshness_check.py`; log dòng `freshness_dual_boundary=` trong `artifacts/logs/run_<run_id>.log`.

---

## 2. Một quyết định kỹ thuật (100–150 từ)

Tôi tách freshness thành **2 boundary** thay vì một con số. Lý do: "dữ liệu cũ" và "pipeline lâu không chạy" là hai sự cố khác nhau cần alert khác nhau. *Ingest boundary* = `now − max(exported_at)` phản ánh tuổi **snapshot nguồn**; *publish boundary* = `now − run_timestamp` phản ánh tuổi **lần chạy**. Status tổng hợp lấy mức nặng nhất (FAIL > WARN > PASS). Tôi chọn band WARN = trễ ≤ 1.5× SLA để có cảnh báo sớm trước khi FAIL. Với CSV mẫu, ingest FAIL (snapshot tháng 4/2026 cũ) là **kỳ vọng**, nên SLA hợp đồng tôi áp cho **publish run**, còn ingest dùng để cảnh báo — ghi rõ trong runbook để tránh "FAIL nhưng vẫn pass lab".

---

## 3. Một lỗi hoặc anomaly đã xử lý (100–150 từ)

**Triệu chứng:** import `chromadb` ném `TypeError: Descriptors cannot be created directly` (xung đột protobuf giữa opentelemetry của chromadb và `google-genai`).
**Phát hiện:** chạy thử `python -c "import chromadb"` → traceback ở `common_pb2.py`.
**Fix:** đặt `os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")` **trước** mọi import chromadb trong 4 entry-point (`etl_pipeline`, `eval_retrieval`, `grading_run`, `dashboard`). Sau fix, `import chromadb` OK (v1.5.9). Đây là điều kiện tiên quyết để embed/grading chạy được khi đã chuyển sang Gemini API.

---

## 4. Bằng chứng trước / sau (80–120 từ)

`run_id=demo-clean` — từ `artifacts/eval/after_inject_bad.csv` vs `after_fix.csv`:

```
# before (inject --no-refund-fix): 1/21 hits_forbidden
q_refund_window, ..., contains_expected=yes, hits_forbidden=yes, top1_doc_expected=yes
# after (pipeline chuẩn): 0/21 hits_forbidden, 21/21 contains_expected, 21/21 top1 đúng
q_refund_window, ..., contains_expected=yes, hits_forbidden=no, top1_doc_expected=yes
```

Freshness: `freshness_dual_boundary=FAIL {"ingest":{"status":"FAIL"...},"publish":{"status":"PASS"...}}`.

---

## 5. Cải tiến tiếp theo (40–80 từ)

Nối freshness FAIL vào webhook Slack `#data-quality` thật (hiện chỉ log), và thêm watermark đọc trực tiếp từ DB nguồn thay vì chỉ `exported_at` trong manifest — để phát hiện nguồn ngừng cập nhật ngay cả khi pipeline vẫn chạy đúng giờ.
