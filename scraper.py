import re
import time
import random
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


# ── Helpers ────────────────────────────────────────────────────────────────────

def random_delay(min_sec: float = 1.0, max_sec: float = 3.0):
    delay = random.uniform(min_sec, max_sec)
    print(f"Waiting {delay:.1f}s...")
    time.sleep(delay)


def rotate_tor_circuit():
    try:
        from stem import Signal
        from stem.control import Controller
        with Controller.from_port(port=9051) as controller:
            controller.authenticate()
            controller.signal(Signal.NEWNYM)
            print("Tor circuit rotated — new exit IP assigned.")
            time.sleep(5)
    except Exception as e:
        print(f"Failed to rotate Tor circuit: {e}")


def detect_recaptcha(page) -> bool:
    indicators = [
        "iframe[src*='recaptcha']",
        "iframe[src*='google.com/recaptcha']",
        ".g-recaptcha",
        "#captcha-verify",
        ".baxia-punish",
        "[id*='captcha']",
    ]
    for selector in indicators:
        try:
            if page.query_selector(selector):
                print(f"reCAPTCHA/block detected via selector: {selector}")
                return True
        except Exception:
            pass

    page_url = page.url.lower()
    if any(kw in page_url for kw in ["baxia", "punish", "captcha", "verify"]):
        print(f"Block detected via URL: '{page.url}'")
        return True

    page_title = page.title()
    page_title_lower = page_title.lower()
    is_product_page = "aliexpress" in page_title_lower and len(page_title) > 40
    if not is_product_page:
        block_titles = ["verify", "captcha", "robot", "access denied", "blocked", "aanmelden", "sign in"]
        if any(kw in page_title_lower for kw in block_titles):
            print(f"Block detected via page title: '{page_title}'")
            return True

    return False


def random_viewport():
    viewports = [
        {"width": 1366, "height": 768},
        {"width": 1536, "height": 864},
        {"width": 1440, "height": 900},
        {"width": 1920, "height": 1080},
        {"width": 1280, "height": 720},
    ]
    return random.choice(viewports)


def normalize_img_url(src: str) -> str:
    if not src:
        return ""
    src = src.strip()
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return "https://www.aliexpress.com" + src
    return src


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ── Strategy 1: Shadow DOM (JS) ───────────────────────────────────────────────

SHADOW_DOM_EXTRACT_JS = """
() => {
    const host = document.querySelector('#product-description [data-spm-anchor-id]');
    if (!host || !host.shadowRoot) return null;

    const root = host.shadowRoot;

    // Remove comparison/price table noise
    const junkSelectors = [
        '.comparison-table',
        '.premium-aplus-module-5',
        '.apm-brand-story-carousel-container',
    ];
    junkSelectors.forEach(sel => {
        root.querySelectorAll(sel).forEach(el => el.remove());
    });

    const textSelectors = [
        '.aplus-p1',
        '.aplus-p3',
        '.aplus-description',
        'h3',
        'h4.aplus-h1',
        'h1.aplus-h3',
        '.card-description p',
        '.column-description p',
        'p',
    ];

    const seen = new Set();
    let text = '';
    for (const sel of textSelectors) {
        root.querySelectorAll(sel).forEach(el => {
            const t = (el.innerText || el.textContent || '').trim();
            if (t && t.length > 5 && !seen.has(t)) {
                seen.add(t);
                text += t + ' ';
            }
        });
    }

    const images = [];
    const seenSrc = new Set();
    root.querySelectorAll('img').forEach(img => {
        let src = img.getAttribute('src') || img.getAttribute('data-src') || '';
        if (!src) return;
        src = src.trim();
        if (src.startsWith('//')) src = 'https:' + src;
        if (src.includes('alicdn') && !seenSrc.has(src)) {
            seenSrc.add(src);
            images.push(src);
        }
    });

    return { text: text.trim(), images };
}
"""


# ── Strategy 2: Plain DOM extraction ─────────────────────────────────────────

