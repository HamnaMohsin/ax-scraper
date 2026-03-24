import re
import time
import random
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from stem import Signal
from stem.control import Controller


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
    """Rotate Tor circuit"""
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
        print(f"⚠️ Tor error: {e}")
        return False


def is_captcha_page(page) -> bool:
    """Detect CAPTCHA"""
    page_url = page.url.lower()
    captcha_url_keywords = ["baxia", "punish", "captcha", "verify"]
    if any(kw in page_url for kw in captcha_url_keywords):
        return True
    
    try:
        if page.locator("iframe[src*='recaptcha']").count() > 0:
            return True
        if page.locator(".baxia-punish").count() > 0:
            return True
    except:
        pass
    
    return False


def extract_description_from_rendered_page(page) -> str:
    """
    Extract description from the RENDERED page (not raw HTML)
    This can access Shadow DOM content because Playwright renders it
    """
    print("📝 Extracting description from rendered page...")
    
    try:
        # Wait for dynamic content
        page.wait_for_timeout(3000)
        
        # Get the RENDERED content (includes Shadow DOM)
        # Target the main description area
        print("   🔍 Finding description container...")
        
        # Try to find the product-description container
        desc_container = page.locator('[data-pl="product-description"]').first
        
        if desc_container.count() == 0:
            desc_container = page.locator('#product-description').first
        
        if desc_container.count() == 0:
            desc_container = page.locator('[class*="description"]').first
        
        if desc_container.count() > 0:
            print("   ✓ Found description container")
            
            # Get inner text (this includes rendered Shadow DOM content!)
            try:
                inner_text = desc_container.inner_text(timeout=5000)
                print(f"   ✓ Got {len(inner_text)} chars from description area")
                
                if len(inner_text) > 100:
                    # Clean up the text
                    # Remove excessive whitespace
                    lines = inner_text.split('\n')
                    
                    # Filter out junk lines
                    junk_patterns = [
                        'Positive Feedback',
                        'Free shipping',
                        'Delivery:',
                        'Refund if',
                        'Buy now',
                        'Add to cart',
                        'Similar items',
                        'Help Center',
                        'Safe payments',
                        'Secure personal',
                        'Shop sustainably',
                        'Quantity',
                        'Max. ',
                        'Follow',
                        'Message',
                        'Recommended from',
                        'People also searched',
                        'This product belongs',
                        'Alibaba Group',
                        'Russian',
                        'Portuguese',
                        'Spanish',
                        'French',
                        'German',
                        'Italian',
                        'Dutch',
                        'Turkish',
                        'Japanese',
                        'Korean',
                        'Thai',
                        'Arabic',
                        'Hebrew',
                        'Polish',
                        'All Popular',
                        'Promotion',
                        'Low Price',
                        'Great Value',
                        'Wiki',
                        'Blog',
                        'Video',
                    ]
                    
                    # Keep lines that are description-related
                    description_lines = []
                    for line in lines:
                        line = line.strip()
                        
                        # Skip empty lines temporarily, keep them for formatting
                        if not line:
                            continue
                        
                        # Skip if line matches junk patterns
                        is_junk = False
                        for pattern in junk_patterns:
                            if pattern.lower() in line.lower():
                                is_junk = True
                                break
                        
                        if not is_junk and len(line) > 2:
                            description_lines.append(line)
                    
                    # Combine lines
                    if description_lines:
                        combined = "\n".join(description_lines)
                        
                        # Clean up whitespace
                        combined = re.sub(r'\n\s*\n+', '\n', combined)
                        combined = combined.strip()
                        
                        print(f"   ✓ Extracted {len(combined)} chars after filtering")
                        return combined
        
        # Fallback: Get all text and try to extract description
        print("   ⚠️ Container not found, trying full page extraction...")
        
        page_text = page.inner_text(timeout=5000)
        print(f"   Got {len(page_text)} chars from full page")
        
        # Try to extract just the description part
        # Usually starts with product name/model and ends before pricing
        lines = page_text.split('\n')
        
        description_lines = []
        capture = False
        price_started = False
        
        for line in lines:
            stripped = line.strip()
            
            # Start capturing from product info
            if any(kw in stripped.lower() for kw in ['thermos', 'material', 'stainless', 'product', 'description', 'specification', 'feature']):
                capture = True
            
            # Stop capturing when we hit pricing/seller info
            if any(kw in stripped.lower() for kw in ['$', 'price', 'sold by', 'seller', 'shipping', 'delivery:', 'refund']):
                if capture and any(c.isdigit() for c in stripped):
                    price_started = True
            
            if capture and not price_started:
                # Filter junk
                is_junk = any(junk.lower() in stripped.lower() for junk in [
                    'positive feedback', 'help center', 'add to cart', 'buy now',
                    'similar items', 'people also', 'russian', 'portuguese'
                ])
                
                if not is_junk and len(stripped) > 2:
                    description_lines.append(stripped)
        
        if description_lines:
            result = "\n".join(description_lines)
            result = re.sub(r'\n\s*\n+', '\n', result)
            print(f"   ✓ Extracted {len(result)} chars from fallback method")
            return result
        
        return ""
    
    except Exception as e:
        print(f"   ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return ""


def extract_description_images(page) -> list:
    """Extract images from the page"""
    print("🖼️ Extracting images...")
    
    images = []
    
    try:
        # Get all images on the page
        img_elements = page.locator('img').all()
        print(f"   Found {len(img_elements)} total images on page")
        
        alicdn_count = 0
        for img in img_elements:
            try:
                src = img.get_attribute('src') or img.get_attribute('data-src') or img.get_attribute('data-lazy-src')
                
                if src and 'alicdn.com' in src:
                    # Clean up URL
                    clean_src = src.split('?')[0]
                    
                    # Quality check
                    if (len(clean_src) > 50 and 
                        not any(bad in clean_src.lower() for bad in ['icon', 'logo', '20x20', '50x50', '100x100', '/s.gif']) and
                        clean_src not in images):
                        images.append(clean_src)
                        alicdn_count += 1
            except:
                continue
        
        print(f"   ✓ Found {alicdn_count} quality images")
        
        # Limit to 20
        images = images[:20]
        
        return images
    
    except Exception as e:
        print(f"   ⚠️ Error: {e}")
        return []


def extract_title_universal(page) -> str:
    """Extract title"""
    
    print("📌 Extracting title...")
    
    title_selectors = [
        '[data-pl="product-title"]',
        'h1',
        '[class*="product-title"]',
        'span[class*="title"]',
    ]
    
    for selector in title_selectors:
        try:
            elem = page.locator(selector).first
            if elem.count() > 0:
                title = elem.inner_text().strip()
                if title and len(title) > 5:
                    print(f"✅ Title: {title[:80]}...")
                    return title
        except:
            continue
    
    return ""


def extract_aliexpress_product(url: str) -> dict:
    """
    Extract from AliExpress using Playwright's rendered content
    This can access Shadow DOM content
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
            print(f"   Waiting {wait_time}s...")
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
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                timezone_id='America/New_York'
            )
            
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            try:
                # LOAD PAGE
                print("📡 Loading page...")
                page.goto(url, timeout=120000, wait_until="domcontentloaded")
                time.sleep(2)
                
                # CAPTCHA CHECK
                if is_captcha_page(page):
                    print("⚠️ CAPTCHA detected - retrying...")
                    browser.close()
                    continue
                
                # WAIT FOR RENDERING
                print("⏳ Waiting for page to render...")
                time.sleep(8)
                
                # SCROLL TO LOAD CONTENT
                print("⏳ Scrolling...")
                for _ in range(5):
                    page.mouse.wheel(0, random.randint(150, 300))
                    time.sleep(0.3)
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(1)
                
                # SECOND CAPTCHA CHECK
                if is_captcha_page(page):
                    print("⚠️ CAPTCHA after scroll - retrying...")
                    browser.close()
                    continue
                
                # EXTRACT DATA
                print("\n--- EXTRACTING DATA ---")
                title = extract_title_universal(page)
                description_text = extract_description_from_rendered_page(page)
                images = extract_description_images(page)
                
                browser.close()
                
                # RETURN RESULTS
                result = {
                    "title": title,
                    "description_text": description_text,
                    "images": images,
                    "store_info": {}
                }
                
                print(f"\n✅ RESULTS:")
                print(f"   Title: {len(title)} chars")
                print(f"   Description: {len(description_text)} chars")
                print(f"   Images: {len(images)}")
                
                # Validation
                if description_text:
                    if 'keep hot' in description_text.lower():
                        print("   ✅ Includes temperature specs")
                    if 'material' in description_text.lower():
                        print("   ✅ Includes material")
                    if len(description_text) > 500:
                        print("   ✅ Substantial content")
                
                return result
                
            except PlaywrightTimeoutError as e:
                print(f"⚠️ Timeout: {e}")
                browser.close()
                continue
                
            except Exception as e:
                print(f"❌ Error: {e}")
                import traceback
                traceback.print_exc()
                try:
                    browser.close()
                except:
                    pass
                continue
    
    print("❌ Failed after all retries")
    return empty_result
