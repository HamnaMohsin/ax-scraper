import re
import time
import random
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from stem import Signal
from stem.control import Controller


# ─────────────────────────────────────────────
# SWEDEN FORCING HELPERS
# ─────────────────────────────────────────────

def build_sweden_url(url: str) -> str:
    """Rewrite URL to always target Swedish/global store"""
    # Normalize regional domains → www.aliexpress.com
    url = re.sub(r'https?://[a-z]{2}\.aliexpress\.com', 'https://www.aliexpress.com', url)
    url = re.sub(r'https?://www\.aliexpress\.us', 'https://www.aliexpress.com', url)

    # Force gateway param
    if 'gatewayAdapt=' in url:
        url = re.sub(r'gatewayAdapt=[^&]+', 'gatewayAdapt=glo2swe', url)
    else:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}gatewayAdapt=glo2swe"

    return url


def force_sweden_context(page, context):
    """
    Pre-set cookies and headers so AliExpress serves the Swedish/global
    store regardless of the exit node's IP geolocation.
    Must be called AFTER new_page() but BEFORE page.goto().
    """
    context.add_cookies([
        {
            "name": "aep_usuc_f",
            "value": "site=swe&c_tp=SEK&region=SE&b_locale=en_US",
            "domain": ".aliexpress.com",
            "path": "/",
        },
        {
            "name": "intl_locale",
            "value": "en_US",
            "domain": ".aliexpress.com",
            "path": "/",
        },
        {
            "name": "acs_usuc_f",
            "value": "x_locale=en_US&site=swe",
            "domain": ".aliexpress.com",
            "path": "/",
        },
    ])

    page.set_extra_http_headers({
        "Accept-Language": "en-US,en;q=0.9,sv;q=0.8",
    })
    print("🇸🇪 Sweden context applied (cookies + headers)")


def block_geo_redirects(page):
    """
    Intercept any AliExpress regional redirect (*.aliexpress.us,
    de.aliexpress.com, nl.aliexpress.com, etc.) and rewrite it back
    to www.aliexpress.com with gatewayAdapt=glo2swe before the
    browser follows it.
    """
    regional_pattern = re.compile(
        r'https?://(www\.aliexpress\.us|[a-z]{2}\.aliexpress\.com)/'
    )

    def handle_route(route, request):
        url = request.url
        if regional_pattern.match(url) and "/item/" in url:
            fixed = re.sub(
                r'https?://(www\.aliexpress\.us|[a-z]{2}\.aliexpress\.com)',
                'https://www.aliexpress.com',
                url,
            )
            fixed = re.sub(r'gatewayAdapt=[^&]+', 'gatewayAdapt=glo2swe', fixed)
            if 'gatewayAdapt' not in fixed:
                sep = "&" if "?" in fixed else "?"
                fixed = f"{fixed}{sep}gatewayAdapt=glo2swe"
            print(f"🔀 Geo-redirect intercepted → rewriting to global/SWE")
            print(f"   FROM: {url[:80]}")
            print(f"   TO  : {fixed[:80]}")
            route.continue_(url=fixed)
        else:
            route.continue_()

    page.route("**aliexpress**", handle_route)
    print("🛡️  Geo-redirect interceptor active")


# ─────────────────────────────────────────────
# EXISTING HELPERS (unchanged logic, minor tweaks)
# ─────────────────────────────────────────────

