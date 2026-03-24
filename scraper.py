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
        
        print("   🔍 Looking for store info...")
        store_elem = soup.find('div', class_=lambda x: x and 'store-detail' in x)
        
        if store_elem:
            print(f"   ✓ Found store element")
            table = store_elem.find('table')
            if table:
                print(f"   ✓ Found table")
                for row in table.find_all('tr'):
                    cols = row.find_all('td')
                    if len(cols) >= 2:
                        key = clean_text(cols[0].get_text()).replace(":", "").strip()
                        value = clean_text(cols[1].get_text()).strip()
                        if key and value:
                            store_info[key] = value
        
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
                    print(f"✅ Title: {title[:80]}...")
                    return title
        except:
            continue
    
    print("⚠️ Could not extract title")
    return ""


def expand_all_collapsed_sections(page):
    """
    Click all 'See More', 'Show More', 'Expand' buttons to reveal hidden content.
    """
    print("   🔓 Expanding collapsed sections...")
    
    expand_labels = ["See More", "Show More", "Expand", "More", "view more", "view details"]
    
    for label in expand_labels:
        try:
            selectors = [
                f'text="{label}"',
                f'button:has-text("{label}")',
                f'a:has-text("{label}")',
                f'span:has-text("{label}")',
            ]
            
            expanded_count = 0
            for sel in selectors:
                try:
                    buttons = page.locator(sel).all()
                    print(f"      Found {len(buttons)} buttons with '{label}'")
                    
                    for i in range(len(buttons)):
                        try:
                            btn = page.locator(sel).nth(i)
                            if btn.count() > 0:
                                btn.click(timeout=1500, force=True)
                                expanded_count += 1
                                page.wait_for_timeout(800)
                        except Exception as e:
                            continue
                except Exception:
                    continue
            
            if expanded_count > 0:
                print(f"      ✓ Expanded {expanded_count} sections with '{label}'")
        
        except Exception:
            continue
    
    print("   ✓ Finished expanding sections")


def extract_full_description_text(page) -> str:
    """
    Extract description text from ALL possible containers.
    Aggregates from richTextContainer, overview-content, detailmodule_html, etc.
    """
    print("   🔍 Extracting description text from all containers...")
    
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    
    # List of all possible description containers
    selectors = [
        'div.richTextContainer',
        'div.overview-content',
        'div[class*="detailmodule_html"]',
        'div[class*="product-description"]',
        'div[class*="d-main-description"]',
        'div[class*="description"]',
        '[data-spm="product-description"]',
    ]
    
    containers = []
    for selector in selectors:
        found = soup.select(selector)
        if found:
            print(f"      Found {len(found)} containers for: {selector}")
            containers.extend(found)
    
    # Extract text from all containers
    text_blocks = []
    seen_text = set()
    
    for container in containers:
        try:
            text = container.get_text(' ', strip=True)
            if text and len(text) > 30:
                # Avoid duplicates
                if text not in seen_text:
                    text_blocks.append(text)
                    seen_text.add(text)
        except:
            continue
    
    print(f"      Found {len(text_blocks)} text blocks")
    
    # Also try meta tags as fallback/supplement
    print("      Checking meta tags...")
    meta_description = soup.find('meta', attrs={'name': 'description'})
    if meta_description and meta_description.get('content'):
        meta_text = meta_description['content'].strip()
        if meta_text and meta_text not in seen_text and len(meta_text) > 20:
            text_blocks.append(meta_text)
            print(f"        Added meta description: {len(meta_text)} chars")
    
    og_description = soup.find('meta', attrs={'property': 'og:description'})
    if og_description and og_description.get('content'):
        og_text = og_description['content'].strip()
        if og_text and og_text not in seen_text and len(og_text) > 20:
            text_blocks.append(og_text)
            print(f"        Added OG description: {len(og_text)} chars")
    
    # Combine all text blocks
    if text_blocks:
        description_text = ' '.join(text_blocks)
        # Clean up excessive whitespace
        description_text = re.sub(r'\s+', ' ', description_text).strip()
        print(f"   ✓ Total description: {len(description_text)} chars")
        return description_text
    else:
        print("   ⚠️ No description text found")
        return ""


