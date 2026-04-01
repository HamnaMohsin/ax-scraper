import re
import time
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def extract_store_info_popover(page) -> dict:
    """
    Extract store info DIRECTLY from DOM (no hover needed)
    """
    store_info = {}
    print("📦 Extracting store info from popover...")
    
    # STRATEGY 1: Direct extraction from common store containers
    print("🔍 Strategy 1: Direct store info extraction...")
    store_selectors = [
        '[class*="store-detail"]',
        '[class*="storeInfo"]'
    ]
    
    try:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        for selector in store_selectors:
            elements = soup.select(selector)
            for elem in elements:
                text = clean_text(elem.get_text())
                if any(word in text.lower() for word in ['store', 'seller', 'china', 'since', 'location']):
                    # Extract key-value pairs
                    lines = [line.strip() for line in text.split('\n') if ':' in line and line.strip()]
                    for line in lines:
                        if ':' in line:
                            parts = line.split(':', 1)
                            if len(parts) == 2:
                                key = clean_text(parts[0]).replace(":", "").strip()
                                value = clean_text(parts[1]).strip()
                                if key and value:
                                    store_info[key] = value
    
        if store_info:
            print(f"✅ Store info direct: {store_info}")
            return store_info
            
    except Exception as e:
        print(f"⚠️ Strategy 1 failed: {e}")
    
    # STRATEGY 2: Seller points list
    print("🔍 Strategy 2: Seller points...")
    try:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        seller_points = soup.select('.seo-sellpoints--sellerPoint--RcmFO_y, [class*="seller"], [class*="storeName"]')
        
        for point in seller_points:
            text = clean_text(point.get_text())
            if any(word in text.lower() for word in ['store', 'seller']) and len(text) > 10:
                store_info['Store Name'] = text
                break
    except Exception as e:
        print(f"⚠️ Strategy 2 failed: {e}")
    
    # STRATEGY 3: Store link text
    print("🔍 Strategy 3: Store link...")
    try:
        store_links = page.locator('a[href*="store"], [class*="store"][title]').all()
        for link in store_links:
            title = link.get_attribute('title') or link.inner_text()
            if title and 'store' in title.lower():
                store_info['Store Name'] = clean_text(title)
                break
    except:
        pass
    
    if store_info:
        print(f"✅ Store info found: {store_info}")
    else:
        print("⚠️ Could not extract store info")
        
    return store_info


