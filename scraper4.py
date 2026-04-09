import re
import time
import random
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from stem import Signal
from stem.control import Controller


# ── Utilities ─────────────────────────────────────────────────────────────────

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def random_delay(min_seconds: float = 1, max_seconds: float = 3):
    time.sleep(random.uniform(min_seconds, max_seconds))


def rotate_tor_circuit():
    try:
        with Controller.from_port(port=9051) as controller:
            controller.authenticate()
            controller.signal(Signal.NEWNYM)
            print("   Waiting 15s for new Tor circuit...")
            for i in range(15):
                time.sleep(1)
                if i % 5 == 4:
                    print(f"   ... {15 - i - 1}s remaining")
        print("✅ Tor circuit rotated - new IP acquired")
        return True
    except Exception as e:
        print(f"⚠️ Could not rotate Tor circuit: {e}")
        return False


def _normalize_image_url(src: str) -> str:
    """
    Fix protocol-relative URLs, strip query strings and AliExpress
    resize suffixes (_NNNxNNN.jpg / _.webp) to get the original image.
    Accepts both alicdn.com and aliexpress.com CDN hostnames.
    """
    if not src:
        return ""
    if src.startswith("//"):
        src = "https:" + src
    # FIX: accept ae-pic*.aliexpress.com CDN domains in addition to alicdn.com
    if "alicdn.com" not in src and "aliexpress.com" not in src:
        return ""
    src = src.split("?")[0]
    # Strip resize suffix variants:  _640x640.jpg  /  _640x640Q70.jpg  /  _.webp
    src = re.sub(r'_\d+x\d+\w*\.(jpg|jpeg|png|webp)$', '.jpg', src, flags=re.IGNORECASE)
    src = re.sub(r'_\.\w+$', '.jpg', src)
    return src


# ── Page-level helpers ────────────────────────────────────────────────────────

def is_captcha_page(page) -> bool:
    page_url   = page.url.lower()
    page_title = page.title().lower()

    if any(kw in page_url for kw in ["baxia", "punish", "captcha", "verify", "_____tmd_____"]):
        print("❌ CAPTCHA detected in URL")
        return True

    for sel in ["iframe[src*='recaptcha']", ".baxia-punish", "#captcha-verify",
                "[id*='captcha']", "iframe[src*='geetest']", "[class*='captcha']"]:
        try:
            if page.locator(sel).count() > 0:
                print(f"❌ CAPTCHA detected: {sel}")
                return True
        except:
            continue

    is_product = "aliexpress" in page_title and len(page_title) > 40
    if not is_product and any(kw in page_title for kw in ["verify","access","denied","blocked","challenge"]):
        print("❌ Block page detected from title")
        return True

    return False


def check_exit_node(page) -> bool:
    """Hit httpbin before touching AliExpress — confirms the Tor circuit is alive."""
    try:
        page.goto("https://httpbin.org/ip", timeout=15000, wait_until="domcontentloaded")
        print(f"   ✅ Exit node reachable: {page.inner_text('body').strip()}")
        return True
    except Exception as e:
        print(f"   ⚠️ Exit node unreachable: {e}")
        return False


def rewrite_to_www(page) -> None:
    """
    Country-specific subdomains (de., nl., fr. …) use a different page
    structure — description in iframe, ship-to panel differs, compliance hidden.
    Rewrite the URL to www.aliexpress.com and reload immediately.
    """
    current = page.url
    if "aliexpress.com" in current and not current.startswith("https://www.aliexpress.com"):
        canonical = re.sub(r'https://[a-z]{2}\.aliexpress\.com',
                           'https://www.aliexpress.com', current)
        canonical = re.sub(r'[?&]gatewayAdapt=[^&]*', '', canonical).rstrip('?&')
        print(f"🔄 Country subdomain → www: {canonical}")
        page.goto(canonical, timeout=120000, wait_until="domcontentloaded")
        print(f"   Now on: {page.url}")


