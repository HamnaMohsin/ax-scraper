import re
import time
import random
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from stem import Signal
from stem.control import Controller

def extract_description_shadow_dom(page):
    print("   🌐 Extracting FULL Shadow DOM (no filtering)...")

    description_text = ""
    description_images = []

    try:
        data = page.evaluate("""
        () => {
            const result = { texts: [], images: [] };

            const rootContainer = document.querySelector('#product-description > div');
            if (!rootContainer) return result;

            const shadow = rootContainer.shadowRoot;
            if (!shadow) return result;

            const walker = document.createTreeWalker(
                shadow,
                NodeFilter.SHOW_ELEMENT,
                null,
                false
            );

            let node;
            while (node = walker.nextNode()) {

                // TEXT → NO FILTERING
                if (node.innerText) {
                    result.texts.push(node.innerText);
                }

                // IMAGES
                if (node.tagName === 'IMG') {
                    let src = node.src || node.getAttribute('data-src') || node.getAttribute('data-lazy-src');
                    if (src) {
                        result.images.push(src);
                    }
                }
            }

            return result;
        }
        """)

        # 🔥 NO set(), NO filtering
        if data.get("texts"):
            description_text = " ".join(data["texts"])
            print(f"   ✅ RAW Shadow text: {len(description_text)} chars")

        if data.get("images"):
            description_images = data["images"]
            print(f"   🖼️ RAW images: {len(description_images)}")

    except Exception as e:
        print(f"   ⚠️ Shadow DOM extraction failed: {e}")

    return description_text, description_images
    
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
            # Wait MUCH longer for Tor to establish new circuit
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
    
    # URL indicators
    captcha_url_keywords = ["baxia", "punish", "captcha", "verify", "_____tmd_____"]
    if any(kw in page_url for kw in captcha_url_keywords):
        print("❌ CAPTCHA detected in URL")
        return True
    
    # CAPTCHA iframe selectors
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
    
    # Title indicators
    is_product_page = "aliexpress" in page_title and len(page_title) > 40
    block_title_keywords = ["verify", "access", "denied", "blocked", "challenge"]
    if not is_product_page and any(kw in page_title for kw in block_title_keywords):
        print("❌ Block page detected from title")
        return True
    
    return False


def extract_store_info_universal(page) -> dict:
    """Extract store info - try multiple selectors with debugging"""
    store_info = {}
    
    print("📦 Extracting store info...")
    
    try:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        # Search 1: Direct class selector
        print("   🔍 Search 1: Looking for store-detail elements...")
        store_elem = soup.find('div', class_=lambda x: x and 'store-detail' in x)
        
        if store_elem:
            print(f"   ✓ Found store element")
            
            # Look for table
            table = store_elem.find('table')
            if table:
                print(f"   ✓ Found table with store info")
                for row in table.find_all('tr'):
                    cols = row.find_all('td')
                    if len(cols) >= 2:
                        key = clean_text(cols[0].get_text()).replace(":", "").strip()
                        value = clean_text(cols[1].get_text()).strip()
                        if key and value:
                            store_info[key] = value
                            print(f"      {key}: {value}")
            else:
                print(f"   ⚠️ No table found in store element")
                # Show what's actually in the element
                elem_text = store_elem.get_text(" ", strip=True)[:200]
                print(f"   Element content: {elem_text}...")
        
        # Search 2: If table method failed, try broader search
        if not store_info:
            print("   🔍 Search 2: Broader search for store information...")
            
            # Look for any div with store-related content
            all_divs = soup.find_all('div', class_=lambda x: x and any(s in str(x).lower() for s in ['store', 'seller', 'shop']))
            print(f"   Found {len(all_divs)} potential store divs")
            
            for div in all_divs[:5]:  # Check first 5
                # Look for table in each
                tbl = div.find('table')
                if tbl:
                    print(f"   ✓ Found table in div")
                    for row in tbl.find_all('tr'):
                        cols = row.find_all('td')
                        if len(cols) >= 2:
                            key = clean_text(cols[0].get_text()).replace(":", "").strip()
                            value = clean_text(cols[1].get_text()).strip()
                            if key and value:
                                store_info[key] = value
                                print(f"      {key}: {value}")
                    if store_info:
                        break
        
        # Search 3: Look for span/div elements with store info text
        if not store_info:
            print("   🔍 Search 3: Looking for store info in text elements...")
            
            # Search for "Store no." or "store no" pattern
            all_text = soup.get_text()
            if 'store no' in all_text.lower():
                print("   📝 Store info text found on page but table structure not matching")
                
                # Try to find the container with store info
                containers = soup.find_all(['div', 'span'], class_=lambda x: x and any(s in str(x).lower() for s in ['store', 'seller', 'shop', 'info']))
                
                for container in containers:
                    text = container.get_text()
                    if 'store' in text.lower() or 'seller' in text.lower():
                        # Try to parse key-value pairs
                        lines = [line.strip() for line in text.split('\n') if line.strip()]
                        for i, line in enumerate(lines):
                            if ':' in line or (i + 1 < len(lines) and line.endswith(('Store', 'no.', 'Location', 'since'))):
                                if ':' in line:
                                    parts = line.split(':', 1)
                                    key = parts[0].strip()
                                    value = parts[1].strip() if len(parts) > 1 else ""
                                    if key and value and len(key) < 50:
                                        store_info[key] = value
                                        print(f"      {key}: {value}")
        
        if not store_info:
            print("   ⚠️ Could not extract store information")
            
    except Exception as e:
        print(f"⚠️ Store extraction error: {e}")
        import traceback
        traceback.print_exc()
    
    return store_info


