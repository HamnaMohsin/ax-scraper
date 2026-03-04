"""
debug_scraper.py

Run this on a failing URL to see exactly what the browser finds.
It prints the state of every relevant DOM node so we can pinpoint
where extraction is breaking — no guessing.

Usage:
    python debug_scraper.py "https://www.aliexpress.com/item/XXXXXX.html"
"""

import sys
import json
import time
import random
from playwright.sync_api import sync_playwright


URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.aliexpress.com/item/1005009755205790.html"


def random_viewport():
    return {"width": 1440, "height": 900}


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
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport=random_viewport(),
        locale="en-US",
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        java_script_enabled=True,
        bypass_csp=True,
    )
    page = context.new_page()
    page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )

    print(f"\n{'='*60}")
    print(f"Loading: {URL}")
    print('='*60)

    page.goto(URL, timeout=120000, wait_until="domcontentloaded")
    print(f"Page title: {page.title()}")
    time.sleep(8)

    # Scroll down gradually
    for _ in range(12):
        page.mouse.wheel(0, random.randint(200, 400))
        page.wait_for_timeout(300)

    time.sleep(3)

    # Scroll description into view
    page.evaluate("document.querySelector('#product-description')?.scrollIntoView()")
    time.sleep(4)

    print("\n" + "="*60)
    print("DIAGNOSTIC REPORT")
    print("="*60)

    result = page.evaluate("""
    () => {
        const report = {};

        // ── 1. Does #product-description exist? ──────────────────────────────
        const container = document.querySelector('#product-description');
        report.container_exists = !!container;
        if (!container) return report;

        report.container_html_preview = container.outerHTML.substring(0, 300);

        // ── 2. Find the shadow host ───────────────────────────────────────────
        const host = container.querySelector('[data-spm-anchor-id]');
        report.host_exists = !!host;
        report.host_tag = host ? host.tagName : null;
        report.host_data_spm = host ? host.getAttribute('data-spm-anchor-id') : null;

        if (!host) {
            // Show ALL children of container so we can find the right selector
            report.container_children = Array.from(container.children).map(c => ({
                tag: c.tagName,
                id: c.id,
                className: c.className.substring(0, 80),
                attrs: Array.from(c.attributes).map(a => a.name + '=' + a.value.substring(0, 40))
            }));
            return report;
        }

        // ── 3. Does the host have a shadowRoot? ───────────────────────────────
        report.has_shadow_root = !!host.shadowRoot;

        if (!host.shadowRoot) {
            // Check for <template> elements — declarative shadow DOM
            const templates = host.querySelectorAll('template');
            report.template_count = templates.length;
            report.template_modes = Array.from(templates).map(t => t.getAttribute('shadowrootmode'));
            report.host_inner_html_preview = host.innerHTML.substring(0, 500);
            return report;
        }

        // ── 4. Shadow root exists — what's inside? ────────────────────────────
        const root = host.shadowRoot;
        const rootText = (root.textContent || '').trim();
        report.shadow_root_text_length = rootText.length;
        report.shadow_root_text_preview = rootText.substring(0, 300);
        report.shadow_root_html_preview = root.innerHTML.substring(0, 800);

        // ── 5. Count key elements inside shadow root ──────────────────────────
        report.shadow_element_counts = {
            p:     root.querySelectorAll('p').length,
            span:  root.querySelectorAll('span').length,
            div:   root.querySelectorAll('div').length,
            img:   root.querySelectorAll('img').length,
            h1:    root.querySelectorAll('h1').length,
            h3:    root.querySelectorAll('h3').length,
            h4:    root.querySelectorAll('h4').length,
            table: root.querySelectorAll('table').length,
            style: root.querySelectorAll('style').length,
        };

        // ── 6. List all leaf text nodes (no child elements) ───────────────────
        const leaves = [];
        const allEls = root.querySelectorAll('p, h1, h2, h3, h4, span, li, div, td');
        for (const el of allEls) {
            if (el.children.length > 0) continue;
            const t = (el.innerText || el.textContent || '').trim();
            if (t && t.length >= 5) {
                leaves.push({
                    tag: el.tagName,
                    cls: (el.className || '').substring(0, 60),
                    text: t.substring(0, 100)
                });
            }
        }
        report.leaf_nodes_count = leaves.length;
        // Show first 30 leaves so we can see what's actually there
        report.leaf_nodes_sample = leaves.slice(0, 30);

        // ── 7. All alicdn image URLs ───────────────────────────────────────────
        const imgs = [];
        root.querySelectorAll('img').forEach(img => {
            const src = img.getAttribute('src') || img.getAttribute('data-src') || '';
            if (src.includes('alicdn')) imgs.push(src.substring(0, 100));
        });
        report.alicdn_images = imgs;

        // ── 8. Check for iframes ──────────────────────────────────────────────
        const iframes = document.querySelectorAll('#product-description iframe');
        report.iframes = Array.from(iframes).map(f => ({
            id: f.id,
            name: f.name,
            src: (f.getAttribute('src') || '').substring(0, 100)
        }));

        return report;
    }
    """)

    print(json.dumps(result, indent=2))

    # ── Also dump title selectors state ──────────────────────────────────────
    print("\n" + "="*60)
    print("TITLE SELECTORS")
    print("="*60)
    title_check = page.evaluate("""
    () => {
        const selectors = [
            "[data-pl='product-title']",
            ".title--wrap--NWOaiSp h1",
            ".product-title-text",
            "h1"
        ];
        return selectors.map(sel => {
            const el = document.querySelector(sel);
            return {
                selector: sel,
                found: !!el,
                text: el ? el.textContent.trim().substring(0, 80) : null
            };
        });
    }
    """)
    print(json.dumps(title_check, indent=2))

    browser.close()
    print("\nDone. Paste the full output above so we can fix the scraper precisely.")
