import re
import time
import random
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


# ── Tor Circuit Rotation ───────────────────────────────────────────────────────

def rotate_tor_circuit():
    """
    Rotate Tor circuit to get a new exit IP.
    Requires Tor control port to be enabled on 9051.
    """
    try:
        from stem import Signal
        from stem.control import Controller
        
        with Controller.from_port(port=9051) as controller:
            controller.authenticate()  # No password by default
            
            # Check circuit status
            circuit_status = controller.get_info("status/circuit-established")
            if circuit_status != "1":
                print(f"⚠️  Tor circuit not ready (status={circuit_status})")
                return False
            
            # Rotate to new circuit
            controller.signal(Signal.NEWNYM)
            print("✅ Tor circuit rotated — new exit IP assigned")
            
            # Wait for new circuit to establish
            time.sleep(5)
            return True
            
    except Exception as e:
        print(f"❌ Failed to rotate Tor circuit: {e}")
        return False


# ── Helpers ────────────────────────────────────────────────────────────────────

def random_delay(min_sec: float = 5.0, max_sec: float = 10.0):
    """Sleep for random time between min_sec and max_sec."""
    delay = random.uniform(min_sec, max_sec)
    print(f"⏱️  Waiting {delay:.1f}s...")
    time.sleep(delay)


def random_viewport():
    """Return random viewport to avoid detection."""
    return {
        "width": random.choice([1280, 1366, 1440, 1536, 1600]),
        "height": random.choice([720, 768, 864, 900]),
    }


def is_aliexpress_url(url: str) -> bool:
    return "aliexpress." in url.lower()


def detect_recaptcha(page) -> bool:
    """Detect reCAPTCHA, blocks, or punish redirects."""
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
                print(f"🚫 reCAPTCHA/block detected via selector: {selector}")
                return True
        except Exception:
            pass

    # Check URL for punishment/block keywords
    page_url = page.url.lower()
    if any(kw in page_url for kw in ["punish", "captcha", "verify", "baxia"]):
        print(f"🚫 Block detected via URL: '{page.url}'")
        return True

    # Check page title
    page_title = page.title().lower()
    block_titles = ["verify", "captcha", "robot", "access denied", "blocked", "sign in"]
    is_product_page = is_aliexpress_url(page.url) and len(page_title) > 40
    if not is_product_page and any(kw in page_title for kw in block_titles):
        print(f"🚫 Block detected via page title: '{page_title}'")
        return True

    return False


def safe_scroll(page, steps: int = 12) -> bool:
    """Safely scroll page without triggering detection."""
    for _ in range(steps):
        try:
            if page.is_closed():
                print("⚠️  Page closed during scroll")
                return False
            page.mouse.wheel(0, random.randint(200, 400))
            page.wait_for_timeout(random.randint(200, 400))
        except Exception as e:
            print(f"⚠️  Scroll interrupted: {e}")
            return False
    return True


def normalize_img_url(src: str) -> str:
    """Normalize image URLs."""
    if not src:
        return ""
    src = src.strip()
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return "https://www.aliexpress.com" + src
    return src


def clean_text(text: str) -> str:
    """Clean and normalize text."""
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ── Stealth Scripts ───────────────────────────────────────────────────────────

STEALTH_JS = """
(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', {
        get: () => [
            { name: 'Chrome PDF Plugin',  filename: 'internal-pdf-viewer',             description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer',  filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
            { name: 'Native Client',      filename: 'internal-nacl-plugin',             description: '' },
        ],
    });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
    );
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return 'Intel Inc.';
        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
        return getParameter.call(this, parameter);
    };
    Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
    Object.defineProperty(screen, 'pixelDepth',  { get: () => 24 });
    Object.defineProperty(navigator, 'userAgent', {
        get: () => navigator.userAgent.replace('HeadlessChrome', 'Chrome'),
    });
})();
"""

