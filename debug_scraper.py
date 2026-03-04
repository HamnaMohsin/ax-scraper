"""
debug_scraper.py

Diagnoses exactly what the browser sees on any AliExpress product page.
Saves a screenshot + full HTML dump so there is zero ambiguity.

Usage:
    python debug_scraper.py "https://www.aliexpress.com/item/XXXXXX.html"

Outputs (in current directory):
    debug_screenshot.png   — what the browser actually rendered
    debug_page.html        — full page source at time of extraction
    (JSON report printed to stdout)
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

    # Stealth — mask webdriver flag
    page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )

    print(f"\n{'='*60}\nLoading: {URL}\n{'='*60}")

    # ── Navigate ──────────────────────────────────────────────────────────────
    try:
        page.goto(URL, timeout=120000, wait_until="domcontentloaded")
    except Exception as e:
        print(f"Navigation error: {e}")

    print(f"URL after navigation : {page.url}")
    print(f"Page title after load: '{page.title()}'")

    # ── Initial wait for JS render ────────────────────────────────────────────
    page.wait_for_timeout(10000)
    print(f"Page title after 10s : '{page.title()}'")

    # ── Save screenshot #1 — right after initial load ─────────────────────────
    page.screenshot(path="debug_screenshot_1_initial.png", full_page=False)
    print("Saved: debug_screenshot_1_initial.png")

    # ── Scroll gradually ──────────────────────────────────────────────────────
    for _ in range(15):
        page.mouse.wheel(0, random.randint(200, 400))
        page.wait_for_timeout(300)

    page.wait_for_timeout(3000)

    # ── Scroll #product-description into view ─────────────────────────────────
    page.evaluate("document.querySelector('#product-description')?.scrollIntoView()")
    page.wait_for_timeout(5000)

    # ── Save screenshot #2 — after scroll ─────────────────────────────────────
    page.screenshot(path="debug_screenshot_2_scrolled.png", full_page=False)
    print("Saved: debug_screenshot_2_scrolled.png")

    # ── Save full HTML ────────────────────────────────────────────────────────
    with open("debug_page.html", "w", encoding="utf-8") as f:
        f.write(page.content())
    print("Saved: debug_page.html")

    # ── Wait for shadow root ──────────────────────────────────────────────────
    try:
        page.wait_for_function(
            """() => {
                const host = document.querySelector(
                    '#product-description [data-spm-anchor-id]'
                );
                if (!host || !host.shadowRoot) return false;
                return (host.shadowRoot.textContent || '').trim().length > 50;
            }""",
            timeout=15000,
        )
        print("Shadow root populated!")
    except Exception:
        print("Shadow root wait timed out after 15s.")

    print(f"\nFinal page title: '{page.title()}'")
    print(f"Final URL        : {page.url}")

    # ── Full diagnostic report ────────────────────────────────────────────────
    report = page.evaluate("""
    () => {
        const r = {};

        // ── Basic page state ─────────────────────────────────────────────────
        r.page_title = document.title;
        r.page_url   = location.href;

        // All h1 elements
        r.all_h1 = Array.from(document.querySelectorAll('h1'))
                        .map(el => el.textContent.trim().substring(0, 80));

        // ── Title selectors ──────────────────────────────────────────────────
        const titleSelectors = [
            "[data-pl='product-title']",
            ".title--wrap--NWOaiSp h1",
            ".product-title-text",
            ".title--wrap--UUHae_g h1",
            "h1.pdp-title",
            "#root h1",
            "h1",
        ];
        r.title_selectors = titleSelectors.map(sel => {
            const el = document.querySelector(sel);
            return { sel, found: !!el, text: el ? el.textContent.trim().substring(0, 100) : null };
        });

        // ── #product-description ─────────────────────────────────────────────
        const container = document.querySelector('#product-description');
        r.container_exists = !!container;
        if (!container) {
            // Dump IDs and data-pl attributes of all major divs so we can find the real selector
            r.all_ids = Array.from(document.querySelectorAll('[id]'))
                             .map(el => el.id)
                             .filter(id => id.length > 3 && id.length < 60);
            r.all_data_pl = Array.from(document.querySelectorAll('[data-pl]'))
                                 .map(el => ({ tag: el.tagName, pl: el.getAttribute('data-pl') }));
            return r;
        }

        // ── Shadow host ──────────────────────────────────────────────────────
        const host = container.querySelector('[data-spm-anchor-id]');
        r.host_exists = !!host;
        if (!host) {
            r.container_inner_html_preview = container.innerHTML.substring(0, 600);
            r.container_children = Array.from(container.children).map(c => ({
                tag: c.tagName,
                id: c.id,
                cls: c.className.substring(0, 60),
            }));
            return r;
        }

        r.host_tag = host.tagName;
        r.host_attrs = Array.from(host.attributes).map(a => a.name + '=' + a.value.substring(0, 60));

        // ── Shadow root state ────────────────────────────────────────────────
        r.has_shadow_root = !!host.shadowRoot;

        if (!host.shadowRoot) {
            // Check for unattached <template shadowrootmode>
            const templates = host.querySelectorAll('template[shadowrootmode]');
            r.template_count = templates.length;
            r.template_shadowrootmode = Array.from(templates).map(t => t.getAttribute('shadowrootmode'));
            r.host_innerHTML_preview = host.innerHTML.substring(0, 800);
            return r;
        }

        const root = host.shadowRoot;
        r.shadow_root_text_length = (root.textContent || '').trim().length;
        r.shadow_root_text_preview = (root.textContent || '').trim().substring(0, 400);
        r.shadow_root_html_preview = root.innerHTML.substring(0, 1000);

        r.element_counts = {
            p:     root.querySelectorAll('p').length,
            span:  root.querySelectorAll('span').length,
            div:   root.querySelectorAll('div').length,
            img:   root.querySelectorAll('img').length,
            h1:    root.querySelectorAll('h1').length,
            h3:    root.querySelectorAll('h3').length,
            h4:    root.querySelectorAll('h4').length,
        };

        // Leaf nodes
        const leaves = [];
        for (const el of root.querySelectorAll('p,h1,h2,h3,h4,span,li,div,td')) {
            if (el.children.length > 0) continue;
            const t = (el.innerText || el.textContent || '').trim();
            if (t && t.length >= 5) leaves.push({ tag: el.tagName, cls: (el.className||'').substring(0,50), text: t.substring(0,120) });
        }
        r.leaf_count = leaves.length;
        r.leaves_sample = leaves.slice(0, 40);

        // Images
        r.alicdn_images = [];
        root.querySelectorAll('img').forEach(img => {
            const src = img.getAttribute('src') || img.getAttribute('data-src') || '';
            if (src.includes('alicdn')) r.alicdn_images.push(src.substring(0, 120));
        });

        return r;
    }
    """)

    print("\n" + "="*60)
    print("DIAGNOSTIC REPORT")
    print("="*60)
    print(json.dumps(report, indent=2))

    browser.close()
    print("\n✓ Done. Share the full output + screenshots for exact diagnosis.")
