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
    """Rotate Tor circuit to get new exit IP"""
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
    """Detect if page is a CAPTCHA/block page"""
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
    """Extract store info"""
    store_info = {}
    
    print("📦 Extracting store info...")
    
    try:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        print("   🔍 Looking for store-detail elements...")
        store_elem = soup.find('div', class_=lambda x: x and 'store-detail' in x)
        
        if store_elem:
            print(f"   ✓ Found store element")
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
        
        if not store_info:
            print("   ⚠️ Could not extract store information")
            
    except Exception as e:
        print(f"⚠️ Store extraction error: {e}")
    
    return store_info


def extract_title_universal(page) -> str:
    """Extract title"""
    
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


def extract_complete_description(page) -> str:
    """
    COMPLETE REWRITE: Extract description from ALL sources
    - richTextContainer divs (primary descriptions)
    - product-description containers (specs)
    - Handles multiple content sections
    """
    print("📝 Extracting COMPLETE description from all sources...")
    
    all_descriptions = []
    
    try:
        # Wait for dynamic content
        print("   ⏳ Waiting for page content to fully render...")
        page.wait_for_timeout(5000)
        
        # Get full page HTML
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        print("   🔍 SOURCE 1: Looking for richTextContainer divs...")
        # richTextContainer divs contain the main descriptions
        rich_containers = soup.find_all('div', class_=lambda x: x and 'richTextContainer' in x)
        print(f"      Found {len(rich_containers)} richTextContainer divs")
        
        for i, container in enumerate(rich_containers):
            print(f"      Processing richTextContainer #{i+1}...")
            
            # Get all text from this container
            text_content = container.get_text(separator='\n', strip=True)
            
            if text_content and len(text_content) > 50:
                # Clean up
                text_content = re.sub(r'\n+', '\n', text_content)
                text_content = re.sub(r'[ \t]+', ' ', text_content)
                
                if text_content not in all_descriptions:  # Avoid duplicates
                    all_descriptions.append(text_content)
                    print(f"         ✓ Extracted {len(text_content)} chars")
                else:
                    print(f"         ~ Duplicate (skipped)")
        
        print("   🔍 SOURCE 2: Looking for product-description divs...")
        # Also get from product-description
        prod_descs = soup.find_all('div', id='product-description')
        print(f"      Found {len(prod_descs)} product-description divs")
        
        for i, desc in enumerate(prod_descs):
            print(f"      Processing product-description #{i+1}...")
            
            # Get all text
            text_content = desc.get_text(separator='\n', strip=True)
            
            if text_content and len(text_content) > 30:
                # Clean up
                text_content = re.sub(r'\n+', '\n', text_content)
                text_content = re.sub(r'[ \t]+', ' ', text_content)
                
                if text_content not in all_descriptions:
                    all_descriptions.append(text_content)
                    print(f"         ✓ Extracted {len(text_content)} chars")
                else:
                    print(f"         ~ Duplicate (skipped)")
        
        print("   🔍 SOURCE 3: Looking for detail-desc-decorate-richtext...")
        # Detail-desc-decorate-richtext sections
        detail_descs = soup.find_all('div', class_=lambda x: x and 'detail-desc-decorate-richtext' in x)
        print(f"      Found {len(detail_descs)} detail-desc-decorate-richtext divs")
        
        for i, desc in enumerate(detail_descs):
            print(f"      Processing detail-desc-decorate-richtext #{i+1}...")
            
            text_content = desc.get_text(separator='\n', strip=True)
            
            if text_content and len(text_content) > 30:
                text_content = re.sub(r'\n+', '\n', text_content)
                text_content = re.sub(r'[ \t]+', ' ', text_content)
                
                if text_content not in all_descriptions:
                    all_descriptions.append(text_content)
                    print(f"         ✓ Extracted {len(text_content)} chars")
                else:
                    print(f"         ~ Duplicate (skipped)")
        
        print("   🔍 SOURCE 4: Looking for all p, span elements with product info...")
        # Get all paragraphs and spans that might contain product info
        all_text_nodes = []
        
        # Look for elements that might contain descriptions
        for element in soup.find_all(['p', 'span', 'div']):
            text = element.get_text(strip=True)
            
            # Filter for relevant content (at least 20 chars)
            if text and len(text) > 20:
                # Skip if too long (probably a wrapper)
                if len(text) < 2000:
                    all_text_nodes.append(text)
        
        print(f"      Found {len(all_text_nodes)} individual text nodes")
        
        # Combine all sources
        if all_descriptions:
            print(f"   ✅ Found {len(all_descriptions)} description sections")
            
            # Join all descriptions with a separator
            combined = "\n\n---\n\n".join(all_descriptions)
            
            # Clean up excessive whitespace
            combined = re.sub(r'\n\s*\n+', '\n\n', combined)
            combined = re.sub(r'[ \t]+', ' ', combined)
            combined = combined.strip()
            
            print(f"   📊 Combined description: {len(combined)} chars")
            
            # Verify key content
            if 'keep hot' in combined.lower():
                print("   ✅ Includes 'Keep hot' specifications")
            if 'keep cold' in combined.lower():
                print("   ✅ Includes 'Keep cold' specifications")
            if 'material' in combined.lower():
                print("   ✅ Includes material specifications")
            
            return combined
        else:
            print("   ⚠️ No descriptions found in any source")
            return ""
    
    except Exception as e:
        print(f"❌ Description extraction error: {e}")
        import traceback
        traceback.print_exc()
        return ""


