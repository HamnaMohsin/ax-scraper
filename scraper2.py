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
    block_titles = ["verify", "captcha", "robot", "access denied", "blocked", "aanmelden", "sign in"]
    if any(kw in page_title_lower for kw in block_titles):
        print(f"Block detected via page title: '{page_title}'")
        return True

    return False


def safe_scroll(page, steps: int = 8) -> bool:
    for _ in range(steps):
        try:
            if page.is_closed():
                print("Page closed during scroll — likely a redirect.")
                return False
            page.mouse.wheel(0, random.randint(150, 350))
            page.wait_for_timeout(random.randint(300, 600))
        except Exception as e:
            print(f"Scroll interrupted: {e}")
            return False
    return True


def random_viewport():
    return {
        "width": random.choice([1366, 1440, 1536, 1920]),
        "height": random.choice([768, 864, 900, 1050]),
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


# ── IMPROVED Shadow DOM extraction ────────────────────────────────────────────

SHADOW_DOM_EXTRACT_JS = """
() => {
    const container = document.querySelector('#product-description, .product-detail-tab, [data-spm-anchor-id]');
    if (!container) return { error: 'no description container' };

    const host = container.querySelector(':scope > div, > section');
    if (!host) return { error: 'no child container' };
    
    const root = host.shadowRoot || host;
    
    // Remove junk
    ['style', 'script', '.a-price', '.add-to-cart', '.vse-player-container'].forEach(sel => 
        root.querySelectorAll(sel).forEach(el => el.remove())
    );

    const JUNK = new Set(['add to cart', 'customer reviews', 'price', 'compatibility']);
    const texts = [];
    const seen = new Set();

    function collectText(el) {
        if (el.children.length === 0 || Array.from(el.children).every(c => c.tagName === 'BR')) {
            const t = (el.innerText || el.textContent || '').trim();
            if (t && t.length > 8 && !JUNK.has(t.toLowerCase()) && !seen.has(t)) {
                seen.add(t);
                texts.push(t);
            }
        }
    }

    root.querySelectorAll('p,h1,h2,h3,h4,h5,li,span,td,div').forEach(collectText);

    const images = [];
    const seenSrc = new Set();
    root.querySelectorAll('img').forEach(img => {
        let src = img.src || img.dataset.src || '';
        if (src && src.includes('alicdn') && !seenSrc.has(src)) {
            seenSrc.add(src);
            images.push(src);
        }
    });

    return { text: texts.join(' '), images };
}
"""


def extract_aliexpress_product(url: str, max_retries: int = 5) -> dict:
    base_url = "https://www.aliexpress.com" + url.split('aliexpress.com')[-1].split('?')[0]
    print(f"🎯 Scraping: {base_url}")

    for attempt in range(1, max_retries + 1):
        print(f"🔄 [{attempt}/{max_retries}] Starting...")
        
        browser = None
        page = None
        
        try:
            if attempt > 1:
                rotate_tor_circuit()
                time.sleep(8)

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True, 
                    proxy={"server": "socks5://127.0.0.1:9050"},
                    args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
                )
                
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 768}
                )
                
                page = context.new_page()

                print(f"   → Navigating...")
                page.goto(base_url, timeout=45000, wait_until="domcontentloaded")
                print(f"   → DOM loaded: {page.url}")

                # QUICK US FIX
                page.evaluate("localStorage.setItem('aep_us','US')")
                print(f"   → US region set")

                # WAIT FOR TITLE (10s max)
                print(f"   → Waiting for title...")
                title = page.wait_for_selector("h1, [data-pl='product-title'], .product-title-text", 
                                             timeout=10000, state="visible")
                title_text = title.text_content().strip() if title else ""
                print(f"   → Title: {title_text[:50]}...")

                if not title_text or len(title_text) < 10:
                    print(f"   ❌ Empty title")
                    page.screenshot(path=f"empty_{attempt}.png")
                    continue

                # QUICK SCROLL + DESCRIPTION
                page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
                time.sleep(2)
                
                page.click('#nav-description, .description-tab, [href*="description"]', timeout=3000)
                time.sleep(3)

                # EXTRACT
                result = page.evaluate(SHADOW_DOM_EXTRACT_JS)
                desc = result.get('text', '') if isinstance(result, dict) else ''
                imgs = result.get('images', []) if isinstance(result, dict) else []

                print(f"✅ SUCCESS: {len(title_text)} title chars, {len(desc)} desc chars, {len(imgs)} images")
                return {
                    "title": clean_text(title_text),
                    "description_text": clean_text(desc),
                    "images": [normalize_img_url(img) for img in imgs if img]
                }

        except Exception as e:
            print(f"💥 [{attempt}] FAILED: {str(e)[:100]}")
            
        finally:
            try:
                if page:
                    page.screenshot(path=f"crash_{attempt}.png")
                if browser:
                    browser.close()
            except:
                pass

    print("😞 All attempts failed")
    return {"title": "", "description_text": "", "images": []}