def dismiss_overlays(page) -> None:
    """
    Dismiss GDPR banners and any other full-page overlays that would block
    pointer events.  Uses JS clicks — bypasses comet-v2-modal-wrap entirely.
    """
    page.evaluate("""
        () => {
            const gdpr = document.querySelector('#gdpr-new-container')
                      || document.querySelector('[data-spm="gdpr_v2"]')
                      || document.querySelector('#voyager-gdpr-2025');
            if (gdpr) {
                const btn = gdpr.querySelector('button')
                         || gdpr.querySelector('[class*="accept"]')
                         || gdpr.querySelector('[class*="close"]')
                         || gdpr.querySelector('[class*="agree"]');
                if (btn) btn.click();
                else gdpr.remove();
            }
        }
    """)
    page.wait_for_timeout(400)


# ── Extraction functions ───────────────────────────────────────────────────────

def set_shipping_to_poland(page) -> bool:
    """
    Change Ship To → Poland so AliExpress shows the Product Compliance section
    regardless of the Tor exit node country.
    All clicks use JS to bypass comet-v2-modal-wrap pointer-event interception.
    """
    print("🌍 Setting shipping destination to Poland...")
    try:
        opened = page.evaluate("""
            () => {
                const triggers = [
                    '[class*="es--trigger"]',
                    '[class*="ship-to"] [class*="arrow"]',
                    '[class*="select--arrow"]',
                    '[class*="comet-icon-arrowleftrtl"]',
                ];
                for (const sel of triggers) {
                    const el = document.querySelector(sel);
                    if (el) { el.click(); return sel; }
                }
                return null;
            }
        """)

        page.wait_for_timeout(1200)

        panel_open = page.locator("div[class*='form-item--title']").filter(has_text="Ship to").count() > 0

        if not panel_open:
            page.evaluate("""
                () => {
                    const el = document.querySelector('[class*="select--text"]');
                    if (el) el.click();
                }
            """)
            page.wait_for_timeout(1200)
            panel_open = page.locator("div[class*='form-item--title']").filter(has_text="Ship to").count() > 0

        if not panel_open:
            print("   ⚠️ Could not open ship-to panel")
            return False

        print(f"   ✓ Ship-to panel opened")

        page.evaluate("""
            () => {
                const el = document.querySelector('[class*="form-item--content"]');
                if (el) el.click();
            }
        """)
        page.wait_for_timeout(600)

        for sel in ["div[class*='select--popup'] input", "div[class*='select--search'] input"]:
            inp = page.locator(sel).first
            if inp.count() > 0:
                inp.fill("Poland")
                page.wait_for_timeout(600)
                print("   ✓ Typed 'Poland'")
                break

        clicked = page.evaluate("""
            () => {
                const flag = document.querySelector('span.country-flag-y2023.PL');
                if (flag) { flag.click(); return 'flag'; }
                const items = [...document.querySelectorAll('[class*="select--item"]')];
                const pl = items.find(el => el.textContent.trim() === 'Poland');
                if (pl) { pl.click(); return 'text'; }
                return null;
            }
        """)

        if not clicked:
            print("   ⚠️ Poland item not found in dropdown")
            return False

        print(f"   ✓ Clicked Poland ({clicked})")
        page.wait_for_timeout(400)

        page.evaluate("""
            () => {
                const btn = document.querySelector('[class*="es--saveBtn"]')
                         || document.querySelector('[class*="saveBtn"]');
                if (btn) btn.click();
            }
        """)
        page.wait_for_timeout(3000)
        print("   ✅ Poland set — page updated")
        return True

    except Exception as e:
        print(f"⚠️ set_shipping_to_poland error: {e}")
        return False


def extract_title_universal(page) -> str:
    print("📌 Extracting title...")
    for sel, desc in [
        ('[data-pl="product-title"]', "data-pl"),
        ('h1',                        "h1"),
        ('[class*="product-title"]',  "product-title class"),
        ('[class*="ProductTitle"]',   "ProductTitle class"),
    ]:
        try:
            elem = page.locator(sel).first
            if elem.count() > 0:
                t = elem.inner_text().strip()
                if t and len(t) > 10:
                    print(f"✅ Title ({desc}): {t[:80]}...")
                    return t
        except:
            continue
    print("⚠️ Could not extract title")
    return ""


