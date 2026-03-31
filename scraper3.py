import re
import time
import random
from camoufox.sync_api import Camoufox
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from stem import Signal
from stem.control import Controller


def extract_compliance_info(page) -> dict:
    compliance = {}
    print("📋 Extracting compliance/manufacturer info...")
    try:
        # Scroll to bottom to reveal compliance link
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

        # Only text-based selectors — never h2 title
        compliance_selectors = [
            "span:has-text('Product compliance information')",
            "a:has-text('Product compliance')",
            "div:has-text('Product compliance information') >> nth=0",
            "[data-spm-anchor-id*='i30']",
        ]

        clicked = False
        for sel in compliance_selectors:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0:
                    btn.scroll_into_view_if_needed()
                    page.wait_for_timeout(500)
                    btn.click(force=True, timeout=3000)
                    page.wait_for_timeout(4000)
                    if page.locator(".comet-v2-modal-body").count() > 0:
                        print(f"   ✓ Modal opened via: {sel}")
                        clicked = True
                        break
                    else:
                        print(f"   ⚠️ Clicked {sel} but modal did not open")
            except Exception:
                continue

        if not clicked:
            print("   ⚠️ Compliance trigger not found — skipping")
            return compliance

        modal = page.locator(".comet-v2-modal-body").first
        modal_html = modal.inner_html(timeout=5000)
        soup = BeautifulSoup(modal_html, "html.parser")

        for p in soup.find_all('p'):
            raw_html = str(p)
            strong = p.find('strong')
            section = strong.get_text().strip() if strong else "Info"
            lines = re.split(r'<br\s*/?>', raw_html, flags=re.IGNORECASE)
            section_data = {}
            for line in lines:
                line_text = BeautifulSoup(line, "html.parser").get_text().strip()
                if ':' in line_text:
                    key, _, value = line_text.partition(':')
                    key = key.strip()
                    value = value.strip()
                    if key and value and len(key) < 60 and key != section:
                        section_data[key] = value
            if section_data:
                compliance[section] = section_data
                print(f"   ✓ {section}: {section_data}")

        # EU responsible person (outside <p> tags)
        for p in soup.find_all('p'):
            p.decompose()
        eu_data = {}
        in_eu = False
        for line in soup.get_text("\n").split('\n'):
            line = line.strip()
            if 'EU responsible' in line:
                in_eu = True
                continue
            if in_eu and ':' in line:
                key, _, value = line.partition(':')
                key, value = key.strip(), value.strip()
                if key and value and len(key) < 60:
                    eu_data[key] = value
        if eu_data:
            compliance['EU Responsible Person'] = eu_data
            print(f"   ✓ EU: {eu_data}")

        try:
            page.locator(".comet-v2-modal-close").first.click(timeout=2000)
        except Exception:
            page.keyboard.press("Escape")

        print(f"   ✅ Compliance extracted: {len(compliance)} sections")

    except Exception as e:
        print(f"   ❌ Compliance error: {e}")

    return compliance


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


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
        print("✅ Tor circuit rotated")
        return True
    except Exception as e:
        print(f"⚠️ Could not rotate Tor: {e}")
        return False


def is_captcha_page(page) -> bool:
    page_url = page.url.lower()
    page_title = page.title().lower()

    if any(kw in page_url for kw in ["baxia", "punish", "captcha", "verify", "_____tmd_____"]):
        print("❌ CAPTCHA detected in URL")
        return True

    for selector in ["iframe[src*='recaptcha']", ".baxia-punish", "#captcha-verify",
                     "[id*='captcha']", "iframe[src*='geetest']", "[class*='captcha']"]:
        try:
            if page.locator(selector).count() > 0:
                print(f"❌ CAPTCHA detected: {selector}")
                return True
        except Exception:
            continue

    is_product_page = "aliexpress" in page_title and len(page_title) > 40
    if not is_product_page and any(kw in page_title for kw in
                                   ["verify", "access", "denied", "blocked", "challenge"]):
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
    print("📌 Extracting title...")
    title_selectors = [
        ('[data-pl="product-title"]', "data-pl product-title"),
        # ('[class*="product-title"]', "product-title class"),
        # ('[class*="ProductTitle"]', "ProductTitle class"),
        # ('h1', "h1 heading"),
    ]
    for selector, desc in title_selectors:
        try:
            elem = page.locator(selector).first
            if elem.count() > 0:
                title = elem.inner_text().strip()
                if title and len(title) > 20 : # and '/' not in title:
                    print(f"✅ Title ({desc}): {title[:80]}...")
                    return title
        except Exception:
            continue
    print("⚠️ Could not extract title")
    return ""


