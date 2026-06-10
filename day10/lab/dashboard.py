#!/usr/bin/env python3
"""
Day 10 — Data Pipeline & Observability Dashboard (Streamlit).

Chạy:
    streamlit run dashboard.py

Trực quan hoá: funnel ingest→clean→quarantine, lý do quarantine, coverage nguồn,
expectation suite (warn/halt), freshness 2 boundary, grading 10 câu (Pass/Merit/Distinction),
before/after eval, và truy vấn trực tiếp vector store.

Phần clean/quarantine/expectation tính TRỰC TIẾP từ CSV (không cần API).
Phần embed / grading / live-query cần GEMINI_API_KEY (.env).
"""

from __future__ import annotations

import os

# PHẢI đặt trước MỌI import dùng protobuf (streamlit/chromadb) — nếu không protobuf
# khóa C-implementation và import chromadb trong Live Query sẽ lỗi "Descriptors cannot be created".
# Gán cứng (không dùng setdefault) để override mọi giá trị có sẵn trong môi trường.
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

import json
import subprocess
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from transform.cleaning_rules import (  # noqa: E402
    ALLOWED_DOC_IDS,
    HR_LEAVE_MIN_EFFECTIVE_DATE,
    clean_rows,
    load_raw_csv,
)
from quality.expectations import run_expectations  # noqa: E402
from monitoring.freshness_check import check_dual_boundary_freshness  # noqa: E402

RAW = ROOT / "data" / "raw" / "policy_export_dirty.csv"
ART = ROOT / "artifacts"
MAN_DIR = ART / "manifests"
EVAL_DIR = ART / "eval"

# ----------------------------------------------------------------------------- styling
st.set_page_config(page_title="Day 10 · Data Pipeline Observability", page_icon="🛰️", layout="wide")

PALETTE = {
    "bg": "#0d1117", "panel": "#161b22", "ink": "#e6edf3", "muted": "#8b949e",
    "ok": "#3fb950", "warn": "#d29922", "halt": "#f85149", "brand": "#58a6ff", "violet": "#bc8cff",
}

