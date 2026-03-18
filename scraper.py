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
    Extract store info from hidden popover element.
    The popover is initially hidden with display: none, and appears on hover.
    """
    store_info = {}
    
    print("📦 Extracting store info from popover...")
    
    # =====================
    # STRATEGY 1: Find and hover store element to trigger popover
    # =====================
    print("🔍 Strategy 1: Trigger popover via hover...")
    try:
        # Look for store link/element that triggers popover
        # Common selectors for store elements
        store_selectors = [
            '[data-spm*="store"]',
            '[class*="store"]',
            'a[href*="store"]',
            '[class*="seller"]',
            'span:has-text("Store info")',
            'div.store-detail--storeTitle--isySny7'
        ]
        
        for selector in store_selectors:
            try:
                # Check if element exists
                elem = page.locator(selector).first
                if elem.count() > 0:
                    print(f"   Found element: {selector}")
                    
                    # Hover to trigger popover
                    elem.hover()
                    time.sleep(1)
                    
                    # Wait for popover to become visible
                    popover = page.locator('.comet-v2-popover-wrap').first
                    if popover.count() > 0:
                        # Check if visible (not display: none)
                        is_visible = popover.evaluate("el => window.getComputedStyle(el).display !== 'none'")
                        
                        if is_visible or popover.get_attribute("style") and "display: none" in popover.get_attribute("style"):
                            print("   ⏳ Waiting for popover animation...")
                            time.sleep(0.5)
                            
                            # Try to get the table from popover
                            popover_body = page.locator('.comet-v2-popover-body').first
                            if popover_body.count() > 0:
                                html = popover_body.inner_html()
                                soup = BeautifulSoup(html, "html.parser")
                                table = soup.find("table")
                                
                                if table:
                                    rows = table.find_all("tr")
                                    for row in rows:
                                        cols = row.find_all("td")
                                        if len(cols) == 2:
                                            key = clean_text(cols[0].get_text()).replace(":", "").strip()
                                            value = clean_text(cols[1].get_text()).strip()
                                            if key:
                                                store_info[key] = value
                                    
                                    if store_info:
                                        print(f"✅ Store info from popover: {store_info}")
                                        return store_info
            except Exception as e:
                print(f"   ⚠️ Selector failed: {e}")
                continue
                
    except Exception as e:
        print(f"❌ Popover hover strategy failed: {e}")
 
    # =====================
    # STRATEGY 2: Find hidden popover in DOM and make it visible
    # =====================
    print("🔍 Strategy 2: Find hidden popover and unhide...")
    try:
        # The popover exists in DOM but is hidden
        popover_wrap = page.locator('.comet-v2-popover-wrap').first
        
        if popover_wrap.count() > 0:
            print("   Found popover-wrap in DOM")
            
            # Make it visible via JavaScript
            popover_wrap.evaluate("""
                el => {
                    el.style.display = 'block';
                    el.style.visibility = 'visible';
                }
            """)
            
            time.sleep(0.5)
            
            # Extract from visible popover
            popover_body = page.locator('.comet-v2-popover-body').first
            if popover_body.count() > 0:
                html = popover_body.inner_html()
                soup = BeautifulSoup(html, "html.parser")
                table = soup.find("table")
                
                if table:
                    rows = table.find_all("tr")
                    for row in rows:
                        cols = row.find_all("td")
                        if len(cols) == 2:
                            key = clean_text(cols[0].get_text()).replace(":", "").strip()
                            value = clean_text(cols[1].get_text()).strip()
                            if key:
                                store_info[key] = value
                    
                    if store_info:
                        print(f"✅ Store info from unhidden popover: {store_info}")
                        return store_info
                        
    except Exception as e:
        print(f"⚠️ Unhide strategy failed: {e}")
 
    # =====================
    # STRATEGY 3: Extract directly from page HTML (popover exists but hidden)
    # =====================
    print("🔍 Strategy 3: Extract from page HTML...")
    try:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        # Find the popover section
        popover_wrap = soup.find('div', class_='comet-v2-popover-wrap')
        if popover_wrap:
            print("   Found popover in page HTML")
            
            # Look for store info container
            store_info_div = popover_wrap.find('div', class_=lambda x: x and 'storeInfo' in x)
            if store_info_div:
                table = store_info_div.find("table")
                if table:
                    rows = table.find_all("tr")
                    for row in rows:
                        cols = row.find_all("td")
                        if len(cols) == 2:
                            key = clean_text(cols[0].get_text()).replace(":", "").strip()
                            value = clean_text(cols[1].get_text()).strip()
                            if key:
                                store_info[key] = value
                    
                    if store_info:
                        print(f"✅ Store info from HTML: {store_info}")
                        return store_info
                        
    except Exception as e:
        print(f"⚠️ HTML extraction failed: {e}")
 
    # =====================
    # STRATEGY 4: Search for store info div anywhere in page
    # =====================
    print("🔍 Strategy 4: Search all store-detail divs...")
    try:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        # Find any div with store-detail class
        store_divs = soup.find_all('div', class_=lambda x: x and 'store-detail' in x)
        print(f"   Found {len(store_divs)} store-detail divs")
        
        for store_div in store_divs:
            table = store_div.find("table")
            if table:
                rows = table.find_all("tr")
                temp_info = {}
                
                for row in rows:
                    cols = row.find_all("td")
                    if len(cols) == 2:
                        key = clean_text(cols[0].get_text()).replace(":", "").strip()
                        value = clean_text(cols[1].get_text()).strip()
                        if key:
                            temp_info[key] = value
                
                # Check if looks like store info
                if temp_info and any(k in str(temp_info).lower() 
                                    for k in ['store', 'location', 'name']):
                    print(f"✅ Store info found: {temp_info}")
                    return temp_info
                    
    except Exception as e:
        print(f"⚠️ Div search failed: {e}")
 
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
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',  # ← KEY for GCP
            ]
        )
        page = browser.new_page(
            viewport={'width': 1366, 'height': 768},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )

        try:
            page.goto(url, timeout=120000)  # 60 → 120 seconds
            page.wait_for_load_state("networkidle", timeout=30000)  # 15 → 30 seconds  
            time.sleep(5) 

            # -----------------------
            # TITLE
            # -----------------------
            try:
                page.wait_for_selector('[data-pl="product-title"]', timeout=10000)
                title = page.locator('[data-pl="product-title"]').inner_text().strip()
                print(f"✅ Title: {title[:80]}...")
            except Exception as e:
                print(f"❌ Title extraction failed: {e}")
                title = ""

            # -----------------------
            # STORE INFO (IMPROVED)
            # -----------------------
            store_info = extract_store_info_popover(page)

            # -----------------------
            # SCROLL
            # -----------------------
            print("⏳ Human-like scrolling...")
            for _ in range(10):
                page.evaluate("window.scrollBy(0, 500)")
                time.sleep(0.8)
            time.sleep(2)

            # -----------------------
            # DESCRIPTION
            # -----------------------
            print("📝 Extracting description...")
            description_text = ""
            description_html = ""

            desc_selectors = [
                "#product-description",
                '[class*="product-description"]',
                '[class*="detail-content"]',
                '.product-detail__description'
            ]
            
            for selector in desc_selectors:
                try:
                    page.wait_for_selector(selector, timeout=8000)
                    locators = page.locator(selector)
                    count = locators.count()
                    print(f"🔍 Found {count} desc blocks for {selector}")
                    
                    for i in range(min(count, 3)):
                        block = locators.nth(i)
                        html = block.inner_html()
                        soup = BeautifulSoup(html, "html.parser")
                        
                        for tag in soup(["script", "style", "iframe"]):
                            tag.decompose()
                        
                        text = soup.get_text(" ", strip=True)
                        
                        if (len(text) > 300 and 
                            any(word in text.lower() for word in 
                                ['feature', 'spec', 'parameter', 'function', 'include', 'package', 'material'])):
                            print(f"✅ Description block {i}: {len(text)} chars")
                            description_text = text
                            description_html = html
                            break
                    
                    if description_text:
                        break
                except:
                    continue

            # -----------------------
            # IMAGES
            # -----------------------
            print("🖼️ Extracting images...")
            description_images = []
            
            gallery_selectors = [
                'img[height>=400][src*="alicdn"]',
                '.product-gallery img[src*="alicdn"]',
                '[data-spm*="image"] img',
                'img[src*="-"][src*="alicdn"]'
            ]
            
            for selector in gallery_selectors:
                try:
                    imgs = page.locator(selector).all(max_items=15)
                    for img in imgs:
                        src = img.get_attribute("src") or img.get_attribute("data-src")
                        if src and "alicdn" in src and len(src) > 50:
                            description_images.append(src)
                except:
                    continue
            
            if description_html:
                soup = BeautifulSoup(description_html, "html.parser")
                for img in soup.find_all("img"):
                    src = img.get("src") or img.get("data-src")
                    if src and "alicdn" in src and not any(x in src for x in ["50x50", "icon", "logo"]):
                        description_images.append(src)
            
            description_images = list(set(description_images))[:20]
            print(f"✅ Images: {len(description_images)}")

            browser.close()

            return {
                "title": clean_text(title),
                "description_text": clean_text(description_text),
                "images": list(set(description_images))[:20],
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
