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


def is_aliexpress_url(url: str) -> bool:
    return "aliexpress." in url.lower()


def dismiss_cookie_banner(page):
    """
    Aggressive cookie banner dismissal with multiple strategies
    """
    # Strategy 1: Direct button clicks
    cookie_selectors = [
        # Primary accept buttons
        "button:has-text('Accept')",
        "button:has-text('Accept All')",
        "button:has-text('Accept Cookies')",
        "button:has-text('Agree')",
        "button:has-text('I Accept')",
        "button:has-text('Akzeptieren')",
        "button:has-text('Alle akzeptieren')",
        "button:has-text('Accepteren')",
        "button:has-text('Alle accepteren')",
        "button:has-text('Accepter')",
        "button:has-text('Tout accepter')",
        "button:has-text('Accetta')",
        "button:has-text('Accetta tutto')",
        
        # Class-based selectors
        "button.comet-btn.comet-btn-primary",
        "button.comet-btn[type='submit']",
        "button.gdpr-btn--accept",
        "button.cookies-agree-btn",
        ".gdpr-container button",
        ".cookie-consent button",
        "#gdpr-new-container button",
    ]
    
    for sel in cookie_selectors:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click(force=True)
                print(f"Cookie banner dismissed via '{sel}'")
                page.wait_for_timeout(2000)
                return True
        except Exception:
            continue
    
    # Strategy 2: JavaScript click on any accept button
    try:
        clicked = page.evaluate("""
            () => {
                const buttons = document.querySelectorAll('button');
                const acceptTexts = ['accept', 'akzeptieren', 'accepteren', 'agree', 'allow', 'consent'];
                
                for (const btn of buttons) {
                    const text = btn.innerText.toLowerCase();
                    if (acceptTexts.some(t => text.includes(t))) {
                        btn.click();
                        return true;
                    }
                }
                
                // Try to remove banner if no button found
                const banners = document.querySelectorAll('.gdpr-container, .cookie-consent, [class*="cookie"], [id*="cookie"]');
                banners.forEach(b => b.remove());
                return banners.length > 0;
            }
        """)
        if clicked:
            print("Cookie banner handled via JavaScript")
            page.wait_for_timeout(2000)
            return True
    except Exception:
        pass
    
    return False


def detect_blocked_page(page) -> bool:
    """
    Enhanced detection for blocked pages
    """
    # Check URL for blocking indicators
    url = page.url.lower()
    if any(x in url for x in ['captcha', 'verify', 'baxia', 'punish', 'block']):
        print(f"Block detected via URL: {url}")
        return True
    
    # Get page content
    try:
        title = page.title().lower()
        body_text = page.evaluate("() => document.body?.innerText?.toLowerCase() || ''")
        
        # Blocked page indicators
        block_indicators = [
            'captcha', 'verify', 'robot', 'access denied', 'blocked',
            'sorry', 'try again later', 'security check'
        ]
        
        # EU help page indicators (shows when product is blocked)
        help_indicators = [
            'hilfe', 'help', 'hulp', 'cookie', 'gdpr', 'datenschutz',
            'privacy', 'anmelden', 'sign in', 'inloggen', 'rückgabe',
            'return', 'retour', 'streitigkeiten', 'disputes', 'geschillen'
        ]
        
        # Check title
        if any(ind in title for ind in block_indicators):
            print(f"Block detected via title: {title}")
            return True
        
        # Check if page shows help content instead of product
        if body_text:
            help_count = sum(1 for ind in help_indicators if ind in body_text)
            if help_count >= 3 and 'product' not in body_text and 'item' not in body_text:
                print(f"Help page detected ({help_count} indicators) - likely blocked")
                return True
        
        # Check for product title presence
        has_product_title = page.evaluate("""
            () => {
                const selectors = [
                    '[data-pl="product-title"]',
                    '.title--wrap--UUHae_g h1',
                    '.title--wrap--NWOaiSp h1',
                    '.product-title-text',
                    '#root h1'
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText && el.innerText.length > 10) {
                        return true;
                    }
                }
                return false;
            }
        """)
        
        if not has_product_title and body_text and len(body_text) < 1000:
            print("No product title and limited content - likely blocked")
            return True
            
    except Exception as e:
        print(f"Error checking page state: {e}")
    
    return False


