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
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

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
    store_info = {}
    print("📦 Extracting store info...")
    try:
        store_name_elem = page.locator("span[class*='store-detail--storeName']").first
        if store_name_elem.count() > 0:
            store_name = store_name_elem.inner_text().strip()
            if store_name:
                store_info["Store Name"] = store_name
                print(f"   ✓ Store name: {store_name}")

        # Use mouse.move instead of hover for Camoufox compatibility
        store_link_elem = page.locator("div[class*='store-detail--storeNameWrap']").first
        if store_link_elem.count() > 0:
            store_link_elem.scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            box = store_link_elem.bounding_box()
            if box:
                page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                page.wait_for_timeout(2000)
                print("   ✓ Mouse moved to store element")

        for row_selector in ["div[class*='store-detail'] table tr",
                              "div[class*='storeDetail'] table tr",
                              "[class*='store-detail--detail'] tr"]:
            rows = page.locator(row_selector).all()
            if rows:
                print(f"   ✓ Found {len(rows)} rows")
                for row in rows:
                    try:
                        cols = row.locator('td').all()
                        if len(cols) >= 2:
                            key = cols[0].inner_text().strip().replace(":", "")
                            value = cols[1].inner_text().strip()
                            # Normalize non-English keys to English
                            key_map = {
                                "Shop-Nr.": "Store no.",
                                "Standort": "Location",
                                "Geöffnet seit": "Open since",
                                "Naam": "Name",
                                "Locatie": "Location",
                                "Winkel nr.": "Store no.",
                                "Geopend sinds": "Open since",
                                "Numéro de la boutique": "Store no.",
                                "Localisation": "Location",
                                "Ouvert depuis": "Open since",
                            }
                            key = key_map.get(key, key)
                            if key and value:
                                store_info[key] = value
                                print(f"      {key}: {value}")
                    except Exception:
                        continue
                if len(store_info) > 1:
                    break

        print(f"   ✅ Store info: {store_info}")
    except Exception as e:
        print(f"⚠️ Store extraction error: {e}")
    return store_info


def extract_title_universal(page) -> str:
    print("📌 Extracting title...")
    title_selectors = [
        ('[data-pl="product-title"]', "data-pl product-title"),
        ('[class*="product-title"]', "product-title class"),
        ('[class*="ProductTitle"]', "ProductTitle class"),
        ('h1', "h1 heading"),
    ]
    for selector, desc in title_selectors:
        try:
            elem = page.locator(selector).first
            if elem.count() > 0:
                title = elem.inner_text().strip()
                if title and len(title) > 20 and '/' not in title:
                    print(f"✅ Title ({desc}): {title[:80]}...")
                    return title
        except Exception:
            continue
    print("⚠️ Could not extract title")
    return ""