def extract_description_images(page) -> list:
    """Extract images from all description sources"""
    print("🖼️ Extracting description images...")
    
    description_images = []
    
    try:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        # Find all images in product description areas
        print("   🔍 Looking for images in product-description...")
        
        # Images in product-description
        prod_desc = soup.find('div', id='product-description')
        if prod_desc:
            imgs = prod_desc.find_all('img')
            print(f"      Found {len(imgs)} images in product-description")
            
            for img in imgs:
                src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
                if src and ("alicdn.com" in src or "ae01.alicdn.com" in src):
                    clean_src = src.split('?')[0]
                    if len(clean_src) > 50 and clean_src not in description_images:
                        description_images.append(clean_src)
        
        # Images in richTextContainer
        print("   🔍 Looking for images in richTextContainer...")
        rich_containers = soup.find_all('div', class_=lambda x: x and 'richTextContainer' in x)
        for container in rich_containers:
            imgs = container.find_all('img')
            print(f"      Found {len(imgs)} images in richTextContainer")
            
            for img in imgs:
                src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
                if src and ("alicdn.com" in src or "ae01.alicdn.com" in src):
                    clean_src = src.split('?')[0]
                    if len(clean_src) > 50 and clean_src not in description_images:
                        description_images.append(clean_src)
        
        # Images in detail-desc-decorate-richtext
        print("   🔍 Looking for images in detail-desc-decorate-richtext...")
        detail_descs = soup.find_all('div', class_=lambda x: x and 'detail-desc-decorate-richtext' in x)
        for desc in detail_descs:
            imgs = desc.find_all('img')
            print(f"      Found {len(imgs)} images in detail-desc-decorate-richtext")
            
            for img in imgs:
                src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
                if src and ("alicdn.com" in src or "ae01.alicdn.com" in src):
                    clean_src = src.split('?')[0]
                    if len(clean_src) > 50 and clean_src not in description_images:
                        description_images.append(clean_src)
        
        print(f"   ✓ Extracted {len(description_images)} images")
        
        # Limit to 20 and remove quality filter
        description_images = description_images[:20]
        
        if description_images:
            for i, img_url in enumerate(description_images[:3], 1):
                print(f"      {i}. {img_url[:60]}...")
    
    except Exception as e:
        print(f"❌ Image extraction error: {e}")
    
    return description_images


def extract_aliexpress_product(url: str) -> dict:
    """
    Extract AliExpress product data - COMPLETE REWRITE for multi-source extraction
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
                
                # CAPTCHA CHECK
                if is_captcha_page(page):
                    print("⚠️ CAPTCHA detected - rotating IP and retrying...")
                    browser.close()
                    continue
                
                # Wait for page to load
                print("⏳ Waiting for page to render...")
                time.sleep(8)
                
                # SCROLL
                print("⏳ Scrolling to load content...")
                try:
                    for _ in range(5):
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
                
                # EXTRACT DESCRIPTION - NEW MULTI-SOURCE METHOD
                description_text = extract_complete_description(page)
                
                # EXTRACT IMAGES
                description_images = extract_description_images(page)
                
                # SUCCESS
                browser.close()
                
                result = {
                    "title": title if isinstance(title, str) else "",
                    "description_text": description_text if isinstance(description_text, str) else "",
                    "images": description_images if isinstance(description_images, list) else [],
                    "store_info": store_info if isinstance(store_info, dict) else {}
                }
                
                print(f"\n✅ Extraction Results:")
                print(f"   Title: {len(result['title'])} chars")
                print(f"   Description: {len(result['description_text'])} chars")
                print(f"   Images: {len(result['images'])} images")
                print(f"   Store info: {len(result['store_info'])} fields")
                print(f"\n✅ Extraction successful on attempt {attempt + 1}\n")
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