# def extract_aliexpress_product(url: str, max_retries: int = 3) -> dict:
#     print("Starting scrape...")

#     base_url = url.split('#')[0].strip()
#     if not base_url.startswith("http"):
#         base_url = "https://" + base_url

#     empty_result = {"title": "", "description_text": "", "images": []}

#     for attempt in range(1, max_retries + 1):
#         print(f"\n── Attempt {attempt}/{max_retries} ──")

#         if attempt > 1:
#             rotate_tor_circuit()
#             random_delay(10.0, 20.0)  # Longer delays between retries

#         with sync_playwright() as p:
#             browser = p.chromium.launch(
#                 headless=True,  
#                 proxy={"server": "socks5://127.0.0.1:9050"},
#                 args=[
#                     "--disable-blink-features=AutomationControlled",
#                     "--disable-features=VizDisplayCompositor",
#                     "--no-sandbox",
#                     "--disable-dev-shm-usage",
#                     "--disable-gpu",
#                     "--disable-extensions",
#                     "--disable-plugins",
#                     "--no-first-run",
#                     "--no-service-autorun",
#                     "--password-store=basic",
#                     "--use-mock-keychain",
#                 ]
#             )
            
#             context = browser.new_context(
#                 user_agent=random.choice([
#                     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
#                     "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
#                     "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
#                 ]),
#                 viewport=random_viewport(),
#                 locale="en-US",
#                 timezone_id="America/New_York",
#                 extra_http_headers={
#                     "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
#                     "Accept-Language": "en-US,en;q=0.9",
#                     "Accept-Encoding": "gzip, deflate, br",
#                     "Sec-Fetch-Dest": "document",
#                     "Sec-Fetch-Mode": "navigate",
#                     "Sec-Fetch-Site": "none",
#                     "Sec-Fetch-User": "?1",
#                     "Upgrade-Insecure-Requests": "1",
#                 },
#                 java_script_enabled=True,
#                 bypass_csp=True,
#                 ignore_https_errors=True,
#             )
            
#             page = context.new_page()
            
#             # ← FIXED: More comprehensive stealth script
#             page.add_init_script("""
# () => {
#     // Hide webdriver completely
#     delete navigator.__proto__.webdriver;
#     Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    
#     // Mock plugins
#     Object.defineProperty(navigator, 'plugins', {
#         get: () => [1, 2, 3, 4, 5],
#     });
    
#     // Mock languages
#     Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    
#     // Chrome object
#     window.chrome = {
#         runtime: {},
#         app: {},
#         loadTimes: function() {},
#         csi: function() {},
#     };
    
#     // Permissions mock
#     const originalQuery = window.navigator.permissions.query;
#     window.navigator.permissions.query = (parameters) => (
#         parameters.name === 'notifications' ?
#             Promise.resolve({ state: Notification.permission }) :
#             originalQuery(parameters)
#     );
    
#     // WebGL fingerprinting
#     const getParameter = WebGLRenderingContext.prototype.getParameter;
#     WebGLRenderingContext.prototype.getParameter = function(parameter) {
#         if (parameter === 37445) return 'Intel Inc. (0x8086)';
#         if (parameter === 37446) return 'Intel(R) Iris(TM) Xe Graphics';
#         return getParameter.call(this, parameter);
#     };
    
#     // Screen properties
#     Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
#     Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });
    
#     // Hardware concurrency
#     Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    
#     // Mock touch support
#     Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
    
#     // Clean user agent
#     Object.defineProperty(navigator, 'userAgent', {
#         get: () => navigator.userAgent.replace(/Headless|headless/i, 'Chrome'),
#     });
# }
# """)

#             # Simulate human mouse movement
#             page.add_init_script("""
# () => {
#                 const events = ['mousemove', 'mousedown', 'mouseup', 'click'];
#                 let lastX = window.innerWidth / 2;
#                 let lastY = window.innerHeight / 2;
                
