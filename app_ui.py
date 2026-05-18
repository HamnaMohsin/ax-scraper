"""
dashboard.py — Streamlit UI for AX-Scraper API
Run: streamlit run dashboard.py
"""

import streamlit as st
import requests
import json
import pandas as pd
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────

API_BASE = st.sidebar.text_input("API Base URL", value="http://34.10.186.46:8001")

st.set_page_config(
    page_title="AX-Scraper Dashboard",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Syne:wght@400;600;800&display=swap');

    html, body, [class*="css"] {
        font-family: 'Syne', sans-serif;
    }
    .stApp {
        background: #0d0d0d;
        color: #e8e8e8;
    }
    .block-container {
        padding-top: 2rem;
        max-width: 1400px;
    }
    h1, h2, h3 {
        font-family: 'Syne', sans-serif !important;
        font-weight: 800 !important;
        letter-spacing: -0.03em;
    }
    .metric-card {
        background: #1a1a1a;
        border: 1px solid #2a2a2a;
        border-radius: 12px;
        padding: 1.2rem 1.5rem;
        margin-bottom: 1rem;
    }
    .metric-card .label {
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: #888;
        margin-bottom: 0.3rem;
    }
    .metric-card .value {
        font-family: 'JetBrains Mono', monospace;
        font-size: 1.8rem;
        font-weight: 600;
        color: #f0f0f0;
    }
    .tag-success {
        background: #0f3d1e;
        color: #4ade80;
        padding: 2px 10px;
        border-radius: 20px;
        font-size: 0.75rem;
        font-family: 'JetBrains Mono', monospace;
        font-weight: 600;
    }
    .tag-fail {
        background: #3d0f0f;
        color: #f87171;
        padding: 2px 10px;
        border-radius: 20px;
        font-size: 0.75rem;
        font-family: 'JetBrains Mono', monospace;
        font-weight: 600;
    }
    .tag-warn {
        background: #3d2f0f;
        color: #fbbf24;
        padding: 2px 10px;
        border-radius: 20px;
        font-size: 0.75rem;
        font-family: 'JetBrains Mono', monospace;
        font-weight: 600;
    }
    .stButton > button {
        background: #ff4d00 !important;
        color: white !important;
        border: none !important;
        border-radius: 8px !important;
        font-family: 'Syne', sans-serif !important;
        font-weight: 600 !important;
        letter-spacing: 0.04em !important;
        padding: 0.5rem 1.5rem !important;
        transition: all 0.2s !important;
    }
    .stButton > button:hover {
        background: #ff6a2a !important;
        transform: translateY(-1px) !important;
    }
    .stTextInput > div > div > input,
    .stTextArea > div > div > textarea,
    .stNumberInput > div > div > input {
        background: #1a1a1a !important;
        border: 1px solid #2a2a2a !important;
        color: #e8e8e8 !important;
        border-radius: 8px !important;
        font-family: 'JetBrains Mono', monospace !important;
    }
    .stSelectbox > div > div {
        background: #1a1a1a !important;
        border: 1px solid #2a2a2a !important;
        color: #e8e8e8 !important;
        border-radius: 8px !important;
    }
    .stDataFrame, .stTable {
        background: #1a1a1a !important;
    }
    .stExpander {
        background: #1a1a1a !important;
        border: 1px solid #2a2a2a !important;
        border-radius: 10px !important;
    }
    .stSidebar {
        background: #111111 !important;
        border-right: 1px solid #1e1e1e !important;
    }
    .stTabs [data-baseweb="tab-list"] {
        background: #1a1a1a;
        border-radius: 10px;
        padding: 4px;
        gap: 4px;
    }
    .stTabs [data-baseweb="tab"] {
        background: transparent !important;
        color: #888 !important;
        border-radius: 8px !important;
        font-family: 'Syne', sans-serif !important;
        font-weight: 600 !important;
    }
    .stTabs [aria-selected="true"] {
        background: #ff4d00 !important;
        color: white !important;
    }
    .json-box {
        background: #111;
        border: 1px solid #222;
        border-radius: 10px;
        padding: 1rem;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.8rem;
        color: #a8d8a8;
        overflow-x: auto;
        max-height: 400px;
        overflow-y: auto;
    }
    .section-divider {
        border: none;
        border-top: 1px solid #1e1e1e;
        margin: 1.5rem 0;
    }
    div[data-testid="stSidebarNav"] { display: none; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def api(method: str, path: str, **kwargs):
    url = f"{API_BASE}{path}"
    try:
        resp = getattr(requests, method)(url, timeout=300, **kwargs)
        return resp.status_code, resp.json()
    except requests.exceptions.ConnectionError:
        return 0, {"error": f"Cannot connect to {API_BASE}"}
    except Exception as e:
        return 0, {"error": str(e)}


def show_json(data):
    st.markdown(
        f'<div class="json-box">{json.dumps(data, indent=2, ensure_ascii=False)}</div>',
        unsafe_allow_html=True,
    )


def status_badge(success: bool):
    if success:
        return '<span class="tag-success">✓ success</span>'
    return '<span class="tag-fail">✗ failed</span>'


# ── Sidebar nav ───────────────────────────────────────────────────────────────

st.sidebar.markdown("## 🛒 AX-Scraper")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "Navigation",
    [
        "🏠 Overview",
        "🔍 Scrape Product",
        "📦 Products",
        "✨ Refine & Categorize",
        "🌍 Translations",
        "🏪 Manufacturers",
        "📊 Store Scraper",
        "📤 Export",
    ],
    label_visibility="collapsed",
)

st.sidebar.markdown("---")
st.sidebar.markdown(
    "<div style='font-size:0.7rem;color:#555;font-family:JetBrains Mono,monospace;'>AX-Scraper v1.0</div>",
    unsafe_allow_html=True,
)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════

if page == "🏠 Overview":
    st.markdown("# AX-Scraper Dashboard")
    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    # Fetch quick stats
    col1, col2, col3, col4 = st.columns(4)

    status_code, fetched = api("get", "/products/fetched?limit=1000")
    total_fetched = len(fetched) if isinstance(fetched, list) else 0

    _, refined = api("get", "/products/refined?limit=1000")
    total_refined = len(refined) if isinstance(refined, list) else 0

    _, translations = api("get", "/products/translations?limit=1000")
    total_translated = len(translations) if isinstance(translations, list) else 0

    _, manufacturers = api("get", "/manufacturer?limit=1000")
    total_mfr = len(manufacturers) if isinstance(manufacturers, list) else 0

    with col1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="label">Products Fetched</div>
            <div class="value">{total_fetched}</div>
        </div>""", unsafe_allow_html=True)

    with col2:
        st.markdown(f"""
        <div class="metric-card">
            <div class="label">Products Refined</div>
            <div class="value">{total_refined}</div>
        </div>""", unsafe_allow_html=True)

    with col3:
        st.markdown(f"""
        <div class="metric-card">
            <div class="label">Translated</div>
            <div class="value">{total_translated}</div>
        </div>""", unsafe_allow_html=True)

    with col4:
        st.markdown(f"""
        <div class="metric-card">
            <div class="label">Manufacturers</div>
            <div class="value">{total_mfr}</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)
    st.markdown("### Recent Products")

    if isinstance(fetched, list) and fetched:
        df = pd.DataFrame([
            {
                "product_id":  p.get("product_id"),
                "title":       (p.get("title") or "")[:60] + "…" if p.get("title") else "—",
                "images":      len(p.get("images") or []),
                "specs":       len(p.get("specifications") or {}),
                "exported_at": p.get("exported_at") or "—",
            }
            for p in fetched[-20:][::-1]
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No products scraped yet.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: SCRAPE PRODUCT
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🔍 Scrape Product":
    st.markdown("# Scrape Product")
    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["Scrape Only", "Full Pipeline (Scrape + Refine + Categorize)"])

    with tab1:
        st.markdown("##### Paste one or more AliExpress URLs (comma-separated)")
        urls_input = st.text_area("URLs", placeholder="https://www.aliexpress.com/item/123456789.html", height=100)

        if st.button("🚀 Scrape Only", key="scrape_only_btn"):
            if not urls_input.strip():
                st.warning("Please enter at least one URL.")
            else:
                with st.spinner("Scraping… this may take a minute"):
                    code, result = api("post", "/scrape-only", json={"urls": urls_input.strip()})

                if code == 200:
                    st.success(f"Done — {result.get('success', 0)} succeeded, {result.get('failed', 0)} failed")
                    for r in result.get("results", []):
                        with st.expander(f"{'✅' if r.get('success') else '❌'} {r.get('url', '')[:80]}"):
                            if r.get("success"):
                                c1, c2 = st.columns(2)
                                c1.markdown(f"**Product ID:** `{r.get('product_id')}`")
                                c1.markdown(f"**Title:** {r.get('title', '—')}")
                                c1.markdown(f"**Images:** {len(r.get('images') or [])}")
                                c2.markdown(f"**Specs:** {len(r.get('specifications') or {})} fields")
                                if r.get("specifications"):
                                    st.markdown("**Specifications:**")
                                    spec_df = pd.DataFrame(
                                        r["specifications"].items(), columns=["Key", "Value"]
                                    )
                                    st.dataframe(spec_df, use_container_width=True, hide_index=True)
                                if r.get("store_info"):
                                    st.markdown("**Store Info:**")
                                    show_json(r["store_info"])
                            else:
                                st.error(r.get("error", "Unknown error"))
                else:
                    st.error(f"API error {code}")
                    show_json(result)

    with tab2:
        st.markdown("##### Full pipeline: scrape → refine → categorize → save")
        urls_full = st.text_area("URLs", placeholder="https://www.aliexpress.com/item/123456789.html", height=100, key="full_urls")

        if st.button("🚀 Full Pipeline", key="scrape_full_btn"):
            if not urls_full.strip():
                st.warning("Please enter at least one URL.")
            else:
                with st.spinner("Running full pipeline… this may take several minutes"):
                    code, result = api("post", "/scrape", json={"urls": urls_full.strip()})

                if code == 200:
                    st.success(f"Done — {result.get('success', 0)} succeeded, {result.get('failed', 0)} failed")
                    for r in result.get("results", []):
                        with st.expander(f"{'✅' if r.get('success') else '❌'} {r.get('url', '')[:80]}"):
                            if r.get("success"):
                                st.markdown(f"**Product ID:** `{r.get('product_id')}`")
                                st.markdown(f"**Original Title:** {r.get('original_title', '—')}")
                                st.markdown(f"**Enhanced Title:** {r.get('enhanced_title', '—')}")
                                st.markdown(f"**Category:** {r.get('assigned_category', '—')}")
                                st.markdown(f"**Category ID:** `{r.get('category_id', '—')}`")
                                st.markdown(f"**Similarity:** `{r.get('similarity_score', '—')}`")
                            else:
                                st.error(r.get("error", "Unknown error"))
                else:
                    st.error(f"API error {code}")
                    show_json(result)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: PRODUCTS
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📦 Products":
    st.markdown("# Products")
    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    tab1, tab2, tab3 = st.tabs(["Fetched", "Refined", "Full View"])

    with tab1:
        col1, col2 = st.columns([1, 4])
        limit = col1.number_input("Limit", min_value=1, max_value=500, value=50, key="f_limit")
        offset = col1.number_input("Offset", min_value=0, value=0, key="f_offset")

        if st.button("Load", key="load_fetched"):
            code, data = api("get", f"/products/fetched?limit={limit}&offset={offset}")
            if code == 200 and data:
                df = pd.DataFrame([
                    {
                        "product_id":  p.get("product_id"),
                        "title":       (p.get("title") or "")[:70],
                        "images":      len(p.get("images") or []),
                        "specs":       len(p.get("specifications") or {}),
                        "exported_at": p.get("exported_at") or "—",
                    }
                    for p in data
                ])
                st.dataframe(df, use_container_width=True, hide_index=True)

                st.markdown("##### View a product")
                pid = st.number_input("Product ID", min_value=0, key="view_pid")
                if st.button("Fetch", key="fetch_pid"):
                    c2, p = api("get", f"/products/fetched/{pid}")
                    if c2 == 200:
                        st.markdown(f"**Title:** {p.get('title')}")
                        st.markdown(f"**Description:** {(p.get('description') or '')[:300]}…")
                        if p.get("specifications"):
                            st.markdown("**Specifications:**")
                            spec_df = pd.DataFrame(p["specifications"].items(), columns=["Key", "Value"])
                            st.dataframe(spec_df, use_container_width=True, hide_index=True)
                        if p.get("images"):
                            st.markdown("**Images:**")
                            img_cols = st.columns(min(4, len(p["images"])))
                            for i, img_url in enumerate(p["images"][:8]):
                                img_cols[i % 4].image(img_url, use_column_width=True)
                    else:
                        st.error(f"Not found ({c2})")
            else:
                st.error(f"Error {code}")
                show_json(data)

    with tab2:
        limit_r = st.number_input("Limit", min_value=1, max_value=500, value=50, key="r_limit")
        offset_r = st.number_input("Offset", min_value=0, value=0, key="r_offset")

        if st.button("Load", key="load_refined"):
            code, data = api("get", f"/products/refined?limit={limit_r}&offset={offset_r}")
            if code == 200 and data:
                df = pd.DataFrame([
                    {
                        "product_id":   r.get("product_id"),
                        "enhanced_title": (r.get("enhanced_title") or "")[:70],
                        "has_marketing": bool(r.get("description_marketing")),
                        "has_specs":     bool(r.get("specifications")),
                    }
                    for r in data
                ])
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.error(f"Error {code}")

    with tab3:
        limit_fv = st.number_input("Limit", min_value=1, max_value=100, value=20, key="fv_limit")
        if st.button("Load Full", key="load_full"):
            code, data = api("get", f"/products?limit={limit_fv}&offset=0")
            if code == 200 and data:
                for p in data:
                    with st.expander(f"🏷️ {p.get('product_id')} — {(p.get('title') or '—')[:60]}"):
                        c1, c2 = st.columns(2)
                        c1.markdown(f"**Enhanced Title:** {p.get('enhanced_title') or '—'}")
                        c1.markdown(f"**Category:** {p.get('assigned_category') or '—'}")
                        c1.markdown(f"**Category ID:** `{p.get('category_id') or '—'}`")
                        c2.markdown(f"**Similarity:** `{p.get('similarity_score') or '—'}`")
                        c2.markdown(f"**Exported At:** {p.get('exported_at') or '—'}")
            else:
                st.error(f"Error {code}")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: REFINE & CATEGORIZE
# ══════════════════════════════════════════════════════════════════════════════

elif page == "✨ Refine & Categorize":
    st.markdown("# Refine & Categorize")
    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["Refine", "Assign Category"])

    with tab1:
        st.markdown("Run LLM refinement on a scraped product.")
        pid = st.number_input("Product ID", min_value=0, key="refine_pid")
        if st.button("✨ Refine", key="refine_btn"):
            with st.spinner("Refining…"):
                code, result = api("post", f"/refine/{pid}")
            if code == 200:
                st.success("Refined successfully")
                show_json(result)
            else:
                st.error(f"Error {code}")
                show_json(result)

    with tab2:
        st.markdown("Assign Octopia category via embedding similarity.")
        pid2 = st.number_input("Product ID", min_value=0, key="cat_pid")
        if st.button("🗂️ Assign Category", key="cat_btn"):
            with st.spinner("Categorizing…"):
                code, result = api("post", f"/assign-category/{pid2}")
            if code == 200:
                st.success("Categorized successfully")
                col1, col2 = st.columns(2)
                col1.markdown(f"**LLM Predicted:** {result.get('llm_predicted_category')}")
                col1.markdown(f"**Assigned:** {result.get('assigned_category')}")
                col2.markdown(f"**Category ID:** `{result.get('category_id')}`")
                col2.markdown(f"**Similarity:** `{result.get('similarity_score')}`")
            else:
                st.error(f"Error {code}")
                show_json(result)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: TRANSLATIONS
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🌍 Translations":
    st.markdown("# Translations")
    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["Translate Product", "View Translations"])

    LANGUAGES = ["romanian", "german", "portuguese", "finnish", "french"]
    LANG_FLAGS = {
        "romanian":   "🇷🇴",
        "german":     "🇩🇪",
        "portuguese": "🇵🇹",
        "finnish":    "🇫🇮",
        "french":     "🇫🇷",
    }

    with tab1:
        st.markdown("Translates `enhanced_title`, `enhanced_description`, and `specifications` into 5 languages.")
        pid = st.number_input("Product ID", min_value=0, key="translate_pid")

        if st.button("🌍 Translate", key="translate_btn"):
            with st.spinner("Translating via GPT-4o… may take ~30s"):
                code, result = api("post", f"/translate/{pid}")

            if code == 200:
                cached = result.get("cached", False)
                st.success(f"{'⚡ Cached result' if cached else '✅ Freshly translated'}")

                for lang in LANGUAGES:
                    lang_data = result.get("translations", {}).get(lang, {})
                    with st.expander(f"{LANG_FLAGS.get(lang, '')} {lang.capitalize()}"):
                        st.markdown(f"**Title:** {lang_data.get('title', '—')}")
                        st.markdown(f"**Description:** {lang_data.get('description', '—')}")
                        specs = lang_data.get("specifications")
                        if specs:
                            st.markdown("**Specifications:**")
                            spec_df = pd.DataFrame(specs.items(), columns=["Key", "Value"])
                            st.dataframe(spec_df, use_container_width=True, hide_index=True)
                        else:
                            st.markdown("**Specifications:** —")
            else:
                st.error(f"Error {code}")
                show_json(result)

    with tab2:
        col1, col2 = st.columns([1, 3])
        limit = col1.number_input("Limit", min_value=1, max_value=200, value=20, key="trans_limit")

        view_mode = st.radio("Display mode", ["Table", "Per-product detail"], horizontal=True)

        if st.button("Load Translations", key="load_trans"):
            code, data = api("get", f"/products/translations?limit={limit}")

            if code == 200 and isinstance(data, list) and data:
                if view_mode == "Table":
                    rows = []
                    for t in data:
                        rows.append({
                            "product_id":          t.get("product_id"),
                            "title_romanian":       (t.get("title_romanian") or "")[:50],
                            "title_german":         (t.get("title_german") or "")[:50],
                            "title_portuguese":     (t.get("title_portuguese") or "")[:50],
                            "title_finnish":        (t.get("title_finnish") or "")[:50],
                            "title_french":         (t.get("title_french") or "")[:50],
                            "has_specs_romanian":   bool(t.get("specifications_romanian")),
                            "has_specs_german":     bool(t.get("specifications_german")),
                        })
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

                else:
                    for t in data:
                        with st.expander(f"Product `{t.get('product_id')}`"):
                            for lang in LANGUAGES:
                                st.markdown(f"**{LANG_FLAGS.get(lang,'')} {lang.capitalize()}**")
                                c1, c2 = st.columns(2)
                                c1.markdown(f"Title: {t.get(f'title_{lang}') or '—'}")
                                specs = t.get(f"specifications_{lang}")
                                if specs:
                                    spec_df = pd.DataFrame(specs.items(), columns=["Key", "Value"])
                                    c2.dataframe(spec_df, use_container_width=True, hide_index=True)
                                else:
                                    c2.markdown("Specs: —")
                                st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)
            elif code == 200:
                st.info("No translations found yet.")
            else:
                st.error(f"Error {code}")
                show_json(data)

        st.markdown("##### Lookup single product")
        pid_lookup = st.number_input("Product ID", min_value=0, key="trans_lookup_pid")
        if st.button("Fetch Translation", key="fetch_trans"):
            code, t = api("get", f"/products/translations/{pid_lookup}")
            if code == 200:
                for lang in LANGUAGES:
                    with st.expander(f"{LANG_FLAGS.get(lang,'')} {lang.capitalize()}"):
                        st.markdown(f"**Title:** {t.get(f'title_{lang}') or '—'}")
                        st.markdown(f"**Description:** {(t.get(f'description_{lang}') or '—')[:400]}")
                        specs = t.get(f"specifications_{lang}")
                        if specs:
                            st.markdown("**Specifications:**")
                            spec_df = pd.DataFrame(specs.items(), columns=["Key", "Value"])
                            st.dataframe(spec_df, use_container_width=True, hide_index=True)
            else:
                st.error(f"Error {code}")
                show_json(t)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: MANUFACTURERS
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🏪 Manufacturers":
    st.markdown("# Manufacturer Info")
    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    limit = st.number_input("Limit", min_value=1, max_value=500, value=50)

    if st.button("Load Manufacturers"):
        code, data = api("get", f"/manufacturer?limit={limit}")
        if code == 200 and data:
            df = pd.DataFrame(data)
            st.dataframe(df, use_container_width=True, hide_index=True)
        elif code == 200:
            st.info("No manufacturer data found.")
        else:
            st.error(f"Error {code}")
            show_json(data)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: STORE SCRAPER
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📊 Store Scraper":
    st.markdown("# Store Scraper")
    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    tab1, tab2, tab3 = st.tabs(["Launch Job", "Poll Job", "Merge Results"])

    with tab1:
        st.markdown("Scrape store item counts by CSV row range.")
        col1, col2 = st.columns(2)
        row_start = col1.number_input("Start row", min_value=1, value=1)
        row_end   = col2.number_input("End row",   min_value=1, value=10)
        force     = st.checkbox("Force re-scrape (overwrite existing)")

        if st.button("🚀 Launch Job"):
            code, result = api(
                "post", "/scrape-stores-by-range",
                json={"row_range": f"{row_start}-{row_end}", "force_rescrape": force}
            )
            if code == 202:
                st.success(f"Job started! ID: `{result.get('job_id')}`")
                st.markdown(f"- Total IDs: **{result.get('total_ids')}**")
                st.markdown(f"- Pending: **{result.get('pending_ids')}**")
                st.markdown(f"- Skipped: **{result.get('skipped')}**")
                st.code(result.get("job_id"), language=None)
            else:
                st.error(f"Error {code}")
                show_json(result)

    with tab2:
        job_id_input = st.text_input("Job ID", placeholder="paste job_id here")

        col1, col2 = st.columns(2)
        if col1.button("📊 Summary"):
            if job_id_input:
                code, result = api("get", f"/scrape-stores-by-range/{job_id_input}/summary")
                if code == 200:
                    completed = result.get("completed", 0)
                    pending   = result.get("pending_ids", 0)
                    pct       = result.get("progress_pct", 0)

                    st.progress(pct / 100)
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Status",    result.get("status", "—").upper())
                    c2.metric("Completed", f"{completed}/{pending}")
                    c3.metric("Progress",  f"{pct}%")
                    if result.get("error"):
                        st.error(result["error"])
                else:
                    st.error(f"Error {code}")
            else:
                st.warning("Enter a job ID.")

        if col2.button("📋 Full Results"):
            if job_id_input:
                code, result = api("get", f"/scrape-stores-by-range/{job_id_input}")
                if code == 200:
                    results = result.get("results", [])
                    st.markdown(f"**{len(results)} results in file**")
                    if results:
                        df = pd.DataFrame(results)
                        st.dataframe(df, use_container_width=True, hide_index=True)
                else:
                    st.error(f"Error {code}")
            else:
                st.warning("Enter a job ID.")

    with tab3:
        st.markdown("Merge all `store_results_*.json` into `master_results.json`.")
        if st.button("🔀 Merge Now"):
            with st.spinner("Merging…"):
                code, result = api("post", "/merge-store-results")
            if code == 200:
                st.success(f"Merged {result.get('total_merged')} records")
                show_json(result)
            else:
                st.error(f"Error {code}")
                show_json(result)

        st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)
        st.markdown("##### Store Results Summary")
        if st.button("Load Summary"):
            code, result = api("get", "/store-results/summary")
            if code == 200:
                col1, col2, col3 = st.columns(3)
                col1.metric("Total",       result.get("total", 0))
                col2.metric("Successful",  result.get("successful", 0))
                col3.metric("With Errors", result.get("with_errors", 0))
                if result.get("files"):
                    st.dataframe(pd.DataFrame(result["files"]), use_container_width=True, hide_index=True)
            else:
                st.error(f"Error {code}")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: EXPORT
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📤 Export":
    st.markdown("# Export Templates")
    st.markdown("<hr class='section-divider'>", unsafe_allow_html=True)

    st.markdown("Export categorized products to per-category `.xlsm` files.")

    mode = st.radio(
        "Export mode",
        ["Full rebuild (all products)", "Incremental (only new/unexported)"],
        horizontal=True,
    )
    only_new = mode.startswith("Incremental")

    if st.button("📤 Run Export"):
        with st.spinner("Exporting… this may take a while"):
            code, result = api("post", f"/export-templates?only_new={str(only_new).lower()}")

        if code == 200:
            st.success(
                f"Exported **{result.get('total_products')}** products "
                f"across **{result.get('total_categories')}** categories"
            )
            for f in result.get("files", []):
                with st.expander(f"📁 {f.get('category_name')} ({f.get('product_count')} products)"):
                    st.markdown(f"**Category ID:** `{f.get('category_id')}`")
                    st.markdown(f"**File:** `{f.get('file')}`")
                    if f.get("products"):
                        df = pd.DataFrame(f["products"])
                        st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.error(f"Error {code}")
            show_json(result)