def extract_aliexpress_product(url: str) -> dict:
    print(f"🔍 Scraping: {url}")

    empty_result = {
        "title": "",
        "description_text": "",
        "images": [],
        "store_name": "",
        "store_info": {}
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1366, 'height': 768},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='en-US'
        )
        page = context.new_page()

        try:
            print("⏳ Loading page...")
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            
            # More reliable load wait
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except:
                print("⚠️ Network idle timeout, using domcontentloaded...")
                page.wait_for_load_state("domcontentloaded", timeout=5000)
            
            time.sleep(3)

            # -----------------------
            # TITLE
            # -----------------------
            print("📝 Extracting title...")
            try:
                title_selectors = [
                    '[data-pl="product-title"]',
                    'h1[data-spm-anchor-id]',
                    '.product-title-text',
                    '[class*="product-title"]'
                ]
                
                title = ""
                for selector in title_selectors:
                    try:
                        elem = page.locator(selector).first
                        if elem.count() > 0:
                            title = elem.inner_text(timeout=5000).strip()
                            if title and len(title) > 10:
                                break
                    except:
                        continue
                        
                print(f"✅ Title: {title[:80]}...")
            except Exception as e:
                print(f"❌ Title extraction failed: {e}")
                title = ""

            # -----------------------
            # STORE INFO (RELIABLE)
            # -----------------------
            store_info = extract_store_info_popover(page)

            # -----------------------
            # HUMAN-LIKE SCROLLING
            # -----------------------
            print("⏳ Human-like scrolling...")
            for i in range(12):
                page.evaluate(f"window.scrollBy(0, {400 + i*50})")
                time.sleep(0.6 + i*0.1)
            time.sleep(2)

            # -----------------------
            # DESCRIPTION (CAPTURE ALL)
            # -----------------------
            print("📝 Extracting description...")
            description_text = ""
            description_html = ""
            all_descriptions = []

            desc_selectors = [
                '#product-description',                    # Main desc
                '[class*="detailmodule"]',                 # Your modules ✅
                '.product-detail__description',            # Alt layout
                '[id*="description"], [class*="desc"]'     # Catch-all
            ]
            
            for selector in desc_selectors:
                try:
                    page.wait_for_selector(selector, timeout=8000)
                    locators = page.locator(selector)
                    count = locators.count()
                    print(f"🔍 Found {count} desc blocks for {selector}")
                    
                    for i in range(min(count, 15)):  # Check MORE blocks
                        try:
                            block = locators.nth(i)
                            html = block.inner_html(timeout=3000)
                            soup_block = BeautifulSoup(html, "html.parser")
                            
                            # Remove junk but KEEP ALL content
                            for tag in soup_block(["script", "style", "iframe", "svg"]):
                                tag.decompose()
                            
                            text = soup_block.get_text(" ", strip=True)
                            
                            # Keep ANY substantial block
                            if len(text) > 80:  # Lowered threshold
                                all_descriptions.append({
                                    'text': text,
                                    'html': html,
                                    'length': len(text)
                                })
                                print(f"   📄 Block {i}: {len(text)} chars")
                        except:
                            continue
                            
                except Exception as e:
                    print(f"   ⚠️ Selector {selector} failed")
                    continue

            # Combine ALL descriptions
            if all_descriptions:
                all_descriptions.sort(key=lambda x: x['length'], reverse=True)
                description_text = " ".join([d['text'] for d in all_descriptions[:5]])
                description_html = all_descriptions[0]['html']
                print(f"✅ Combined description: {len(description_text)} chars from {len(all_descriptions)} blocks")

            # -----------------------
            # IMAGES (COMPREHENSIVE)
            # -----------------------
            print("🖼️ Extracting images...")
            all_images = set()

            # 1. Gallery & Large images
            gallery_selectors = [
                'img[src*="alicdn"]',                      # All alicdn
                '.detail-desc-decorate-image',             # Your images ✅
                '.detailmodule_image img',                 # Module images ✅
            ]

            for selector in gallery_selectors:
                try:
                    imgs = page.locator(selector).all(max_items=60)
                    for img in imgs:
                        src = (img.get_attribute("src") or 
                              img.get_attribute("data-src") or 
                              img.get_attribute("data-lazy-src"))
                        
                        if src and ("alicdn.com" in src or "ae01.alicdn.com" in src) and len(src) > 40:
                            clean_src = src.split('?')[0].split('|')[0]
                            all_images.add(clean_src)
                except:
                    continue

            # 2. Description images (ALL)
            if description_html:
                soup_desc = BeautifulSoup(description_html, "html.parser")
                for img in soup_desc.find_all("img"):
                    src = (img.get("src") or img.get("data-src") or img.get("data-lazy-src"))
                    if src and ("alicdn.com" in src or "ae01.alicdn.com" in src):
                        clean_src = src.split('?')[0].split('|')[0]
                        all_images.add(clean_src)

            # 3. Page-wide backup search
            try:
                page_html = page.content()
                soup_page = BeautifulSoup(page_html, "html.parser")
                for img in soup_page.find_all("img", src=re.compile(r"alicdn")):
                    src = img.get("src") or img.get("data-src")
                    if src and len(src) > 40:
                        clean_src = src.split('?')[0].split('|')[0]
                        all_images.add(clean_src)
            except:
                pass

            # Filter & limit
            images = list(all_images)
            # Remove tiny previews/icons
            images = [img for img in images 
                     if not any(x in img.lower() for x in ["50x50", "icon", "logo", "avatar", "100x100"])]
            
            images = images[:30]  # Top 30 highest quality
            print(f"✅ Images: {len(images)} (found {len(all_images)} total)")

            browser.close()
            
            return {
                "title": clean_text(title),
                "description_text": clean_text(description_text),
                "images": images,
                "store_info": store_info
            }

        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            try:
                browser.close()
            except:
                pass
            return empty_result


# # Test function
# if __name__ == "__main__":
#     url = "https://www.aliexpress.com/item/1005010735189221.html"
#     result = extract_aliexpress_product(url)
#     print("\n" + "="*50)
#     print("FINAL RESULT:")
#     print(f"Title: {result['title'][:100]}...")
#     print(f"Description: {len(result['description_text'])} chars")
#     print(f"Images: {len(result['images'])}")
#     print(f"Store: {result['store_info']}")
