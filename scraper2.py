import re
import time
import random
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


# ── Helpers ────────────────────────────────────────────────────────────────────

def random_delay(min_sec: float = 0.5, max_sec: float = 1.5):  # Reduced delays
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
            time.sleep(2)  # Reduced from 5s
    except Exception as e:
        print(f"Failed to rotate Tor circuit: {e}")


def is_aliexpress_url(url: str) -> bool:
    return "aliexpress." in url.lower()


def dismiss_cookie_banner(page):
    """
    EU domains (.de, .nl, .it, .fr etc.) show a GDPR cookie consent banner
    that overlays the page and blocks all interaction including title rendering.
    Must be dismissed before any content can be extracted.
    """
    # More aggressive selectors
    cookie_selectors = [
        # Primary AliExpress cookie buttons
        "button.comet-btn.comet-btn-primary",
        "button[class*='accept']",
        "button[class*='accept-all']",
        "button[data-role='accept']",
        "button[id*='accept']",
        
        # Text-based (fastest)
        "button:has-text('Accept All')",
        "button:has-text('Accept')",
        "button:has-text('Akzeptieren')",
        "button:has-text('Accepteren')",
        "button:has-text('Accepter')",
        "button:has-text('Accetta')",
        
        # Container buttons
        ".gdpr-container button",
        "#gdpr-new-container button",
    ]
    
    for sel in cookie_selectors:
        try:
            # Quick check without waiting
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click(force=True)
                print(f"Cookie banner dismissed via '{sel}'")
                page.wait_for_timeout(500)  # Reduced from 1500ms
                return True
        except Exception:
            continue
    
    # Try JavaScript as fallback
    try:
        page.evaluate("""
            () => {
                const btns = document.querySelectorAll('button');
                for (const btn of btns) {
                    const text = btn.innerText.toLowerCase();
                    if (text.includes('accept') || text.includes('akzept') || 
                        text.includes('accep')) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }
        """)
        page.wait_for_timeout(500)
        return True
    except Exception:
        pass
    
    return False


def detect_recaptcha(page) -> bool:
    # Fast checks first
    page_url = page.url.lower()
    if any(kw in page_url for kw in ["baxia", "punish", "captcha", "verify"]):
        print(f"Block detected via URL: '{page.url}'")
        return True
    
    # Quick selector checks
    fast_indicators = [".g-recaptcha", "#captcha-verify", ".baxia-punish"]
    for selector in fast_indicators:
        try:
            if page.query_selector(selector):
                print(f"reCAPTCHA/block detected via selector: {selector}")
                return True
        except Exception:
            pass

    return False


def safe_scroll(page, steps: int = 6) -> bool:  # Reduced steps
    for _ in range(steps):
        try:
            if page.is_closed():
                return False
            page.mouse.wheel(0, random.randint(100, 200))
            page.wait_for_timeout(random.randint(50, 100))  # Faster scroll
        except Exception:
            return False
    return True