#                 events.forEach(event => {
#                     document.addEventListener(event, (e) => {
#                         if (Math.random() < 0.1) {
#                             const nowX = lastX + (Math.random() - 0.5) * 100;
#                             const nowY = lastY + (Math.random() - 0.5) * 100;
#                             lastX = nowX;
#                             lastY = nowY;
#                         }
#                     });
#                 });
#             }
#             """)

#             try:
#                 print(f"Navigating to: {base_url}")
#                 page.goto(base_url, timeout=60000, wait_until="networkidle")
#                 print(f"Landed on: {page.url}")

#                 if not is_aliexpress_url(page.url):
#                     print(f"Redirected off AliExpress to {page.url} — skipping.")
#                     browser.close()
#                     continue

#                 random_delay(5.0, 8.0)

#                 if detect_recaptcha(page):
#                     print("CAPTCHA detected — retrying.")
#                     browser.close()
#                     continue

#                 # ← FIXED: Much more robust title detection with longer timeout
#                 print("Waiting for page to fully load...")
#                 page.wait_for_timeout(5000)

#                 title_selectors = [
#                     "[data-pl='product-title']",
#                     ".product-title-text",
#                     ".title--wrap--UUHae_g h1",
#                     ".title--wrap--NWOaiSp h1",
#                     "h1",
#                     ".product-title-container h1",
#                     "[data-spm-anchor-id] h1",
#                     ".sku-title",
#                     ".product-name",
#                 ]

#                 title = ""
#                 for selector in title_selectors:
#                     try:
#                         el = page.wait_for_selector(selector, timeout=10000, state="visible")
#                         if el:
#                             title = el.text_content().strip()
#                             if title and len(title) > 10 and "verify" not in title.lower():
#                                 print(f"✓ Title found via '{selector}': {title[:80]}...")
#                                 break
#                     except Exception:
#                         continue

#                 if not title:
#                     print("❌ No title found after trying all selectors")
#                     # Save screenshot for debugging
#                     try:
#                         page.screenshot(path=f"debug_attempt_{attempt}.png")
#                         print(f"Screenshot saved: debug_attempt_{attempt}.png")
#                     except:
#                         pass
#                     browser.close()
#                     continue

#                 # Scroll more gently
#                 safe_scroll(page, steps=6)
#                 random_delay(2.0, 4.0)

#                 if detect_recaptcha(page):
#                     print("CAPTCHA after scroll — retrying.")
#                     browser.close()
#                     continue

#                 # Try to activate description tab
#                 desc_selectors = ['#nav-description', '.tab-description', '[data-role="description"]']
#                 for selector in desc_selectors:
#                     try:
#                         tab = page.query_selector(selector)
#                         if tab:
#                             tab.scroll_into_view_if_needed()
#                             random_delay(1.0, 2.0)
#                             tab.click(force=True)
#                             print(f"Clicked description tab: {selector}")
#                             page.wait_for_timeout(3000)
#                             break
#                     except:
#                         continue

#                 random_delay(2.0, 4.0)

#                 # Extract content
#                 description_text = ""
#                 images = []

#                 try:
#                     result = page.evaluate(SHADOW_DOM_EXTRACT_JS)
#                     if result and "error" not in result:
#                         description_text = result.get("text", "")
#                         images = result.get("images", [])
#                         print(f"✓ Extracted: {len(description_text)} chars, {len(images)} images")
#                 except Exception as e:
#                     print(f"Shadow DOM extraction failed: {e}")

#                 # Fallback extraction
#                 if not description_text:
#                     try:
#                         texts = page.evaluate("""
# () => {
#                             const texts = [];
#                             document.querySelectorAll('p, li, .product-description, .detail-content')
#                                 .forEach(el => {
#                                     const t = el.innerText.trim();
#                                     if (t.length > 20) texts.push(t);
#                                 });
#                             return texts.slice(0, 10).join(' ');
#                         }
#                         """)
#                         description_text = texts if texts else ""
#                     except:
#                         pass

#                 images = list(dict.fromkeys([normalize_img_url(img) for img in images if "alicdn" in img]))

#                 browser.close()

#                 return {
#                     "title": clean_text(title),
#                     "description_text": clean_text(description_text),
#                     "images": images,
#                 }

#             except Exception as e:
#                 print(f"Navigation/Extraction error: {e}")
#                 try:
#                     page.screenshot(path=f"error_attempt_{attempt}.png")
#                 except:
#                     pass
#                 browser.close()
#                 continue