def extract_from_plain_dom(page) -> tuple:
    description_text = ""
    images = []

    container = page.query_selector("#product-description")
    if not container:
        print("Description container not found in plain DOM either.")
        return "", []

    print("Falling back to plain DOM extraction...")

    # ── Images ────────────────────────────────────────────────────────────────
    # Search all known image-bearing module containers.
    # These are sibling divs — images are NOT always inside the text container.
    IMAGE_SELECTORS = (
        "div.detailmodule_image img, "
        "div.detailmodule_html img, "
        "div.detail-desc-decorate-richtext img, "
        "div.richTextContainer img, "
        "div.styleIsolation img, "
        "[data-rich-text-render] img"
    )
    for img in container.query_selector_all(IMAGE_SELECTORS):
        src = img.get_attribute("src") or img.get_attribute("data-src") or ""
        src = normalize_img_url(src)
        if "alicdn" in src and src not in images:
            images.append(src)

    # Fallback: grab all alicdn images anywhere in the container
    if not images:
        for img in container.query_selector_all("img"):
            src = img.get_attribute("src") or img.get_attribute("data-src") or ""
            src = normalize_img_url(src)
            if "alicdn" in src and src not in images:
                images.append(src)

    if images:
        print(f"Plain DOM: found {len(images)} images")

    # ── Text ──────────────────────────────────────────────────────────────────
    # Try most specific selector first, then progressively broader fallbacks.

    # Priority 1: exact AliExpress paragraph class
    text_container = (
        container.query_selector(".detailmodule_text") or
        container.query_selector(".detail-desc-decorate-richtext") or
        container.query_selector(".detailmodule_html") or
        container.query_selector(".richTextContainer") or
        container.query_selector(".styleIsolation") or
        container
    )

    specific_els = text_container.query_selector_all("p.detail-desc-decorate-content")
    if specific_els:
        for el in specific_els:
            text = el.text_content().strip()
            if text and len(text) > 5:
                description_text += text + " "
        print("Plain DOM: text via p.detail-desc-decorate-content")

    # Priority 2: richTextContainer / styleIsolation — text is raw HTML with <br>
    # tags, not wrapped in <p> elements, so read innerText directly
    if not description_text:
        for sel in ("div.richTextContainer", "div.styleIsolation"):
            for el in container.query_selector_all(sel):
                try:
                    text = el.evaluate("e => e.innerText").strip()
                    if text and len(text) > 5:
                        description_text += text + " "
                except Exception:
                    pass
        if description_text:
            print("Plain DOM: text via richTextContainer/styleIsolation innerText")

    # Priority 3: leaf-node fallback across all known text containers
    if not description_text:
        for el in text_container.query_selector_all("p, li, h3, h4"):
            try:
                child_count = el.evaluate("e => e.children.length")
                text = el.text_content().strip()
                if child_count == 0 and text and len(text) > 5:
                    description_text += text + " "
            except Exception:
                pass

    # Discard if mostly price comparison data
    if description_text:
        dollar_ratio = description_text.count("$") / max(len(description_text), 1)
        if dollar_ratio > 0.02:
            print("Plain DOM: text looks like price data — discarding.")
            description_text = ""

    if description_text:
        print(f"Plain DOM: found {len(description_text)} chars of text")

    return description_text.strip(), list(dict.fromkeys(images))


# ── Main scraper ───────────────────────────────────────────────────────────────