def find_all_images_on_page(page) -> list:
    """
    Comprehensive image extraction using multiple methods.
    Searches EVERY img tag on the entire page.
    """
    print("   🖼️ Searching for ALL images on page...")
    
    images = []
    
    try:
        # Method 1: Use Playwright to find all img tags
        print("      Method 1: Locating all img elements...")
        img_locators = page.locator('img').all()
        print(f"         Found {len(img_locators)} img tags")
        
        for img in img_locators:
            try:
                src = (img.get_attribute("src") or 
                       img.get_attribute("data-src") or 
                       img.get_attribute("data-lazy-src"))
                
                if src and ("alicdn.com" in src or "ae01.alicdn.com" in src):
                    clean_src = src.split('?')[0]
                    if clean_src not in images and len(clean_src) > 50:
                        images.append(clean_src)
            except:
                continue
        
        print(f"      After Method 1: {len(images)} images")
        
        # Method 2: Parse entire HTML with BeautifulSoup
        print("      Method 2: Parsing HTML for img tags...")
        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        all_imgs = soup.find_all('img')
        print(f"         Found {len(all_imgs)} img tags in HTML")
        
        for img in all_imgs:
            src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
            if src and ('alicdn.com' in src or 'ae01.alicdn.com' in src):
                clean_src = src.split('?')[0]
                if clean_src not in images and len(clean_src) > 50:
                    images.append(clean_src)
        
        print(f"      After Method 2: {len(images)} images")
        
        # Method 3: Search for images in picture elements
        print("      Method 3: Looking for picture/source elements...")
        pictures = soup.find_all('picture')
        print(f"         Found {len(pictures)} picture elements")
        
        for pic in pictures:
            for src in pic.find_all('source'):
                srcset = src.get('srcset') or src.get('data-srcset')
                if srcset:
                    urls = re.findall(r'(https?://[^\s]+(?:alicdn\.com|ae01\.alicdn\.com)[^\s]*)', srcset)
                    for url in urls:
                        clean_url = url.split('?')[0]
                        if clean_url not in images and len(clean_url) > 50:
                            images.append(clean_url)
        
        print(f"      After Method 3: {len(images)} images")
        
        # Method 4: Search for images in data-url attributes
        print("      Method 4: Searching for data-url attributes...")
        data_url_elems = soup.find_all(attrs={'data-url': True})
        print(f"         Found {len(data_url_elems)} elements with data-url")
        
        for elem in data_url_elems:
            data_url = elem.get('data-url')
            if data_url and ('alicdn.com' in data_url or 'ae01.alicdn.com' in data_url):
                clean_url = data_url.split('?')[0]
                if clean_url not in images and len(clean_url) > 50:
                    images.append(clean_url)
        
        print(f"      After Method 4: {len(images)} images")
        
        # Method 5: Use JavaScript to extract all visible image URLs
        print("      Method 5: Using JavaScript to find images...")
        try:
            js_images = page.evaluate("""
                () => {
                    const urls = new Set();
                    // Find all img src
                    document.querySelectorAll('img').forEach(img => {
                        const src = img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy-src');
                        if (src && (src.includes('alicdn.com') || src.includes('ae01.alicdn.com'))) {
                            urls.add(src.split('?')[0]);
                        }
                    });
                    // Find in background-image styles
                    document.querySelectorAll('[style*="background-image"]').forEach(el => {
                        const match = el.getAttribute('style').match(/url\\(['"]?([^'")]+)['\"]?\\)/);
                        if (match && (match[1].includes('alicdn.com') || match[1].includes('ae01.alicdn.com'))) {
                            urls.add(match[1].split('?')[0]);
                        }
                    });
                    return Array.from(urls);
                }
            """)
            
            if js_images:
                print(f"         JavaScript found {len(js_images)} image URLs")
                for url in js_images:
                    if url not in images and len(url) > 50:
                        images.append(url)
            
            print(f"      After Method 5: {len(images)} images")
        except Exception as e:
            print(f"      Method 5 error: {e}")
        
        # Deduplicate and filter
        images = list(set(images))
        
        # Filter out low-quality/tiny images
        quality_images = []
        for img in images:
            if len(img) < 50:
                continue
            if any(bad in img.lower() for bad in ['icon', 'logo', '20x20', '50x50', '100x100', 'placeholder', 'avatar']):
                continue
            quality_images.append(img)
        
        # Limit to 30 images
        quality_images = quality_images[:30]
        
        print(f"   ✓ Final: {len(quality_images)} quality images")
        
        return quality_images
        
    except Exception as e:
        print(f"   ❌ Image extraction error: {e}")
        import traceback
        traceback.print_exc()
        return []


