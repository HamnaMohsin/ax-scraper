"""
streamlit_app.py — AliExpress Scraper Dashboard
Run with: streamlit run streamlit_app.py
Base API URL: http://34.10.186.46:8001
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
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500;700&display=swap');

html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif;
}
h1, h2, h3 {
    font-family: 'Space Mono', monospace !important;
}
.stButton > button {
    font-family: 'Space Mono', monospace;
    font-size: 13px;
    border-radius: 4px;
    border: 1px solid #333;
    padding: 8px 20px;
}
.metric-card {
    background: #0f0f0f;
    border: 1px solid #222;
    border-radius: 8px;
    padding: 16px 20px;
    margin-bottom: 8px;
}
.stTabs [data-baseweb="tab"] {
    font-family: 'Space Mono', monospace;
    font-size: 13px;
}
.status-badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 3px;
    font-family: 'Space Mono', monospace;
    font-size: 12px;
    font-weight: 700;
}
</style>
""", unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

def api(method: str, path: str, **kwargs):
    """Wrapper around requests that surfaces errors cleanly."""
    try:
        resp = getattr(requests, method)(f"{BASE_URL}{path}", timeout=30, **kwargs)
        resp.raise_for_status()
        return resp.json(), None
    except requests.exceptions.HTTPError as e:
        try:
            detail = e.response.json().get("detail", str(e))
        except Exception:
            detail = str(e)
        return None, detail
    except Exception as e:
        return None, str(e)


def status_badge(status: str) -> str:
    colors = {
        "running":   ("#fbbf24", "#000"),
        "completed": ("#22c55e", "#000"),
        "failed":    ("#ef4444", "#fff"),
    }
    bg, fg = colors.get(status, ("#6b7280", "#fff"))
    return f'<span class="status-badge" style="background:{bg};color:{fg}">{status.upper()}</span>'


def fmt_ts(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", ""))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🛒 AX-Scraper")
    st.caption(f"API: `{BASE_URL}`")
    st.divider()

    page = st.radio(
        "Navigation",
        [
            "🔍 Scrape Products",
            "📦 Products",
            "🗂 Category Scraper",
            "🏪 Store Item Counts",
            "📊 Store Results",
            "📤 Export Templates",
            "🏭 Manufacturers",
        ],
        label_visibility="collapsed",
    )

    st.divider()
    if st.button("🔁 Refresh page"):
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Scrape Products
# ══════════════════════════════════════════════════════════════════════════════