def extract_compliance_info(page) -> dict:
    """
    Click the 'Product compliance information' h2 heading to open the modal,
    then parse manufacturer info, EU responsible person, and product identifier.
    Only visible on GB exit nodes.
    """
    compliance = {}
    print("📋 Extracting compliance info...")

    try:
        heading_selector = "h2.title--title--O6xcB1q"
        heading = page.locator(heading_selector).filter(
            has_text="Product compliance information"
        ).first

        if heading.count() == 0:
            print("   ⚠️ Compliance heading not found (may not be a GB IP or product has none)")
            return compliance

        print("   ✓ Found compliance heading — clicking...")
        heading.click()
        page.wait_for_timeout(2000)

        modal_selector = "div.comet-v2-modal-body"
        try:
            page.wait_for_selector(modal_selector, timeout=8000)
        except:
            print("   ⚠️ Modal did not appear after click")
            return compliance

        modal = page.locator(modal_selector).first
        if modal.count() == 0:
            print("   ⚠️ Modal body not found")
            return compliance

        raw_text = modal.inner_text().strip()
        print(f"   ✓ Modal text ({len(raw_text)} chars):\n      {raw_text[:300]}")

        section_headers = [
            "Manufacturer information",
            "EU responsible person information",
            "Product identifier",
        ]

        lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
        current_section = None
        section_lines: dict[str, list[str]] = {}

        for line in lines:
            matched_header = next(
                (h for h in section_headers if line.lower().startswith(h.lower())),
                None,
            )
            if matched_header:
                current_section = matched_header
                section_lines[current_section] = []
                remainder = line[len(matched_header):].strip().lstrip(":").strip()
                if remainder:
                    section_lines[current_section].append(remainder)
            elif current_section:
                section_lines[current_section].append(line)

        def parse_kv_block(lines_list: list[str]) -> dict:
            result = {}
            for l in lines_list:
                if ":" in l:
                    k, _, v = l.partition(":")
                    k = k.strip()
                    v = v.strip()
                    if k and v and len(k) < 60:
                        result[k] = v
                else:
                    if l and not result.get("value"):
                        result["value"] = l
            return result

        for section, s_lines in section_lines.items():
            parsed = parse_kv_block(s_lines)
            if parsed:
                compliance[section] = parsed
                print(f"   ✅ {section}: {parsed}")

        close_selectors = [
            "button.comet-v2-modal-close",
            "[class*='modal-close']",
            "[aria-label='Close']",
        ]
        for sel in close_selectors:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0:
                    btn.click()
                    page.wait_for_timeout(500)
                    print("   ✓ Closed compliance modal")
                    break
            except:
                continue

    except Exception as e:
        print(f"⚠️ Compliance extraction error: {e}")
        import traceback
        traceback.print_exc()

    return compliance


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def random_delay(min_seconds: float = 1, max_seconds: float = 3):
    delay = random.uniform(min_seconds, max_seconds)
    time.sleep(delay)


def random_viewport():
    viewports = [
        {'width': 1366, 'height': 768},
        {'width': 1920, 'height': 1080},
        {'width': 1440, 'height': 900},
        {'width': 1280, 'height': 720},
    ]
    return random.choice(viewports)


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


def is_captcha_page(page) -> bool:
    """
    Detect if page is a CAPTCHA/block page.
    Fixed: no longer false-positives on reCAPTCHA v3 when a product title exists.
    """
    page_url = page.url.lower()
    page_title = page.title().lower()

    captcha_url_keywords = ["baxia", "punish", "captcha", "verify", "_____tmd_____"]
    if any(kw in page_url for kw in captcha_url_keywords):
        print("❌ CAPTCHA detected in URL")
        return True

    captcha_selectors = [
        ".baxia-punish",
        "#captcha-verify",
        "[id*='captcha']",
        "iframe[src*='geetest']",
        "[class*='captcha']",
    ]

    try:
        frames = page.locator("iframe[src*='recaptcha']")
        for i in range(frames.count()):
            frame = frames.nth(i)
            if not frame.is_visible():
                continue
            box = frame.bounding_box()
            if box and box.get("width", 0) > 200:
                # ── FIX: check if a product title is also present ──
                # If yes, this is reCAPTCHA v3 background scoring, not a block
                try:
                    title_elem = page.locator('[data-pl="product-title"], h1').first
                    if title_elem.count() > 0:
                        title_text = title_elem.inner_text(timeout=3000).strip()
                        if len(title_text) > 5:
                            print(
                                f"ℹ️ reCAPTCHA iframe present (w={box['width']}px) "
                                f"but product title found — likely v3 scoring, not a block"
                            )
                            return False
                except Exception:
                    pass
                print(f"❌ Visible reCAPTCHA challenge (width={box['width']}px)")
                return True
    except Exception:
        pass

    is_product_page = "aliexpress" in page_title and len(page_title) > 40
    block_title_keywords = ["verify", "access", "denied", "blocked", "challenge"]
    if not is_product_page and any(kw in page_title for kw in block_title_keywords):
        print("❌ Block page detected from title")
        return True

    return False


