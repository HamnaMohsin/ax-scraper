"""
Streamlit UI for AliExpress Scraper API
Base URL: http://34.10.186.46:8001
"""

import streamlit as st
import requests
import json
import time
import pandas as pd
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = "http://34.10.186.46:8001"

st.set_page_config(
    page_title="AX-Scraper Dashboard",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Styling ───────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif;
}

h1, h2, h3, .stTabs [data-baseweb="tab"] {
    font-family: 'Space Mono', monospace !important;
}

.stApp {
    background: #0d0f14;
    color: #e8e6e1;
}

.block-container {
    padding-top: 2rem;
    padding-bottom: 2rem;
}

.metric-card {
    background: #161a23;
    border: 1px solid #2a2f3d;
    border-radius: 8px;
    padding: 1.2rem 1.5rem;
    margin-bottom: 0.75rem;
}

.metric-card .label {
    font-size: 0.72rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #6b7280;
    font-family: 'Space Mono', monospace;
}

.metric-card .value {
    font-size: 1.8rem;
    font-weight: 700;
    color: #f0e6c8;
    font-family: 'Space Mono', monospace;
    line-height: 1.2;
}

.metric-card .delta-pos {
    font-size: 0.8rem;
    color: #4ade80;
}

.metric-card .delta-neg {
    font-size: 0.8rem;
    color: #f87171;
}

.tag {
    display: inline-block;
    background: #1e2433;
    border: 1px solid #3a4255;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 0.72rem;
    font-family: 'Space Mono', monospace;
    color: #a3b1cc;
    margin-right: 4px;
}

