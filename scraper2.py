import re
import time
import random
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from stem import Signal
from stem.control import Controller

def extract_compliance_info(page) -> dict:
    """
    Extract manufacturer and compliance info from the product compliance modal.
    Clicks the compliance link to open the modal, then parses the content.
    """
    compliance = {}

    print("📋 Extracting compliance/manufacturer info...")

    try:
        # Click the compliance link to open the modal
        compliance_selectors = [
            "a[href*='compliance']",
            "[data-spm*='compliance']",
            "span:has-text('Product compliance')",
            "a:has-text('compliance')",
        ]
        for sel in compliance_selectors:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0:
                    btn.click(force=True, timeout=3000)
                    page.wait_for_timeout(2000)
                    print(f"   ✓ Clicked compliance trigger: {sel}")
                    break
            except Exception:
                continue

        # Wait for modal
        page.wait_for_selector(".comet-v2-modal-body", timeout=5000)

        # Get the modal body HTML
        modal = page.locator(".comet-v2-modal-body").first
        if modal.count() == 0:
            print("   ⚠️ Modal not found")
            return compliance

        # Parse all <p> blocks inside the modal
        paragraphs = modal.locator("p").all()
        for para in paragraphs:
            try:
                html = para.inner_html(timeout=2000)
                text = para.inner_text(timeout=2000).strip()
                if not text:
                    continue

                # Detect section heading from <strong> tag
                heading_el = para.locator("strong").first
                heading = heading_el.inner_text().strip() if heading_el.count() > 0 else ""

                # Parse key:value pairs from <br>-separated lines
                lines = re.split(r'<br\s*/?>', html, flags=re.IGNORECASE)
                section_data = {}
                for line in lines:
                    line_text = BeautifulSoup(line, "html.parser").get_text().strip()
                    if ':' in line_text:
                        key, _, value = line_text.partition(':')
                        key = key.strip()
                        value = value.strip()
                        if key and value and len(key) < 60:
                            section_data[key] = value

                if heading and section_data:
                    compliance[heading] = section_data
                    print(f"   ✓ {heading}: {section_data}")
                elif section_data:
                    compliance.update(section_data)

            except Exception:
                continue

        # Also grab EU responsible person (outside <p> tags)
        try:
            body_text = modal.inner_html(timeout=3000)
            # Find text between </p> tags that has key:value pairs
            raw = BeautifulSoup(body_text, "html.parser")
            for br_block in raw.find_all(string=re.compile(r'Name:|Address:|Email:')):
                parent_text = br_block.parent.get_text()
                for line in parent_text.split('\n'):
                    if ':' in line:
                        key, _, value = line.partition(':')
                        key = key.strip()
                        value = value.strip()
                        if key and value and len(key) < 60:
                            compliance.setdefault("EU Responsible Person", {})[key] = value
        except Exception:
            pass

        # Close modal
        try:
            page.locator(".comet-v2-modal-close").click(timeout=2000)
        except Exception:
            pass

        print(f"   ✅ Compliance info: {compliance}")

    except Exception as e:
        print(f"   ⚠️ Compliance extraction error: {e}")

    return compliance
def clean_text(text: str) -> str:
    """Clean and normalize text"""
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def random_delay(min_seconds: float = 1, max_seconds: float = 3):
    """Random delay to mimic human behavior"""
    delay = random.uniform(min_seconds, max_seconds)
    time.sleep(delay)


def random_viewport():
    """Return random viewport size"""
    viewports = [
        {'width': 1366, 'height': 768},
        {'width': 1920, 'height': 1080},
        {'width': 1440, 'height': 900},
        {'width': 1280, 'height': 720},
    ]
    return random.choice(viewports)


def rotate_tor_circuit():
    """Rotate Tor circuit to get new exit IP - wait longer for actual change"""
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
    """Detect if page is a CAPTCHA/block page - multiple selectors"""
    page_url = page.url.lower()
    page_title = page.title().lower()

    captcha_url_keywords = ["baxia", "punish", "captcha", "verify", "_____tmd_____"]
    if any(kw in page_url for kw in captcha_url_keywords):
        print("❌ CAPTCHA detected in URL")
        return True

    captcha_selectors = [
        "iframe[src*='recaptcha']",
        ".baxia-punish",
        "#captcha-verify",
        "[id*='captcha']",
        "iframe[src*='geetest']",
        "[class*='captcha']",
    ]

    for selector in captcha_selectors:
        try:
            if page.locator(selector).count() > 0:
                print(f"❌ CAPTCHA detected: {selector}")
                return True
        except:
            continue

    is_product_page = "aliexpress" in page_title and len(page_title) > 40
    block_title_keywords = ["verify", "access", "denied", "blocked", "challenge"]
    if not is_product_page and any(kw in page_title for kw in block_title_keywords):
        print("❌ Block page detected from title")
        return True

    return False