SHADOW_DOM_EXTRACT_JS = """
() => {
    const container = document.querySelector('#product-description');
    if (!container) return { error: 'no #product-description' };

    const host = container.querySelector(':scope > div');
    if (!host)            return { error: 'no child div in #product-description' };
    if (!host.shadowRoot) return { error: 'no shadowRoot on child div' };

    const root = host.shadowRoot;

    [
        'style', 'script',
        '.a-price', '.a-offscreen', '.a-icon-alt',
        '.comparison-table', '.premium-aplus-module-5',
        '.apm-brand-story-carousel-container',
        '.vse-player-container',
        '.add-to-cart',
        '.aplus-carousel-actions',
        '.aplus-carousel-index',
        '.aplus-review-right-padding',
    ].forEach(sel => root.querySelectorAll(sel).forEach(el => el.remove()));

    const JUNK = new Set([
        'hero-video', 'product description', 'add to cart',
        'find more moko cases', 'customer reviews', 'price',
        'compatibility', 'material', 'features',
        'multi-color options', 'viewing & typing angles',
    ]);

    const texts = [];
    const seen  = new Set();

    function isCollectable(el) {
        if (el.children.length === 0) return true;
        return Array.from(el.children).every(c => c.tagName === 'BR' || c.tagName === 'IMG');
    }

    for (const el of root.querySelectorAll('p,h1,h2,h3,h4,h5,li,span,td,div')) {
        if (!isCollectable(el)) continue;

        const raw = (el.innerText || el.textContent || '');
        const t   = raw.replace(/\n+/g, ' ').trim();

        if (!t || t.length < 6) continue;
        if (/^[\d\s\.\,\$\€\£\¥\%\+\-\&nbsp;]+$/.test(t)) continue;
        if (JUNK.has(t.toLowerCase())) continue;
        if (seen.has(t)) continue;

        seen.add(t);
        texts.push(t);
    }

    const images  = [];
    const seenSrc = new Set();
    root.querySelectorAll('img').forEach(img => {
        let src = img.getAttribute('src') || img.getAttribute('data-src') || '';
        if (!src) return;
        src = src.trim();
        if (src.startsWith('//')) src = 'https:' + src;
        const s = src.toLowerCase();
        if ((s.includes('alicdn.com') || s.includes('aliexpress-media.com')) && !seenSrc.has(src)) {
            seenSrc.add(src);
            images.push(src);
        }
    });

    return { text: texts.join(' '), images };
}
"""

TITLE_SELECTORS = [
    "[data-pl='product-title']",
    ".title--wrap--UUHae_g h1",
    ".title--wrap--NWOaiSp h1",
    ".product-title-text",
    "#root h1",
]

BLOCKED_TITLES = {
    "aliexpress", "", "aanmelden", "sign in",
    "log in", "login", "verify", "robot",
}


# ── Main Scraper ───────────────────────────────────────────────────────────────