def extract_description_text(page) -> tuple[str, list]:
    """Enhanced description extraction - tries ALL methods independently"""
    print("\n📝 === DESCRIPTION EXTRACTION DEBUG ===")
    description_text = ""
    description_images = []
    all_methods_text = {}
    
    # Reset any modals first
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    except:
        pass

    # Method 0: Click description tab first
    print("🔍 Method 0: Description tab click...")
    try:
        buttons = page.locator('a.comet-v2-anchor-link').all()
        print(f"   Found {len(buttons)} anchor buttons")
        clicked_desc = False
        for i, btn in enumerate(buttons[:10]):  # Limit to first 10
            try:
                btn_text = btn.inner_text().strip().lower()
                print(f"   Button {i}: '{btn_text[:50]}'")
                if any(word in btn_text for word in ['description', 'product info', 'detail']):
                    btn.scroll_into_view_if_needed()
                    page.wait_for_timeout(300)
                    btn.click(force=True, timeout=2000)
                    page.wait_for_timeout(3000)
                    print(f"   ✓ CLICKED Description tab: '{btn_text}'")
                    clicked_desc = True
                    break
            except Exception as e:
                print(f"   ⚠️ Button {i} error: {e}")
                continue
        
        if not clicked_desc:
            print("   ⚠️ No description tab found/clicked")
    except Exception as e:
        print(f"   ❌ Method 0 error: {e}")

    # Deep scroll to description area
    print("🔄 Deep scrolling to description...")
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)
        page.locator('#product-description, .product-description, [class*="description"]').scroll_into_view_if_needed() if page.locator('#product-description, .product-description, [class*="description"]').count() > 0 else None
        page.wait_for_timeout(3000)
        print("   ✓ Deep scroll complete")
    except Exception as e:
        print(f"   ⚠️ Scroll error: {e}")

    # Method 1: #product-description container (PRIMARY)
    print("\n🔍 Method 1: #product-description container...")
    method1_text = ""
    try:
        desc_container = page.locator('#product-description').first
        if desc_container.count() > 0:
            print("   ✓ Found #product-description")
            method1_text = desc_container.inner_text(timeout=5000).strip()
            method1_text = re.sub(r'\s+', ' ', method1_text).strip()
            print(f"   ✓ Raw: {len(method1_text)} chars")
            print(f"   Preview: {method1_text[:150]}...")
        else:
            print("   ❌ #product-description NOT found")
    except Exception as e:
        print(f"   ❌ Method 1 error: {e}")

    all_methods_text['method1'] = method1_text

    # Method 2: All <p> tags in description area
    print("\n🔍 Method 2: All <p> tags...")
    method2_text = ""
    try:
        p_selectors = [
            '#product-description p',
            '.product-description p', 
            '[class*="description"] p',
            'div[style*="description"] p'
        ]
        all_p_texts = []
        for selector in p_selectors:
            try:
                paragraphs = page.locator(selector).all()
                print(f"   {selector}: {len(paragraphs)} <p> tags")
                for i, p in enumerate(paragraphs[:20]):  # Limit to 20
                    txt = p.inner_text(timeout=1000).strip()
                    if txt and len(txt) > 5:
                        all_p_texts.append(txt)
                        if i < 3:  # Show first 3
                            print(f"     P{i+1}: {txt[:80]}...")
            except:
                continue
        
        if all_p_texts:
            method2_text = re.sub(r'\s+', ' ', ' '.join(all_p_texts)).strip()
            print(f"   ✓ Combined: {len(method2_text)} chars")
        else:
            print("   ❌ No valid <p> content")
    except Exception as e:
        print(f"   ❌ Method 2 error: {e}")
    
    all_methods_text['method2'] = method2_text

    # Method 3: Full page text extraction (fallback)
    print("\n🔍 Method 3: Full page text (fallback)...")
    method3_text = ""
    try:
        full_text = page.inner_text(timeout=10000).strip()
        # Filter to likely description content
        lines = full_text.split('\n')
        desc_lines = []
        for line in lines:
            line = line.strip()
            if len(line) > 20 and len(line) < 500 and not any(skip in line.lower() for skip in 
                ['aliexpress', 'free shipping', 'price', 'store', 'add to cart', 'buy now']):
                desc_lines.append(line)
        
        method3_text = re.sub(r'\s+', ' ', ' '.join(desc_lines[:50])).strip()  # Limit lines
        print(f"   ✓ Filtered: {len(method3_text)} chars")
    except Exception as e:
        print(f"   ❌ Method 3 error: {e}")
    
    all_methods_text['method3'] = method3_text

    # Method 4: Specific description divs by class patterns
    print("\n🔍 Method 4: Class pattern divs...")
    method4_text = ""
    try:
        div_selectors = [
            'div[class*="product-detail"]',
            'div[class*="description-content"]', 
            'div[class*="product-info"]',
            '.html-content',
            '[class*="detail"]'
        ]
        for selector in div_selectors:
            try:
                divs = page.locator(selector).all()[:5]  # Limit to 5
                for div in divs:
                    txt = div.inner_text(timeout=2000).strip()
                    if len(txt) > 100 and len(txt) < 10000:
                        method4_text += txt + " "
                        print(f"   ✓ {selector}: {len(txt)} chars")
                        break
                if method4_text:
                    break
            except:
                continue
        method4_text = re.sub(r'\s+', ' ', method4_text).strip()
        print(f"   Final: {len(method4_text)} chars")
    except Exception as e:
        print(f"   ❌ Method 4 error: {e}")
    
    all_methods_text['method4'] = method4_text

    # COMBINE BEST RESULTS
    print("\n🔗 Combining results...")
    combined_parts = [t for t in all_methods_text.values() if t and len(t) > 50]
    if combined_parts:
        description_text = re.sub(r'\s+', ' ', ' '.join(combined_parts)).strip()
        print(f"   ✓ COMBINED: {len(description_text)} chars")
        print(f"   Preview: {description_text[:200]}...")
    else:
        print("   ❌ No valid text from any method")
    
    print(f"📊 Method lengths: { {k: len(v) for k,v in all_methods_text.items()} }")

    # EXTRACT IMAGES (always run)
    print("\n🖼️ Extracting images...")
    try:
        img_selectors = ['#product-description img', '.product-description img', '[class*="description"] img', 'img']
        all_imgs = []
        for selector in img_selectors:
            imgs = page.locator(selector).all()
            all_imgs.extend(imgs)
            if imgs:
                print(f"   {selector}: {len(imgs)} images")
        
        print(f"   Total unique images to process: {len(all_imgs)}")
        description_images_set = set()
        
        for i, img in enumerate(all_imgs[:50]):  # Limit to 50
            try:
                src = None
                for attr in ['src', 'data-src', 'data-lazy-src', 'lazy-src', 'srcset']:
                    src = img.get_attribute(attr)
                    if src and src.strip():
                        break
                
                if src:
                    clean_src = src.split('?')[0].split('#')[0].split(',')[0].strip()
                    valid_domains = ['alicdn.com', 'ae01.alicdn.com', 'amazonaws.com']
                    bad_patterns = ['icon', 'logo', 'avatar', '20x20', '50x50', '100x100']
                    
                    if (len(clean_src) > 40 and 
                        any(d in clean_src for d in valid_domains) and
                        not any(b in clean_src.lower() for b in bad_patterns)):
                        
                        description_images_set.add(clean_src)
                        if len(description_images_set) <= 5:  # Show first 5
                            print(f"      ✅ {clean_src[-80:]}")
            except Exception:
                continue
        
        description_images = list(description_images_set)[:20]
        print(f"   ✅ Final: {len(description_images)} valid images")
        
    except Exception as e:
        print(f"   ❌ Image extraction error: {e}")

    print("✅ === DESCRIPTION EXTRACTION COMPLETE ===\n")
    return description_text, description_images