def safe_scroll(page, steps: int = 8) -> bool:
    for _ in range(steps):
        try:
            if page.is_closed():
                return False
            page.mouse.wheel(0, random.randint(100, 300))
            page.wait_for_timeout(random.randint(200, 500))
        except Exception as e:
            print(f"Scroll interrupted: {e}")
            return False
    return True


def random_viewport():
    return {
        "width": random.choice([1280, 1366, 1440, 1536, 1600, 1920]),
        "height": random.choice([720, 768, 864, 900, 1080]),
    }


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


# ── Shadow DOM extraction ──────────────────────────────────────────────────────

SHADOW_DOM_EXTRACT_JS = """
() => {
    const container = document.querySelector('#product-description');
    if (!container) return { error: 'no #product-description' };

    const host = container.querySelector(':scope > div');
    if (!host)            return { error: 'no child div in #product-description' };
    if (!host.shadowRoot) return { error: 'no shadowRoot on child div' };

    const root = host.shadowRoot;

    // Remove junk elements
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
        'find more', 'customer reviews', 'price',
        'compatibility', 'material', 'features',
        'multi-color options', 'viewing & typing angles',
        'shipping', 'payment', 'warranty', 'feedback'
    ]);

    const texts = [];
    const seen  = new Set();

    function isCollectable(el) {
        if (el.children.length === 0) return true;
        return Array.from(el.children).every(c => c.tagName === 'BR');
    }

    for (const el of root.querySelectorAll('p,h1,h2,h3,h4,h5,li,span,td,div')) {
        if (!isCollectable(el)) continue;

        const raw = (el.innerText || el.textContent || '');
        const t   = raw.replace(/\\n+/g, ' ').trim();

        if (!t || t.length < 6) continue;
        if (/^[\\d\\s\\.\\,\\$\\€\\£\\¥\\%\\+\\-\\&nbsp;]+$/.test(t)) continue;
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

# ── Enhanced stealth script ─────────────────────────────────────────────────────

STEALTH_JS = """
(() => {
    // Pass all automated browser checks
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    
    Object.defineProperty(navigator, 'plugins', {
        get: () => [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
        ],
    });
    
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en', 'en-GB'] });
    
    // Chrome runtime
    window.chrome = { 
        runtime: {}, 
        loadTimes: function() {}, 
        csi: function() {}, 
        app: {} 
    };
    
    // Permissions
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
    );
    
    // WebGL vendor
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return 'Intel Inc.';
        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
        return getParameter.call(this, parameter);
    };
    
    // Screen properties
    Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
    Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });
    
    // User agent
    Object.defineProperty(navigator, 'userAgent', {
        get: () => navigator.userAgent.replace('HeadlessChrome', 'Chrome'),
    });
    
    // Add navigator.hardwareConcurrency
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    
    // Add navigator.deviceMemory
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
    
    // Override connection
    Object.defineProperty(navigator, 'connection', {
        get: () => ({
            effectiveType: '4g',
            rtt: 50,
            downlink: 10,
            saveData: false
        })
    });
})();
"""

TITLE_SELECTORS = [
    "[data-pl='product-title']",
    ".title--wrap--UUHae_g h1",
    ".title--wrap--NWOaiSp h1",
    ".product-title-text",
    "#root h1",
    "h1[class*='title']",
]


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
            rotate_tor_circuit()
            random_delay(10.0, 20.0)  # Longer delay between attempts

        with sync_playwright() as p:
            # Launch with more stealth options
            browser = p.chromium.launch(
                headless=True,
                proxy={"server": "socks5://127.0.0.1:9050"},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--disable-web-security",
                    "--disable-features=BlockInsecurePrivateNetworkRequests",
                    "--disable-features=OutOfBlinkCors",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-zygote",
                    "--disable-setuid-sandbox",
                    "--disable-accelerated-2d-canvas",
                    "--disable-canvas-aa",
                    "--disable-2d-canvas-clip-aa",
                    "--disable-gl-drawing-for-tests",
                    "--mute-audio",
                ]
            )
            
            # Create context with realistic settings
            context = browser.new_context(
                user_agent=random.choice([
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                ]),
                viewport=random_viewport(),
                locale="en-US,en;q=0.9",
                timezone_id="America/New_York",
                permissions=["geolocation"],
                device_scale_factor=1,
                has_touch=False,
                is_mobile=False,
                color_scheme="light",
                reduced_motion="no-preference",
                forced_colors="none",
                extra_http_headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Cache-Control": "max-age=0",
                },
                java_script_enabled=True,
                bypass_csp=True,
                ignore_https_errors=True,
            )
            
            page = context.new_page()
            page.add_init_script(STEALTH_JS)

            # ── Navigate with stealth ──────────────────────────────────────
            try:
                # Navigate with minimal waiting first
                response = page.goto(base_url, timeout=60000, wait_until="commit")
                
                # Immediately handle cookie banner
                dismiss_cookie_banner(page)
                
                # Now wait for content
                page.wait_for_load_state("domcontentloaded", timeout=15000)
                
                # Additional banner check
                dismiss_cookie_banner(page)
                
            except Exception as e:
                print(f"Navigation failed: {e}")
                browser.close()
                continue

            print(f"Landed on: {page.url}")

            if not is_aliexpress_url(page.url):
                print(f"Redirected off AliExpress to {page.url} — skipping.")
                browser.close()
                continue

            # Check if page is blocked
            if detect_blocked_page(page):
                print("Page appears blocked - retrying with different approach...")
                browser.close()
                continue

            # ── Wait for product to load ─────────────────────────────────────
            try:
                # Wait for title to appear
                page.wait_for_function("""
                    () => {
                        const selectors = [
                            '[data-pl="product-title"]',
                            '.title--wrap--UUHae_g h1',
                            '.product-title-text'
                        ];
                        for (const sel of selectors) {
                            const el = document.querySelector(sel);
                            if (el && el.innerText && el.innerText.length > 10) {
                                return true;
                            }
                        }
                        return false;
                    }
                """, timeout=30000)
                print("Product title detected")
            except Exception:
                print("Timeout waiting for product title")
                # Check one more time if blocked
                if detect_blocked_page(page):
                    print("Page still appears blocked")
                    browser.close()
                    continue

            random_delay(2.0, 4.0)

            # ── Extract title ─────────────────────────────────────────────────
            def safe_text(sel: str) -> str:
                try:
                    el = page.query_selector(sel)
                    return el.text_content().strip() if el else ""
                except Exception:
                    return ""

            title = ""
            for sel in TITLE_SELECTORS:
                candidate = safe_text(sel)
                if candidate and len(candidate) > 10 and "aliexpress" not in candidate.lower():
                    title = candidate
                    print(f"Title via '{sel}': {title[:70]}")
                    break

            if not title:
                print("Could not extract title")
                browser.close()
                continue

            # ── Scroll naturally ────────────────────────────────────────
            safe_scroll(page, steps=6)
            random_delay(1.0, 2.0)

            # ── Click description tab ────────────────────────────────────
            try:
                # Try multiple possible selectors for description tab
                desc_tab_selectors = [
                    '#nav-description',
                    'a[href="#nav-description"]',
                    'li:has-text("Description")',
                    'li:has-text("Product Description")',
                    'li:has-text("Beschreibung")',  # German
                    'li:has-text("Omschrijving")',  # Dutch
                ]
                
                for sel in desc_tab_selectors:
                    desc_tab = page.query_selector(sel)
                    if desc_tab and desc_tab.is_visible():
                        desc_tab.scroll_into_view_if_needed()
                        random_delay(0.5, 1.0)
                        desc_tab.click(force=True)
                        print(f"Clicked description tab via '{sel}'")
                        page.wait_for_timeout(5000)  # Wait for XHR
                        break
            except Exception as e:
                print(f"Could not click description tab: {e}")

            # ── Extract description ─────────────────────────────────────
            description_text = ""
            images = []

            # Try shadow DOM extraction
            try:
                result = page.evaluate(SHADOW_DOM_EXTRACT_JS)
                if result and "error" not in result:
                    description_text = result.get("text", "").strip()
                    images = result.get("images", [])
                    print(f"Shadow DOM: {len(description_text)} chars, {len(images)} images")
            except Exception as e:
                print(f"Shadow DOM evaluate error: {e}")

            # Fallback to plain DOM
            if not description_text and not images:
                print("Trying plain DOM fallback...")
                try:
                    container = page.query_selector("#product-description")
                    if container:
                        # Get text
                        for el in container.query_selector_all("p, span, li, div:not(:has(*))"):
                            text = el.text_content().strip()
                            if text and len(text) >= 10 and not re.match(r'^[\d\s\.,\$€£¥%+-]+$', text):
                                description_text += text + " "
                        
                        # Get images
                        for img in container.query_selector_all("img"):
                            src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                            src = normalize_img_url(src)
                            if "alicdn.com" in src.lower() or "aliexpress-media.com" in src.lower():
                                images.append(src)
                except Exception as e:
                    print(f"Plain DOM fallback error: {e}")

            # Deduplicate images
            images = list(dict.fromkeys(images))

            browser.close()

            # Only return if we got at least a title
            if title:
                return {
                    "title": clean_text(title),
                    "description_text": clean_text(description_text),
                    "images": images,
                }
            else:
                print("No title extracted - retrying...")

    print(f"All {max_retries} attempts exhausted.")
    return empty_result
# import re
# import time
# import random
# from playwright.sync_api import sync_playwright
# from bs4 import BeautifulSoup


# # ── Helpers ────────────────────────────────────────────────────────────────────

# def random_delay(min_sec: float = 1.0, max_sec: float = 3.0):
#     delay = random.uniform(min_sec, max_sec)
#     print(f"Waiting {delay:.1f}s...")
#     time.sleep(delay)


# def rotate_tor_circuit():
#     try:
#         from stem import Signal
#         from stem.control import Controller
#         with Controller.from_port(port=9051) as controller:
#             controller.authenticate()
#             controller.signal(Signal.NEWNYM)
#             print("Tor circuit rotated — new exit IP assigned.")
#             time.sleep(5)
#     except Exception as e:
#         print(f"Failed to rotate Tor circuit: {e}")


# def is_aliexpress_url(url: str) -> bool:
#     return "aliexpress." in url.lower()


# def detect_recaptcha(page) -> bool:
#     indicators = [
#         "iframe[src*='recaptcha']",
#         "iframe[src*='google.com/recaptcha']",
#         ".g-recaptcha",
#         "#captcha-verify",
#         ".baxia-punish",
#         "[id*='captcha']",
#     ]
#     for selector in indicators:
#         try:
#             if page.query_selector(selector):
#                 print(f"reCAPTCHA/block detected via selector: {selector}")
#                 return True
#         except Exception:
#             pass

#     page_url = page.url.lower()
#     if any(kw in page_url for kw in ["baxia", "punish", "captcha", "verify"]):
#         print(f"Block detected via URL: '{page.url}'")
#         return True

#     page_title = page.title()
#     page_title_lower = page_title.lower()
#     block_titles = ["verify", "captcha", "robot", "access denied", "blocked", "aanmelden", "sign in"]
#     if any(kw in page_title_lower for kw in block_titles):
#         print(f"Block detected via page title: '{page_title}'")
#         return True

#     return False


# def safe_scroll(page, steps: int = 8) -> bool:
#     for _ in range(steps):
#         try:
#             if page.is_closed():
#                 print("Page closed during scroll — likely a redirect.")
#                 return False
#             page.mouse.wheel(0, random.randint(150, 350))
#             page.wait_for_timeout(random.randint(300, 600))
#         except Exception as e:
#             print(f"Scroll interrupted: {e}")
#             return False
#     return True


# def random_viewport():
#     return {
#         "width": random.choice([1366, 1440, 1536, 1920]),
#         "height": random.choice([768, 864, 900, 1050]),
#     }


# def normalize_img_url(src: str) -> str:
#     if not src:
#         return ""
#     src = src.strip()
#     if src.startswith("//"):
#         return "https:" + src
#     if src.startswith("/"):
#         return "https://www.aliexpress.com" + src
#     return src


# def clean_text(text: str) -> str:
#     if not text:
#         return ""
#     text = BeautifulSoup(text, "html.parser").get_text(" ")
#     text = re.sub(r"\s+", " ", text)
#     return text.strip()


# # ── IMPROVED Shadow DOM extraction ────────────────────────────────────────────

# SHADOW_DOM_EXTRACT_JS = """
# () => {
#     const container = document.querySelector('#product-description, .product-detail-tab, [data-spm-anchor-id]');
#     if (!container) return { error: 'no description container' };