def extract_store_info_universal(page) -> dict:
    """
    Trigger the store popup via JS mouse events — Playwright .hover() always
    fails because comet-v2-modal-wrap is a persistent React portal that
    intercepts pointer events across the full viewport at all times.
    """
    store_info = {}
    print("📦 Extracting store info...")

    try:
        elem = page.locator("span[class*='store-detail--storeName']").first
        if elem.count() > 0:
            name = elem.inner_text().strip()
            if name:
                store_info["Store Name"] = name
                print(f"   ✓ Store name: {name}")

        try:
            elem.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
        except:
            pass

        print("   🔍 Triggering store popup via JS mouse events...")
        page.evaluate("""
            () => {
                const el = document.querySelector('[class*="store-detail--storeNameWrap"]');
                if (!el) return;
                ['mouseover','mouseenter','mousemove'].forEach(type =>
                    el.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true}))
                );
            }
        """)
        page.wait_for_timeout(1500)
        print("   ✓ JS mouse events dispatched")

        for row_sel in [
            "div[class*='store-detail'] table tr",
            "div[class*='storeDetail'] table tr",
            "[class*='store-detail--detail'] tr",
        ]:
            rows = page.locator(row_sel).all()
            if rows:
                for row in rows:
                    try:
                        cols = row.locator('td').all()
                        if len(cols) >= 2:
                            k = cols[0].inner_text().strip().replace(":", "")
                            v = cols[1].inner_text().strip()
                            if k and v:
                                store_info[k] = v
                                print(f"      {k}: {v}")
                    except:
                        continue
                if len(store_info) > 1:
                    break

        if len(store_info) <= 1:
            for popup_sel in [
                "div[class*='store-detail--storePopup']",
                "div[class*='store-detail--popup']",
                "div[class*='storePopup']",
            ]:
                popup = page.locator(popup_sel).first
                if popup.count() > 0:
                    text = popup.inner_text().strip()
                    for line in text.splitlines():
                        line = line.strip()
                        if ':' in line:
                            k, _, v = line.partition(':')
                            k, v = k.strip(), v.strip()
                            if k and v and len(k) < 50:
                                store_info[k] = v
                    if len(store_info) > 1:
                        break

        print(f"   ✅ Store info: {store_info}")
    except Exception as e:
        print(f"⚠️ Store extraction error: {e}")
        import traceback; traceback.print_exc()

    return store_info


def extract_compliance_info(page) -> dict:
    """
    Click the 'Product compliance information' h2 via JS, wait for the modal,
    parse the three sections (Manufacturer / EU responsible person / Product identifier).
    JS click bypasses comet-v2-modal-wrap pointer-event interception.
    """
    compliance = {}
    print("📋 Extracting compliance info...")

    try:
        heading = page.locator("h2").filter(has_text="Product compliance information").first
        if heading.count() == 0:
            print("   ⚠️ Compliance heading not found")
            return compliance

        print("   ✓ Found compliance heading — JS clicking...")
        heading.scroll_into_view_if_needed()
        page.wait_for_timeout(400)

        page.evaluate("""
            () => {
                const el = [...document.querySelectorAll('h2')]
                    .find(h => h.textContent.includes('Product compliance information'));
                if (el) el.click();
            }
        """)
        page.wait_for_timeout(2000)

        try:
            page.wait_for_selector("div.comet-v2-modal-body", timeout=8000)
        except:
            print("   ⚠️ Compliance modal did not appear")
            return compliance

        raw_text = page.locator("div.comet-v2-modal-body").first.inner_text().strip()
        print(f"   ✓ Modal text ({len(raw_text)} chars): {raw_text[:200]}")

        section_headers = [
            "Manufacturer information",
            "EU responsible person information",
            "Product identifier",
        ]
        lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
        current = None
        section_lines: dict = {}

        for line in lines:
            hdr = next((h for h in section_headers if line.lower().startswith(h.lower())), None)
            if hdr:
                current = hdr
                section_lines[current] = []
                remainder = line[len(hdr):].strip().lstrip(":").strip()
                if remainder:
                    section_lines[current].append(remainder)
            elif current:
                section_lines[current].append(line)

        def parse_kv(lines_list):
            result = {}
            for l in lines_list:
                if ":" in l:
                    k, _, v = l.partition(":")
                    k, v = k.strip(), v.strip()
                    if k and v and len(k) < 60:
                        result[k] = v
                elif l and "value" not in result:
                    result["value"] = l
            return result

        for section, s_lines in section_lines.items():
            parsed = parse_kv(s_lines)
            if parsed:
                compliance[section] = parsed
                print(f"   ✅ {section}: {parsed}")

        page.evaluate("""
            () => {
                const btn = document.querySelector('button.comet-v2-modal-close')
                         || document.querySelector('[class*="modal-close"]')
                         || document.querySelector('[aria-label="Close"]');
                if (btn) btn.click();
            }
        """)
        page.wait_for_timeout(500)

    except Exception as e:
        print(f"⚠️ Compliance extraction error: {e}")
        import traceback; traceback.print_exc()

    return compliance