def is_valid_product_page(page) -> bool:
    """Returns True if we have actual product content loaded"""
    try:
        title_sel = '[data-pl="product-title"], h1[class*="title"]'
        elem = page.locator(title_sel).first
        if elem.count() > 0:
            text = elem.inner_text(timeout=3000).strip()
            if len(text) > 10:
                return True
    except Exception:
        pass
    return False


def extract_store_info_universal(page) -> dict:
    store_info = {}
    print("📦 Extracting store info...")

    try:
        print("   🔍 Step 1: Extracting store name...")
        store_name_selector = "span[class*='store-detail--storeName']"
        store_name_elem = page.locator(store_name_selector).first

        if store_name_elem.count() > 0:
            store_name = store_name_elem.inner_text().strip()
            if store_name:
                store_info["Store Name"] = store_name
                print(f"   ✓ Store name: {store_name}")
        else:
            print("   ⚠️ Store name element not found")

        print("   🔍 Step 2: Hovering to reveal store detail popup...")
        store_link_selector = "div[class*='store-detail--storeNameWrap']"
        store_link_elem = page.locator(store_link_selector).first

        if store_link_elem.count() > 0:
            store_link_elem.hover()
            page.wait_for_timeout(1500)
            print("   ✓ Hovered over store element")
        else:
            print("   ⚠️ Store link element not found, skipping hover")

        print("   🔍 Step 3: Extracting popup store details...")
        row_selectors = [
            "div[class*='store-detail'] table tr",
            "div[class*='storeDetail'] table tr",
            "[class*='store-detail--detail'] tr",
        ]

        for row_selector in row_selectors:
            rows = page.locator(row_selector).all()
            if rows:
                print(f"   ✓ Found {len(rows)} rows with: {row_selector}")
                for row in rows:
                    try:
                        cols = row.locator('td').all()
                        if len(cols) >= 2:
                            key = cols[0].inner_text().strip().replace(":", "")
                            value = cols[1].inner_text().strip()
                            if key and value:
                                store_info[key] = value
                                print(f"      {key}: {value}")
                    except:
                        continue
                if len(store_info) > 1:
                    break

        if len(store_info) <= 1:
            print("   🔍 Step 4: Fallback — reading popup text directly...")
            popup_selectors = [
                "div[class*='store-detail--storePopup']",
                "div[class*='store-detail--popup']",
                "div[class*='storePopup']",
                "div[class*='store-detail']:not(a)",
            ]

            for popup_selector in popup_selectors:
                popup = page.locator(popup_selector).first
                if popup.count() > 0:
                    text = popup.inner_text().strip()
                    if text:
                        print(f"   ✓ Popup text ({popup_selector}):\n      {text[:200]}")
                        for line in text.split('\n'):
                            line = line.strip()
                            if ':' in line:
                                parts = line.split(':', 1)
                                key = parts[0].strip()
                                value = parts[1].strip()
                                if key and value and len(key) < 50:
                                    store_info[key] = value
                                    print(f"      {key}: {value}")
                    if len(store_info) > 1:
                        break

        if not store_info:
            print("   ⚠️ Could not extract store information")
        else:
            print(f"   ✅ Store info extracted: {store_info}")

    except Exception as e:
        print(f"⚠️ Store extraction error: {e}")
        import traceback
        traceback.print_exc()

    return store_info


def extract_title_universal(page) -> str:
    print("📌 Extracting title...")

    title_selectors = [
        ('[data-pl="product-title"]', "data-pl product-title"),
        ('h1', "h1 heading"),
        ('[class*="product-title"]', "product-title class"),
        ('[class*="ProductTitle"]', "ProductTitle class"),
        ('span[class*="title"]', "span title class"),
    ]

    for selector, desc in title_selectors:
        try:
            elem = page.locator(selector).first
            if elem.count() > 0:
                title = elem.inner_text().strip()
                if title and len(title) > 10:
                    print(f"✅ Title ({desc}): {title[:80]}...")
                    return title
        except:
            continue

    print("⚠️ Could not extract title")
    return ""