def extract_aliexpress_product(url: str) -> dict:
    print(f"\n🔍 Scraping: {url}")

    empty_result = {"title": "", "description_text": "", "images": [], "store_info": {}, "compliance_info": {}}
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
            locale="en-GB",
        ) as browser:

            page = browser.new_page()
            page.set_extra_http_headers({"Accept-Language": "en-GB,en;q=0.9"})

            try:
                print("📡 Loading page...")
                page.goto(url, timeout=120000, wait_until="domcontentloaded")
                time.sleep(3)

                if is_captcha_page(page):
                    print("⚠️ CAPTCHA detected — retrying...")
                    browser.close()
                    continue

                print("⏳ Waiting for page to render...")
                time.sleep(15)  # Increased wait

                # Deep scroll
                print("⏳ Deep scrolling...")
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(4)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.7)")
                time.sleep(2)
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(2)

                if is_captcha_page(page):
                    print("⚠️ CAPTCHA after scroll — retrying...")
                    browser.close()
                    continue

                # EXTRACT TITLE
                title = extract_title_universal(page)

                # EXTRACT STORE INFO
                store_info = extract_store_info_universal(page)

                # EXTRACT DESCRIPTION (NEW ENHANCED FUNCTION)
                description_text, description_images = extract_description_text(page)

                # COMPLIANCE
                compliance_info = extract_compliance_info(page)

                browser.close()

                result = {
                    "title": title or "",
                    "description_text": description_text or "",
                    "images": description_images or [],
                    "store_info": store_info or {},
                    "compliance_info": compliance_info or {},
                }

                print(f"\n🎉 FINAL RESULT:")
                print(f"   Title: {len(result['title'])} chars")
                print(f"   Description: {len(result['description_text'])} chars")
                print(f"   Images: {len(result['images'])}")
                print(f"   Store info: {len(result['store_info']) } items")
                print(f"✅ SUCCESS on attempt {attempt + 1}!\n")
                
                # Only return if we have meaningful description
                if len(description_text) > 50:
                    return result
                else:
                    print("⚠️ Description too short, retrying...\n")

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