if page == "🔍 Scrape Products":
    st.title("🔍 Scrape Products")
    st.caption("Paste one or more AliExpress product URLs (comma-separated).")

    mode = st.radio(
        "Pipeline mode",
        ["Full pipeline (scrape → refine → categorize)", "Scrape only (raw data, no LLM)"],
        horizontal=True,
    )

    urls_input = st.text_area(
        "Product URLs",
        placeholder="https://www.aliexpress.com/item/123456789.html, ...",
        height=120,
    )

    col1, col2 = st.columns([1, 4])
    with col1:
        run = st.button("▶ Run", use_container_width=True, type="primary")

    if run:
        urls = [u.strip() for u in urls_input.split(",") if u.strip()]
        if not urls:
            st.warning("Please enter at least one URL.")
        else:
            endpoint = "/scrape" if "Full" in mode else "/scrape-only"
            with st.spinner(f"Running {len(urls)} URL(s)…"):
                data, err = api("post", endpoint, json={"urls": ",".join(urls)})
            if err:
                st.error(f"Error: {err}")
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("Total", data["total"])
                c2.metric("✅ Success", data["success"])
                c3.metric("❌ Failed", data["failed"])

                for r in data.get("results", []):
                    with st.expander(
                        f"{'✅' if r['success'] else '❌'} {r.get('url', '')[:80]}",
                        expanded=not r["success"],
                    ):
                        if r["success"]:
                            st.json({k: v for k, v in r.items() if k not in ("images",)})
                            imgs = r.get("images", [])
                            if imgs:
                                st.markdown(f"**Images ({len(imgs)}):**")
                                cols = st.columns(min(len(imgs), 5))
                                for i, img in enumerate(imgs[:5]):
                                    cols[i].image(img, use_container_width=True)
                        else:
                            st.error(r.get("error", "Unknown error"))

    st.divider()
    st.subheader("Step-by-step: Refine / Categorize")
    col_r, col_c = st.columns(2)

    with col_r:
        with st.form("refine_form"):
            pid_refine = st.number_input("Product ID to Refine", min_value=1, step=1)
            if st.form_submit_button("Refine →"):
                data, err = api("post", f"/refine/{int(pid_refine)}")
                if err:
                    st.error(err)
                else:
                    st.success("Refined!")
                    st.json(data)

    with col_c:
        with st.form("cat_form"):
            pid_cat = st.number_input("Product ID to Categorize", min_value=1, step=1)
            if st.form_submit_button("Assign Category →"):
                data, err = api("post", f"/assign-category/{int(pid_cat)}")
                if err:
                    st.error(err)
                else:
                    st.success("Categorized!")
                    st.json(data)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Products
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📦 Products":
    st.title("📦 Products")

    tabs = st.tabs(["All Products", "Fetched", "Refined", "Lookup by ID", "Delete"])

    # ── All Products ──────────────────────────────────────────────────────────
    with tabs[0]:
        col1, col2 = st.columns([3, 1])
        with col1:
            limit  = st.slider("Limit", 5, 100, 20, key="all_limit")
            offset = st.number_input("Offset", 0, step=20, key="all_offset")
        with col2:
            st.write("")
            st.write("")
            load = st.button("Load Products", key="load_all")

        if load:
            data, err = api("get", f"/products?limit={limit}&offset={offset}")
            if err:
                st.error(err)
            elif data:
                df = pd.DataFrame(data)
                display_cols = ["product_id", "title", "assigned_category", "category_id",
                                "similarity_score", "enhanced_title"]
                df_show = df[[c for c in display_cols if c in df.columns]]
                st.dataframe(df_show, use_container_width=True)
                with st.expander("Raw JSON"):
                    st.json(data)
            else:
                st.info("No products found.")

    # ── Fetched ───────────────────────────────────────────────────────────────
    with tabs[1]:
        limit_f  = st.slider("Limit", 5, 100, 20, key="f_limit")
        offset_f = st.number_input("Offset", 0, step=20, key="f_offset")
        if st.button("Load", key="load_fetched"):
            data, err = api("get", f"/products/fetched?limit={limit_f}&offset={offset_f}")
            if err:
                st.error(err)
            elif data:
                df = pd.DataFrame(data)
                st.dataframe(df[["product_id", "title", "url", "exported_at"]], use_container_width=True)
            else:
                st.info("No data.")

    # ── Refined ───────────────────────────────────────────────────────────────
    with tabs[2]:
        limit_r  = st.slider("Limit", 5, 100, 20, key="r_limit")
        offset_r = st.number_input("Offset", 0, step=20, key="r_offset")
        if st.button("Load", key="load_refined"):
            data, err = api("get", f"/products/refined?limit={limit_r}&offset={offset_r}")
            if err:
                st.error(err)
            elif data:
                df = pd.DataFrame(data)
                st.dataframe(
                    df[["product_id", "enhanced_title", "enhanced_description", "description_marketing"]],
                    use_container_width=True,
                )
            else:
                st.info("No data.")

    # ── Lookup by ID ──────────────────────────────────────────────────────────
    with tabs[3]:
        pid = st.number_input("Product ID", min_value=1, step=1, key="lookup_pid")
        lookup_type = st.radio("Data type", ["Full", "Fetched", "Refined"], horizontal=True)
        if st.button("Lookup", key="do_lookup"):
            path_map = {"Full": f"/products/{pid}", "Fetched": f"/products/fetched/{pid}", "Refined": f"/products/refined/{pid}"}
            data, err = api("get", path_map[lookup_type])
            if err:
                st.error(err)
            else:
                if lookup_type == "Full" and data.get("images"):
                    cols = st.columns(min(len(data["images"]), 5))
                    for i, img in enumerate(data["images"][:5]):
                        cols[i].image(img, use_container_width=True)
                st.json(data)

    # ── Delete ────────────────────────────────────────────────────────────────
    with tabs[4]:
        st.warning("⚠️ Deletion cascades to all related tables.")
        del_pid = st.number_input("Product ID to delete", min_value=1, step=1)
        confirm = st.checkbox("I confirm I want to delete this product")
        if st.button("🗑 Delete", type="primary", disabled=not confirm):
            data, err = api("delete", f"/products/fetched/{int(del_pid)}")
            if err:
                st.error(err)
            else:
                st.success(data.get("message", "Deleted"))


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Category Scraper
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🗂 Category Scraper":
    st.title("🗂 Category Scraper (scr2.py)")
    st.caption("Runs the AliExpress category crawler as a background job.")

    if "cat_job_id" not in st.session_state:
        st.session_state.cat_job_id = None

    if st.button("▶ Start Category Scraper", type="primary"):
        data, err = api("post", "/run-category-scraper")
        if err:
            st.error(err)
        else:
            st.session_state.cat_job_id = data["job_id"]
            st.success(f"Job started: `{data['job_id']}`")

    if st.session_state.cat_job_id:
        st.divider()
        st.subheader(f"Job: `{st.session_state.cat_job_id}`")

        col1, col2 = st.columns([1, 4])
        with col1:
            poll = st.button("🔄 Poll status")

        if poll:
            data, err = api("get", f"/run-category-scraper/{st.session_state.cat_job_id}")
            if err:
                st.error(err)
            else:
                st.markdown(status_badge(data["status"]), unsafe_allow_html=True)
                c1, c2 = st.columns(2)
                c1.markdown(f"**Started:** {fmt_ts(data.get('started_at'))}")
                c2.markdown(f"**Finished:** {fmt_ts(data.get('finished_at'))}")
                if data.get("error"):
                    st.error(f"Error: {data['error']}")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Store Item Counts
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🏪 Store Item Counts":
    st.title("🏪 Store Item Count Scraper")

    tabs = st.tabs(["Start Job", "Monitor Jobs", "Retry by Source", "Retry by Error", "Merge Results"])

    # ── Start Job ─────────────────────────────────────────────────────────────
    with tabs[0]:
        st.subheader("Scrape stores by CSV row range")

        with st.form("store_scrape_form"):
            row_range = st.text_input("Row range (e.g. 1-200)", value="1-50")
            force     = st.checkbox("Force re-scrape (overwrite existing)")
            submit    = st.form_submit_button("▶ Start Job", type="primary")

        if submit:
            payload = {"row_range": row_range, "force_rescrape": force}
            data, err = api("post", "/scrape-stores-by-range", json=payload)
            if err:
                st.error(err)
            else:
                st.success(f"Job started: `{data['job_id']}`")
                st.json(data)
                if "active_jobs" not in st.session_state:
                    st.session_state.active_jobs = []
                st.session_state.active_jobs.append(data["job_id"])

    # ── Monitor Jobs ──────────────────────────────────────────────────────────
    with tabs[1]:
        st.subheader("Monitor a job")

        if "active_jobs" not in st.session_state:
            st.session_state.active_jobs = []

        job_id_input = st.text_input(
            "Job ID",
            value=st.session_state.active_jobs[-1] if st.session_state.active_jobs else "",
        )

        col1, col2 = st.columns(2)
        with col1:
            poll_summary = st.button("📊 Poll Summary (lightweight)")
        with col2:
            poll_full = st.button("📋 Poll Full Results")

        if poll_summary and job_id_input:
            data, err = api("get", f"/scrape-stores-by-range/{job_id_input}/summary")
            if err:
                st.error(err)
            else:
                st.markdown(status_badge(data["status"]), unsafe_allow_html=True)
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Total IDs", data["total_ids"])
                c2.metric("Pending", data["pending_ids"])
                c3.metric("Completed", data["completed"])
                c4.metric("Remaining", data["remaining"])
                st.progress(data["progress_pct"] / 100, text=f"{data['progress_pct']}%")
                c5, c6 = st.columns(2)
                c5.markdown(f"**Started:** {fmt_ts(data.get('started_at'))}")
                c6.markdown(f"**Finished:** {fmt_ts(data.get('finished_at'))}")
                if data.get("error"):
                    st.error(data["error"])

        if poll_full and job_id_input:
            data, err = api("get", f"/scrape-stores-by-range/{job_id_input}")
            if err:
                st.error(err)
            else:
                st.markdown(status_badge(data["status"]), unsafe_allow_html=True)
                st.metric("Records in file", data.get("total_in_file", 0))
                results = data.get("results", [])
                if results:
                    df = pd.DataFrame(results)
                    st.dataframe(df, use_container_width=True)

    # ── Retry by Source ───────────────────────────────────────────────────────
    with tabs[2]:
        st.subheader("Retry stores by source type")
        source = st.selectbox("Source", ["unknown", "redirect", "page_error", "exception"])
        if st.button("🔁 Retry by Source", type="primary"):
            data, err = api("post", f"/retry-stores-by-source/{source}")
            if err:
                st.error(err)
            else:
                st.success(f"Retry job started: `{data['job_id']}`")
                st.json(data)

    # ── Retry by Error ────────────────────────────────────────────────────────
    with tabs[3]:
        st.subheader("Retry stores by error keyword")
        keyword = st.text_input("Error keyword (e.g. timeout, redirect, hard_timeout_3min)")
        if st.button("🔁 Retry by Error", type="primary"):
            if not keyword:
                st.warning("Enter a keyword.")
            else:
                data, err = api("post", f"/retry-stores-by-error?keyword={keyword}")
                if err:
                    st.error(err)
                else:
                    st.success(f"Retry job started: `{data['job_id']}`")
                    st.json(data)

    # ── Merge Results ─────────────────────────────────────────────────────────
    with tabs[4]:
        st.subheader("Merge all store_results_*.json → master_results.json")
        st.info("Merges per-job files into one master file. Later scraped_at wins on duplicate store_id.")
        if st.button("🔀 Merge Now", type="primary"):
            data, err = api("post", "/merge-store-results")
            if err:
                st.error(err)
            else:
                c1, c2 = st.columns(2)
                c1.metric("Total Merged", data["total_merged"])
                c2.markdown(f"**Output:** `{data['output_file']}`")
                st.subheader("Breakdown")
                if data.get("breakdown"):
                    bd = data["breakdown"]
                    st.json(bd)
                st.subheader("Files read")
                if data.get("files_read"):
                    df = pd.DataFrame(data["files_read"])
                    st.dataframe(df, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Store Results
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📊 Store Results":
    st.title("📊 Store Results Summary")

    if st.button("📊 Load Summary"):
        data, err = api("get", "/store-results/summary")
        if err:
            st.error(err)
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Total", data["total"])
            c2.metric("✅ Successful", data["successful"])
            c3.metric("❌ With Errors", data["with_errors"])

            st.subheader("Source breakdown")
            if data.get("breakdown"):
                bd_df = pd.DataFrame(
                    [{"source": k, "count": v} for k, v in data["breakdown"].items()]
                )
                st.bar_chart(bd_df.set_index("source"))

            st.subheader("Files")
            if data.get("files"):
                st.dataframe(pd.DataFrame(data["files"]), use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Export Templates
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📤 Export Templates":
    st.title("📤 Export Templates")
    st.caption("Exports categorized products to per-category `.xlsm` files.")

    mode = st.radio(
        "Export mode",
        ["Full rebuild (all products)", "Incremental (only new/unexported)"],
        horizontal=True,
    )
    only_new = "Incremental" in mode

    if st.button("📤 Export Now", type="primary"):
        with st.spinner("Exporting…"):
            data, err = api("post", f"/export-templates?only_new={str(only_new).lower()}")
        if err:
            st.error(err)
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Mode", data["mode"])
            c2.metric("Total Products", data["total_products"])
            c3.metric("Total Categories", data["total_categories"])
            st.caption(f"Output dir: `{data['output_dir']}`")

            for f in data.get("files", []):
                with st.expander(f"📁 {f['category_name']} (ID: {f['category_id']}) — {f['product_count']} products"):
                    st.caption(f"`{f['file']}`")
                    if f.get("products"):
                        df = pd.DataFrame(f["products"])
                        st.dataframe(df, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: Manufacturers
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🏭 Manufacturers":
    st.title("🏭 Manufacturer Info")
    limit_m = st.slider("Limit", 5, 100, 10)

    if st.button("Load Manufacturers"):
        data, err = api("get", f"/manufacturer?limit={limit_m}")
        if err:
            st.error(err)
        elif data:
            df = pd.DataFrame(data)
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No manufacturer data found.")