def extract_specifications(page) -> dict:
    specifications = {}
    print("📋 Extracting specifications...")

    try:
        spec_section = page.locator("#nav-specification")
        if spec_section.count() == 0:
            print("   ⚠️ #nav-specification not found")
            return specifications

        spec_section.scroll_into_view_if_needed()
        page.wait_for_timeout(2500)

        view_more_sel = "#nav-specification > button"
        try:
            view_more_btn = page.locator(view_more_sel).first
            if view_more_btn.count() > 0:
                print("   🔽 'View more' button found — clicking...")
                view_more_btn.scroll_into_view_if_needed()
                page.wait_for_timeout(500)
                view_more_btn.click(timeout=5000)
                page.wait_for_timeout(2000)
                print("   ✓ 'View more' clicked — full spec list should be visible")
            else:
                print("   ℹ️ No 'View more' button — spec list already fully expanded")
        except Exception as btn_err:
            print(f"   ⚠️ Could not click 'View more' (non-fatal): {btn_err}")

        try:
            box = spec_section.bounding_box()
            if box:
                bottom = box["y"] + box["height"]
                current = box["y"]
                while current < bottom:
                    page.mouse.wheel(0, 300)
                    page.wait_for_timeout(400)
                    current += 300
                page.evaluate(
                    "el => el.scrollIntoView({block:'start'})",
                    spec_section.element_handle()
                )
                page.wait_for_timeout(1000)
        except Exception as scroll_err:
            print(f"   ⚠️ Scroll-through error (non-fatal): {scroll_err}")

        li_selector = "#nav-specification ul li"
        spec_items = page.locator(li_selector).all()

        if not spec_items:
            print("   ⚠️ No <li> items found inside #nav-specification ul")
            return specifications

        print(f"   ✓ Found {len(spec_items)} spec <li> rows")

        prop_sel  = "[class*='specification--prop']"
        title_sel = "[class*='specification--title'] span, [class*='specTitle'] span"
        desc_sel  = "[class*='specification--desc'] span, [class*='specValue'] span"

        for idx, item in enumerate(spec_items):
            try:
                props = item.locator(prop_sel).all()

                if props:
                    for prop in props:
                        try:
                            t_el = prop.locator(title_sel).first
                            d_el = prop.locator(desc_sel).first
                            key = t_el.inner_text(timeout=3000).strip() if t_el.count() > 0 else ""
                            val = d_el.inner_text(timeout=3000).strip() if d_el.count() > 0 else ""
                            if key and val:
                                specifications[key] = val
                                print(f"      [A] {key}: {val}")
                        except Exception:
                            continue
                else:
                    spans = item.locator("span").all()
                    if len(spans) >= 2:
                        try:
                            key = spans[0].inner_text(timeout=2000).strip()
                            val = spans[1].inner_text(timeout=2000).strip()
                            if key and val:
                                specifications[key] = val
                                print(f"      [B] {key}: {val}")
                            continue
                        except Exception:
                            pass

                    try:
                        raw = item.inner_text(timeout=2000).strip()
                        lines = [l.strip() for l in raw.splitlines() if l.strip()]
                        if len(lines) >= 2:
                            key, val = lines[0], lines[1]
                            if key and val:
                                specifications[key] = val
                                print(f"      [C] {key}: {val}")
                        elif len(lines) == 1 and ":" in lines[0]:
                            k, _, v = lines[0].partition(":")
                            if k.strip() and v.strip():
                                specifications[k.strip()] = v.strip()
                                print(f"      [C:] {k.strip()}: {v.strip()}")
                    except Exception:
                        continue

            except Exception as row_err:
                print(f"   ⚠️ Row {idx} error: {row_err}")
                continue

        print(f"   ✅ Specifications extracted: {len(specifications)} fields")

    except Exception as e:
        print(f"⚠️ Specification extraction error: {e}")
        import traceback
        traceback.print_exc()

    return specifications


# ─────────────────────────────────────────────
# MAIN EXTRACTION FUNCTION
# ─────────────────────────────────────────────