def extract_store_info_universal(page) -> dict:
    """Extract store info by hovering over the store element to trigger the popup."""
    store_info = {}
 
    print("📦 Extracting store info...")
 
    try:
        # Step 1: Extract store name directly from known selector (always visible)
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
 
        # Step 2: Hover over the store link to trigger the popup
        print("   🔍 Step 2: Hovering to reveal store detail popup...")
        store_link_selector = "div[class*='store-detail--storeNameWrap']"
        store_link_elem = page.locator(store_link_selector).first
 
        if store_link_elem.count() > 0:
            store_link_elem.hover()
            page.wait_for_timeout(1500)
            print("   ✓ Hovered over store element")
        else:
            print("   ⚠️ Store link element not found, skipping hover")
 
        # Step 3: Extract all key-value rows from the popup (renders after hover)
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
 
        # Step 4: Fallback — read visible popup text and parse key: value lines
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
    """Extract title - try multiple selectors"""

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


def extract_aliexpress_product(url: str) -> dict:
    """
    Extract AliExpress product data with Tor routing and anti-detection.
    """

    print(f"\n🔍 Scraping: {url}")

    empty_result = {
        "title": "",
        "description_text": "",
        "images": [],
        "store_info": {}
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

            page = browser.new_page(
                viewport=random_viewport(),
                user_agent=random.choice([
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                ]),
                timezone_id=random.choice([
                    'America/New_York',
                    'America/Chicago',
                    'America/Denver',
                    'America/Los_Angeles',
                ])
            )

            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page.add_init_script("Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]})")

            try:
                # NAVIGATION
                print("📡 Loading page...")
                page.goto(url, timeout=120000, wait_until="domcontentloaded")
                time.sleep(2)

                current_url = page.url
                if current_url != url:
                    print(f"⚠️ Redirected to: {current_url}")

                if is_captcha_page(page):
                    print("⚠️ CAPTCHA detected - rotating IP and retrying...")
                    browser.close()
                    continue

                print("⏳ Waiting for page to render...")
                time.sleep(8)

                print("⏳ Scrolling to load images...")
                try:
                    for _ in range(3):
                        page.mouse.wheel(0, random.randint(150, 300))
                        time.sleep(random.uniform(0.2, 0.6))
                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(1)
                except Exception as e:
                    print(f"⚠️ Scroll error: {e}")

                if is_captcha_page(page):
                    print("⚠️ CAPTCHA after scroll - rotating IP and retrying...")
                    browser.close()
                    continue

                # EXTRACT TITLE
                title = extract_title_universal(page)

                # EXTRACT STORE INFO
                store_info = extract_store_info_universal(page)

                # EXTRACT DESCRIPTION
                print("📝 Loading description...")
                description_text = ""
                description_images = []

                try:
                    # Click description tab
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

                    # METHOD 0: Extract all <p> tags inside #product-description
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

                    # METHOD 1: inner_text() on full container
                    desc_container = page.locator('#product-description').first

                    if desc_container.count() > 0:
                        print("   ✓ Found #product-description container")
                        print("   🎯 Method 1: inner_text() on container...")

                        method1_text = desc_container.inner_text(timeout=5000).strip()
                        method1_text = re.sub(r'\s+', ' ', method1_text).strip()
                        print(f"   ✓ Method 1: {len(method1_text)} chars")

                        # Retry once if too short
                        if len(method1_text) < 100:
                            print("   ⏳ Content short, waiting 5s and retrying...")
                            page.wait_for_timeout(5000)
                            method1_text = desc_container.inner_text(timeout=5000).strip()
                            method1_text = re.sub(r'\s+', ' ', method1_text).strip()
                            print(f"   ✓ Method 1 after retry: {len(method1_text)} chars")

                        # CONCATENATE: Method 0 + Method 1
                        parts = [t for t in [method0_text, method1_text] if t]
                        description_text = ' '.join(parts)
                        description_text = re.sub(r'\s+', ' ', description_text).strip()
                        print(f"   ✅ Combined (Method 0 + Method 1): {len(description_text)} chars")

                        # IMAGE EXTRACTION: direct locator on container
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

                        # Quality filter + limit
                        description_images = [
                            img for img in description_images
                            if len(img) > 50 and not any(
                                bad in img.lower() for bad in ['icon', 'logo', '20x20', '50x50', '100x100']
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
                # ── Extract compliance/manufacturer info ──────────────────────────────
                compliance_info = extract_compliance_info(page)
                if compliance_info:
                    print(f"compliance info: {compliance_info[:80]}")
                # SUCCESS
                browser.close()

                result = {
                    "title": title if isinstance(title, str) else "",
                    "description_text": description_text if isinstance(description_text, str) else "",
                    "images": description_images if isinstance(description_images, list) else [],
                    "store_info": store_info if isinstance(store_info, dict) else {}
                    "compliance_info": compliance_info,
                }

                print(f"\n🔍 DEBUG RETURN VALUES:")
                print(f"   title: {len(result['title'])} chars")
                print(f"   description_text: {len(result['description_text'])} chars")
                print(f"   images: {len(result['images'])} images")
                print(f"   store_info: {result['store_info']}")
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