.tag-success { border-color: #14532d; color: #4ade80; background: #052e16; }
.tag-warning { border-color: #713f12; color: #fbbf24; background: #2d1a02; }
.tag-error   { border-color: #7f1d1d; color: #f87171; background: #2d0a0a; }
.tag-info    { border-color: #1e3a5f; color: #60a5fa; background: #0d1f35; }

.section-header {
    font-family: 'Space Mono', monospace;
    font-size: 0.75rem;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: #4b5563;
    border-bottom: 1px solid #1f2435;
    padding-bottom: 0.5rem;
    margin-bottom: 1rem;
    margin-top: 1.5rem;
}

.stButton > button {
    background: #f0e6c8 !important;
    color: #0d0f14 !important;
    border: none !important;
    font-family: 'Space Mono', monospace !important;
    font-size: 0.78rem !important;
    letter-spacing: 0.08em !important;
    font-weight: 700 !important;
    border-radius: 4px !important;
    padding: 0.5rem 1.2rem !important;
    transition: all 0.15s ease !important;
}

.stButton > button:hover {
    background: #ffffff !important;
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(240,230,200,0.2) !important;
}

.stButton > button[kind="secondary"] {
    background: #1e2433 !important;
    color: #a3b1cc !important;
    border: 1px solid #2a3347 !important;
}

.stTextInput > div > div > input,
.stTextArea > div > div > textarea,
.stSelectbox > div > div,
.stNumberInput > div > div > input {
    background: #161a23 !important;
    border: 1px solid #2a2f3d !important;
    color: #e8e6e1 !important;
    border-radius: 4px !important;
    font-family: 'DM Sans', sans-serif !important;
}

.stTabs [data-baseweb="tab-list"] {
    background: transparent !important;
    border-bottom: 1px solid #1f2435;
    gap: 0;
}

.stTabs [data-baseweb="tab"] {
    background: transparent !important;
    color: #4b5563 !important;
    border: none !important;
    font-size: 0.75rem !important;
    letter-spacing: 0.1em !important;
    padding: 0.5rem 1.2rem !important;
}

.stTabs [aria-selected="true"] {
    color: #f0e6c8 !important;
    border-bottom: 2px solid #f0e6c8 !important;
}

.stDataFrame {
    border: 1px solid #2a2f3d !important;
    border-radius: 6px !important;
}

.stExpander {
    border: 1px solid #2a2f3d !important;
    border-radius: 6px !important;
    background: #161a23 !important;
}

.status-running  { color: #fbbf24; }
.status-complete { color: #4ade80; }
.status-failed   { color: #f87171; }

.progress-bar-outer {
    background: #1e2433;
    border-radius: 4px;
    height: 8px;
    width: 100%;
    margin: 8px 0;
}

.progress-bar-inner {
    background: linear-gradient(90deg, #f0e6c8, #fbbf24);
    border-radius: 4px;
    height: 8px;
    transition: width 0.4s ease;
}

.json-box {
    background: #111318;
    border: 1px solid #1e2433;
    border-radius: 6px;
    padding: 1rem;
    font-family: 'Space Mono', monospace;
    font-size: 0.72rem;
    color: #a3b1cc;
    overflow-x: auto;
    max-height: 400px;
    overflow-y: auto;
}
</style>
""", unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

def api(method: str, path: str, **kwargs):
    url = BASE_URL + path
    try:
        r = getattr(requests, method)(url, timeout=30, **kwargs)
        return r.status_code, r.json() if r.content else {}
    except requests.exceptions.ConnectionError:
        return 0, {"error": "Cannot connect to API. Is the server running?"}
    except Exception as e:
        return 0, {"error": str(e)}


def status_tag(status: str) -> str:
    cls = {
        "running": "tag-warning",
        "completed": "tag-success",
        "failed": "tag-error",
        "cancelled": "tag-info",
    }.get(status, "tag")
    return f'<span class="tag {cls}">{status.upper()}</span>'


def render_json(data):
    st.markdown(
        f'<div class="json-box">{json.dumps(data, indent=2, default=str)}</div>',
        unsafe_allow_html=True,
    )


def section(title: str):
    st.markdown(f'<div class="section-header">{title}</div>', unsafe_allow_html=True)


def metric_card(label: str, value, delta=None, delta_positive=True):
    delta_html = ""
    if delta is not None:
        cls = "delta-pos" if delta_positive else "delta-neg"
        delta_html = f'<div class="{cls}">{delta}</div>'
    st.markdown(f"""
    <div class="metric-card">
        <div class="label">{label}</div>
        <div class="value">{value}</div>
        {delta_html}
    </div>
    """, unsafe_allow_html=True)


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("# 🛒 AX-Scraper")
    st.markdown('<span class="tag tag-info">v1.0</span>', unsafe_allow_html=True)
    st.markdown("---")

    # Connection check
    code, resp = api("get", "/docs")
    if code == 200:
        st.markdown('<span class="tag tag-success">● API ONLINE</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="tag tag-error">● API OFFLINE</span>', unsafe_allow_html=True)
        st.caption(f"http://34.10.186.46:8001")

    st.markdown("---")
    st.caption("Navigation")
    st.markdown("""
    - 📦 Product Pipeline
    - 🗂️ Category Scraper
    - 🏪 Store Scraper
    - 📋 View Products
    - 📁 Export Templates
    - ℹ️ Manufacturers
    """)

    st.markdown("---")
    if st.button("🔁 Refresh Page"):
        st.rerun()

# ── Main Tabs ─────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📦  PIPELINE",
    "🗂️  CATEGORY SCRAPER",
    "🏪  STORE SCRAPER",
    "📋  PRODUCTS",
    "📁  EXPORT",
    "ℹ️  MANUFACTURERS",
])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — PRODUCT PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

with tab1:
    st.markdown("## Product Pipeline")
    st.caption("Scrape AliExpress products and run them through the LLM refinement + categorisation pipeline.")

    col1, col2 = st.columns([3, 1])

    with col1:
        section("INPUT URLS")
        urls_input = st.text_area(
            "AliExpress product URLs (comma-separated or one per line)",
            placeholder="https://www.aliexpress.com/item/1234567890.html\nhttps://www.aliexpress.com/item/9876543210.html",
            height=120,
            key="pipeline_urls",
            label_visibility="collapsed",
        )

    with col2:
        section("MODE")
        mode = st.radio(
            "Pipeline mode",
            ["Full (scrape + refine + categorize)", "Scrape only"],
            label_visibility="collapsed",
        )
        st.markdown("")
        run_btn = st.button("▶  RUN PIPELINE", use_container_width=True)

    if run_btn and urls_input.strip():
        urls_clean = ",".join(
            u.strip() for u in urls_input.replace("\n", ",").split(",") if u.strip()
        )
        endpoint = "/scrape" if "Full" in mode else "/scrape-only"

        with st.spinner("Processing…"):
            code, result = api("post", endpoint, json={"urls": urls_clean})

        if code == 200:
            total   = result.get("total", 0)
            success = result.get("success", 0)
            failed  = result.get("failed", 0)

            c1, c2, c3 = st.columns(3)
            with c1: metric_card("Total URLs", total)
            with c2: metric_card("Successful", success, delta_positive=True)
            with c3: metric_card("Failed", failed, delta_positive=failed == 0)

            section("RESULTS")
            for r in result.get("results", []):
                with st.expander(
                    f"{'✅' if r.get('success') else '❌'}  {r.get('title', r.get('url', ''))[:80]}",
                    expanded=r.get("success", False),
                ):
                    if r.get("success"):
                        rc1, rc2 = st.columns(2)
                        with rc1:
                            st.markdown(f"**Product ID:** `{r.get('product_id')}`")
                            st.markdown(f"**Category:** `{r.get('assigned_category', '—')}`")
                        with rc2:
                            st.markdown(f"**Category ID:** `{r.get('category_id', '—')}`")
                            score = r.get('similarity_score')
                            if score:
                                st.markdown(f"**Similarity:** `{score:.3f}`")
                        if r.get("images"):
                            st.caption(f"{len(r['images'])} image(s) scraped")
                    else:
                        st.error(r.get("error", "Unknown error"))
        else:
            st.error(f"API Error ({code}): {resp.get('error', result)}")

    elif run_btn:
        st.warning("Please enter at least one URL.")

    # ── Step-by-step panel ─────────────────────────────────────────────────
    st.markdown("---")
    section("STEP-BY-STEP CONTROLS")
    st.caption("Run individual pipeline stages on already-scraped products.")

    sc1, sc2 = st.columns(2)

    with sc1:
        st.markdown("**Refine (LLM)**")
        refine_id = st.number_input("Product ID", min_value=1, step=1, key="refine_id")
        if st.button("▶  Refine Product", key="btn_refine"):
            code, result = api("post", f"/refine/{int(refine_id)}")
            if code == 200:
                st.success("Refined successfully!")
                render_json(result)
            else:
                st.error(f"Error {code}: {result}")

    with sc2:
        st.markdown("**Assign Category**")
        cat_id = st.number_input("Product ID", min_value=1, step=1, key="cat_id")
        if st.button("▶  Assign Category", key="btn_cat"):
            code, result = api("post", f"/assign-category/{int(cat_id)}")
            if code == 200:
                st.success("Category assigned!")
                render_json(result)
            else:
                st.error(f"Error {code}: {result}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — CATEGORY SCRAPER
# ═══════════════════════════════════════════════════════════════════════════════

with tab2:
    st.markdown("## Category Scraper")
    st.caption("Run the background category scraper (scr2.py). Returns a job ID to track progress.")

    if "cat_job_id" not in st.session_state:
        st.session_state.cat_job_id = None

    col_start, col_poll = st.columns([1, 2])

    with col_start:
        section("START JOB")
        if st.button("▶  START CATEGORY SCRAPER", use_container_width=True):
            code, result = api("post", "/run-category-scraper")
            if code == 202:
                st.session_state.cat_job_id = result.get("job_id")
                st.success(f"Job started: `{result['job_id'][:8]}…`")
            else:
                st.error(f"Error {code}: {result}")

    with col_poll:
        section("POLL JOB STATUS")
        job_id_input = st.text_input(
            "Job ID",
            value=st.session_state.cat_job_id or "",
            key="cat_job_poll",
            label_visibility="collapsed",
            placeholder="Paste job ID here…",
        )
        if st.button("🔍  Check Status", key="btn_cat_poll"):
            if job_id_input.strip():
                code, result = api("get", f"/run-category-scraper/{job_id_input.strip()}")
                if code == 200:
                    st.markdown(
                        f"**Status:** {status_tag(result.get('status', ''))}  "
                        f"Started: `{result.get('started_at', '—')[:19]}`  "
                        f"Finished: `{result.get('finished_at', '—') or '—'}`",
                        unsafe_allow_html=True,
                    )
                    if result.get("error"):
                        st.error(result["error"])
                else:
                    st.error(f"Error {code}: {result}")
            else:
                st.warning("Enter a job ID.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — STORE SCRAPER
# ═══════════════════════════════════════════════════════════════════════════════

with tab3:
    st.markdown("## Store Item-Count Scraper")
    st.caption("Scrape item counts for stores by CSV row range. Supports parallel jobs, cancellation, and retry.")

    store_tab_a, store_tab_b, store_tab_c, store_tab_d = st.tabs([
        "▶  NEW JOB", "🔍  JOB STATUS", "🔁  RETRY", "📊  SUMMARY",
    ])

    # ── New Job ────────────────────────────────────────────────────────────
    with store_tab_a:
        section("CONFIGURE JOB")

        ja, jb = st.columns(2)
        with ja:
            row_range = st.text_input("Row Range", value="1-50", placeholder="e.g. 1-200")
        with jb:
            force_rescrape = st.checkbox("Force re-scrape (overwrite existing)", value=False)

        if st.button("▶  LAUNCH STORE SCRAPE JOB", use_container_width=True):
            code, result = api("post", "/scrape-stores-by-range", json={
                "row_range": row_range,
                "force_rescrape": force_rescrape,
            })
            if code == 202:
                st.success(f"Job launched! ID: `{result['job_id']}`")
                st.session_state["last_store_job"] = result["job_id"]

                c1, c2, c3, c4 = st.columns(4)
                with c1: metric_card("Total IDs", result.get("total_ids", 0))
                with c2: metric_card("To Scrape", result.get("pending_ids", 0))
                with c3: metric_card("Skipped", result.get("skipped", 0))
                with c4: st.markdown(f'<br><span class="tag tag-warning">RUNNING</span>', unsafe_allow_html=True)

                st.info(f"Output file: `{result.get('results_file', '—')}`")
            else:
                st.error(f"Error {code}: {result}")

    # ── Job Status ─────────────────────────────────────────────────────────
    with store_tab_b:
        section("POLL JOB")
        default_job = st.session_state.get("last_store_job", "")
        poll_id = st.text_input("Job ID", value=default_job, key="store_poll_id", label_visibility="collapsed", placeholder="Job ID…")

        pc1, pc2, pc3 = st.columns(3)
        with pc1:
            poll_summary = st.button("🔍  Summary", key="btn_store_summary")
        with pc2:
            poll_full = st.button("📋  Full Results", key="btn_store_full")
        with pc3:
            cancel_job = st.button("🛑  Cancel Job", key="btn_cancel")

        if poll_id.strip():
            if cancel_job:
                code, result = api("post", f"/scrape-stores-by-range/{poll_id.strip()}/cancel")
                if code == 200:
                    st.warning(f"Cancellation requested. Completed: {result.get('completed')}")
                else:
                    st.error(f"Error {code}: {result}")

            if poll_summary:
                code, result = api("get", f"/scrape-stores-by-range/{poll_id.strip()}/summary")
                if code == 200:
                    pct = result.get("progress_pct", 0)
                    st.markdown(
                        f"**Status:** {status_tag(result.get('status', ''))}",
                        unsafe_allow_html=True,
                    )
                    st.markdown(f"""
                    <div class="progress-bar-outer">
                        <div class="progress-bar-inner" style="width:{pct}%"></div>
                    </div>
                    <small style="color:#6b7280;">{pct}% — {result.get('completed',0)} / {result.get('pending_ids',0)} completed, {result.get('remaining',0)} remaining</small>
                    """, unsafe_allow_html=True)

                    c1, c2, c3 = st.columns(3)
                    with c1: metric_card("Total IDs", result.get("total_ids", 0))
                    with c2: metric_card("Completed", result.get("completed", 0))
                    with c3: metric_card("Skipped", result.get("skipped", 0))

                    if result.get("error"):
                        st.error(result["error"])
                else:
                    st.error(f"Error {code}: {result}")

            if poll_full:
                with st.spinner("Loading full results…"):
                    code, result = api("get", f"/scrape-stores-by-range/{poll_id.strip()}")
                if code == 200:
                    st.markdown(f"**Status:** {status_tag(result.get('status', ''))}", unsafe_allow_html=True)
                    rows = result.get("results", [])
                    if rows:
                        df = pd.DataFrame(rows)
                        st.dataframe(df, use_container_width=True)
                    else:
                        st.info("No results yet.")
                else:
                    st.error(f"Error {code}: {result}")

    # ── Retry ──────────────────────────────────────────────────────────────
    with store_tab_c:
        section("RETRY BY SOURCE")
        source = st.selectbox("Source type", ["unknown", "redirect", "page_error", "exception"], key="retry_source")
        if st.button("🔁  Retry by Source"):
            code, result = api("post", f"/retry-stores-by-source/{source}")
            if code == 202:
                st.success(f"Retry job started: `{result['job_id']}`")
                st.session_state["last_store_job"] = result["job_id"]
                st.info(f"Retrying {result.get('total_ids', 0)} stores with source=`{source}`")
            else:
                st.error(f"Error {code}: {result}")

        section("RETRY BY ERROR KEYWORD")
        keyword = st.text_input("Error keyword", placeholder="e.g. hard_timeout_3min, Redirected away", key="retry_keyword")
        if st.button("🔁  Retry by Error Keyword"):
            if keyword.strip():
                code, result = api("post", "/retry-stores-by-error", params={"keyword": keyword.strip()})
                if code == 202:
                    st.success(f"Retry job started: `{result['job_id']}`")
                    st.session_state["last_store_job"] = result["job_id"]
                    st.info(f"Retrying {result.get('total_ids', 0)} stores matching `{keyword}`")
                else:
                    st.error(f"Error {code}: {result}")
            else:
                st.warning("Enter a keyword.")

    # ── Summary ────────────────────────────────────────────────────────────
    with store_tab_d:
        section("AGGREGATE RESULTS SUMMARY")
        if st.button("📊  Load Summary", use_container_width=True):
            code, result = api("get", "/store-results/summary")
            if code == 200:
                c1, c2, c3 = st.columns(3)
                with c1: metric_card("Total Stores", result.get("total", 0))
                with c2: metric_card("Successful", result.get("successful", 0))
                with c3: metric_card("With Errors", result.get("with_errors", 0))

                section("SOURCE BREAKDOWN")
                breakdown = result.get("breakdown", {})
                if breakdown:
                    df_bd = pd.DataFrame(list(breakdown.items()), columns=["Source", "Count"])
                    st.dataframe(df_bd, use_container_width=True, hide_index=True)

                section("FILES")
                files = result.get("files", [])
                if files:
                    df_f = pd.DataFrame(files)
                    st.dataframe(df_f, use_container_width=True, hide_index=True)
            else:
                st.error(f"Error {code}: {result}")

        st.markdown("---")
        section("MERGE ALL JOB FILES")
        st.caption("Merge all `store_results_*.json` per-job files into `store_results.json`.")
        if st.button("🔀  Merge Store Results"):
            code, result = api("post", "/merge-store-results")
            if code == 200:
                st.success(f"Merged {result.get('total_merged', 0)} entries into `store_results.json`")
                st.info(result.get("message", ""))
                render_json(result.get("files_read", []))
            else:
                st.error(f"Error {code}: {result}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — VIEW PRODUCTS
# ═══════════════════════════════════════════════════════════════════════════════

with tab4:
    st.markdown("## Products")

    prod_tab_a, prod_tab_b, prod_tab_c = st.tabs(["📋  FULL LIST", "🔍  LOOKUP BY ID", "🗑️  DELETE"])

    with prod_tab_a:
        section("LIST PRODUCTS")
        lc1, lc2, lc3 = st.columns([1, 1, 2])
        with lc1:
            limit = st.number_input("Limit", value=20, min_value=1, max_value=200, step=10)
        with lc2:
            offset = st.number_input("Offset", value=0, min_value=0, step=20)
        with lc3:
            view_mode = st.selectbox("View", ["Full (with refinement)", "Fetched only", "Refined only"])

        if st.button("🔍  Load Products", use_container_width=True):
            endpoint_map = {
                "Full (with refinement)": "/products",
                "Fetched only":           "/products/fetched",
                "Refined only":           "/products/refined",
            }
            ep = endpoint_map[view_mode]
            code, result = api("get", ep, params={"limit": limit, "offset": offset})
            if code == 200 and result:
                df = pd.json_normalize(result)
                # Truncate long text columns for display
                for col in ["description", "enhanced_description", "description_marketing"]:
                    if col in df.columns:
                        df[col] = df[col].astype(str).str[:120] + "…"
                st.dataframe(df, use_container_width=True)
                st.caption(f"Showing {len(result)} products (offset={offset})")
            elif code == 200:
                st.info("No products found.")
            else:
                st.error(f"Error {code}: {result}")

    with prod_tab_b:
        section("LOOKUP SINGLE PRODUCT")
        lookup_id = st.number_input("Product ID", min_value=1, step=1, key="lookup_id")

        lk1, lk2 = st.columns(2)
        with lk1:
            if st.button("📦  Get Full Product"):
                code, result = api("get", f"/products/{int(lookup_id)}")
                if code == 200:
                    render_json(result)
                else:
                    st.error(f"Error {code}: {result}")
        with lk2:
            if st.button("🔧  Get Refined Data"):
                code, result = api("get", f"/products/refined/{int(lookup_id)}")
                if code == 200:
                    render_json(result)
                else:
                    st.error(f"Error {code}: {result}")

    with prod_tab_c:
        section("DELETE PRODUCT")
        st.warning("⚠️ This will cascade-delete all related records (refined, category).")
        del_id = st.number_input("Product ID to delete", min_value=1, step=1, key="del_id")
        confirm = st.checkbox("I understand this is irreversible")
        if st.button("🗑️  DELETE", key="btn_delete", type="secondary"):
            if confirm:
                code, result = api("delete", f"/products/fetched/{int(del_id)}")
                if code == 200:
                    st.success(result.get("message", "Deleted."))
                else:
                    st.error(f"Error {code}: {result}")
            else:
                st.warning("Check the confirmation box first.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — EXPORT TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════════

with tab5:
    st.markdown("## Export Templates")
    st.caption("Export categorised products to per-category `.xlsm` files.")

    ec1, ec2 = st.columns([2, 1])
    with ec1:
        only_new = st.toggle("Incremental mode (only export new/unexported products)", value=False)
        st.caption(
            "**Incremental** appends only products not yet exported.  \n"
            "**Full** rebuilds all category files from scratch."
        )
    with ec2:
        export_btn = st.button("📤  RUN EXPORT", use_container_width=True)

    if export_btn:
        with st.spinner("Exporting — this may take a moment…"):
            code, result = api("post", "/export-templates", params={"only_new": str(only_new).lower()})

        if code == 200:
            c1, c2, c3 = st.columns(3)
            with c1: metric_card("Total Products", result.get("total_products", 0))
            with c2: metric_card("Categories", result.get("total_categories", 0))
            with c3: metric_card("Mode", "Incremental" if result.get("mode") == "incremental" else "Full")

            st.info(f"Output directory: `{result.get('output_dir', '—')}`")

            section("FILES WRITTEN")
            for f in result.get("files", []):
                with st.expander(f"📂  {f.get('category_name', '—')} ({f.get('product_count', 0)} products)"):
                    st.caption(f"File: `{f.get('file', '—')}`")
                    products = f.get("products", [])
                    if products:
                        df = pd.DataFrame(products)
                        st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.error(f"Error {code}: {result}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 6 — MANUFACTURERS
# ═══════════════════════════════════════════════════════════════════════════════

with tab6:
    st.markdown("## Manufacturer Info")
    st.caption("View manufacturer/store info collected during scraping.")

    mc1, mc2 = st.columns([1, 3])
    with mc1:
        mfr_limit = st.number_input("Limit", value=10, min_value=1, max_value=100, step=10, key="mfr_limit")
    with mc2:
        st.markdown("")

    if st.button("📋  Load Manufacturers", use_container_width=True):
        code, result = api("get", "/manufacturer", params={"limit": mfr_limit})
        if code == 200 and result:
            df = pd.DataFrame(result)
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.caption(f"{len(result)} records shown")
        elif code == 200:
            st.info("No manufacturer records found yet.")
        else:
            st.error(f"Error {code}: {result}")

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown(
    '<p style="text-align:center;color:#374151;font-family:Space Mono,monospace;font-size:0.68rem;letter-spacing:0.1em;">'
    'AX-SCRAPER DASHBOARD · http://34.10.186.46:8001'
    '</p>',
    unsafe_allow_html=True,
)
