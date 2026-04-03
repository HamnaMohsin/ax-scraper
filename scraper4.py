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
    Returns empty string if not a valid alicdn URL.
    """
    if not src:
        return ""
    if src.startswith("//"):
        src = "https:" + src
    if "alicdn.com" not in src:
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
    # GDPR / cookie consent banners
    page.evaluate("""
        () => {
            // AliExpress GDPR 2025 banner
            const gdpr = document.querySelector('#gdpr-new-container')
                      || document.querySelector('[data-spm="gdpr_v2"]')
                      || document.querySelector('#voyager-gdpr-2025');
            if (gdpr) {
                // Try accept / close buttons first
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
        # Open the ship-to panel via JS — try several trigger selectors
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

        # Confirm panel opened
        panel_open = page.locator("div[class*='form-item--title']").filter(has_text="Ship to").count() > 0

        if not panel_open:
            # Fallback: click the visible country-flag / text area
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

        # Open the country dropdown
        page.evaluate("""
            () => {
                const el = document.querySelector('[class*="form-item--content"]');
                if (el) el.click();
            }
        """)
        page.wait_for_timeout(600)

        # Type "Poland" into the search box
        for sel in ["div[class*='select--popup'] input", "div[class*='select--search'] input"]:
            inp = page.locator(sel).first
            if inp.count() > 0:
                inp.fill("Poland")
                page.wait_for_timeout(600)
                print("   ✓ Typed 'Poland'")
                break

        # Click Poland — PL flag span is the most reliable target
        clicked = page.evaluate("""
            () => {
                // Try flag span first
                const flag = document.querySelector('span.country-flag-y2023.PL');
                if (flag) { flag.click(); return 'flag'; }
                // Fall back to item div containing 'Poland'
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

        # Click Save
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
        # Step 1: Store name (always visible, no hover needed)
        elem = page.locator("span[class*='store-detail--storeName']").first
        if elem.count() > 0:
            name = elem.inner_text().strip()
            if name:
                store_info["Store Name"] = name
                print(f"   ✓ Store name: {name}")

        # Step 2: Scroll the store element into view
        try:
            elem.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
        except:
            pass

        # Step 3: Dispatch mouse events via JS — bypasses overlay interception
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

        # Step 4: Read popup rows (table-based)
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

        # Step 5: Fallback — parse raw popup text as key: value lines
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
        # Check heading exists
        heading = page.locator("h2").filter(has_text="Product compliance information").first
        if heading.count() == 0:
            print("   ⚠️ Compliance heading not found")
            return compliance

        print("   ✓ Found compliance heading — JS clicking...")
        heading.scroll_into_view_if_needed()
        page.wait_for_timeout(400)

        # JS click — bypasses overlay interception
        page.evaluate("""
            () => {
                const el = [...document.querySelectorAll('h2')]
                    .find(h => h.textContent.includes('Product compliance information'));
                if (el) el.click();
            }
        """)
        page.wait_for_timeout(2000)

        # Wait for modal
        try:
            page.wait_for_selector("div.comet-v2-modal-body", timeout=8000)
        except:
            print("   ⚠️ Compliance modal did not appear")
            return compliance

        raw_text = page.locator("div.comet-v2-modal-body").first.inner_text().strip()
        print(f"   ✓ Modal text ({len(raw_text)} chars): {raw_text[:200]}")

        # Parse sections
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

        # Close modal via JS
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


def extract_description(page) -> tuple:
    """
    Extract description text + images.
    Handles:
      - New format : #product-description  (h3/p/li/div content)
      - Old format : div.detail-desc-decorate-richtext  (detailmodule_text / _image)
      - Iframe     : content embedded in a child <iframe> (some locales)
    All tab clicks via JS — bypasses comet-v2-modal-wrap.
    Image URLs normalized (protocol-relative fix + resize-suffix strip).
    """
    description_text   = ""
    description_images = []

    print("📝 Extracting description...")

    try:
        # Step 1: Click Description tab via JS
        clicked = page.evaluate("""
            () => {
                const links = [...document.querySelectorAll('a.comet-v2-anchor-link')];
                const el = links.find(a => a.textContent.toLowerCase().includes('description'));
                if (el) { el.click(); return true; }
                return false;
            }
        """)
        if clicked:
            print("   ✓ Description tab clicked via JS")
            page.wait_for_timeout(2000)
        else:
            print("   ⚠️ Description tab not found — will try containers directly")

        # Step 2: Detect which description format this page uses
        container = None
        fmt       = None

        for sel, label in [
            ('#product-description',             'new'),
            ('div.detail-desc-decorate-richtext', 'old'),
        ]:
            try:
                page.wait_for_selector(sel, timeout=7000)
                elem = page.locator(sel).first
                if elem.count() > 0:
                    container = elem
                    fmt       = label
                    print(f"   ✓ Description format: {label} ({sel})")
                    break
            except:
                continue

        # Scroll fallback if neither appeared
        if container is None:
            print("   ⚠️ No container found — scrolling to trigger lazy load...")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            page.wait_for_timeout(3000)
            for sel, label in [('#product-description','new'),
                                ('div.detail-desc-decorate-richtext','old')]:
                elem = page.locator(sel).first
                if elem.count() > 0:
                    container = elem
                    fmt       = label
                    print(f"   ✓ Found after scroll: {label}")
                    break

        if container is None:
            print("   ❌ No description container found")
            return description_text, description_images

        # Scroll container into view to trigger lazy rendering
        try:
            container.scroll_into_view_if_needed()
            page.wait_for_timeout(2500)
        except:
            pass

        # Step 3: Extract text
        if fmt == 'old':
            # Old format: text lives in div.detailmodule_text
            parts = []
            seen  = set()
            for el in container.locator('div.detailmodule_text p, div.detailmodule_text').all():
                try:
                    t = el.inner_text(timeout=2000).strip()
                    if t and t not in seen:
                        seen.add(t)
                        parts.append(t)
                except:
                    continue
            description_text = ' | '.join(parts)
            print(f"   ✅ Old format text: {len(description_text)} chars")

        else:
            # New format: JS DOM walker (handles h3/p/li/div in DOM order)
            raw = page.evaluate("""
                () => {
                    const c = document.querySelector('#product-description');
                    if (!c) return '';
                    const parts = [];
                    const BLOCK = new Set(['h1','h2','h3','h4','h5','h6',
                                           'p','li','div','tr','td','th',
                                           'blockquote','pre']);
                    const walk = (node) => {
                        if (node.nodeType === Node.TEXT_NODE) {
                            const t = node.textContent.trim();
                            if (t && t.length > 1) parts.push(t);
                            return;
                        }
                        if (node.nodeType !== Node.ELEMENT_NODE) return;
                        const tag = node.tagName.toLowerCase();
                        if (tag === 'script' || tag === 'style') return;
                        if (BLOCK.has(tag)) {
                            const text = (node.innerText || '').trim();
                            if (text && text.length > 1) parts.push(text);
                            return;
                        }
                        for (const child of node.childNodes) walk(child);
                    };
                    walk(c);
                    return parts.filter((v,i) => v !== parts[i-1]).join(' | ');
                }
            """)

            if raw and len(raw) > 50:
                description_text = re.sub(r'\s+', ' ', raw).strip()
                print(f"   ✅ JS DOM walker: {len(description_text)} chars")
            else:
                fb = container.inner_text(timeout=8000).strip()
                description_text = re.sub(r'\s+', ' ', fb).strip()
                print(f"   ✅ inner_text fallback: {len(description_text)} chars")

        # Step 4: iframe fallback (some locales embed content in a child iframe)
        if len(description_text) < 50:
            print("   ⚠️ Text still empty — checking child iframes...")
            for iframe_sel in [
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
                                const BLOCK = new Set(['h1','h2','h3','h4','h5','h6',
                                                       'p','li','div','tr','td']);
                                const walk = (node) => {
                                    if (node.nodeType === Node.TEXT_NODE) {
                                        const t = node.textContent.trim();
                                        if (t && t.length > 1) parts.push(t);
                                        return;
                                    }
                                    if (node.nodeType !== Node.ELEMENT_NODE) return;
                                    const tag = node.tagName.toLowerCase();
                                    if (tag === 'script' || tag === 'style') return;
                                    if (BLOCK.has(tag)) {
                                        const text = (node.innerText || '').trim();
                                        if (text && text.length > 1) parts.push(text);
                                        return;
                                    }
                                    for (const child of node.childNodes) walk(child);
                                };
                                walk(document.body);
                                return parts.filter((v,i) => v !== parts[i-1]).join(' | ');
                            }
                        """)
                        if iframe_text and len(iframe_text) > 50:
                            description_text = re.sub(r'\s+', ' ', iframe_text).strip()
                            print(f"   ✅ iframe text: {len(description_text)} chars")
                            # Also collect images from inside the iframe
                            for src in (frame.evaluate("""
                                () => [...document.querySelectorAll('img')]
                                    .map(i => i.getAttribute('src') || i.getAttribute('data-src') || '')
                                    .filter(Boolean)
                            """) or []):
                                n = _normalize_image_url(src)
                                if n and n not in description_images:
                                    description_images.append(n)
                            break

        # Step 5: Image extraction via JS — works for both formats
        print("   🖼️ Extracting description images...")
        raw_srcs = page.evaluate("""
            () => {
                const c = document.querySelector('#product-description')
                       || document.querySelector('div.detail-desc-decorate-richtext');
                if (!c) return [];
                return [...c.querySelectorAll('img')].map(img =>
                    img.getAttribute('src') ||
                    img.getAttribute('data-src') ||
                    img.getAttribute('data-lazy-src') || ''
                ).filter(Boolean);
            }
        """)
        print(f"      Raw img srcs: {len(raw_srcs)}")

        for src in raw_srcs:
            n = _normalize_image_url(src)
            if n and len(n) > 50 and n not in description_images:
                description_images.append(n)

        description_images = [
            u for u in description_images
            if not any(bad in u.lower() for bad in ['icon','logo','20x20','50x50','100x100'])
        ][:20]

        print(f"   ✓ Description images: {len(description_images)}")
        for i, u in enumerate(description_images[:3], 1):
            print(f"      {i}. {u[:80]}")

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
                locale='en-GB',   # keeps compliance text in English
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

                # Rewrite country subdomain → www immediately
                rewrite_to_www(page)

                if is_captcha_page(page):
                    print("⚠️ CAPTCHA — rotating IP...")
                    browser.close()
                    continue

                # Wait for product title to confirm render
                print("⏳ Waiting for page render...")
                try:
                    page.wait_for_selector('[data-pl="product-title"]', timeout=15000)
                    print("   ✓ Title element detected")
                except:
                    try:
                        page.wait_for_selector('h1', timeout=8000)
                    except:
                        print("   ⚠️ Render confirmation failed — proceeding")

                # Gentle scroll to trigger lazy loading
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
                    "title":            title            if isinstance(title, str)             else "",
                    "description_text": description_text if isinstance(description_text, str)  else "",
                    "images":           description_images if isinstance(description_images, list) else [],
                    "store_info":       store_info       if isinstance(store_info, dict)       else {},
                    "compliance_info":  compliance_info  if isinstance(compliance_info, dict)  else {},
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