def _wait_for_full_description(page, timeout_ms: int = 20000) -> bool:
    """
    Poll until div.detail-desc-decorate-richtext stops growing.

    Real structure (confirmed from live HTML inspection):
        #product-description          ← outer shell, appears immediately (empty)
          └── .detailmodule_html      ← injected by page JS after load
                └── .detail-desc-decorate-richtext   ← actual content

    Waiting on #product-description returns instantly on the empty shell.
    We must wait for .detail-desc-decorate-richtext specifically, then also
    wait for its innerHTML to stabilise so we don't cut off mid-injection.
    """
    print("   ⏳ Waiting for .detail-desc-decorate-richtext to appear and stabilise...")

    # Step 1: wait for the inner content div to exist at all
    try:
        page.wait_for_selector(
            "div.detail-desc-decorate-richtext",
            timeout=timeout_ms,
            state="attached",
        )
        print("   ✓ .detail-desc-decorate-richtext attached to DOM")
    except:
        print("   ⚠️ .detail-desc-decorate-richtext never appeared")
        return False

    # Step 2: poll until innerHTML stops changing — confirms injection is complete.
    # AliExpress injects description HTML in chunks; a single snapshot after
    # "attached" often captures an incomplete render.
    stable_checks = 0
    required_stable = 3          # must be unchanged for 3 consecutive checks
    poll_interval_ms = 600
    max_polls = int(timeout_ms / poll_interval_ms)
    last_len = -1

    for i in range(max_polls):
        current_len = page.evaluate("""
            () => {
                const el = document.querySelector('div.detail-desc-decorate-richtext');
                return el ? el.innerHTML.length : 0;
            }
        """)
        if current_len == last_len and current_len > 0:
            stable_checks += 1
            if stable_checks >= required_stable:
                print(f"   ✓ Content stabilised at {current_len} chars (after {i+1} polls)")
                return True
        else:
            stable_checks = 0
            last_len = current_len

        page.wait_for_timeout(poll_interval_ms)

    # If we exit the loop without stabilising, still proceed with whatever we have
    print(f"   ⚠️ Stabilisation timeout — proceeding with {last_len} chars")
    return last_len > 0