def extract_aliexpress_product(url: str) -> dict:
    """
    Extract AliExpress product data with Tor routing, anti-detection,
    and forced Sweden/global store (regardless of exit node IP).
    """
    # Always target Swedish/global store
    url = build_sweden_url(url)
    print(f"\n🔍 Scraping: {url}")

    empty_result = {
        "title": "",
        "description_text": "",
        "images": [],
        "store_info": {},
        "compliance_info": {},
        "specifications": {},
    }

    max_retries = 3

    for attempt in range(max_retries):
        print(f"\n📍 Attempt {attempt + 1}/{max_retries}")

        if attempt > 0:
            print("🔄 Rotating Tor circuit...")
            rotate_tor_circuit()
            wait_time = 20 + (attempt * 5)
            print(f"   Waiting {wait_time}s before next attempt...")
            time.sleep(wait_time)

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

            # ── Create context with Stockholm identity ──
            context = browser.new_context(
                viewport=random_viewport(),
                user_agent=random.choice([
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                ]),
                timezone_id="Europe/Stockholm",
                locale="en-US",
                geolocation={"latitude": 59.3293, "longitude": 18.0686},  # Stockholm
                permissions=["geolocation"],
            )

            page = context.new_page()

            # Anti-detection patches
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page.add_init_script("Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]})")

            # ── Apply Sweden fixes before navigation ──
            block_geo_redirects(page)          # Fix 3: intercept regional redirects
            force_sweden_context(page, context) # Fix 1: cookies + headers

            try:
                # NAVIGATION
                print("📡 Loading page...")
                page.goto(url, timeout=120000, wait_until="domcontentloaded")

                # Wait for JS to settle before checking for blocks
                page.wait_for_timeout(5000)

                current_url = page.url
                if current_url != url:
                    print(f"⚠️ Final URL: {current_url}")

                # ── Wait for product title as a proxy for "real page loaded" ──
                print("⏳ Waiting for product title to appear...")
                try:
                    page.wait_for_selector(
                        '[data-pl="product-title"], h1',
                        timeout=15000
                    )
                    print("✅ Product title found — real page loaded")
                except Exception:
                    print("⚠️ Product title not found within 15s")

                # ── CAPTCHA check (with v3 false-positive fix) ──
                if is_captcha_page(page) and not is_valid_product_page(page):
                    print("⚠️ Blocked and no product content — rotating IP and retrying...")
                    browser.close()
                    continue
                elif is_captcha_page(page):
                    print("ℹ️ CAPTCHA present but product loaded — proceeding anyway")

                print("⏳ Scrolling to load images...")
                try:
                    for _ in range(3):
                        page.mouse.wheel(0, random.randint(150, 300))
                        time.sleep(random.uniform(0.2, 0.6))
                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(1)
                except Exception as e:
                    print(f"⚠️ Scroll error: {e}")

                # Second CAPTCHA check after scroll
                if is_captcha_page(page) and not is_valid_product_page(page):
                    print("⚠️ CAPTCHA after scroll — rotating IP and retrying...")
                    browser.close()
                    continue

                # EXTRACT TITLE
                title = extract_title_universal(page)

                # EXTRACT STORE INFO
                store_info = extract_store_info_universal(page)

                # EXTRACT COMPLIANCE INFO
                compliance_info = extract_compliance_info(page)

                # EXTRACT DESCRIPTION
                print("📝 Loading description...")
                description_text = ""
                description_images = []

                try:
                    print("   Clicking Description tab...")
                    try:
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(300)

                        buttons = page.locator('a.comet-v2-anchor-link').all()
                        for btn in buttons:
                            if 'description' in btn.inner_text().strip().lower():
                                print("   ✓ Found Description button (comet-v2-anchor-link)")
                                btn.click(force=True, timeout=2000)
                                print("   ⏳ Waiting for description content to load...")
                                page.wait_for_timeout(3000)
                                try:
                                    page.locator('#product-description').scroll_into_view_if_needed()
                                    page.wait_for_timeout(2000)
                                except:
                                    pass
                                page.wait_for_timeout(3000)
                                print("   ✓ Clicked Description tab")
                                break
                    except Exception as e:
                        print(f"   ⚠️ Description tab click error: {e}")

                    # Method 0: paragraph text
                    print("   🎯 Method 0: Extracting paragraph text...")
                    method0_text = ""
                    try:
                        all_paragraphs = page.locator('#product-description p').all()
                        all_text_parts = []
                        for p in all_paragraphs:
                            try:
                                txt = p.inner_text(timeout=2000).strip()
                                if txt and len(txt) > 2:
                                    all_text_parts.append(txt)
                            except:
                                pass
                        if all_text_parts:
                            method0_text = ' '.join(all_text_parts)
                            method0_text = re.sub(r'\s+', ' ', method0_text).strip()
                            print(f"   ✓ Method 0: {len(method0_text)} chars")
                        else:
                            print("   ⚠️ Method 0: no <p> content found")
                    except Exception as e:
                        print(f"   ⚠️ Method 0 failed: {e}")

                    # Method 1: inner_text on full container
                    desc_container = page.locator('#product-description').first

                    if desc_container.count() > 0:
                        print("   ✓ Found #product-description container")
                        print("   🎯 Method 1: inner_text() on container...")

                        method1_text = desc_container.inner_text(timeout=5000).strip()
                        method1_text = re.sub(r'\s+', ' ', method1_text).strip()
                        print(f"   ✓ Method 1: {len(method1_text)} chars")

                        if len(method1_text) < 100:
                            print("   ⏳ Content short, waiting 5s and retrying...")
                            page.wait_for_timeout(5000)
                            method1_text = desc_container.inner_text(timeout=5000).strip()
                            method1_text = re.sub(r'\s+', ' ', method1_text).strip()
                            print(f"   ✓ Method 1 after retry: {len(method1_text)} chars")

                        parts = [t for t in [method0_text, method1_text] if t]
                        description_text = ' '.join(parts)
                        description_text = re.sub(r'\s+', ' ', description_text).strip()
                        print(f"   ✅ Combined: {len(description_text)} chars")

                        # Image extraction
                        print("   🖼️ Extracting images...")
                        all_imgs = desc_container.locator('img').all()
                        print(f"      Found {len(all_imgs)} <img> tags")

                        for img in all_imgs:
                            src = (img.get_attribute("src") or
                                   img.get_attribute("data-src") or
                                   img.get_attribute("data-lazy-src"))
                            if src and "alicdn.com" in src:
                                clean_src = src.split('?')[0]
                                if clean_src not in description_images:
                                    description_images.append(clean_src)

                        description_images = [
                            img for img in description_images
                            if len(img) > 50 and not any(
                                bad in img.lower()
                                for bad in ['icon', 'logo', '20x20', '50x50', '100x100']
                            )
                        ][:20]
                        print(f"   ✓ Images: {len(description_images)}")

                        if description_images:
                            for i, img_url in enumerate(description_images[:3], 1):
                                print(f"      {i}. {img_url[:60]}...")
                    else:
                        print("   ❌ #product-description not found")

                except Exception as e:
                    print(f"⚠️ Description extraction error: {e}")

                # EXTRACT SPECIFICATIONS
                specifications = extract_specifications(page)

                browser.close()

                result = {
                    "title":            title if isinstance(title, str) else "",
                    "description_text": description_text if isinstance(description_text, str) else "",
                    "images":           description_images if isinstance(description_images, list) else [],
                    "store_info":       store_info if isinstance(store_info, dict) else {},
                    "compliance_info":  compliance_info if isinstance(compliance_info, dict) else {},
                    "specifications":   specifications if isinstance(specifications, dict) else {},
                }

                print(f"\n🔍 DEBUG RETURN VALUES:")
                print(f"   title: {len(result['title'])} chars")
                print(f"   description_text: {len(result['description_text'])} chars")
                print(f"   images: {len(result['images'])} images")
                print(f"   store_info: {result['store_info']}")
                print(f"   compliance_info: {result['compliance_info']}")
                print(f"✅ Extraction successful on attempt {attempt + 1}\n")
                return result

            except PlaywrightTimeoutError as e:
                print(f"⚠️ Timeout on attempt {attempt + 1}: {e}")
                browser.close()
                continue

            except Exception as e:
                print(f"❌ Error on attempt {attempt + 1}: {e}")
                import traceback
                traceback.print_exc()
                try:
                    browser.close()
                except:
                    pass
                continue

    print(f"❌ Failed after {max_retries} attempts")
    return empty_result
