"""
debug_scraper.py  (v3)

Handles aliexpress.com → aliexpress.us redirect.
Clicks the Description nav tab, then hunts for the real description container.

Usage:
    python debug_scraper.py "https://www.aliexpress.com/item/XXXXXX.html"

Outputs:
    debug_shot_1_loaded.png       after page load
    debug_shot_2_after_click.png  after clicking Description tab
    debug_page_after_click.html   full HTML after click
"""

import sys
import json
import time
import random
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.aliexpress.com/item/1005009755205790.html"


with sync_playwright() as p:

    browser = p.chromium.launch(
        headless=True,
        proxy={"server": "socks5://127.0.0.1:9050"},
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ]
    )
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1440, "height": 900},
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        java_script_enabled=True,
        bypass_csp=True,
    )
    page = context.new_page()
    page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )

    print(f"\n{'='*60}\nLoading: {URL}\n{'='*60}")

    page.goto(URL, timeout=120000, wait_until="domcontentloaded")
    page.wait_for_timeout(10000)

    print(f"Landed on : {page.url}")
    print(f"Page title: {page.title()}")

    # Scroll to trigger lazy loads
    for _ in range(15):
        page.mouse.wheel(0, random.randint(200, 400))
        page.wait_for_timeout(300)
    page.wait_for_timeout(3000)

    page.screenshot(path="debug_shot_1_loaded.png", full_page=False)
    print("Saved: debug_shot_1_loaded.png")

    # ── Phase 1: find ALL elements that could be the description container ────
    phase1 = page.evaluate("""
    () => {
        // Every element whose id or data-pl suggests "description"
        const byId = Array.from(document.querySelectorAll('[id]'))
            .filter(el => el.id.toLowerCase().includes('desc'))
            .map(el => ({
                tag: el.tagName,
                id: el.id,
                cls: el.className.substring(0, 80),
                text_preview: el.textContent.trim().substring(0, 100),
                has_shadow: !!el.shadowRoot,
                children_count: el.children.length,
            }));

        // Elements with data-pl containing "description"
        const byDataPl = Array.from(document.querySelectorAll('[data-pl]'))
            .filter(el => el.getAttribute('data-pl').toLowerCase().includes('desc'))
            .map(el => ({
                tag: el.tagName,
                id: el.id,
                data_pl: el.getAttribute('data-pl'),
                cls: el.className.substring(0, 80),
                text_preview: el.textContent.trim().substring(0, 100),
                has_shadow: !!el.shadowRoot,
            }));

        // nav-description element specifically
        const navDesc = document.querySelector('#nav-description');
        const navDescInfo = navDesc ? {
            tag: navDesc.tagName,
            outerHTML: navDesc.outerHTML.substring(0, 300),
            href: navDesc.getAttribute('href'),
            parent_id: navDesc.parentElement?.id,
            parent_cls: navDesc.parentElement?.className.substring(0, 80),
        } : null;

        // All shadow hosts on the page
        const shadowHosts = [];
        document.querySelectorAll('*').forEach(el => {
            if (el.shadowRoot) {
                const text = (el.shadowRoot.textContent || '').trim();
                shadowHosts.push({
                    tag: el.tagName,
                    id: el.id,
                    cls: el.className.substring(0, 60),
                    shadow_text_len: text.length,
                    shadow_text_preview: text.substring(0, 150),
                });
            }
        });

        return { byId, byDataPl, navDescInfo, shadowHosts };
    }
    """)

    print("\n── Phase 1: Description-related elements ─────────────────────────")
    print(json.dumps(phase1, indent=2))

    # ── Phase 2: click #nav-description and wait ──────────────────────────────
    print("\n── Phase 2: Clicking #nav-description ────────────────────────────")
    try:
        nav = page.query_selector('#nav-description')
        if nav:
            nav.scroll_into_view_if_needed()
            page.wait_for_timeout(1000)
            nav.click(force=True)
            print("Clicked #nav-description")
            page.wait_for_timeout(5000)
        else:
            print("#nav-description not found — skipping click")
    except Exception as e:
        print(f"Click failed: {e}")

    page.screenshot(path="debug_shot_2_after_click.png", full_page=False)
    print("Saved: debug_shot_2_after_click.png")

    with open("debug_page_after_click.html", "w", encoding="utf-8") as f:
        f.write(page.content())
    print("Saved: debug_page_after_click.html")

    # ── Phase 3: re-scan for description content after click ─────────────────
    phase3 = page.evaluate("""
    () => {
        // Re-check all shadow hosts — one of them should now have description text
        const shadowHosts = [];
        document.querySelectorAll('*').forEach(el => {
            if (el.shadowRoot) {
                const root = el.shadowRoot;
                const text = (root.textContent || '').trim();
                if (text.length > 30) {
                    // Get leaf nodes inside this shadow root
                    const leaves = [];
                    root.querySelectorAll('p,h1,h2,h3,h4,span,li,div,td').forEach(child => {
                        if (child.children.length > 0) return;
                        const t = (child.innerText || child.textContent || '').trim();
                        if (t && t.length >= 5) leaves.push(t.substring(0, 100));
                    });
                    shadowHosts.push({
                        tag: el.tagName,
                        id: el.id,
                        cls: el.className.substring(0, 60),
                        parent_id: el.parentElement?.id,
                        parent_cls: el.parentElement?.className.substring(0, 60),
                        shadow_text_len: text.length,
                        shadow_text_preview: text.substring(0, 300),
                        leaf_count: leaves.length,
                        leaves_sample: leaves.slice(0, 20),
                        img_count: root.querySelectorAll('img').length,
                        alicdn_images: Array.from(root.querySelectorAll('img'))
                            .map(img => img.getAttribute('src') || img.getAttribute('data-src') || '')
                            .filter(src => src.includes('alicdn'))
                            .slice(0, 5),
                    });
                }
            }
        });

        // Also check all [id*=desc] elements fresh
        const descEls = Array.from(document.querySelectorAll('[id*="desc"],[id*="Desc"],[id*="DESC"]'))
            .map(el => ({
                tag: el.tagName,
                id: el.id,
                text_len: el.textContent.trim().length,
                text_preview: el.textContent.trim().substring(0, 200),
                has_shadow: !!el.shadowRoot,
                children_count: el.children.length,
            }));

        return { shadow_hosts_with_content: shadowHosts, desc_elements: descEls };
    }
    """)

    print("\n── Phase 3: After click — shadow hosts + desc elements ───────────")
    print(json.dumps(phase3, indent=2))

    browser.close()
    print("\n✓ Done. Share terminal output + all 3 files.")