def extract_description(page) -> tuple:
    """
    Extract description text + images from div.detail-desc-decorate-richtext.

    Key structural facts (confirmed from live product HTML):
      - #product-description is an OUTER SHELL that appears immediately but
        starts empty. Waiting on it returns before content is injected.
      - .detailmodule_html and .detail-desc-decorate-richtext are injected
        dynamically by AliExpress page JS after initial DOM load.
      - All three products tested use the SAME nesting:
            #product-description > .detailmodule_html > .detail-desc-decorate-richtext
      - Images in .detail-desc-decorate-richtext have their src set directly
        in the injected HTML (not lazy via IntersectionObserver), so they are
        available as soon as injection completes — no extra scroll needed.
      - Text is in <p><span>…</span></p> leaves; the walker must not call
        innerText on container divs (double-counts all descendant text).
    """
    description_text   = ""
    description_images = []

    print("📝 Extracting description...")

    try:
        # ── Step 1: Click Description tab to trigger injection ────────────────
        # Scroll partway down first so the anchor element is interactive.
        page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.35)")
        page.wait_for_timeout(800)

        clicked = page.evaluate("""
            () => {
                const links = [...document.querySelectorAll('a.comet-v2-anchor-link')];
                const el = links.find(a => /description/i.test(a.textContent));
                if (el) { el.click(); return true; }
                return false;
            }
        """)
        if clicked:
            print("   ✓ Description tab clicked via JS")
        else:
            # Fallback: scroll deep enough to trigger the lazy section load
            print("   ⚠️ Description tab not found — scrolling to trigger injection...")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.7)")
        page.wait_for_timeout(1500)

        # ── Step 2: Wait for the INNER content div (not the outer shell) ──────
        # _wait_for_full_description polls until innerHTML stabilises so we
        # never capture a half-injected description.
        ready = _wait_for_full_description(page, timeout_ms=25000)
        if not ready:
            # Last-resort: try iframe fallback before giving up
            print("   ⚠️ Content div not ready — will attempt iframe fallback only")

        # ── Step 3: Extract text via JS walker ────────────────────────────────
        # Target .detail-desc-decorate-richtext directly — never #product-description
        # (the outer shell may still be empty or only partially filled).
        #
        # Walker rules:
        #   - LEAF tags (p, li, h1-h6, td, th, blockquote, pre): grab innerText
        #     and DO NOT recurse — avoids double-counting <span> children.
        #   - DIV and other containers: recurse into children only, never
        #     call innerText on them (would include all descendant text).
        #   - SKIP: script, style, noscript — never collect their text.
        #   - Global seen-set dedup (not just adjacent pairs).
        raw = page.evaluate("""
            () => {
                const c = document.querySelector('div.detail-desc-decorate-richtext');
                if (!c) return '';
                const parts = [];
                const seen  = new Set();
                const SKIP  = new Set(['script', 'style', 'noscript']);
                const LEAF  = new Set(['p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                                       'td', 'th', 'blockquote', 'pre', 'figcaption']);
                const walk = (node) => {
                    if (node.nodeType === Node.TEXT_NODE) {
                        const t = node.textContent.trim();
                        if (t.length > 1 && !seen.has(t)) { seen.add(t); parts.push(t); }
                        return;
                    }
                    if (node.nodeType !== Node.ELEMENT_NODE) return;
                    const tag = node.tagName.toLowerCase();
                    if (SKIP.has(tag)) return;
                    if (LEAF.has(tag)) {
                        // Use innerText for leaf blocks — respects CSS visibility
                        // and collapses whitespace correctly. Do NOT recurse after
                        // this so child <span> text isn't pushed a second time.
                        const t = (node.innerText || '').trim();
                        if (t.length > 1 && !seen.has(t)) { seen.add(t); parts.push(t); }
                        return;
                    }
                    // Container tags (div, section, article, etc.): recurse only
                    for (const child of node.childNodes) walk(child);
                };
                walk(c);
                return parts.join(' | ');
            }
        """)

        if raw and len(raw) > 50:
            description_text = re.sub(r'\s+', ' ', raw).strip()
            print(f"   ✅ JS DOM walker: {len(description_text)} chars")
        else:
            # Plain innerText fallback — less clean but always gets something
            try:
                fb_elem = page.locator("div.detail-desc-decorate-richtext").first
                if fb_elem.count() > 0:
                    fb = fb_elem.inner_text(timeout=8000).strip()
                    description_text = re.sub(r'\s+', ' ', fb).strip()
                    print(f"   ✅ inner_text fallback: {len(description_text)} chars")
            except Exception as e:
                print(f"   ⚠️ inner_text fallback failed: {e}")

        # ── Step 4: iframe fallback (some locales embed in a child <iframe>) ──
        if len(description_text) < 50:
            print("   ⚠️ Text still short — checking child iframes...")
            for iframe_sel in [
                'div.detail-desc-decorate-richtext iframe',
                '#product-description iframe',
                "iframe[id*='description']",
                "iframe[src*='description']",
            ]:
                iframe_elem = page.locator(iframe_sel).first
                if iframe_elem.count() > 0:
                    frame = iframe_elem.content_frame()
                    if frame:
                        iframe_text = frame.evaluate("""
                            () => {
                                const parts = [];
                                const seen  = new Set();
                                const SKIP  = new Set(['script', 'style', 'noscript']);
                                const LEAF  = new Set(['p', 'li', 'h1', 'h2', 'h3', 'h4',
                                                       'h5', 'h6', 'td', 'th', 'blockquote']);
                                const walk = (node) => {
                                    if (node.nodeType === Node.TEXT_NODE) {
                                        const t = node.textContent.trim();
                                        if (t.length > 1 && !seen.has(t)) {
                                            seen.add(t); parts.push(t);
                                        }
                                        return;
                                    }
                                    if (node.nodeType !== Node.ELEMENT_NODE) return;
                                    const tag = node.tagName.toLowerCase();
                                    if (SKIP.has(tag)) return;
                                    if (LEAF.has(tag)) {
                                        const t = (node.innerText || '').trim();
                                        if (t.length > 1 && !seen.has(t)) {
                                            seen.add(t); parts.push(t);
                                        }
                                        return;
                                    }
                                    for (const child of node.childNodes) walk(child);
                                };
                                walk(document.body);
                                return parts.join(' | ');
                            }
                        """)
                        if iframe_text and len(iframe_text) > 50:
                            description_text = re.sub(r'\s+', ' ', iframe_text).strip()
                            print(f"   ✅ iframe text: {len(description_text)} chars")
                            # Collect images from inside the iframe too
                            for src in (frame.evaluate("""
                                () => [...document.querySelectorAll('img')]
                                    .map(i => i.getAttribute('src') || i.getAttribute('data-src') || '')
                                    .filter(Boolean)
                            """) or []):
                                n = _normalize_image_url(src)
                                if n and n not in description_images:
                                    description_images.append(n)
                            break

        # ── Step 5: Image extraction ──────────────────────────────────────────
        # Images in .detail-desc-decorate-richtext have src set directly in the
        # injected HTML (confirmed from real product HTML — no lazy observer swap).
        # We still check data-src / data-lazy-src as a belt-and-suspenders fallback.
        #
        # Query .detail-desc-decorate-richtext first; fall back to the outer shell
        # only if the inner div isn't found (should never happen after step 2).
        print("   🖼️ Extracting description images...")
        raw_srcs = page.evaluate("""
    () => {
        // Primary: target the content div directly
        let imgs = [];
        const c = document.querySelector('div.detail-desc-decorate-richtext');
        if (c) {
            imgs = [...c.querySelectorAll('img')];
        }

        // Secondary: also sweep the outer #product-description in case
        // the browser's invalid-HTML repair (div inside p) orphaned some
        // <img> elements outside the inner container but still inside the
        // outer wrapper.
        const outer = document.querySelector('#product-description');
        if (outer) {
            const outerImgs = [...outer.querySelectorAll('img')];
            const existing = new Set(imgs);
            for (const img of outerImgs) {
                if (!existing.has(img)) imgs.push(img);
            }
        }

        return imgs.map(img =>
            img.getAttribute('src') ||
            img.getAttribute('data-src') ||
            img.getAttribute('data-lazy-src') || ''
        ).filter(Boolean);
    }
""")
        print(f"      Raw img srcs found: {len(raw_srcs)}")

        for src in raw_srcs:
            n = _normalize_image_url(src)
            if n and len(n) > 50 and n not in description_images:
                description_images.append(n)

        description_images = [
            u for u in description_images
            if not any(bad in u.lower() for bad in ['icon', 'logo', '20x20', '50x50', '100x100'])
        ][:20]

        print(f"   ✓ Description images collected: {len(description_images)}")
        for i, u in enumerate(description_images[:3], 1):
            print(f"      {i}. {u[:90]}")

    except Exception as e:
        print(f"⚠️ Description extraction error: {e}")
        import traceback; traceback.print_exc()

    return description_text, description_images