#     print(f"All {max_retries} attempts exhausted.")
#     return empty_result
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
#     """Accept any regional AliExpress domain: .com, .us, .co.uk, .it, etc."""
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
#     is_product_page = is_aliexpress_url(page.url) and len(page_title) > 40
#     if not is_product_page:
#         block_titles = ["verify", "captcha", "robot", "access denied", "blocked", "aanmelden", "sign in"]
#         if any(kw in page_title_lower for kw in block_titles):
#             print(f"Block detected via page title: '{page_title}'")
#             return True

#     return False


# def safe_scroll(page, steps: int = 12) -> bool:
#     """
#     Scroll gradually. Returns False if the page closed mid-scroll
#     (happens when AliExpress fires a mid-page redirect).
#     """
#     for _ in range(steps):
#         try:
#             if page.is_closed():
#                 print("Page closed during scroll — likely a redirect.")
#                 return False
#             page.mouse.wheel(0, random.randint(200, 400))
#             page.wait_for_timeout(random.randint(200, 400))
#         except Exception as e:
#             print(f"Scroll interrupted: {e}")
#             return False
#     return True


# def random_viewport():
#     return {
#         "width": random.choice([1280, 1366, 1440, 1536, 1600]),
#         "height": random.choice([720, 768, 864, 900]),
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


# # ── Shadow DOM extraction ──────────────────────────────────────────────────────
# #
# # CONFIRMED FROM DEBUG OUTPUT:
# #   - Shadow host = `#product-description > div`  (anonymous div, no id/attrs)
# #   - Before #nav-description click: shadow root = CSS only (~3634 chars)
# #   - After click: XHR fires, real content appears, text.length > 4500
# #   - Leaf nodes confirmed: product description paragraphs + feature headings
# #   - Junk confirmed: hero-video, Add to Cart (x4), Find More MoKo Cases,
# #     comparison table prices, brand carousel duplicates

# SHADOW_DOM_EXTRACT_JS = """
# () => {
#     const container = document.querySelector('#product-description');
#     if (!container) return { error: 'no #product-description' };

#     const host = container.querySelector(':scope > div');
#     if (!host)            return { error: 'no child div in #product-description' };
#     if (!host.shadowRoot) return { error: 'no shadowRoot on child div' };

#     const root = host.shadowRoot;

#     // Strip junk before traversal so their text never reaches the collector
#     [
#         'style', 'script',
#         '.a-price', '.a-offscreen', '.a-icon-alt',
#         '.comparison-table', '.premium-aplus-module-5',
#         '.apm-brand-story-carousel-container',
#         '.vse-player-container',
#         '.add-to-cart',
#         '.aplus-carousel-actions',
#         '.aplus-carousel-index',
#         '.aplus-review-right-padding',
#     ].forEach(sel => root.querySelectorAll(sel).forEach(el => el.remove()));

#     // Leaf-node text collection — only elements with zero child elements
#     const JUNK = new Set([
#         'hero-video', 'product description', 'add to cart',
#         'find more moko cases', 'customer reviews', 'price',
#         'compatibility', 'material', 'features',
#         'multi-color options', 'viewing & typing angles',
#     ]);

#     const texts = [];
#     const seen  = new Set();

#     function isCollectable(el) {
#         if (el.children.length === 0) return true;
#         return Array.from(el.children).every(c => c.tagName === 'BR');
#     }

#     for (const el of root.querySelectorAll('p,h1,h2,h3,h4,h5,li,span,td,div')) {
#         if (!isCollectable(el)) continue;

#         const t = (el.innerText || el.textContent || '').trim();
#         if (!t || t.length < 6) continue;
#         if (/^[\d\s\.\,\$\€\£\¥\%\+\-\&nbsp;]+$/.test(t)) continue;
#         if (JUNK.has(t.toLowerCase())) continue;
#         if (seen.has(t)) continue;

#         seen.add(t);
#         texts.push(t);
#     }

#     // alicdn images — deduplicated
#     const images  = [];
#     const seenSrc = new Set();
#     root.querySelectorAll('img').forEach(img => {
#         let src = img.getAttribute('src') || img.getAttribute('data-src') || '';
#         if (!src) return;
#         src = src.trim();
#         if (src.startsWith('//')) src = 'https:' + src;
#         if (src.includes('alicdn') && !seenSrc.has(src)) {
#             seenSrc.add(src);
#             images.push(src);
#         }
#     });

#     return { text: texts.join(' '), images };
# }
# """


