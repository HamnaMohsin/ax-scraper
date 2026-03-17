"""
Simple AliExpress Scraper - Final Version with Geo-Redirect Fix
Uses multiple selectors and JavaScript to find description
Handles regional redirects (.us, .uk, etc.)
"""

import re
import time
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


def clean_text(text: str) -> str:
    """Clean and normalize text."""
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_aliexpress_product(url: str) -> dict:
    """
    Extract AliExpress product - Final version with geo-redirect fix.
    Uses multiple selectors and JavaScript to find all content.
    Handles regional redirects (.us, .uk, etc.)
    
    Returns:
        {
            "title": str,
            "description_text": str,
            "images": list[str]
        }
    """
    print(f"🔍 Scraping: {url}")

    base_url = url.split('#')[0].strip()
    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    empty_result = {"title": "", "description_text": "", "images": []}

    with sync_playwright() as p:
        try:
            # Launch browser
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # Navigate to URL
            print("⏳ Loading page...")
            page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
            
            actual_url = page.url
            print(f"✅ Loaded: {actual_url}")
            
            # ── NEW: Detect and fix geo-redirect ───────────────────────────────────
            if actual_url != base_url and any(domain in actual_url for domain in [".us", ".uk", ".de", ".fr"]):
                print(f"⚠️  Geo-redirect detected: {base_url} → {actual_url}")
                print("🔄 Forcing original domain...")
                
                try:
                    # Extract original domain from base_url
                    original_domain = "/".join(base_url.split("/")[:3])  # https://www.aliexpress.com
                    # Extract product ID
                    product_id = base_url.split("/item/")[1].split(".")[0].split("?")[0]
                    # Reconstruct original URL
                    original_url = f"{original_domain}/item/{product_id}.html"
                    
                    print(f"🔄 Retrying with: {original_url}")
                    page.goto(original_url, timeout=60000, wait_until="domcontentloaded")
                    print(f"✅ Reloaded: {page.url}")
                except Exception as e:
                    print(f"⚠️  Could not force original domain: {e}")
            # ─────────────────────────────────────────────────────────────────────────
            
            # Wait for page to fully render
            print("⏳ Waiting for content to render...")
            time.sleep(3)

            # Wait for product title with FALLBACK selectors
            print("⏳ Waiting for title...")
            title = ""
            # ── NEW: Multiple title selectors with fallbacks ─────────────────────────
            title_selectors = [
                '[data-pl="product-title"]',         # Main selector
                'h1[data-pl="product-title"]',       # With h1 tag
                '.productTitle_N8TgC',                # US version
                '.product-title-text',
                'h1.product-title',
                '[class*="product-title"]',          # Any class with product-title
            ]
            
            for selector in title_selectors:
                try:
                    elem = page.query_selector(selector)
                    if elem:
                        text = elem.text_content().strip()
                        # Skip if it's just "Aliexpress" (logo) or too short
                        if text and text.lower() != "aliexpress" and len(text) > 10:
                            title = text
                            print(f"✅ Title found with: {selector}")
                            break
                except Exception as e:
                    pass
            # ─────────────────────────────────────────────────────────────────────────
            
            if not title:
                print(f"❌ Title not found after trying {len(title_selectors)} selectors")
                browser.close()
                return empty_result
            
            print(f"✅ Title: {title[:70]}")

            # Scroll down to load more content
            print("⏳ Scrolling to load content...")
            page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
            time.sleep(2)

            # Extract description using multiple methods
            print("📝 Extracting description...")
            description_text = ""
            
            # List of selectors to try, in order
            selectors_to_try = [
                "#product-description",
                ".product-description",
                ".detailmodule_html",
                ".detail-desc-decorate-richtext",
                "[class*='product-description']",
                "[class*='detail-desc']",
                "[class*='detailmodule']",
            ]
            
            desc_elem = None
            used_selector = None
            
            for selector in selectors_to_try:
                try:
                    elem = page.query_selector(selector)
                    if elem:
                        # Check if it has meaningful content
                        text_content = elem.text_content().strip()
                        if len(text_content) > 50:  # Has enough text
                            desc_elem = elem
                            used_selector = selector
                            print(f"✅ Found description with: {selector}")
                            break
                except Exception as e:
                    pass
            
            if desc_elem:
                try:
                    # Method 1: Direct text extraction
                    description_text = desc_elem.text_content().strip()
                except Exception as e:
                    print(f"⚠️  Direct extraction failed: {e}")
            
            # If still no description, try JavaScript search
            if not description_text or len(description_text) < 100:
                print("⏳ Trying JavaScript search for description...")
                try:
                    js_description = page.evaluate("""
                    () => {
                        // Try to find divs with "Product Features" or "Product Advantages" text
                        const allDivs = document.querySelectorAll('div, article, section');
                        
                        for (let elem of allDivs) {
                            const text = elem.textContent;
                            // Look for indicators of description
                            if ((text.includes('Product Features') || 
                                 text.includes('Product Advantages') ||
                                 text.includes('Multi-functional') ||
                                 text.includes('Adjustment Design') ||
                                 text.includes('Description') ||
                                 text.includes('Details')) &&
                                text.length > 500) {
                                
                                // Extract all text from this element
                                let result = '';
                                const walker = document.createTreeWalker(
                                    elem,
                                    NodeFilter.SHOW_TEXT,
                                    null,
                                    false
                                );
                                
                                let node;
                                while (node = walker.nextNode()) {
                                    const trimmed = node.textContent.trim();
                                    if (trimmed && trimmed.length > 2) {
                                        result += trimmed + ' ';
                                    }
                                }
                                
                                if (result.length > 500) {
                                    return result.trim();
                                }
                            }
                        }
                        
                        // Fallback: just get body text
                        return document.body.innerText;
                    }
                    """)
                    
                    if js_description and len(js_description) > 100:
                        description_text = js_description
                        print(f"✅ Description found via JavaScript: {len(description_text)} chars")
                except Exception as e:
                    print(f"⚠️  JavaScript search failed: {e}")
            
            # Clean up description
            if description_text:
                description_text = re.sub(r'\s+', ' ', description_text).strip()
                
                # Limit to 4000 chars (but keep full content if it's product description)
                if len(description_text) > 4000:
                    # Try to find a good cutoff point (end of sentences)
                    desc_cut = description_text[:4000]
                    last_period = desc_cut.rfind('.')
                    if last_period > 3000:
                        description_text = desc_cut[:last_period + 1]
                    else:
                        description_text = desc_cut
                
                print(f"✅ Description: {len(description_text)} chars")
            else:
                print("⚠️  No description found")

            # Extract images
            print("📝 Extracting images...")
            images = []
            
            try:
                all_images = page.query_selector_all("img")
                for img in all_images:
                    src = img.get_attribute("src")
                    data_src = img.get_attribute("data-src")
                    
                    img_url = src or data_src
                    
                    if img_url:
                        img_url = img_url.strip()
                        # Filter for AliExpress CDN images and reasonable sizes
                        if ("alicdn" in img_url or "aliexpress" in img_url) and img_url not in images:
                            images.append(img_url)
            except Exception as e:
                print(f"⚠️  Error extracting images: {e}")
            
            print(f"✅ Images: {len(images)} found")

            browser.close()

            # Verify we got something
            if not title:
                print("❌ No title extracted")
                return empty_result
            
            return {
                "title":            clean_text(title),
                "description_text": clean_text(description_text) if description_text else "",
                "images":           images[:20],  # Limit to 20 images
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