# ── Main entry point ──────────────────────────────────────────────────────────

def extract_aliexpress_product(url: str) -> dict:
    """
    Full scrape pipeline:
      1. Exit-node pre-flight check
      2. Load page → rewrite country subdomain → www.aliexpress.com
      3. Dismiss GDPR / overlay banners
      4. Set Ship To = Poland (EU → compliance section visible)
      5. Extract title, store info, compliance, description + images
    All pointer-event actions use JS to bypass comet-v2-modal-wrap.
    """
    print(f"\n🔍 Scraping: {url}")

    empty_result = {
        "title":            "",
        "description_text": "",
        "images":           [],
        "store_info":       {},
        "compliance_info":  {},
    }

    for attempt in range(3):
        print(f"\n📍 Attempt {attempt + 1}/3")

        if attempt > 0:
            rotate_tor_circuit()
            wait = 20 + attempt * 5
            print(f"   Waiting {wait}s...")
            time.sleep(wait)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                proxy={"server": "socks5://127.0.0.1:9050"},
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                ]
            )

            page = browser.new_page(
                viewport=random.choice([
                    {'width': 1920, 'height': 1080},
                    {'width': 1440, 'height': 900},
                    {'width': 1366, 'height': 768},
                ]),
                user_agent=random.choice([
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                ]),
                timezone_id=random.choice([
                    'Europe/London', 'Europe/Berlin',
                    'Europe/Paris',  'Europe/Amsterdam', 'Europe/Warsaw',
                ]),
                locale='en-GB',
            )

            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page.add_init_script("Object.defineProperty(navigator, 'plugins',   {get: () => [1,2,3]})")

            try:
                # ── 1. Exit-node check ────────────────────────────────────────
                print("🌐 Checking exit node...")
                if not check_exit_node(page):
                    browser.close()
                    rotate_tor_circuit()
                    continue

                # ── 2. Load page ──────────────────────────────────────────────
                print("📡 Loading page...")
                page.goto(url, timeout=120000, wait_until="domcontentloaded")

                rewrite_to_www(page)

                if is_captcha_page(page):
                    print("⚠️ CAPTCHA — rotating IP...")
                    browser.close()
                    continue

                print("⏳ Waiting for page render...")
                try:
                    page.wait_for_selector('[data-pl="product-title"]', timeout=15000)
                    print("   ✓ Title element detected")
                except:
                    try:
                        page.wait_for_selector('h1', timeout=8000)
                    except:
                        print("   ⚠️ Render confirmation failed — proceeding")

                # Gentle scroll to trigger lazy loading of above-fold elements
                for _ in range(3):
                    page.mouse.wheel(0, random.randint(150, 300))
                    time.sleep(random.uniform(0.3, 0.6))
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(1)

                if is_captcha_page(page):
                    print("⚠️ CAPTCHA after scroll — rotating IP...")
                    browser.close()
                    continue

                # ── 3. Dismiss GDPR / overlays ────────────────────────────────
                dismiss_overlays(page)

                # ── 4. Set Ship To = Poland ───────────────────────────────────
                set_shipping_to_poland(page)

                # ── 5. Extract all data ───────────────────────────────────────
                title           = extract_title_universal(page)
                store_info      = extract_store_info_universal(page)
                compliance_info = extract_compliance_info(page)
                description_text, description_images = extract_description(page)

                browser.close()

                result = {
                    "title":            title             if isinstance(title, str)              else "",
                    "description_text": description_text  if isinstance(description_text, str)   else "",
                    "images":           description_images if isinstance(description_images, list) else [],
                    "store_info":       store_info        if isinstance(store_info, dict)         else {},
                    "compliance_info":  compliance_info   if isinstance(compliance_info, dict)    else {},
                }

                print(f"\n🔍 DEBUG:")
                print(f"   title:            {len(result['title'])} chars")
                print(f"   description_text: {len(result['description_text'])} chars")
                print(f"   images:           {len(result['images'])}")
                print(f"   store_info:       {result['store_info']}")
                print(f"   compliance_info:  {result['compliance_info']}")
                print(f"✅ Done on attempt {attempt + 1}\n")
                return result

            except PlaywrightTimeoutError as e:
                print(f"⚠️ Timeout attempt {attempt + 1}: {e}")
                browser.close()
            except Exception as e:
                print(f"❌ Error attempt {attempt + 1}: {e}")
                import traceback; traceback.print_exc()
                try:
                    browser.close()
                except:
                    pass

    print("❌ Failed after 3 attempts")
    return empty_result