# # ── Main scraper ───────────────────────────────────────────────────────────────

# def extract_aliexpress_product(url: str, max_retries: int = 3) -> dict:
#     print("Starting scrape...")

#     base_url = url.split('#')[0].strip()
#     if not base_url.startswith("http"):
#         base_url = "https://" + base_url

#     empty_result = {"title": "", "description_text": "", "images": []}

#     for attempt in range(1, max_retries + 1):
#         print(f"\n── Attempt {attempt}/{max_retries} ──")

#         if attempt > 1:
#             rotate_tor_circuit()
#             random_delay(8.0, 15.0)

#         with sync_playwright() as p:
#             browser = p.chromium.launch(
#                 headless=True,
#     proxy={"server": "socks5://127.0.0.1:9050"},
#                 args=[
#                     "--disable-blink-features=AutomationControlled",
#                     "--no-sandbox",
#                     "--disable-dev-shm-usage",
#                     "--disable-gpu",
#                     "--no-zygote",
#                 ]
#             )
#             context = browser.new_context(
#                 user_agent=random.choice([
#                     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
#                     "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
#                     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
#                     "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
#                 ]),
#                 viewport=random_viewport(),
#                 locale="en-US",
#                 timezone_id=random.choice([
#                     "America/New_York",
#                     "America/Chicago",
#                     "America/Los_Angeles",
#                 ]),
#                 extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
#                 java_script_enabled=True,
#                 bypass_csp=True,
#             )
#             page = context.new_page()
#             page.add_init_script("""
# (() => {
#     Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
#     Object.defineProperty(navigator, 'plugins', {
#         get: () => [
#             { name: 'Chrome PDF Plugin',  filename: 'internal-pdf-viewer',             description: 'Portable Document Format' },
#             { name: 'Chrome PDF Viewer',  filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
#             { name: 'Native Client',      filename: 'internal-nacl-plugin',             description: '' },
#         ],
#     });
#     Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
#     window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
#     const originalQuery = window.navigator.permissions.query;
#     window.navigator.permissions.query = (parameters) => (
#         parameters.name === 'notifications'
#             ? Promise.resolve({ state: Notification.permission })
#             : originalQuery(parameters)
#     );
#     const getParameter = WebGLRenderingContext.prototype.getParameter;
#     WebGLRenderingContext.prototype.getParameter = function(parameter) {
#         if (parameter === 37445) return 'Intel Inc.';
#         if (parameter === 37446) return 'Intel Iris OpenGL Engine';
#         return getParameter.call(this, parameter);
#     };
#     Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
#     Object.defineProperty(screen, 'pixelDepth',  { get: () => 24 });
#     Object.defineProperty(navigator, 'userAgent', {
#         get: () => navigator.userAgent.replace('HeadlessChrome', 'Chrome'),
#     });
# })();
# """)

#             # ── Navigate ──────────────────────────────────────────────────────
#             try:
#                 page.goto(base_url, timeout=120000, wait_until="domcontentloaded")
#             except Exception as e:
#                 print(f"Navigation failed: {e}")
#                 browser.close()
#                 continue

#             print(f"Landed on: {page.url}")

#             # Reject if we ended up off AliExpress entirely
#             if not is_aliexpress_url(page.url):
#                 print(f"Redirected off AliExpress to {page.url} — skipping.")
#                 browser.close()
#                 continue

#             random_delay(3.0, 6.0)

#             if detect_recaptcha(page):
#                 print("CAPTCHA detected — retrying.")
#                 browser.close()
#                 continue
#             # Wait for title element before scrolling
#             # Scroll can trigger redirects that close the page — secure title first
#             try:
#                 page.wait_for_selector("[data-pl='product-title']", timeout=20000, state="visible")
#                 print("Title element confirmed visible.")
#             except Exception:
#                 print("Title selector timed out — trying h1 fallback...")
#                 try:
#                     page.wait_for_selector("h1", timeout=5000, state="visible")
#                 except Exception:
#                     print("No h1 found either — page likely blocked.")
#                     browser.close()
#                     continue
            
#             # ── Scroll ────────────────────────────────────────────────────────
#             scroll_ok = safe_scroll(page, steps=12)
#             if not scroll_ok:
#                 print("Scroll failed — extracting title only before retry.")

#             random_delay(1.0, 2.0)

#             if page.is_closed():
#                 print("Page closed unexpectedly after scroll.")
#                 browser.close()
#                 continue