#     const host = container.querySelector(':scope > div, > section');
#     if (!host) return { error: 'no child container' };
    
#     const root = host.shadowRoot || host;
    
#     // Remove junk
#     ['style', 'script', '.a-price', '.add-to-cart', '.vse-player-container'].forEach(sel => 
#         root.querySelectorAll(sel).forEach(el => el.remove())
#     );

#     const JUNK = new Set(['add to cart', 'customer reviews', 'price', 'compatibility']);
#     const texts = [];
#     const seen = new Set();

#     function collectText(el) {
#         if (el.children.length === 0 || Array.from(el.children).every(c => c.tagName === 'BR')) {
#             const t = (el.innerText || el.textContent || '').trim();
#             if (t && t.length > 8 && !JUNK.has(t.toLowerCase()) && !seen.has(t)) {
#                 seen.add(t);
#                 texts.push(t);
#             }
#         }
#     }

#     root.querySelectorAll('p,h1,h2,h3,h4,h5,li,span,td,div').forEach(collectText);

#     const images = [];
#     const seenSrc = new Set();
#     root.querySelectorAll('img').forEach(img => {
#         let src = img.src || img.dataset.src || '';
#         if (src && src.includes('alicdn') && !seenSrc.has(src)) {
#             seenSrc.add(src);
#             images.push(src);
#         }
#     });