def extract_aliexpress_product(url: str, max_retries: int = 3) -> dict:
    """
    Extract AliExpress product with Tor circuit rotation.
    
    Args:
        url: AliExpress product URL
        max_retries: Number of retry attempts with Tor rotation (default: 3)
    
    Returns:
        {
            "title": str,
            "description_text": str,
            "images": list[str]
        }
    """
    print(f"\n{'='*80}")
    print(f"🔍 Starting scrape (max_retries={max_retries})...")
    print(f"{'='*80}")

    base_url = url.split('#')[0].strip()
    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    empty_result = {"title": "", "description_text": "", "images": []}

    for attempt in range(1, max_retries + 1):
        print(f"\n{'─'*80}")
        print(f"📍 Attempt {attempt}/{max_retries}")
        print(f"{'─'*80}")

        # Rotate Tor circuit before each retry (except first attempt)
        if attempt > 1:
            success = rotate_tor_circuit()
            if not success:
                print("⚠️  Tor rotation failed, but continuing...")
            
            # Wait longer between retries
            random_delay(20.0, 40.0)

        with sync_playwright() as p:
            try:
                # Launch browser with Tor SOCKS5 proxy
                browser = p.chromium.launch(
                    headless=True,
                    proxy={"server": "socks5://127.0.0.1:9050"},
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--no-zygote",
                    ]
                )

                # Create context with realistic settings
                context = browser.new_context(
                    user_agent=random.choice([
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    ]),
                    viewport=random_viewport(),
                    locale="en-US",
                    timezone_id=random.choice([
                        "America/New_York",
                        "America/Chicago",
                        "America/Los_Angeles",
                    ]),
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                    java_script_enabled=True,
                    bypass_csp=True,
                )

                page = context.new_page()
                page.add_init_script(STEALTH_JS)

                # ── Step 1: Detect regional domain ────────────────────────────
                print("\n📍 Step 1: Detecting regional domain...")
                try:
                    page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
                    regional_url = page.url
                    from urllib.parse import urlparse
                    parsed = urlparse(regional_url)
                    regional_homepage = f"{parsed.scheme}://{parsed.netloc}"
                    print(f"✅ Regional domain: {regional_homepage}")
                except Exception as e:
                    print(f"❌ Regional domain detection failed: {e}")
                    browser.close()
                    continue

                if not is_aliexpress_url(page.url):
                    print(f"🚫 Redirected off AliExpress — skipping")
                    browser.close()
                    continue

                # ── Step 2: Pre-visit regional homepage ──────────────────────
                print("\n📍 Step 2: Pre-visiting regional homepage...")
                try:
                    page.goto(regional_homepage, timeout=60000, wait_until="domcontentloaded")
                    random_delay(5.0, 10.0)
                    print(f"✅ Regional homepage loaded: '{page.title()[:60]}'")
                except Exception as e:
                    print(f"❌ Regional homepage visit failed: {e}")

                # ── Step 3: Navigate to product page ────────────────────────
                print("\n📍 Step 3: Loading product page with session cookies...")
                try:
                    page.goto(regional_url, timeout=120000, wait_until="domcontentloaded")
                    print(f"✅ Landed on: {page.url}")
                except Exception as e:
                    print(f"❌ Product navigation failed: {e}")
                    browser.close()
                    continue

                random_delay(5.0, 10.0)

                # ── Check for blocks/punish ─────────────────────────────────
                if detect_recaptcha(page):
                    print("🚫 CAPTCHA/block detected — retrying with new Tor circuit...")
                    browser.close()
                    continue

                # ── Wait for title element ──────────────────────────────────
                print("\n📍 Waiting for title element...")
                try:
                    page.wait_for_selector("h1", timeout=20000, state="visible")
                    print("✅ h1 element visible")
                except Exception:
                    print("❌ Title element never appeared")
                    try:
                        print(f"   Page title: '{page.title()}'")
                        body = page.evaluate("() => document.body?.innerText?.slice(0, 200) || 'empty'")
                        print(f"   Body preview: {repr(body)}")
                    except Exception:
                        pass
                    browser.close()
                    continue

                # ── Scroll page ────────────────────────────────────────────
                print("\n📍 Scrolling page...")
                random_delay(2.0, 4.0)
                scroll_ok = safe_scroll(page, steps=12)
                if not scroll_ok:
                    print("❌ Scroll failed — retrying...")
                    browser.close()
                    continue

                random_delay(2.0, 4.0)

                if page.is_closed():
                    print("❌ Page closed after scroll")
                    browser.close()
                    continue

                # ── Check for blocks again ──────────────────────────────────
                if detect_recaptcha(page):
                    print("🚫 CAPTCHA/block detected after scroll — retrying...")
                    browser.close()
                    continue

                # ── Extract title ──────────────────────────────────────────
                print("\n📍 Extracting title...")
                def safe_text(sel: str) -> str:
                    try:
                        el = page.query_selector(sel)
                        return el.text_content().strip() if el else ""
                    except Exception:
                        return ""

                title = ""
                for sel in TITLE_SELECTORS:
                    candidate = safe_text(sel)
                    if candidate and candidate.lower().strip() not in BLOCKED_TITLES:
                        title = candidate
                        print(f"✅ Title found: {title[:70]}")
                        break

                if not title:
                    print("❌ Title not found — page likely blocked")
                    browser.close()
                    continue

                # ── Click description nav and extract ───────────────────────
                description_text = ""
                images = []

                print("\n📍 Extracting description...")
                try:
                    nav_desc = page.query_selector('#nav-description')
                    if nav_desc:
                        nav_desc.scroll_into_view_if_needed()
                        random_delay(2.0, 3.0)
                        nav_desc.click(force=True)
                        print("✅ Clicked #nav-description — waiting for XHR...")

                        try:
                            page.wait_for_function(
                                """() => {
                                    const host = document.querySelector('#product-description > div');
                                    if (!host || !host.shadowRoot) return false;
                                    return (host.shadowRoot.textContent || '').trim().length > 4500;
                                }""",
                                timeout=15000,
                            )
                            print("✅ Description content loaded")
                        except Exception:
                            print("⚠️  XHR wait timed out — attempting extraction anyway")

                        random_delay(2.0, 3.0)
                    else:
                        print("⚠️  #nav-description not found")
                except Exception as e:
                    print(f"⚠️  Could not click #nav-description: {e}")

                # ── Shadow DOM extraction ───────────────────────────────────
                print("\n📍 Extracting from Shadow DOM...")
                try:
                    result = page.evaluate(SHADOW_DOM_EXTRACT_JS)
                    if result and "error" not in result:
                        description_text = result.get("text", "").strip()
                        images = result.get("images", [])
                        print(f"✅ Shadow DOM: {len(description_text)} chars, {len(images)} images")
                    else:
                        print(f"⚠️  Shadow DOM error: {result.get('error', 'unknown')}")
                except Exception as e:
                    print(f"❌ Shadow DOM extraction error: {e}")

                # ── Fallback: plain DOM extraction ──────────────────────────
                if not description_text and not images:
                    print("\n📍 Fallback: Extracting from plain DOM...")
                    try:
                        container = page.query_selector("#product-description")
                        if container:
                            for el in container.query_selector_all("p, span, li, h3, h4, div"):
                                try:
                                    only_br_or_img = el.evaluate(
                                        "e => e.children.length === 0 || "
                                        "Array.from(e.children).every(c => c.tagName === 'BR' || c.tagName === 'IMG')"
                                    )
                                    text = el.text_content().strip()
                                    if only_br_or_img and text and len(text) >= 6:
                                        if not re.match(r'^[\d\s\.\,\$\€\£\¥\%\+\-]+$', text):
                                            description_text += text + " "
                                except Exception:
                                    pass

                            for img in container.query_selector_all("img"):
                                src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                                src = normalize_img_url(src)
                                s = src.lower()
                                if "alicdn.com" in s or "aliexpress-media.com" in s:
                                    images.append(src)

                            if description_text:
                                print(f"✅ Plain DOM: {len(description_text)} chars, {len(images)} images")
                    except Exception as e:
                        print(f"⚠️  Plain DOM fallback error: {e}")

                images = list(dict.fromkeys(images))

                if not description_text:
                    print("⚠️  No description text (seller may use image-only description)")
                if not images:
                    print("⚠️  No description images extracted")

                browser.close()

                # ── Success! Return results ─────────────────────────────────
                print(f"\n{'='*80}")
                print(f"✅ EXTRACTION SUCCESSFUL (Attempt {attempt})")
                print(f"{'='*80}")
                return {
                    "title":            clean_text(title),
                    "description_text": clean_text(description_text),
                    "images":           images,
                }

            except Exception as e:
                print(f"❌ Unexpected error: {e}")
                try:
                    browser.close()
                except Exception:
                    pass
                continue

    print(f"\n{'='*80}")
    print(f"❌ ALL {max_retries} ATTEMPTS EXHAUSTED")
    print(f"{'='*80}")
    return empty_result
