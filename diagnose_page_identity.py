"""
Diagnostic to check:
1. What page URL we actually end up on (after redirects)
2. What page title is
3. Take a screenshot
4. Show page content preview
5. Identify if it's the right page
"""

import time
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

def diagnose_page_identity(url: str):
    """Check what page is actually being loaded"""
    
    print("🔍 DIAGNOSING PAGE IDENTITY...")
    print("=" * 70)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
            ]
        )
        
        page = browser.new_page(
            viewport={'width': 1366, 'height': 768},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )

        try:
            print(f"\n📍 REQUESTED URL:")
            print(f"   {url}\n")
            
            # =====================
            # NAVIGATE AND WAIT
            # =====================
            print("📡 Navigating...")
            page.goto(url, timeout=120000, wait_until="domcontentloaded")
            print("   ✅ Loaded (domcontentloaded)")
            
            page.wait_for_load_state("networkidle", timeout=30000)
            print("   ✅ Network idle")
            
            time.sleep(5)
            print("   ✅ Waited 5 seconds")
            
            # =====================
            # CHECK ACTUAL URL (after redirects)
            # =====================
            print(f"\n🔗 ACTUAL URL (after navigation):")
            actual_url = page.url
            print(f"   {actual_url}")
            
            if actual_url != url:
                print(f"\n   ⚠️ REDIRECTED!")
                print(f"      From: {url}")
                print(f"      To:   {actual_url}")
            
            # =====================
            # PAGE TITLE
            # =====================
            page_title = page.title()
            print(f"\n📄 PAGE TITLE:")
            print(f"   {page_title}")
            
            # =====================
            # PAGE SIZE & CONTENT PREVIEW
            # =====================
            html = page.content()
            print(f"\n📊 PAGE SIZE: {len(html)} bytes")
            
            # Check for product page indicators
            print(f"\n🔍 CONTENT CHECKS:")
            
            indicators = {
                'AliExpress keywords': ('alicdn', 'aliexpress', 'product'),
                'Product page keywords': ('price', 'add to cart', 'store', 'reviews'),
                'Expected selectors': ('data-pl', 'product-title', 'product-name'),
                'Shopping cart elements': ('cart', 'quantity', 'buy'),
            }
            
            for check_name, keywords in indicators.items():
                found = []
                for keyword in keywords:
                    if keyword.lower() in html.lower():
                        found.append(keyword)
                
                if found:
                    print(f"   ✅ {check_name}: {', '.join(found)}")
                else:
                    print(f"   ❌ {check_name}: None found")
            
            # =====================
            # IDENTIFY PAGE TYPE
            # =====================
            print(f"\n🏷️ PAGE IDENTIFICATION:")
            
            soup = BeautifulSoup(html, "html.parser")
            
            # Look for product ID in URL or page
            if "item/" in actual_url:
                item_id = actual_url.split("item/")[-1].split("?")[0].split(".")[0]
                print(f"   Product ID from URL: {item_id}")
            
            # Check page structure
            headings = soup.find_all(['h1', 'h2', 'h3'])
            if headings:
                print(f"   Found {len(headings)} headings:")
                for heading in headings[:3]:
                    text = heading.get_text().strip()[:60]
                    print(f"      - {heading.name}: {text}")
            
            # Check for error indicators
            print(f"\n⚠️ ERROR INDICATORS:")
            error_keywords = ['404', 'error', 'not found', 'access denied', 'suspended', '403', '500']
            found_errors = [kw for kw in error_keywords if kw.lower() in html.lower()]
            
            if found_errors:
                print(f"   ❌ Found error keywords: {found_errors}")
            else:
                print(f"   ✅ No error indicators found")
            
            # =====================
            # CHECK IF CONTENT IS DYNAMIC (JavaScript)
            # =====================
            print(f"\n⚙️ DYNAMIC CONTENT CHECK:")
            
            # Check for common JS framework indicators
            frameworks = {
                'React': ('data-react', '__react'),
                'Vue': ('data-vue', '__vue'),
                'Angular': ('data-ng', 'ng-'),
                'Script tags': ('<script',),
            }
            
            for framework, patterns in frameworks.items():
                found = any(pattern in html for pattern in patterns)
                if found:
                    print(f"   ✅ {framework}: Yes")
                else:
                    print(f"   ❌ {framework}: No")
            
            # =====================
            # SCROLL AND RECHECK
            # =====================
            print(f"\n📜 CHECKING LAZY-LOADED CONTENT:")
            print(f"   Scrolling to trigger dynamic loading...")
            
            page.evaluate("window.scrollBy(0, 500)")
            time.sleep(2)
            
            # Get updated HTML
            html_after_scroll = page.content()
            print(f"   Page size after scroll: {len(html_after_scroll)} bytes")
            
            if len(html_after_scroll) > len(html):
                print(f"   ✅ Content increased by {len(html_after_scroll) - len(html)} bytes")
                print(f"   ⚠️ Some content is lazy-loaded!")
            else:
                print(f"   ✅ No change after scroll")
            
            # =====================
            # TAKE SCREENSHOT
            # =====================
            print(f"\n📸 TAKING SCREENSHOT...")
            try:
                page.screenshot(path="/tmp/page_screenshot.png")
                print(f"   ✅ Screenshot saved to /tmp/page_screenshot.png")
                print(f"   You can download this to see what page actually looks like")
            except Exception as e:
                print(f"   ⚠️ Screenshot failed: {e}")
            
            # =====================
            # RECOMMENDATIONS
            # =====================
            print(f"\n💡 DIAGNOSIS SUMMARY:")
            print(f"-" * 70)
            
            if actual_url != url:
                print(f"⚠️ URL REDIRECT DETECTED")
                print(f"   The page redirected. Check if this is expected.")
            
            if 'product' not in page_title.lower() and 'item' not in page_title.lower():
                print(f"⚠️ PAGE TITLE UNEXPECTED")
                print(f"   Title doesn't look like a product page: {page_title}")
            
            if len(html_after_scroll) > len(html) + 10000:
                print(f"⚠️ SIGNIFICANT LAZY LOADING DETECTED")
                print(f"   Try waiting longer or scrolling before extracting")
            
            if 'data-pl' not in html and 'data-spm' not in html:
                print(f"⚠️ MISSING DATA ATTRIBUTES")
                print(f"   Page might not be AliExpress product page structure")
            
            print(f"\n✅ ACTUAL PAGE:")
            print(f"   URL: {actual_url}")
            print(f"   Title: {page_title}")
            print(f"   Size: {len(html)} bytes")
            
            browser.close()
            
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            browser.close()


if __name__ == "__main__":
    url = "https://www.aliexpress.com/item/1005010735189221.html"
    diagnose_page_identity(url)