#     return { text: texts.join(' '), images };
# }
# """


# def extract_aliexpress_product(url: str, max_retries: int = 5) -> dict:
#     base_url = "https://www.aliexpress.com" + url.split('aliexpress.com')[-1].split('?')[0]
#     print(f"🎯 Scraping: {base_url}")

#     for attempt in range(1, max_retries + 1):
#         print(f"🔄 [{attempt}/{max_retries}] Starting...")
        
#         browser = None
#         page = None
        
#         try:
#             if attempt > 1:
#                 rotate_tor_circuit()
#                 time.sleep(8)

#             with sync_playwright() as p:
#                 browser = p.chromium.launch(
#                     headless=True, 
#                     proxy={"server": "socks5://127.0.0.1:9050"},
#                     args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
#                 )
                
#                 context = browser.new_context(
#                     user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
#                     viewport={"width": 1366, "height": 768}
#                 )
                
#                 page = context.new_page()

#                 print(f"   → Navigating...")
#                 page.goto(base_url, timeout=45000, wait_until="domcontentloaded")
#                 print(f"   → DOM loaded: {page.url}")

#                 # QUICK US FIX
#                 page.evaluate("localStorage.setItem('aep_us','US')")
#                 print(f"   → US region set")

#                 # WAIT FOR TITLE (10s max)
#                 print(f"   → Waiting for title...")
#                 title = page.wait_for_selector("h1, [data-pl='product-title'], .product-title-text", 
#                                              timeout=10000, state="visible")
#                 title_text = title.text_content().strip() if title else ""
#                 print(f"   → Title: {title_text[:50]}...")