def extract_description_universal(page) -> tuple:
    """
    Extract full description text AND images.
    1. Expand all collapsed sections
    2. Extract text from ALL containers
    3. Extract all images
    """
    print("📝 Extracting complete description...")
    
    try:
        # Step 1: Try to click description tab
        print("   🔍 Looking for Description tab...")
        try:
            tab_selectors = [
                'text=Description',
                'a:has-text("Description")',
                '[role="tab"]:has-text("Description")',
            ]
            
            for selector in tab_selectors:
                try:
                    tab = page.locator(selector).first
                    if tab.count() > 0:
                        print(f"   ✓ Found and clicking Description tab")
                        tab.click(timeout=3000, force=True)
                        page.wait_for_timeout(2000)
                        break
                except:
                    continue
        except Exception as e:
            print(f"   ⚠️ Could not click Description tab: {e}")
        
        # Step 2: Expand all collapsed/hidden sections
        expand_all_collapsed_sections(page)
        
        # Wait for DOM to update after clicking
        page.wait_for_timeout(2500)
        
        # Step 3: Extract full description text from all containers
        description_text = extract_full_description_text(page)
        
        # Step 4: Extract all images
        images = find_all_images_on_page(page)
        
        return description_text, images
        
    except Exception as e:
        print(f"⚠️ Description extraction error: {e}")
        import traceback
        traceback.print_exc()
        return "", []


def extract_aliexpress_product(url: str) -> dict:
    """
    Extract AliExpress product data with Tor routing.
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
                
                final_url = page.url
                print(f"📍 Final URL: {final_url}")
                
                # CAPTCHA CHECK
                if is_captcha_page(page):
                    print("⚠️ CAPTCHA detected - rotating IP and retrying...")
                    browser.close()
                    continue
                
                # Wait for page to render
                print("⏳ Waiting for page to render...")
                time.sleep(8)
                
                # SCROLL to load lazy images
                print("⏳ Scrolling to load images...")
                try:
                    for _ in range(5):
                        page.mouse.wheel(0, random.randint(200, 400))
                        time.sleep(random.uniform(0.3, 0.7))
                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(1)
                except Exception as e:
                    print(f"⚠️ Scroll error: {e}")
                
                # SECOND CAPTCHA CHECK
                if is_captcha_page(page):
                    print("⚠️ CAPTCHA after scroll - rotating IP and retrying...")
                    browser.close()
                    continue
                
                # EXTRACT DATA
                title = extract_title_universal(page)
                store_info = extract_store_info_universal(page)
                description_text, description_images = extract_description_universal(page)
                
                # SUCCESS
                browser.close()
                
                result = {
                    "title": title if isinstance(title, str) else "",
                    "description_text": description_text if isinstance(description_text, str) else "",
                    "images": description_images if isinstance(description_images, list) else [],
                    "store_info": store_info if isinstance(store_info, dict) else {}
                }
                
                print(f"\n✅ EXTRACTION COMPLETE:")
                print(f"   Title: {len(result['title'])} chars")
                print(f"   Description: {len(result['description_text'])} chars")
                print(f"   Images: {len(result['images'])}")
                if result['images']:
                    print(f"   First image: {result['images'][0][:80]}...")
                print(f"   Store Info: {len(result['store_info'])} fields\n")
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