def extract_title_universal(page) -> str:
    """Extract title - try multiple selectors"""
    
    print("📌 Extracting title...")
    
    # Multiple selectors with fallbacks
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
            # Wait longer between retries to ensure new IP is ready
            wait_time = 20 + (attempt * 5)  # 25s, 30s for attempts 2, 3
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
                
                # CAPTCHA CHECK
                if is_captcha_page(page):
                    print("⚠️ CAPTCHA detected - rotating IP and retrying...")
                    browser.close()
                    continue
                
                # Wait for page to load
                print("⏳ Waiting for page to render...")
                time.sleep(8)
                
                # SCROLL
                print("⏳ Scrolling to load images...")
                try:
                    for _ in range(3):
                        page.mouse.wheel(0, random.randint(150, 300))
                        time.sleep(random.uniform(0.2, 0.6))
                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(1)
                except Exception as e:
                    print(f"⚠️ Scroll error: {e}")
                
                # SECOND CAPTCHA CHECK
                if is_captcha_page(page):
                    print("⚠️ CAPTCHA after scroll - rotating IP and retrying...")
                    browser.close()
                    continue
                
                # EXTRACT TITLE
                title = extract_title_universal(page)
                
                # EXTRACT STORE INFO
                store_info = extract_store_info_universal(page)
                
                # EXTRACT DESCRIPTION using inner_html (working method)
                print("📝 Loading description...")
                description_text = ""
                description_images = []
                
                try:
                    # Try to click description tab
                    print("   Clicking Description tab...")
                    try:
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(300)
                        
                        # Multiple selectors for description button
                        desc_button_selectors = [
                            ('a.comet-v2-anchor-link', "comet-v2-anchor-link"),
                            ('button[class*="Description"]', "button Description class"),
                            ('a:has-text("Description")', "anchor with Description text"),
                            ('div[role="tab"]:has-text("Description")', "tab Description"),
                        ]
                        
                        clicked = False
                        for selector, desc in desc_button_selectors:
                            try:
                                buttons = page.locator(selector).all()
                                
                                for btn in buttons:
                                    text = btn.inner_text().strip().lower()
                                    if 'description' in text:
                                        print(f"   ✓ Found Description button ({desc})")
                                        btn.click(force=True, timeout=2000)
                                        print("   ⏳ Waiting for description content to load...")
                                        page.wait_for_timeout(3000)
                                        
                                        # Scroll to description container to trigger rendering
                                        try:
                                            page.locator('#product-description').scroll_into_view()
                                            page.wait_for_timeout(2000)
                                        except:
                                            pass
                                        
                                        # Wait more for content to load
                                        page.wait_for_timeout(3000)
                                        print("   ✓ Clicked Description tab")
                                        clicked = True
                                        break
                                
                                if clicked:
                                    break
                            except:
                                continue
                        
                        if not clicked:
                            print("   ⚠️ Could not click description tab, will try to find content anyway")
                    except Exception as e:
                        print(f"   ⚠️ Description tab click error: {e}")
                    
                    # Get the MAIN description container using Playwright's inner_html
                    desc_container = page.locator('#product-description').first
                    # NEW: Extract ALL visible text inside description (handles shadow + normal DOM fallback)
                    print("   🎯 Method 0: Extracting ALL paragraph text...")

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
                            description_text = ' '.join(all_text_parts)
                            description_text = re.sub(r'\s+', ' ', description_text).strip()
                            print(f"   ✓ Extracted from ALL <p>: {len(description_text)} chars")
                    except Exception as e:
                        print(f"   ⚠️ Method 0 failed: {e}")
                    
                    if desc_container.count() > 0:
                        print("   ✓ Found #product-description container")
                        
                        # GET HTML FROM RENDERED PAGE
                        desc_html = page.evaluate("""
                        () => {
                            const el = document.querySelector('#product-description');
                            if (!el) return "";

                            // Check shadow root
                            const shadowHost = el.querySelector('[shadowrootmode]');
                            if (shadowHost && shadowHost.shadowRoot) {
                                return shadowHost.shadowRoot.innerHTML;
                            }

                            return el.innerHTML;
                        }
                        """)
                        print(f"   📊 inner_html size: {len(desc_html)} chars")
                        
                        # If inner_html is too small, content might still be loading - wait and retry
                        if desc_html and len(desc_html) < 50:
                            print(f"   ⏳ Content still loading, waiting 2s and retrying...")
                            page.wait_for_timeout(2000)
                            desc_html = desc_container.inner_html(timeout=5000)
                            print(f"   📊 inner_html size (retry): {len(desc_html)} chars")
                        
                        # EXTRACT TEXT using Playwright (not BeautifulSoup)
                        try:
                            print(f"   🎯 Method 1: Targeting description paragraph elements directly...")
                            
                            # Get all title and content paragraphs directly
                            titles = page.locator('#product-description p.detail-desc-decorate-title').all()
                            contents = page.locator('#product-description p.detail-desc-decorate-content').all()
                            
                            print(f"      Found {len(titles)} titles, {len(contents)} content paragraphs")
                            
                            text_parts = []
                            
                            # Extract title text
                            for title in titles:
                                try:
                                    text = title.inner_text(timeout=2000).strip()
                                    if text:
                                        text_parts.append(text)
                                        print(f"      Title: {text[:50]}...")
                                except:
                                    pass
                            
                            # Extract content text
                            for content in contents:
                                try:
                                    text = content.inner_text(timeout=2000).strip()
                                    if text:
                                        text_parts.append(text)
                                        print(f"      Content: {text[:50]}...")
                                except:
                                    pass
                            
                            if text_parts:
                                description_text = ' '.join(text_parts)
                                description_text = re.sub(r'\s+', ' ', description_text).strip()
                                print(f"   ✓ Text extracted directly: {len(description_text)} chars")
                            else:
                                print(f"   ⚠️ No text found in paragraph elements ({len(titles)} titles, {len(contents)} contents)")
                                
                                # Fallback Method 2: Use inner_text() on container
                                print(f"   🎯 Method 2: Using Playwright inner_text() on container...")
                                inner_text = desc_container.inner_text(timeout=5000).strip()
                                print(f"      Got {len(inner_text)} chars")
                                
                                if inner_text and len(inner_text) > 100:
                                    description_text = inner_text
                                    print(f"   ✓ Text extracted via container: {len(description_text)} chars")
                                else:
                                    print(f"   ⚠️ Container inner_text too short ({len(inner_text)})")
                                    print(f"   ⏳ Waiting 5 more seconds...")
                                    page.wait_for_timeout(5000)
                                    
                                    # Try one more time
                                    inner_text = desc_container.inner_text(timeout=5000).strip()
                                    if inner_text and len(inner_text) > 100:
                                        description_text = inner_text
                                        print(f"   ✓ Text extracted after wait: {len(description_text)} chars")
                                    else:
                                        print(f"   ❌ FAILED: Could not extract description text")
                        
                        except Exception as e:
                            print(f"   ❌ Text extraction error: {e}")
                            import traceback
                            traceback.print_exc()
                        
                        print("   🔁 Enhancing description using Shadow DOM...")

                        shadow_text, shadow_images = extract_description_shadow_dom(page)
                        
                        # ✅ MERGE TEXT (VERY IMPORTANT)
                        # Priority-based selection
                        #description_text = shadow_text + description_text
                                                
                        # ✅ REMOVE DUPLICATE SENTENCES (smart cleanup)
                        # if description_text:
                        #     sentences = list(set(description_text.split('. ')))
                        #     description_text = '. '.join(sentences)
                        
                        # ✅ MERGE IMAGES
                        if shadow_images:
                            description_images = list(set(description_images + shadow_images))
                        
                        print(f"   ✅ Final description length: {len(description_text)} chars")
                        print(f"   🖼️ Total images after merge: {len(description_images)}")
                        
                        # EXTRACT IMAGES (INDEPENDENT of text extraction)
                        try:
                            print(f"   🖼️ Starting image extraction...")
                            
                            # Method 1: Target specific image class in Playwright
                            print(f"   Method 1: Looking for detail-desc-decorate-image class...")
                            
                            # Find all img.detail-desc-decorate-image
                            imgs = desc_container.locator('img.detail-desc-decorate-image').all()
                            print(f"      Found {len(imgs)} detail-desc-decorate-image tags")
                            
                            for img in imgs:
                                src = (img.get_attribute("src") or 
                                      img.get_attribute("data-src") or 
                                      img.get_attribute("data-lazy-src"))
                                if src and ("alicdn.com" in src or "ae01.alicdn.com" in src):
                                    clean_src = src.split('?')[0]
                                    description_images.append(clean_src)
                                    print(f"      Added: {src[:60]}...")
                            
                            print(f"      After Method 1: {len(description_images)} images")
                            
                            # Method 2: Also look for all images in case structure varies
                            print(f"   Method 2: Looking for all images...")
                            all_imgs = desc_container.locator('img').all()
                            print(f"      Found {len(all_imgs)} total <img> tags")
                            
                            for img in all_imgs:
                                src = (img.get_attribute("src") or 
                                      img.get_attribute("data-src") or 
                                      img.get_attribute("data-lazy-src"))
                                if src and ("alicdn.com" in src or "ae01.alicdn.com" in src):
                                    clean_src = src.split('?')[0]
                                    if clean_src not in description_images:  # Avoid duplicates
                                        description_images.append(clean_src)
                            
                            print(f"      After Method 2: {len(description_images)} total images")
                            
                            # Method 3: HTML parsing (catches any missed images)
                            print(f"   Method 3: Using HTML parsing...")
                            if desc_html and len(desc_html) > 50:
                                soup_desc = BeautifulSoup(desc_html, "html.parser")
                                html_imgs = soup_desc.find_all("img")
                                print(f"      Found {len(html_imgs)} <img> in HTML")
                                
                                for img in html_imgs:
                                    src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
                                    if src and ("alicdn.com" in src or "ae01.alicdn.com" in src):
                                        clean_src = src.split('?')[0]
                                        if clean_src not in description_images:
                                            description_images.append(clean_src)
                            else:
                                print(f"      Skipping (desc_html too small)")
                            
                            print(f"      After Method 3: {len(description_images)} total before dedup")
                            
                            # Dedupe + quality filter
                            unique_desc_images = list(set(description_images))
                            print(f"      After dedupe: {len(unique_desc_images)} unique images")
                            
                            quality_images = [img for img in unique_desc_images 
                                            if len(img) > 50 and not any(bad in img.lower() for bad in ['icon', 'logo', '20x20', '50x50', '100x100'])]
                            print(f"      After quality filter: {len(quality_images)} quality images")
                            
                            description_images = quality_images[:20]  # Limit to 20
                            print(f"   ✓ Images: {len(description_images)} extracted")
                            
                            if description_images:
                                for i, img_url in enumerate(description_images[:3], 1):
                                    print(f"      {i}. {img_url[:60]}...")
                        except Exception as e:
                            print(f"   ❌ Image extraction error: {e}")
                            import traceback
                            traceback.print_exc()
                            description_images = []
                    else:
                        print("   ❌ #product-description not found")
                    
                except Exception as e:
                    print(f"⚠️ Description extraction error: {e}")
                
                # SUCCESS
                browser.close()
                
                result = {
                    "title": title if isinstance(title, str) else "",
                    "description_text": description_text if isinstance(description_text, str) else "",
                    "images": description_images if isinstance(description_images, list) else [],
                    "store_info": store_info if isinstance(store_info, dict) else {}
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