#                 if not title_text or len(title_text) < 10:
#                     print(f"   ❌ Empty title")
#                     page.screenshot(path=f"empty_{attempt}.png")
#                     continue

#                 # QUICK SCROLL + DESCRIPTION
#                 page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
#                 time.sleep(2)
                
#                 page.click('#nav-description, .description-tab, [href*="description"]', timeout=3000)
#                 time.sleep(3)

#                 # EXTRACT
#                 result = page.evaluate(SHADOW_DOM_EXTRACT_JS)
#                 desc = result.get('text', '') if isinstance(result, dict) else ''
#                 imgs = result.get('images', []) if isinstance(result, dict) else []

#                 print(f"✅ SUCCESS: {len(title_text)} title chars, {len(desc)} desc chars, {len(imgs)} images")
#                 return {
#                     "title": clean_text(title_text),
#                     "description_text": clean_text(desc),
#                     "images": [normalize_img_url(img) for img in imgs if img]
#                 }

#         except Exception as e:
#             print(f"💥 [{attempt}] FAILED: {str(e)[:100]}")
            
#         finally:
#             try:
#                 if page:
#                     page.screenshot(path=f"crash_{attempt}.png")
#                 if browser:
#                     browser.close()
#             except:
#                 pass

#     print("😞 All attempts failed")
#     return {"title": "", "description_text": "", "images": []}

