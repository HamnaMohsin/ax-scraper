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
    except:
        pass
    
    return False


def extract_title_universal(page) -> str:
    """Extract title - try multiple selectors"""
    
    print("📌 Extracting title...")
    
    title_selectors = [
        ('[data-pl="product-title"]', "data-pl product-title"),
        ('h1', "h1 heading"),
        ('[class*="product-title"]', "product-title class"),
        ('span[class*="title"]', "span title class"),
    ]
    
    for selector, desc in title_selectors:
        try:
            elem = page.locator(selector).first
            if elem.count() > 0:
                title = elem.inner_text().strip()
                if title and len(title) > 5:
                    print(f"✅ Title ({desc}): {title[:80]}...")
                    return title
        except:
            continue
    
    print("⚠️ Could not extract title from selectors")
    return ""


def extract_aliexpress_product(url: str) -> dict:
    """
    Extract AliExpress product data
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
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                timezone_id='America/New_York'
            )
            
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            try:
                # LOAD
                print("📡 Loading page...")
                page.goto(url, timeout=120000, wait_until="domcontentloaded")
                time.sleep(2)
                
                # CAPTCHA CHECK
                if is_captcha_page(page):
                    print("⚠️ CAPTCHA - retrying...")
                    browser.close()
                    continue
                
                # WAIT
                print("⏳ Rendering...")
                time.sleep(8)
                
                # SCROLL
                print("⏳ Scrolling...")
                for _ in range(5):
                    page.mouse.wheel(0, random.randint(150, 300))
                    time.sleep(0.3)
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(1)
                
                # SECOND CAPTCHA CHECK
                if is_captcha_page(page):
                    print("⚠️ CAPTCHA - retrying...")
                    browser.close()
                    continue
                
                # EXTRACT TITLE
                title = extract_title_universal(page)
                
                # ✅ VALIDATION: Ensure title is captured
                if not title or len(title) == 0:
                    print("⚠️ Title extraction failed, trying fallback...")
                    try:
                        page_title = page.title()
                        if page_title and len(page_title) > 5:
                            # Try to extract just the product name part
                            title = page_title.split(' | ')[0] if ' | ' in page_title else page_title
                            print(f"   ✓ Got title from page.title(): {title[:80]}")
                    except:
                        pass
                
                # ✅ GUARD: Make sure title doesn't get cleared later
                original_title = title
                print(f"🔐 Title backed up: {len(original_title)} chars")
                
                # EXTRACT DESCRIPTION
                print("📝 Extracting description...")
                description_text = ""
                description_images = []
                
                try:
                    # Get paragraph text
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
                            print(f"   ✓ Got {len(description_text)} chars from paragraphs")
                    except Exception as e:
                        print(f"   ⚠️ Method 0 failed: {e}")
                    
                    # Get container inner text
                    print("   🎯 Method 1: Container inner_text...")
                    try:
                        desc_container = page.locator('#product-description').first
                        if desc_container.count() > 0:
                            inner_text = desc_container.inner_text(timeout=5000).strip()
                            if inner_text and len(inner_text) > 100:
                                description_text = inner_text
                                print(f"   ✓ Got {len(description_text)} chars")
                    except Exception as e:
                        print(f"   ⚠️ Method 1 failed: {e}")
                    
                    # Extract from Shadow DOM
                    print("   🎯 Method 2: Shadow DOM extraction...")
                    try:
                        shadow_text, shadow_images = extract_description_shadow_dom(page)
                        
                        if shadow_text:
                            combined = description_text + " " + shadow_text
                            description_text = re.sub(r"\s+", " ", combined).strip()
                            print(f"   ✓ Combined with shadow: {len(description_text)} chars")
                        
                        if shadow_images:
                            description_images = list(set(description_images + shadow_images))
                    except Exception as e:
                        print(f"   ⚠️ Shadow DOM error: {e}")
                    
                    # Extract images
                    print("🖼️ Extracting images...")
                    try:
                        desc_container = page.locator('#product-description').first
                        if desc_container.count() > 0:
                            imgs = desc_container.locator('img').all()
                            for img in imgs:
                                src = img.get_attribute('src') or img.get_attribute('data-src')
                                if src and 'alicdn.com' in src:
                                    clean_src = src.split('?')[0]
                                    if len(clean_src) > 50 and clean_src not in description_images:
                                        description_images.append(clean_src)
                        
                        description_images = description_images[:20]
                        print(f"   ✓ Got {len(description_images)} images")
                    except Exception as e:
                        print(f"   ⚠️ Image error: {e}")
                
                except Exception as e:
                    print(f"❌ Error: {e}")
                    import traceback
                    traceback.print_exc()
                
                browser.close()
                
                # ✅ RESTORE TITLE IF NEEDED
                if not title or len(title) == 0:
                    title = original_title
                    print(f"🔐 Restored title from backup: {len(title)} chars")
                
                # BUILD RESULT
                result = {
                    "title": title if isinstance(title, str) and len(title) > 0 else original_title,
                    "description_text": description_text if isinstance(description_text, str) else "",
                    "images": description_images if isinstance(description_images, list) else [],
                    "store_info": {}
                }
                
                print(f"\n✅ FINAL RESULT:")
                print(f"   Title: {len(result['title'])} chars")
                print(f"   Description: {len(result['description_text'])} chars")
                print(f"   Images: {len(result['images'])}")
                
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
    
    print("❌ Failed")
    return empty_result