#             if detect_recaptcha(page):
#                 print("CAPTCHA detected after scroll — retrying.")
#                 browser.close()
#                 continue

#             # ── Extract title ─────────────────────────────────────────────────
#             def safe_text(sel: str) -> str:
#                 try:
#                     el = page.query_selector(sel)
#                     return el.text_content().strip() if el else ""
#                 except Exception:
#                     return ""

#             BLOCKED = {
#                 "aliexpress", "", "aanmelden", "sign in",
#                 "log in", "login", "verify", "robot",
#             }
#             title = ""
#             for sel in [
#                 "[data-pl='product-title']",
#                 ".title--wrap--UUHae_g h1",
#                 ".title--wrap--NWOaiSp h1",
#                 ".product-title-text",
#                 "#root h1",
#             ]:
#                 candidate = safe_text(sel)
#                 if candidate and candidate.lower().strip() not in BLOCKED:
#                     title = candidate
#                     print(f"Title via '{sel}': {title[:70]}")
#                     break

#             if not title:
#                 print("Title not found — page likely blocked.")
#                 browser.close()
#                 continue

#             # ── Click #nav-description to trigger description XHR ────────────
#             description_text = ""
#             images = []

#             try:
#                 nav_desc = page.query_selector('#nav-description')
#                 if nav_desc:
#                     nav_desc.scroll_into_view_if_needed()
#                     random_delay(1.0, 2.0)
#                     nav_desc.click(force=True)
#                     print("Clicked #nav-description — waiting for XHR...")

#                     try:
#                         page.wait_for_function(
#                             """() => {
#                                 const host = document.querySelector(
#                                     '#product-description > div'
#                                 );
#                                 if (!host || !host.shadowRoot) return false;
#                                 return (host.shadowRoot.textContent || '').trim().length > 4500;
#                             }""",
#                             timeout=15000,
#                         )
#                         print("Description content loaded.")
#                     except Exception:
#                         print("XHR wait timed out — attempting extraction anyway...")

#                     random_delay(1.0, 2.0)
#                 else:
#                     print("#nav-description not found — description XHR won't fire.")
#             except Exception as e:
#                 print(f"Could not click #nav-description: {e}")

#             # ── Extract via Shadow DOM JS ─────────────────────────────────────
#             try:
#                 result = page.evaluate(SHADOW_DOM_EXTRACT_JS)
#                 if result and "error" not in result:
#                     description_text = result.get("text", "").strip()
#                     images           = result.get("images", [])
#                     print(f"Shadow DOM: {len(description_text)} chars, {len(images)} images")
#                 elif result and "error" in result:
#                     print(f"Shadow DOM JS returned: {result['error']}")
#             except Exception as e:
#                 print(f"Shadow DOM evaluate error: {e}")

#             # ── Fallback: plain DOM (older pages without shadow root) ─────────
#             if not description_text and not images:
#                 print("Shadow DOM empty — trying plain DOM fallback...")
#                 try:
#                     container = page.query_selector("#product-description")
#                     if container:
#                         for el in container.query_selector_all("p, span, li, h3, h4, div"):
#                             try:
#                                 only_br = el.evaluate(
#                                     "e => e.children.length === 0 || "
#                                     "Array.from(e.children).every(c => c.tagName === 'BR')"
#                                 )
#                                 text = el.text_content().strip()
#                                 if only_br and text and len(text) >= 6:
#                                     if not re.match(r'^[\d\s\.\,\$\€\£\¥\%\+\-]+$', text):
#                                         description_text += text + " "
#                             except Exception:
#                                 pass

#                         for img in container.query_selector_all("img"):
#                             src = img.get_attribute("src") or img.get_attribute("data-src") or ""
#                             src = normalize_img_url(src)
#                             if "alicdn" in src:
#                                 images.append(src)

#                         if description_text:
#                             dollar_ratio = description_text.count("$") / max(len(description_text), 1)
#                             if dollar_ratio > 0.02:
#                                 print("Plain DOM text looks like price data — discarding.")
#                                 description_text = ""
#                 except Exception as e:
#                     print(f"Plain DOM fallback error: {e}")

#             images = list(dict.fromkeys(images))

#             if not description_text:
#                 print("No description text (seller may use image-only description).")
#             if not images:
#                 print("No description images extracted.")

#             browser.close()

#             return {
#                 "title":            clean_text(title),
#                 "description_text": clean_text(description_text),
#                 "images":           images,
#             }

#     print(f"All {max_retries} attempts exhausted.")
#     return empty_result