st.markdown(
    f"""
    <style>
      .stApp {{ background: {PALETTE['bg']}; }}
      .block-container {{ padding-top: 1.4rem; max-width: 1280px; }}
      h1, h2, h3, h4, p, span, div, label {{ color: {PALETTE['ink']}; }}
      .hero {{
        background: linear-gradient(120deg, #1f2a44 0%, #2a1f44 60%, #14233a 100%);
        border: 1px solid #2b3650; border-radius: 18px; padding: 22px 26px; margin-bottom: 14px;
      }}
      .hero h1 {{ font-size: 1.7rem; margin: 0 0 4px 0; letter-spacing:.3px; }}
      .hero p {{ color: {PALETTE['muted']}; margin: 0; font-size: .92rem; }}
      .kpi {{
        background: {PALETTE['panel']}; border: 1px solid #232c39; border-radius: 16px;
        padding: 16px 18px; height: 100%;
      }}
      .kpi .label {{ color: {PALETTE['muted']}; font-size: .72rem; text-transform: uppercase; letter-spacing: 1px; }}
      .kpi .value {{ font-size: 2.0rem; font-weight: 700; line-height: 1.1; margin-top: 2px; }}
      .kpi .sub {{ color: {PALETTE['muted']}; font-size: .78rem; margin-top: 2px; }}
      .pill {{ display:inline-block; padding: 2px 10px; border-radius: 999px; font-size:.72rem; font-weight:600; }}
      .pill.ok   {{ background: rgba(63,185,80,.15);  color: {PALETTE['ok']};   border:1px solid rgba(63,185,80,.4);}}
      .pill.warn {{ background: rgba(210,153,34,.15); color: {PALETTE['warn']}; border:1px solid rgba(210,153,34,.4);}}
      .pill.halt {{ background: rgba(248,81,73,.15);  color: {PALETTE['halt']}; border:1px solid rgba(248,81,73,.4);}}
      .flow {{ font-size:.9rem; color:{PALETTE['muted']}; }}
      .flow b {{ color:{PALETTE['brand']}; }}
      .tier {{ font-size: 1.5rem; font-weight: 800; }}
      [data-testid="stDataFrame"] {{ border-radius: 12px; overflow: hidden; }}
      .stTabs [data-baseweb="tab-list"] {{ gap: 4px; }}
      .stTabs [data-baseweb="tab"] {{ background:{PALETTE['panel']}; border-radius: 10px 10px 0 0; padding: 6px 16px;}}
    </style>
    """,
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------------- data layer
@st.cache_data(show_spinner=False)
def compute_pipeline(refund_fix: bool):
    rows = load_raw_csv(RAW)
    cleaned, quar = clean_rows(rows, apply_refund_window_fix=refund_fix)
    results, halt = run_expectations(cleaned)
    return rows, cleaned, quar, results, halt


def latest_manifest() -> Path | None:
    files = sorted(MAN_DIR.glob("manifest_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def kpi(col, label, value, sub="", color=None):
    color = color or PALETTE["ink"]
    col.markdown(
        f"<div class='kpi'><div class='label'>{label}</div>"
        f"<div class='value' style='color:{color}'>{value}</div>"
        f"<div class='sub'>{sub}</div></div>",
        unsafe_allow_html=True,
    )


def run_cmd(args: list[str]):
    env = dict(os.environ)
    env.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
    proc = subprocess.run(
        [sys.executable, *args], cwd=str(ROOT), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


HAS_KEY = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))

# ----------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("### ⚙️ Điều khiển")
    refund_fix = st.toggle("Áp dụng refund-window fix (14→7)", value=True,
                           help="Tắt = chế độ inject corruption (Sprint 3): refund stale 14 ngày sẽ lọt.")
    st.caption(f"Allowlist ({len(ALLOWED_DOC_IDS)}): " + ", ".join(sorted(ALLOWED_DOC_IDS)))
    st.caption(f"HR cutoff version: `{HR_LEAVE_MIN_EFFECTIVE_DATE}`")
    st.divider()
    st.markdown("**Gemini API**")
    if HAS_KEY:
        st.success("GEMINI_API_KEY đã nạp", icon="✅")
    else:
        st.warning("Chưa có GEMINI_API_KEY trong .env — embed/grading/query bị khoá.", icon="⚠️")
    st.divider()
    st.markdown("**Pipeline actions**")
    run_clicked = st.button("▶️ Chạy pipeline (embed)", use_container_width=True, disabled=not HAS_KEY, type="primary")
    grade_clicked = st.button("🎯 Chạy grading (10 câu)", use_container_width=True, disabled=not HAS_KEY)
    if run_clicked:
        with st.spinner("Đang ingest → clean → validate → embed…"):
            rc, out = run_cmd(["etl_pipeline.py", "run"])
        st.session_state["run_log"] = out
        st.session_state["run_rc"] = rc
        compute_pipeline.clear()
    if grade_clicked:
        with st.spinner("Đang chấm 10 câu grading…"):
            rc, out = run_cmd(["grading_run.py", "--out", "artifacts/eval/grading_run.jsonl"])
        st.session_state["grade_log"] = out
        st.session_state["grade_rc"] = rc

rows, cleaned, quar, results, halt = compute_pipeline(refund_fix)

# ----------------------------------------------------------------------------- hero
mode = "INJECT (no refund-fix)" if not refund_fix else "CHUẨN (refund-fix ON)"
st.markdown(
    f"<div class='hero'><h1>🛰️ Day 10 — Data Pipeline & Observability</h1>"
    f"<p>ingest → clean → validate → embed (Gemini) → serve · Chế độ hiện tại: <b>{mode}</b> · "
    f"nguồn: <code>data/raw/policy_export_dirty.csv</code></p></div>",
    unsafe_allow_html=True,
)

# ----------------------------------------------------------------------------- KPI row
c1, c2, c3, c4, c5 = st.columns(5)
kpi(c1, "Raw records", len(rows), "export thô từ 5+ nguồn")
kpi(c2, "Cleaned", len(cleaned), f"{len(cleaned)/max(len(rows),1)*100:.0f}% pass", PALETTE["ok"])
kpi(c3, "Quarantine", len(quar), f"{len(quar)/max(len(rows),1)*100:.0f}% loại", PALETTE["warn"])
n_halt_fail = sum(1 for r in results if not r.passed and r.severity == "halt")
kpi(c4, "Expectation HALT", "FAIL" if halt else "PASS",
    f"{n_halt_fail} halt-rule fail", PALETTE["halt"] if halt else PALETTE["ok"])
kpi(c5, "Coverage nguồn", f"{len({r['doc_id'] for r in cleaned})}/{len(ALLOWED_DOC_IDS)}",
    "doc_id allowlist", PALETTE["brand"])

st.markdown(
    f"<p class='flow' style='margin-top:10px'>Luồng: "
    f"<b>{len(rows)}</b> raw → <b>{len(cleaned)}</b> cleaned → embed Gemini "
    f"<code>{os.environ.get('EMBEDDING_MODEL','text-embedding-004')}</code> → Chroma "
    f"<code>{os.environ.get('CHROMA_COLLECTION','day10_kb')}</code></p>",
    unsafe_allow_html=True,
)

tab_clean, tab_exp, tab_grade, tab_query, tab_fresh = st.tabs(
    ["🧹 Cleaning & Quarantine", "✅ Expectations", "📊 Eval & Grading", "🔍 Live Query", "📡 Freshness"]
)

# ----------------------------------------------------------------------------- tab clean
with tab_clean:
    left, right = st.columns([1, 1])
    qdf = pd.DataFrame(quar)
    with left:
        st.markdown("#### Lý do quarantine")
        if not qdf.empty:
            rc = qdf["reason"].value_counts().reset_index()
            rc.columns = ["reason", "count"]
            chart = (
                alt.Chart(rc).mark_bar(cornerRadiusEnd=6).encode(
                    x=alt.X("count:Q", title="số record"),
                    y=alt.Y("reason:N", sort="-x", title=None),
                    color=alt.Color("reason:N", legend=None, scale=alt.Scale(scheme="turbo")),
                    tooltip=["reason", "count"],
                ).properties(height=300)
            )
            st.altair_chart(chart, use_container_width=True)
    with right:
        st.markdown("#### Coverage nguồn (cleaned)")
        cdf = pd.DataFrame(cleaned)
        if not cdf.empty:
            cc = cdf["doc_id"].value_counts().reset_index()
            cc.columns = ["doc_id", "chunks"]
            chart2 = (
                alt.Chart(cc).mark_arc(innerRadius=55).encode(
                    theta="chunks:Q",
                    color=alt.Color("doc_id:N", scale=alt.Scale(scheme="blues"), legend=alt.Legend(orient="bottom")),
                    tooltip=["doc_id", "chunks"],
                ).properties(height=300)
            )
            st.altair_chart(chart2, use_container_width=True)

    st.markdown("#### 🧩 Rule mới của nhóm & metric impact")
    impact = pd.DataFrame(
        [
            ["R-fix allowlist access_control_sop", "0 chunk access", f"{sum(1 for r in cleaned if r['doc_id']=='access_control_sop')} chunk", "fix gq_d10_10"],
            ["R-N1 strip_noise_markers", "marker !!! / ghi chú sync", "0 marker còn lại", "lộ thêm bản trùng cho dedup"],
            ["R-N2 collapse_repeated_text", "câu/cụm lặp 5×", "gộp còn 1", "chuẩn hoá refund/padding"],
            ["R-N3 quarantine_stale_hr_2025", "bản HR 2025 lọt theo ngày", f"{sum(1 for r in quar if r.get('reason')=='stale_hr_2025_annual_leave')} chunk cách ly", "fix gq_d10_09"],
            ["R-N4 quarantine_unclear_flag", "cờ 'Nội dung không rõ ràng'", f"{sum(1 for r in quar if r.get('reason')=='flagged_unclear_content')} chunk cách ly", "low-trust → không embed"],
        ],
        columns=["Rule", "Trước", "Sau", "Tác động"],
    )
    st.dataframe(impact, use_container_width=True, hide_index=True)

    with st.expander("Xem bảng cleaned / quarantine"):
        st.markdown("**Cleaned**")
        st.dataframe(pd.DataFrame(cleaned), use_container_width=True, hide_index=True, height=240)
        st.markdown("**Quarantine**")
        st.dataframe(qdf, use_container_width=True, hide_index=True, height=240)

# ----------------------------------------------------------------------------- tab expectations
with tab_exp:
    st.markdown("#### Expectation suite (warn / halt)")
    exp_rows = []
    for r in results:
        badge = "ok" if r.passed else ("halt" if r.severity == "halt" else "warn")
        exp_rows.append({
            "Expectation": r.name,
            "Kết quả": "PASS" if r.passed else "FAIL",
            "Severity": r.severity.upper(),
            "Chi tiết": r.detail,
            "_badge": badge,
        })
    edf = pd.DataFrame(exp_rows)
    for _, row in edf.iterrows():
        pill = "ok" if row["Kết quả"] == "PASS" else ("halt" if row["Severity"] == "HALT" else "warn")
        st.markdown(
            f"<div style='display:flex;align-items:center;gap:12px;padding:8px 12px;margin-bottom:6px;"
            f"background:{PALETTE['panel']};border:1px solid #232c39;border-radius:10px'>"
            f"<span class='pill {pill}'>{row['Kết quả']}</span>"
            f"<b style='min-width:240px'>{row['Expectation']}</b>"
            f"<span class='pill {'halt' if row['Severity']=='HALT' else 'warn'}'>{row['Severity']}</span>"
            f"<span style='color:{PALETTE['muted']}'>{row['Chi tiết']}</span></div>",
            unsafe_allow_html=True,
        )
    st.info("E7 coverage / E9 dedup / E10 pydantic-schema là **halt**; E8 noise-marker là **warn**. "
            "E10 dùng pydantic validate thật (bonus +2).", icon="🧪")

# ----------------------------------------------------------------------------- tab grade
with tab_grade:
    st.markdown("#### Grading — 10 câu (gq_d10_01 … gq_d10_10)")
    gpath = EVAL_DIR / "grading_run.jsonl"
    if "grade_log" in st.session_state:
        with st.expander("Log grading gần nhất"):
            st.code(st.session_state["grade_log"][-3000:])
    if gpath.is_file():
        recs = [json.loads(l) for l in gpath.read_text(encoding="utf-8").splitlines() if l.strip()]
        passed = sum(1 for r in recs if r.get("contains_expected") and not r.get("hits_forbidden")
                     and (r.get("top1_doc_matches") in (True, None)))
        # tier
        def ok(r):
            return r.get("contains_expected") and not r.get("hits_forbidden") and (r.get("top1_doc_matches") in (True, None))
        ids = {r["id"]: ok(r) for r in recs}
        base = all(ids.get(f"gq_d10_0{i}") for i in range(1, 6))
        merit = base and all(ids.get(f"gq_d10_0{i}") for i in range(6, 9))
        dist = merit and ids.get("gq_d10_09") and ids.get("gq_d10_10")
        tier = "DISTINCTION" if dist else ("MERIT" if merit else ("PASS" if base else "—"))
        tcol = PALETTE["violet"] if dist else (PALETTE["brand"] if merit else (PALETTE["ok"] if base else PALETTE["halt"]))
        k1, k2 = st.columns([1, 3])
        kpi(k1, "Pass", f"{passed}/{len(recs)}", "contains & !forbidden & top1", PALETTE["ok"])
        k2.markdown(f"<div class='kpi'><div class='label'>Hạng đạt được</div>"
                    f"<div class='tier' style='color:{tcol}'>{tier}</div>"
                    f"<div class='sub'>Pass=01–05 · Merit=+06–08 · Distinction=+09–10</div></div>",
                    unsafe_allow_html=True)
        view = pd.DataFrame([{
            "id": r["id"],
            "top1_doc_id": r.get("top1_doc_id"),
            "contains_expected": "✅" if r.get("contains_expected") else "❌",
            "hits_forbidden": "⛔" if r.get("hits_forbidden") else "✅",
            "top1_match": {True: "✅", False: "❌", None: "—"}.get(r.get("top1_doc_matches"), "—"),
            "question": r.get("question", "")[:70] + "…",
        } for r in recs])
        st.dataframe(view, use_container_width=True, hide_index=True)
    else:
        st.warning("Chưa có `artifacts/eval/grading_run.jsonl`. Bấm **🎯 Chạy grading** ở sidebar (cần API key).")

    st.markdown("#### Before / After eval")
    files = sorted(EVAL_DIR.glob("*.csv"))
    if files:
        names = [f.name for f in files]
        pick = st.multiselect("Chọn file eval để so sánh", names, default=names[:2])
        for nm in pick:
            df = pd.read_csv(EVAL_DIR / nm)
            forb = (df.get("hits_forbidden") == "yes").sum() if "hits_forbidden" in df else 0
            okc = (df.get("contains_expected") == "yes").sum() if "contains_expected" in df else 0
            st.markdown(f"**{nm}** — contains_expected=yes: **{okc}/{len(df)}** · hits_forbidden=yes: **{forb}**")
            st.dataframe(df, use_container_width=True, hide_index=True, height=200)
    else:
        st.caption("Chạy `python eval_retrieval.py --out artifacts/eval/<tên>.csv` để tạo bằng chứng before/after.")

# ----------------------------------------------------------------------------- tab query
with tab_query:
    st.markdown("#### Truy vấn trực tiếp vector store (Chroma + Gemini)")
    if not HAS_KEY:
        st.warning("Cần GEMINI_API_KEY trong .env để embed câu truy vấn.")
    q = st.text_input("Câu hỏi", value="Nhân viên dưới 3 năm được bao nhiêu ngày phép năm?")
    topk = st.slider("top-k", 1, 8, 5)
    if st.button("Tìm", disabled=not HAS_KEY):
        try:
            import chromadb
            from transform.embedding_gemini import get_embedding_function
            client = chromadb.PersistentClient(path=os.environ.get("CHROMA_DB_PATH", str(ROOT / "chroma_db")))
            col = client.get_collection(
                name=os.environ.get("CHROMA_COLLECTION", "day10_kb"),
                embedding_function=get_embedding_function(),
            )
            res = col.query(query_texts=[q], n_results=topk)
            docs = (res.get("documents") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            for i, (d, m) in enumerate(zip(docs, metas)):
                st.markdown(
                    f"<div style='padding:10px 14px;margin-bottom:8px;background:{PALETTE['panel']};"
                    f"border-left:3px solid {PALETTE['brand']};border-radius:8px'>"
                    f"<span class='pill ok'>#{i+1}</span> "
                    f"<b>{(m or {}).get('doc_id','?')}</b> "
                    f"<span style='color:{PALETTE['muted']}'>· dist={dists[i]:.3f} · eff={(m or {}).get('effective_date','')}</span>"
                    f"<div style='margin-top:6px'>{d}</div></div>",
                    unsafe_allow_html=True,
                )
        except Exception as e:
            st.error(f"Lỗi truy vấn: {e}")

# ----------------------------------------------------------------------------- tab freshness
with tab_fresh:
    st.markdown("#### Freshness — 2 boundary (ingest snapshot + publish run)")
    man = latest_manifest()
    if man:
        status, detail = check_dual_boundary_freshness(man, sla_hours=float(os.environ.get("FRESHNESS_SLA_HOURS", "24")))
        st.caption(f"Manifest: `{man.relative_to(ROOT)}` · SLA = {detail['sla_hours']}h")
        fc1, fc2, fc3 = st.columns(3)
        for col, key, title in [(fc1, "ingest", "Ingest (snapshot nguồn)"), (fc2, "publish", "Publish (lần chạy)")]:
            b = detail[key]
            scol = {"PASS": PALETTE["ok"], "WARN": PALETTE["warn"], "FAIL": PALETTE["halt"]}[b["status"]]
            age = b["age_hours"]
            kpi(col, title, b["status"], f"age={age}h · {b['timestamp']}", scol)
        ocol = {"PASS": PALETTE["ok"], "WARN": PALETTE["warn"], "FAIL": PALETTE["halt"]}[detail["overall"]]
        kpi(fc3, "Overall", detail["overall"], "max(ingest, publish)", ocol)
        st.json(detail)
        st.info("CSV mẫu có exported_at cũ → ingest thường FAIL là HỢP LÝ (xem runbook). "
                "Publish phản ánh thời điểm chạy pipeline.", icon="🕒")
    else:
        st.warning("Chưa có manifest. Chạy pipeline trước (sidebar).")

    if "run_log" in st.session_state:
        with st.expander("Log pipeline gần nhất"):
            st.code(st.session_state["run_log"][-4000:])

st.caption("Day 10 · AI in Action — dashboard quan sát pipeline dữ liệu. Số liệu clean/quarantine/expectation tính trực tiếp từ CSV, cập nhật theo toggle ở sidebar.")