def extract_aliexpress_product(url: str, max_retries: int = 3) -> dict:
    print("Starting scrape...")

    base_url = url.split('#')[0].strip()
    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    empty_result = {"title": "", "description_text": "", "images": []}

    for attempt in range(1, max_retries + 1):
        print(f"\n── Attempt {attempt}/{max_retries} ──")

        if attempt > 1:
            print("Rotating Tor circuit before new attempt...")
            rotate_tor_circuit()
            random_delay(8.0, 15.0)

        with sync_playwright() as p:
            print("Opening browser...")
            browser = p.chromium.launch(
                headless=True,
                proxy={"server": "socks5://127.0.0.1:9050"},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--single-process",
                    "--disable-web-security",
                    "--disable-features=VizDisplayCompositor",
                ]
            )
            
            context = browser.new_context(
                **random_viewport(),
                user_agent=random.choice([
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                ]),
                locale="en-US",
                timezone_id="America/New_York",
                extra_http_headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1",
                    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="130", "Google Chrome";v="130"',
                    "Sec-Ch-Ua-Mobile": "?0",
                    "Sec-Ch-Ua-Platform": '"Windows"',
                },
                java_script_enabled=True,
                bypass_csp=True,
                permissions=["geolocation"],
                geolocation={"latitude": 40.7128, "longitude": -74.0060},
                color_scheme="light",
                reduced_motion="no-preference",
                forced_colors="none",
                has_touch=False,
            )
            
            page = context.new_page()

            # ── FIXED STEALTH JS ─────────────────────────────────────────────────────
            stealth_js = """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 0});
            Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
            Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
            Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
            Object.defineProperty(navigator, 'pdfViewerEnabled', {get: () => true});
            Object.defineProperty(navigator, 'connection', {
                get: () => ({effectiveType: '4g', type: 'wifi', downlinkMax: 10, rtt: 50, saveData: false})
            });
            Object.defineProperty(window, 'chrome', {get: () => ({runtime: {}})});
            window.chrome = {runtime: {}};
            Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {get: () => window, configurable: true});
            delete navigator.__proto__.webdriver;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
            """
            page.add_init_script(stealth_js)

            # ── SINGLE NAVIGATION (FIXED) ────────────────────────────────────────────
            try:
                print("🌐 Navigating...")
                response = page.goto(base_url, timeout=120000, wait_until="domcontentloaded")
                if not response or response.status != 200:
                    print(f"HTTP {response.status if response else 'No response'} - retrying...")
                    browser.close()
                    continue
                
                # FIXED: Network idle AFTER navigation
                page.wait_for_load_state("networkidle", timeout=15000)
                random_delay(4.0, 7.0)
                
            except Exception as e:
                print(f"Navigation failed: {e}")
                browser.close()
                continue

            # Early CAPTCHA check
            if detect_recaptcha(page):
                print("❌ CAPTCHA detected immediately — rotating circuit.")
                browser.close()
                continue

            # ── HUMAN-LIKE INTERACTION (IMPROVED) ────────────────────────────────────
            print("🎮 Human-like mouse + scroll...")
            
            # Initial mouse movement
            page.mouse.move(random.randint(300, 900), random.randint(300, 600))
            random_delay(1.0, 2.0)
            
            # Progressive scrolling with mouse
            for i in range(15):
                page.mouse.move(
                    random.randint(200, 1100), 
                    random.randint(250, 650)
                )
                page.mouse.wheel(0, random.randint(180, 350))
                page.wait_for_timeout(random.randint(400, 900))
            
            random_delay(3.0, 5.0)

            # Second CAPTCHA check
            if detect_recaptcha(page):
                print("❌ CAPTCHA after scroll — rotating circuit.")
                browser.close()
                continue

            # ── Scroll description into view ───────────────────────────────────
            try:
                page.evaluate("document.querySelector('#product-description')?.scrollIntoView()")
                page.wait_for_timeout(3000)
            except:
                pass

            # ── Wait for content (unchanged) ────────────────────────────────────────
            try:
                page.wait_for_function(
                    """() => {
                        const container = document.querySelector('#product-description');
                        if (!container) return false;
                        const host = container.querySelector('[data-spm-anchor-id]');
                        if (host && host.shadowRoot) {
                            return (host.shadowRoot.textContent || '').trim().length > 50;
                        }
                        const plainSelectors = ['.detailmodule_text','.detailmodule_html','.richTextContainer','.styleIsolation'];
                        for (const sel of plainSelectors) {
                            const el = container.querySelector(sel);
                            if (el && el.textContent.trim().length > 20) return true;
                        }
                        return false;
                    }""",
                    timeout=15000,
                )
                print("✅ Content loaded")
            except:
                print("⚠️ Content slow — continuing anyway")

            # ── TITLE EXTRACTION (unchanged) ────────────────────────────────────────
            def safe_query_text(selector: str) -> str:
                el = page.query_selector(selector)
                return el.text_content().strip() if el else ""

            title = ""
            BLOCKED_TITLES = {"aliexpress", "", "aanmelden", "sign in", "log in", "login", "verify", "robot"}
            title_selectors = [
                "[data-pl='product-title']",
                ".title--wrap--NWOaiSp h1",
                ".product-title-text",
                ".title--wrap--UUHae_g h1",
                "h1.pdp-title",
                "#root h1",
                "h1",
            ]
            for sel in title_selectors:
                candidate = safe_query_text(sel)
                if candidate and candidate.lower().strip() not in BLOCKED_TITLES:
                    title = candidate
                    print(f"✅ Title: {title[:60]}...")
                    break

            if not title:
                print("❌ No title — page blocked")
                browser.close()
                continue

            # ── DESCRIPTION + IMAGES (unchanged) ───────────────────────────────────
            description_text = ""
            images = []

            try:
                result = page.evaluate(SHADOW_DOM_EXTRACT_JS)
                if result and (result.get("text") or result.get("images")):
                    description_text = result.get("text", "").strip()
                    images = result.get("images", [])
                    print(f"✅ Shadow DOM: {len(description_text)} chars, {len(images)} imgs")
            except Exception as e:
                print(f"Shadow DOM failed: {e}")

            if not description_text and not images:
                description_text, images = extract_from_plain_dom(page)

            images = list(dict.fromkeys(images))[:15]

            browser.close()
            return {
                "title": clean_text(title),
                "description_text": clean_text(description_text),
                "images": images,
            }

    print(f"❌ All {max_retries} attempts failed")
    return empty_result