def random_viewport():
    return {
        "width": random.choice([1280, 1366, 1440]),
        "height": random.choice([720, 768]),
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


# Optimized shadow DOM extraction
SHADOW_DOM_EXTRACT_JS = """
() => {
    const container = document.querySelector('#product-description');
    if (!container) return { error: 'no #product-description' };

    const host = container.querySelector(':scope > div');
    if (!host || !host.shadowRoot) return { error: 'no shadowRoot' };

    const root = host.shadowRoot;
    
    // Quick text extraction
    const texts = [];
    const seen = new Set();
    
    // Get all text from common elements
    root.querySelectorAll('p, span, div, li, h1, h2, h3, h4, h5').forEach(el => {
        if (el.children.length === 0 || Array.from(el.children).every(c => c.tagName === 'BR')) {
            const text = (el.innerText || el.textContent || '').trim();
            if (text && text.length > 10 && !seen.has(text)) {
                seen.add(text);
                texts.push(text);
            }
        }
    });

    // Quick image extraction
    const images = [];
    const seenSrc = new Set();
    root.querySelectorAll('img').forEach(img => {
        let src = img.getAttribute('src') || img.getAttribute('data-src') || '';
        if (!src) return;
        src = src.trim();
        if (src.startsWith('//')) src = 'https:' + src;
        if ((src.includes('alicdn.com') || src.includes('aliexpress-media.com')) && !seenSrc.has(src)) {
            seenSrc.add(src);
            images.push(src);
        }
    });

    return { text: texts.join(' '), images };
}
"""

# ── Stealth script ─────────────────────────────────────────────────────────────
STEALTH_JS = """
(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3] });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    window.chrome = { runtime: {} };
})();
"""

TITLE_SELECTORS = [
    "[data-pl='product-title']",
    ".title--wrap--UUHae_g h1",
    ".product-title-text",
    "#root h1",
]

BLOCKED_TITLES = {"aliexpress", "", "aanmelden", "sign in", "login"}


# ── Main scraper ───────────────────────────────────────────────────────────────

def extract_aliexpress_product(url: str, max_retries: int = 2) -> dict:  # Reduced retries
    print("Starting scrape...")

    base_url = url.split('#')[0].strip()
    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    empty_result = {"title": "", "description_text": "", "images": []}

    for attempt in range(1, max_retries + 1):
        print(f"\n── Attempt {attempt}/{max_retries} ──")

        if attempt > 1:
            rotate_tor_circuit()
            random_delay(3.0, 5.0)  # Reduced delay

        with sync_playwright() as p:
            # Launch with faster settings
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
            
            context = browser.new_context(
                user_agent=random.choice([
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                ]),
                viewport=random_viewport(),
                locale="en-US",
                timezone_id="America/New_York",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                java_script_enabled=True,
                bypass_csp=True,
            )
            
            page = context.new_page()
            page.add_init_script(STEALTH_JS)

            # ── Fast navigation ─────────────────────────────────────────────
            try:
                # Use commit to start interaction faster
                page.goto(base_url, timeout=30000, wait_until="commit")  # Reduced timeout
                
                # IMMEDIATELY dismiss cookie banner
                dismiss_cookie_banner(page)
                
                # Wait for DOM to be ready
                page.wait_for_load_state("domcontentloaded", timeout=5000)
                
            except Exception as e:
                print(f"Navigation failed: {e}")
                browser.close()
                continue

            print(f"Landed on: {page.url}")

            if not is_aliexpress_url(page.url):
                print(f"Redirected off AliExpress — skipping.")
                browser.close()
                continue

            # Quick check for blocks
            if detect_recaptcha(page):
                print("Block detected — retrying.")
                browser.close()
                continue

            # ── Smart wait for title ────────────────────────────────────────
            # Wait for title to appear (max 8 seconds)
            title = ""
            start_time = time.time()
            while time.time() - start_time < 8:
                # Check for title
                for sel in TITLE_SELECTORS:
                    try:
                        el = page.query_selector(sel)
                        if el:
                            candidate = el.text_content().strip()
                            if candidate and candidate.lower() not in BLOCKED_TITLES:
                                title = candidate
                                print(f"Title found in {time.time() - start_time:.1f}s: {title[:50]}")
                                break
                    except Exception:
                        pass
                
                if title:
                    break
                
                # Check if we're blocked
                if detect_recaptcha(page):
                    print("Block detected while waiting for title")
                    break
                
                # Short wait before checking again
                page.wait_for_timeout(500)
            
            if not title:
                print("Title not found — page likely blocked.")
                browser.close()
                continue

            # ── Quick scroll ─────────────────────────────────────────────────
            safe_scroll(page, steps=4)  # Reduced scrolling
            random_delay(0.5, 1.0)

            # ── Click description tab if needed ─────────────────────────────
            description_text = ""
            images = []

            try:
                nav_desc = page.query_selector('#nav-description')
                if nav_desc:
                    nav_desc.click(force=True)
                    print("Clicked #nav-description")
                    # Wait briefly for content
                    page.wait_for_timeout(2000)
            except Exception:
                pass

            # ── Extract data ────────────────────────────────────────────────
            try:
                result = page.evaluate(SHADOW_DOM_EXTRACT_JS)
                if result and "error" not in result:
                    description_text = result.get("text", "").strip()
                    images = result.get("images", [])
                    print(f"Extracted: {len(description_text)} chars, {len(images)} images")
            except Exception as e:
                print(f"Extraction error: {e}")

            browser.close()

            return {
                "title": clean_text(title),
                "description_text": clean_text(description_text),
                "images": images,
            }

    print(f"All attempts exhausted.")
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

