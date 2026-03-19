import re
import time
import random
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from stem import Signal
from stem.control import Controller


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
    """Extract store info - try multiple selectors"""
    store_info = {}
    
    print("📦 Extracting store info...")
    
    try:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        # Multiple selectors for store info container
        store_selectors = [
            ('div.store-detail--storeDesc--zjMyBuV', "store-detail--storeDesc"),
            ('div[class*="store-detail"]', "store-detail class"),
            ('div[class*="storeDesc"]', "storeDesc class"),
            ('div[data-spm*="store"]', "data-spm store"),
        ]
        
        for selector, desc in store_selectors:
            try:
                if selector.startswith('div.'):
                    store_elem = soup.find('div', class_=selector.replace('div.', '').split('[')[0])
                else:
                    store_elem = soup.select_one(selector)
                
                if store_elem:
                    print(f"   ✓ Found store info ({desc})")
                    
                    # Debug: Show what we found
                    store_html = str(store_elem)[:300]
                    print(f"   Element preview: {store_html}...")
                    
                    # Find table
                    table = store_elem.find('table')
                    if table:
                        print(f"   ✓ Found table in store element")
                        for row in table.find_all('tr'):
                            cols = row.find_all('td')
                            if len(cols) == 2:
                                key = clean_text(cols[0].get_text()).replace(":", "").strip()
                                value = clean_text(cols[1].get_text()).strip()
                                if key and value:
                                    store_info[key] = value
                                    print(f"   {key}: {value}")
                    else:
                        print(f"   ⚠️ No table found in store element")
                        # Try to extract text as fallback
                        text = store_elem.get_text(" ", strip=True)
                        print(f"   Element text: {text[:100]}...")
                    
                    if store_info:
                        return store_info
            except Exception as e:
                print(f"   ⚠️ Selector {desc} error: {e}")
                continue
        
        if not store_info:
            print("   ⚠️ No store info found")
            
    except Exception as e:
        print(f"⚠️ Store info extraction error: {e}")
    
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
                
                # CAPTCHA CHECK (early)
                if is_captcha_page(page):
                    print("⚠️ CAPTCHA detected - rotating IP and retrying...")
                    browser.close()
                    continue
                
                # SIMPLE WAIT - don't poll content repeatedly!
                print("⏳ Waiting for page to render...")
                time.sleep(8)
                
                # NATURAL SCROLLING
                print("⏳ Scrolling to load images...")
                try:
                    for _ in range(3):  # 3 scrolls (human-like)
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
                
                # EXTRACT DESCRIPTION
                print("📝 Loading description...")
                description_text = ""
                description_images = []
                description_element = None
                
                try:
                    # Click the Description tab using multiple selector options
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
                                        page.wait_for_timeout(3000)  # Increased: give description time to load
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
                    
                    # Wait for #product-description to be visible
                    try:
                        page.wait_for_selector("#product-description", timeout=5000)
                        print("   ✓ #product-description is visible")
                    except:
                        print("   ⚠️ #product-description not immediately visible, continuing...")
                    
                    html = page.content()
                    soup = BeautifulSoup(html, "html.parser")
                    
                    # Multiple selectors for description container
                    desc_selectors = [
                        ('div#product-description', "#product-description"),
                        ('div[id="product-description"]', "[id=product-description]"),
                        ('div[class*="product-description"]', "product-description class"),
                    ]
                    
                    print("🔍 Looking for description container...")
                    product_desc = None
                    for selector, desc in desc_selectors:
                        try:
                            if selector.startswith('div#'):
                                product_desc = soup.find('div', id='product-description')
                            else:
                                product_desc = soup.select_one(selector)
                            
                            if product_desc:
                                print(f"   ✓ Found description container ({desc})")
                                break
                        except:
                            continue
                    
                    if product_desc:
                        description_element = product_desc
                        
                        # Wait for content to actually load
                        print("   Waiting for description content to load...")
                        page.wait_for_timeout(3000)  # Extra wait after finding element
                        
                        # Get fresh HTML after wait
                        html = page.content()
                        soup = BeautifulSoup(html, "html.parser")
                        product_desc = soup.find('div', id='product-description')
                        
                        if not product_desc:
                            print("   ⚠️ Description element disappeared after wait")
                            product_desc = description_element
                        
                        # Remove script and style tags first
                        for unwanted in product_desc(['script', 'style']):
                            unwanted.decompose()
                        
                        # Get all text
                        full_text = product_desc.get_text(" ", strip=True)
                        
                        if not full_text:
                            print("   🔍 DEBUG: Found element but text is empty")
                            print(f"   HTML length: {len(product_desc.get_text())} chars")
                            print(f"   Element HTML preview: {str(product_desc)[:200]}...")
                        
                        # Remove excessive whitespace
                        description_text = re.sub(r'\s+', ' ', full_text).strip()
                        
                        if description_text:
                            print(f"   ✓ Extracted description: {len(description_text)} chars")
                            print(f"   Preview: {description_text[:100]}...")
                        else:
                            print("   ⚠️ Description text is empty after extraction")
                    else:
                        print("⚠️ Description container not found")
                    
                except Exception as e:
                    print(f"⚠️ Description extraction error: {e}")
                
                # EXTRACT IMAGES FROM DESCRIPTION
                print("🖼️ Extracting images from description...")
                
                try:
                    if description_element is not None:
                        # Try multiple image selection strategies
                        img_selectors = [
                            ('img.detail-desc-decorate-image', "detail-desc-decorate-image class"),
                            ('img[slate-data-type="image"]', "[slate-data-type=image]"),
                            ('img', "all img tags"),
                        ]
                        
                        for selector, desc in img_selectors:
                            try:
                                if selector == 'img':
                                    all_imgs = description_element.find_all(selector)
                                elif selector.startswith('img.'):
                                    all_imgs = description_element.find_all('img', class_=selector.replace('img.', ''))
                                else:
                                    all_imgs = description_element.select(selector)
                                
                                if all_imgs:
                                    print(f"   Found {len(all_imgs)} imgs ({desc})")
                                    
                                    for img in all_imgs:
                                        src = img.get('src') or img.get('data-src') or img.get('data-original')
                                        
                                        if src and isinstance(src, str) and "alicdn" in src and len(src) > 50:
                                            # Skip very small images
                                            if not any(x in src.lower() for x in ["20x20", "50x50", "100x100"]):
                                                description_images.append(src)
                            except:
                                continue
                        
                        # Remove duplicates
                        description_images = list(set(description_images))
                        
                        if description_images:
                            print(f"   ✓ Extracted {len(description_images)} unique images")
                            for img_url in description_images[:5]:
                                print(f"      {img_url[:80]}...")
                    else:
                        print("   ⚠️ No description element found")
                    
                except Exception as e:
                    print(f"⚠️ Image extraction error: {e}")
                
                description_images = description_images[:20]  # Limit to 20
                print(f"✅ Images: {len(description_images)} total")
                
                # SUCCESS
                browser.close()
                
                # DEBUG: Show what we're returning
                print(f"\n🔍 DEBUG RETURN VALUES:")
                print(f"   title: {len(title)} chars - {title[:50] if title else 'EMPTY'}")
                print(f"   description_text: {len(description_text)} chars - {description_text[:50] if description_text else 'EMPTY'}")
                print(f"   description_images: {len(description_images)} images")
                print(f"   store_info: {store_info}")
                
                result = {
                    "title": title,
                    "description_text": description_text,
                    "images": description_images,
                    "store_info": store_info
                }
                
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