def extract_aliexpress_product(url: str) -> dict:
    print(f"\n🔍 Scraping: {url}")

    empty_result = {"title": "", "description_text": "", "images": [], "store_info": {}}
    max_retries = 5

    for attempt in range(max_retries):
        print(f"\n📍 Attempt {attempt + 1}/{max_retries}")

        if attempt > 0:
            print("🔄 Rotating Tor circuit...")
            rotate_tor_circuit()
            wait_time = 30 + (attempt * 5)
            print(f"   Waiting {wait_time}s...")
            time.sleep(wait_time)

       with Camoufox(
                headless=True,
                proxy={"server": "socks5://127.0.0.1:9050"},
                geoip=True,
            ) as browser:
                # Create a context and set the AliExpress 'Global' cookie
                context = browser.new_context(locale="en-US")
                context.add_cookies([{
                    'name': 'aep_usuc_f',
                    'value': 'site=glo&region=US&b_locale=en_US&currency=USD',
                    'domain': '.aliexpress.com',
                    'path': '/'
                }])
                page = context.new_page()
                
                # Use the same URL but the cookie will prevent the /de/ redirect
                page.goto(url + "?gatewayAdapt=glo2usa", timeout=120000)
                time.sleep(2)

                current_url = page.url
                if current_url != url:
                    print(f"⚠️ Redirected to: {current_url}")

                if is_captcha_page(page):
                    print("⚠️ CAPTCHA detected — retrying...")
                    browser.close()
                    continue

                print("⏳ Waiting for page to render...")
                time.sleep(12)

                # Deep scroll to trigger lazy loads
                print("⏳ Deep scrolling...")
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(3)
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
                    time.sleep(1)
                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(1)
                except Exception as e:
                    print(f"⚠️ Scroll error: {e}")

                if is_captcha_page(page):
                    print("⚠️ CAPTCHA after scroll — retrying...")
                    browser.close()
                    continue

                # EXTRACT TITLE
                title = extract_title_universal(page)

                # EXTRACT STORE INFO
                store_info = extract_store_info_universal(page)

                # COMPLIANCE
                compliance_info = extract_compliance_info(page)

                # EXTRACT DESCRIPTION
                print("📝 Loading description...")
                description_text = ""
                description_images = []

                try:
                    # Click description tab
                    try:
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(300)
                        buttons = page.locator('a.comet-v2-anchor-link').all()
                        for btn in buttons:
                            if 'description' in btn.inner_text().strip().lower():
                                btn.click(force=True, timeout=2000)
                                print("   ✓ Clicked Description tab")
                                page.wait_for_timeout(3000)
                                break
                    except Exception as e:
                        print(f"   ⚠️ Description tab error: {e}")

                    # Scroll to #product-description to trigger lazy load
                    try:
                        page.locator('#product-description').scroll_into_view_if_needed()
                        page.wait_for_timeout(3000)
                    except Exception:
                        pass

                    # Method 0: <p> tags
                    method0_text = ""
                    try:
                        all_paragraphs = page.locator('#product-description p').all()
                        parts = []
                        for p in all_paragraphs:
                            try:
                                txt = p.inner_text(timeout=2000).strip()
                                if txt and len(txt) > 2:
                                    parts.append(txt)
                            except Exception:
                                pass
                        if parts:
                            method0_text = re.sub(r'\s+', ' ', ' '.join(parts)).strip()
                            print(f"   ✓ Method 0: {len(method0_text)} chars")
                        else:
                            print("   ⚠️ Method 0: no <p> content")
                    except Exception as e:
                        print(f"   ⚠️ Method 0 failed: {e}")

                    # Method 1: full container inner_text
                    desc_container = page.locator('#product-description').first
                    method1_text = ""
                    method2_text = ""

                    if desc_container.count() > 0:
                        print("   ✓ Found #product-description")
                        method1_text = desc_container.inner_text(timeout=5000).strip()
                        method1_text = re.sub(r'\s+', ' ', method1_text).strip()
                        print(f"   ✓ Method 1: {len(method1_text)} chars")

                        if len(method1_text) < 100:
                            page.wait_for_timeout(5000)
                            method1_text = desc_container.inner_text(timeout=5000).strip()
                            method1_text = re.sub(r'\s+', ' ', method1_text).strip()
                            print(f"   ✓ Method 1 retry: {len(method1_text)} chars")

                        # Method 2: JS evaluate — handles deeply nested divs
                        if len(method1_text) < 100:
                            print("   🎯 Method 2: JS innerText evaluation...")
                            try:
                                js_text = page.evaluate("""
                                    () => {
                                        const container = document.querySelector('#product-description');
                                        if (!container) return '';
                                        const clone = container.cloneNode(true);
                                        clone.querySelectorAll('img, script, style').forEach(el => el.remove());
                                        return clone.innerText || clone.textContent || '';
                                    }
                                """)
                                if js_text:
                                    method2_text = re.sub(r'\s+', ' ', js_text).strip()
                                    print(f"   ✓ Method 2: {len(method2_text)} chars")
                                else:
                                    print("   ⚠️ Method 2: empty")
                            except Exception as e:
                                print(f"   ⚠️ Method 2 failed: {e}")

                        # Combine all methods
                        description_text = re.sub(r'\s+', ' ',
                            ' '.join(t for t in [method0_text, method1_text, method2_text] if t)
                        ).strip()
                        print(f"   ✅ Combined: {len(description_text)} chars")

                        # Image extraction
                        all_imgs = desc_container.locator('img').all()
                        print(f"      Found {len(all_imgs)} <img> tags")
                        description_images_set = set()
                        for img in all_imgs:
                            try:
                                src = None
                                for attr in ['src', 'data-src', 'data-lazy-src', 'lazy-src']:
                                    src = img.get_attribute(attr)
                                    if src and src.strip():
                                        break
                                if src:
                                    clean_src = src.split('?')[0].split('#')[0].strip()
                                    # Fix missing protocol
                                    if clean_src.startswith('//'):
                                        clean_src = 'https:' + clean_src
                                    elif clean_src.startswith('/'):
                                        clean_src = 'https://ae01.alicdn.com' + clean_src
                                    valid_domains = ['alicdn.com', 'ae01.alicdn.com',
                                                     'm.media-amazon.com', 'amazonaws.com']
                                    bad_patterns = ['icon', 'logo', 'avatar', '20x20', '50x50']
                                    if (len(clean_src) > 40 and
                                            any(d in clean_src for d in valid_domains) and
                                            not any(b in clean_src.lower() for b in bad_patterns)):
                                        description_images_set.add(clean_src)
                                        print(f"      ✅ {clean_src[-60:]}")
                            except Exception:
                                continue
                        description_images = list(description_images_set)[:20]
                        print(f"   ✓ {len(description_images)} images")
                    else:
                        print("   ❌ #product-description not found")

                except Exception as e:
                    print(f"⚠️ Description error: {e}")

                

                browser.close()

                result = {
                    "title":            title if isinstance(title, str) else "",
                    "description_text": description_text if isinstance(description_text, str) else "",
                    "images":           description_images if isinstance(description_images, list) else [],
                    "store_info":       store_info if isinstance(store_info, dict) else {},
                    "compliance_info":  compliance_info,
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
                except Exception:
                    pass
                continue

    print(f"❌ Failed after {max_retries} attempts")
    return empty_result
