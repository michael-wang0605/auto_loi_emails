#!/usr/bin/env python3
"""
Human-like script to collect Zillow property URLs by clicking through property cards.
Opens each property in a new tab and collects the URL from the address bar.
Filters for houses (single-family rentals) only.
"""
import argparse
import csv
import logging
import random
import time
from typing import List, Set
from urllib.parse import urlparse, urlunparse

from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BASE_URL = "https://www.zillow.com"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def normalize_url(url: str) -> str:
    """Normalize URL by removing query parameters and fragments."""
    parsed = urlparse(url)
    normalized = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        '',  # params
        '',  # query
        ''   # fragment
    ))
    return normalized.rstrip('/')


def human_like_scroll(page: Page, scroll_pause: float = 1.0):
    """Scroll the page in a human-like manner with random pauses."""
    # Get page height
    page_height = page.evaluate("document.body.scrollHeight")
    viewport_height = page.viewport_size['height']
    
    # Scroll in chunks with random pauses
    current_position = 0
    scroll_amount = random.randint(300, 600)  # Random scroll amount
    
    while current_position < page_height:
        # Random pause before scrolling
        time.sleep(random.uniform(0.5, scroll_pause))
        
        # Scroll
        current_position += scroll_amount
        page.evaluate(f"window.scrollTo(0, {current_position})")
        
        # Random pause after scrolling (mimic reading time)
        time.sleep(random.uniform(0.8, 1.5))
        
        # Update page height (in case new content loaded)
        new_height = page.evaluate("document.body.scrollHeight")
        if new_height > page_height:
            page_height = new_height
        
        # Random chance to scroll back up a bit (human behavior)
        if random.random() < 0.1:  # 10% chance
            back_scroll = random.randint(100, 300)
            current_position = max(0, current_position - back_scroll)
            page.evaluate(f"window.scrollTo(0, {current_position})")
            time.sleep(random.uniform(0.3, 0.7))


def filter_for_houses(page: Page):
    """Filter search results to show only houses (single-family rentals)."""
    try:
        logger.info("Filtering for houses (single-family rentals)...")
        
        # Wait for filters to load
        time.sleep(random.uniform(1.0, 2.0))
        
        # Look for filter panel/button that opens filters
        filter_open_selectors = [
            'button[data-test="filter-button"]',
            'button:has-text("Filters")',
            '[data-test="filter-panel-toggle"]',
            'button[aria-label*="Filter"]',
        ]
        
        # Try to open filter panel if it exists
        for selector in filter_open_selectors:
            try:
                filter_button = page.query_selector(selector)
                if filter_button and filter_button.is_visible():
                    logger.info("Opening filter panel...")
                    filter_button.click()
                    time.sleep(random.uniform(1.0, 1.5))
                    break
            except Exception:
                continue
        
        # Now look for the house filter option - try multiple strategies
        house_selectors = [
            # Checkbox/button with "House" text
            'input[type="checkbox"][value*="house" i]',
            'input[type="checkbox"][value*="1"]',  # Sometimes house is value 1
            'label:has-text("House") input[type="checkbox"]',
            'button[aria-label*="House" i]',
            'button:has-text("House")',
            '[data-test*="house" i]',
            '[data-test*="property-type"] button:has-text("House")',
            '[data-test*="property-type"] input[value*="house" i]',
            # Look for property type section
            'text=House',
            'text=Single Family',
        ]
        
        # Try to find and click house filter
        for selector in house_selectors:
            try:
                # Try query_selector first
                house_element = page.query_selector(selector)
                
                if not house_element:
                    # Try to find by text content - but only in filter areas
                    # Look for filter sections/panels first
                    filter_sections = page.query_selector_all('[class*="filter"], [data-test*="filter"], [id*="filter"]')
                    
                    for section in filter_sections:
                        try:
                            # Look for "House" text within filter sections
                            house_options = section.query_selector_all('button, input, label, a')
                            for elem in house_options:
                                try:
                                    text = elem.inner_text().lower().strip()
                                    # Must be exactly "house" or contain "house" with property type context
                                    if (text == 'house' or 
                                        (text.startswith('house') and len(text) < 20) or
                                        'single family' in text):
                                        tag = elem.evaluate('el => el.tagName.toLowerCase()')
                                        if tag in ['button', 'input', 'label']:
                                            # Make sure it's in a filter context
                                            parent_text = elem.evaluate('el => el.closest("div, section, form")?.textContent?.toLowerCase() || ""')
                                            if 'property type' in parent_text or 'filter' in parent_text or 'apartment' in parent_text or 'condo' in parent_text:
                                                house_element = elem
                                                logger.info(f"Found house filter in filter section: {text}")
                                                break
                                except Exception:
                                    continue
                            if house_element:
                                break
                        except Exception:
                            continue
                
                if house_element:
                    try:
                        # Check if it's already selected
                        if house_element.evaluate('el => el.tagName.toLowerCase()') == 'input':
                            is_checked = house_element.is_checked()
                            if is_checked:
                                logger.info("House filter already selected")
                                return True
                            else:
                                logger.info(f"Clicking house filter checkbox: {selector}")
                                house_element.check()
                                time.sleep(random.uniform(1.5, 2.5))
                                return True
                        else:
                            # It's a button or other element
                            is_selected = (house_element.get_attribute('aria-pressed') == 'true' or
                                         'selected' in (house_element.get_attribute('class') or '').lower() or
                                         'active' in (house_element.get_attribute('class') or '').lower())
                            
                            if not is_selected:
                                logger.info(f"Clicking house filter button: {selector}")
                                house_element.click()
                                time.sleep(random.uniform(1.5, 2.5))
                                
                                # Wait for results to update
                                page.wait_for_timeout(2000)
                                return True
                            else:
                                logger.info("House filter already selected")
                                return True
                    except Exception as e:
                        logger.debug(f"Error clicking {selector}: {e}")
                        continue
            except Exception as e:
                logger.debug(f"Selector {selector} not found: {e}")
                continue
        
        # Alternative: Try URL parameter approach
        current_url = page.url
        if 'propertyType' not in current_url:
            # Try different URL parameter formats
            separator = '&' if '?' in current_url else '?'
            test_urls = [
                f"{current_url}{separator}propertyType=house",
                f"{current_url}{separator}propertyType=1",
                f"{current_url}{separator}propertytype=house",
            ]
            
            for test_url in test_urls:
                try:
                    logger.info(f"Trying filtered URL: {test_url}")
                    page.goto(test_url, wait_until='domcontentloaded', timeout=30000)
                    time.sleep(random.uniform(2.0, 3.0))
                    
                    # Check if we got results
                    cards = page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]')
                    if cards:
                        logger.info(f"Filtered URL worked, found {len(cards)} cards")
                        return True
                except Exception:
                    continue
        
        logger.warning("Could not find or apply house filter, continuing with all property types...")
        return False
        
    except Exception as e:
        logger.warning(f"Error filtering for houses: {e}")
        return False


def click_property_card_and_collect_url(context, card, seen_urls: Set[str], page: Page) -> str:
    """
    Click a property card to open it in a NEW TAB, then collect the URL from the address bar.
    This mimics human behavior of Ctrl+Click or middle-click to open in new tab.
    Returns the URL if successful, None otherwise.
    """
    try:
        # Find the clickable link within the card - try multiple selectors
        link = None
        link_selectors = [
            'a[href*="homedetails"]',  # Try homedetails first (more common)
            'a[href*="/b/"]',
            'a[data-test*="property-card-link"]',
            'a',
        ]
        
        for selector in link_selectors:
            link = card.query_selector(selector)
            if link:
                href = link.get_attribute('href')
                if href and ('/b/' in href or 'homedetails' in href) and '/browse/' not in href and 'zpid' in href:
                    break
                else:
                    link = None
        
        if not link:
            logger.warning("    ❌ No valid link found in card")
            return None
        
        # Get the href to check if it's a valid detail page
        href = link.get_attribute('href')
        logger.info(f"    Found href: {href}")
        
        if not href:
            logger.warning("    ❌ No href attribute")
            return None
            
        if '/browse/' in href:
            logger.warning("    ❌ Skipping browse URL")
            return None
        
        # Make href absolute
        if href.startswith('/'):
            full_url = f"{BASE_URL}{href}"
        elif href.startswith('http'):
            full_url = href
        else:
            return None
        
        # Create a new page (new tab) BEFORE clicking
        new_page = context.new_page()
        
        try:
            # Use Ctrl+Click (or Cmd+Click on Mac) to open in new tab - more human-like
            # This should trigger the browser to open the link in the new page
            logger.info(f"    Opening in new tab: {full_url}")
            
            # Method 1: Navigate the new page directly (simulates opening in new tab)
            # This is more reliable than trying to intercept the click
            new_page.goto(full_url, wait_until='domcontentloaded', timeout=30000)
            
            # Wait for page to fully load
            time.sleep(random.uniform(2.0, 3.5))
            
            # Get the URL from the address bar of the new tab
            url = new_page.url
            logger.info(f"    → URL from address bar: {url}")
            
            # Normalize it
            normalized = normalize_url(url)
            
            # Check if page was blocked
            try:
                page_title = new_page.title()
                logger.info(f"    → Page title: {page_title}")
                
                if 'denied' in page_title.lower() or 'blocked' in page_title.lower() or 'bot' in page_title.lower():
                    logger.warning(f"  ⚠️  Page BLOCKED: {normalized}")
                    return None
            except Exception:
                pass
            
            # Check if it's a valid detail page URL
            # Zillow uses both /homedetails/ and /b/ formats for property pages
            is_valid_detail_page = (
                normalized.startswith(BASE_URL) and
                ('/homedetails/' in normalized or '/b/' in normalized) and
                '/browse/' not in normalized and
                'zpid' in normalized  # Zillow property IDs
            )
            
            if is_valid_detail_page:
                if normalized not in seen_urls:
                    logger.info(f"  ✅ SUCCESS - Collected: {normalized}")
                    return normalized
                else:
                    logger.info(f"  ⏭️  Already seen, skipping: {normalized}")
                    return None
            else:
                logger.warning(f"  ❌ Invalid URL format: {normalized}")
                logger.warning(f"     - Starts with BASE_URL: {normalized.startswith(BASE_URL)}")
                logger.warning(f"     - Has /homedetails/ or /b/: {'/homedetails/' in normalized or '/b/' in normalized}")
                logger.warning(f"     - Not /browse/: {'/browse/' not in normalized}")
                logger.warning(f"     - Has zpid: {'zpid' in normalized}")
                return None
                
        finally:
            # Close the new tab (like a human would)
            new_page.close()
            # Small delay between tabs
            time.sleep(random.uniform(0.8, 1.5))
            
    except Exception as e:
        logger.warning(f"Error clicking card: {e}")
        return None


def save_url_to_csv(url: str, output_csv: str):
    """Append a single URL to the CSV file immediately."""
    try:
        import os
        file_exists = os.path.exists(output_csv)
        
        with open(output_csv, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            # Write header if file is new
            if not file_exists:
                writer.writerow(['url'])
            # Write the URL
            writer.writerow([url])
    except Exception as e:
        logger.warning(f"Error saving URL to CSV: {e}")


def collect_urls_from_page(context, page: Page, seen_urls: Set[str], output_csv: str) -> List[str]:
    """
    Collect URLs by slowly scrolling and clicking each property card one at a time.
    Saves each URL to CSV immediately after collection.
    Returns list of new URLs collected.
    """
    collected_urls = []
    processed_card_hrefs = set()  # Track which cards we've already processed
    
    try:
        # Start at the top
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(random.uniform(1.0, 1.5))
        
        logger.info("Starting slow scroll to collect property cards one at a time...")
        
        # Get initial page dimensions
        viewport_height = page.viewport_size['height']
        page_height = page.evaluate("document.body.scrollHeight")
        current_scroll = 0
        scroll_step = 300  # Scroll in small increments
        max_scroll = page_height - viewport_height + 100
        
        card_count = 0
        
        while current_scroll < max_scroll:
            # Scroll down slowly
            current_scroll += scroll_step
            if current_scroll > max_scroll:
                current_scroll = max_scroll
            
            page.evaluate(f"window.scrollTo(0, {current_scroll})")
            time.sleep(random.uniform(1.5, 2.5))  # Slow, human-like scrolling
            
            # Check if page height increased (new content loaded)
            new_page_height = page.evaluate("document.body.scrollHeight")
            if new_page_height > page_height:
                logger.info(f"  Page height increased: {page_height} -> {new_page_height}, continuing...")
                page_height = new_page_height
                max_scroll = page_height - viewport_height + 100
            
            # Find all visible property cards on the page
            property_cards = page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]')
            
            # Process each card that we haven't seen yet
            for card in property_cards:
                try:
                    # Get the href from the card to check if we've processed it
                    link = card.query_selector('a[href*="homedetails"], a[href*="/b/"]')
                    if not link:
                        continue
                    
                    href = link.get_attribute('href')
                    if not href:
                        continue
                    
                    # Skip if we've already processed this card
                    if href in processed_card_hrefs:
                        continue
                    
                    # Check if card is visible in viewport
                    box = card.bounding_box()
                    if not box:
                        continue
                    
                    # Only process cards that are in or near the viewport
                    card_bottom = box['y'] + box['height']
                    card_top = box['y']
                    viewport_bottom = current_scroll + viewport_height
                    viewport_top = current_scroll
                    
                    # Process if card is visible (with some buffer)
                    if card_bottom >= viewport_top - 200 and card_top <= viewport_bottom + 200:
                        # Mark as processed
                        processed_card_hrefs.add(href)
                        card_count += 1
                        
                        # Scroll card into view more precisely
                        page.evaluate(f"window.scrollTo(0, {card_top - 150})")
                        time.sleep(random.uniform(0.5, 1.0))
                        
                        # Small random mouse movement (human behavior)
                        try:
                            x = box['x'] + box['width'] / 2 + random.randint(-10, 10)
                            y = box['y'] + box['height'] / 2 + random.randint(-10, 10)
                            page.mouse.move(x, y)
                            time.sleep(random.uniform(0.3, 0.6))
                        except Exception:
                            pass
                        
                        logger.info(f"Clicking card {card_count}...")
                        
                        # Click and collect URL
                        url = click_property_card_and_collect_url(context, card, seen_urls, page)
                        
                        if url:
                            collected_urls.append(url)
                            seen_urls.add(url)
                            
                            # Save to CSV immediately
                            save_url_to_csv(url, output_csv)
                            logger.info(f"  ✅ Collected and saved: {url}")
                        else:
                            logger.warning(f"  ❌ Failed to collect URL from card {card_count}")
                        
                        # Random pause between clicks (human reading time)
                        time.sleep(random.uniform(1.5, 2.5))
                        
                except Exception as e:
                    logger.debug(f"Error processing card: {e}")
                    continue
            
            # Occasionally scroll back up a bit (human behavior)
            if random.random() < 0.1:  # 10% chance
                page.evaluate(f"window.scrollTo(0, {current_scroll - 500})")
                time.sleep(random.uniform(0.8, 1.2))
                page.evaluate(f"window.scrollTo(0, {current_scroll})")
                time.sleep(random.uniform(0.5, 1.0))
        
        # Final scroll to bottom to catch any remaining cards
        logger.info("Final scroll to bottom to catch any remaining cards...")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(random.uniform(2.0, 3.0))
        
        # Check for any remaining cards
        final_cards = page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]')
        for card in final_cards:
            try:
                link = card.query_selector('a[href*="homedetails"], a[href*="/b/"]')
                if not link:
                    continue
                
                href = link.get_attribute('href')
                if not href or href in processed_card_hrefs:
                    continue
                
                processed_card_hrefs.add(href)
                card_count += 1
                
                box = card.bounding_box()
                if box:
                    page.evaluate(f"window.scrollTo(0, {box['y'] - 150})")
                    time.sleep(random.uniform(0.5, 1.0))
                
                logger.info(f"Clicking final card {card_count}...")
                url = click_property_card_and_collect_url(context, card, seen_urls, page)
                
                if url:
                    collected_urls.append(url)
                    seen_urls.add(url)
                    save_url_to_csv(url, output_csv)
                    logger.info(f"  ✅ Collected and saved: {url}")
                
                time.sleep(random.uniform(1.5, 2.5))
                
            except Exception as e:
                logger.debug(f"Error processing final card: {e}")
                continue
        
        logger.info(f"✅ Finished processing page. Collected {len(collected_urls)} new URLs from {card_count} cards.")
        
    except Exception as e:
        logger.error(f"Error collecting URLs from page: {e}")
    
    return collected_urls


def get_next_page_url(base_url: str, page_num: int) -> str:
    """
    Construct the next page URL using the simple pattern:
    - Page 1: base_url (e.g., https://www.zillow.com/atlanta-ga/rent-houses/)
    - Page 2: base_url/2_p/
    - Page 3: base_url/3_p/
    """
    if page_num == 1:
        return base_url
    else:
        # Remove trailing slash if present, then append /{page_num}_p/
        base = base_url.rstrip('/')
        return f"{base}/{page_num}_p/"


def load_existing_urls(csv_file: str) -> Set[str]:
    """Load existing URLs from CSV file to avoid duplicates."""
    seen_urls = set()
    try:
        import os
        if os.path.exists(csv_file):
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader, None)  # Skip header
                for row in reader:
                    if row and row[0].strip():
                        normalized = normalize_url(row[0].strip())
                        seen_urls.add(normalized)
            logger.info(f"Loaded {len(seen_urls)} existing URLs from {csv_file}")
    except Exception as e:
        logger.warning(f"Error loading existing URLs: {e}")
    return seen_urls


def collect_urls(city: str, state: str, delay: float, output_csv: str, headless: bool = False, max_pages: int = None):
    """Collect property URLs from Zillow search pages by clicking through cards.
    Runs indefinitely until no more pages are available (no property cards found on consecutive pages).
    """
    city_normalized = city.lower().replace(' ', '-').replace(',', '').replace("'", "")
    state_normalized = state.lower()
    
    all_urls = []
    seen_urls = load_existing_urls(output_csv)  # Load existing URLs
    # Use the rent-houses URL format (page 1: /rent-houses/, page 2: /rent-houses/2_p/, etc.)
    search_url = f"{BASE_URL}/{city_normalized}-{state_normalized}/rent-houses/"
    
    logger.info("=" * 80)
    logger.info("ZILLOW URL COLLECTOR (Human-like)")
    logger.info("=" * 80)
    logger.info(f"City: {city}, State: {state}")
    logger.info(f"Max pages: {'Unlimited (runs until no more pages)' if max_pages is None else max_pages}")
    logger.info(f"Output: {output_csv}")
    logger.info(f"Headless: {headless}")
    logger.info("=" * 80)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                '--disable-blink-features=AutomationControlled',
            ]
        )
        
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={'width': 1920, 'height': 1080},
            locale='en-US',
            timezone_id='America/New_York',
        )
        
        # Add stealth script
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        page = context.new_page()
        
        try:
            # Store base URL for pagination (page 1 doesn't have /1_p, it's just the base URL)
            base_url = search_url.rstrip('/')
            
            page_num = 0
            consecutive_empty_pages = 0
            max_empty_pages = 2  # Stop after 2 consecutive pages with no cards
            
            while True:
                # Optional safety limit
                if max_pages is not None and page_num >= max_pages:
                    logger.info(f"Reached max_pages limit ({max_pages}), stopping...")
                    break
                page_num += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"PAGE {page_num} (running indefinitely until no more pages)")
                logger.info(f"{'='*80}")
                
                # Construct URL for this page
                page_url = get_next_page_url(base_url, page_num)
                logger.info(f"Navigating to: {page_url}")
                
                # Navigate to the page
                try:
                    page.goto(page_url, wait_until='domcontentloaded', timeout=60000)
                    time.sleep(random.uniform(2.0, 3.5))
                except Exception as e:
                    logger.warning(f"Error navigating to page {page_num}: {e}")
                    consecutive_empty_pages += 1
                    if consecutive_empty_pages >= max_empty_pages:
                        logger.info("❌ Too many navigation errors, stopping...")
                        break
                    continue
                
                # Wait for page to load and check if property cards exist
                try:
                    page.wait_for_selector('[data-test="property-card"], [data-testid="property-card"]', timeout=15000)
                    logger.info("✅ Property cards loaded")
                    consecutive_empty_pages = 0  # Reset counter if we found cards
                except Exception:
                    logger.warning("Property cards not found on this page")
                    consecutive_empty_pages += 1
                    if consecutive_empty_pages >= max_empty_pages:
                        logger.info("❌ No property cards found on consecutive pages, stopping...")
                        break
                    # Still try to collect in case cards load slowly
                
                logger.info(f"Current URL: {page.url}")
                
                # Collect URLs by clicking through cards (saves to CSV incrementally)
                urls = collect_urls_from_page(context, page, seen_urls, output_csv)
                
                if urls:
                    all_urls.extend(urls)
                    logger.info(f"\n✅ Collected {len(urls)} new URLs from this page")
                    logger.info(f"📊 Total unique URLs so far: {len(all_urls)}")
                    consecutive_empty_pages = 0  # Reset counter if we collected URLs
                else:
                    logger.warning(f"No new URLs found on page {page_num}")
                    consecutive_empty_pages += 1
                    if consecutive_empty_pages >= max_empty_pages:
                        logger.info("❌ No URLs collected from consecutive pages, stopping...")
                        break
                
                # Small delay before next page
                time.sleep(random.uniform(delay, delay + 1.0))
            
            logger.info(f"\n{'='*80}")
            logger.info(f"COLLECTION COMPLETE")
            logger.info(f"{'='*80}")
            logger.info(f"Total unique URLs collected: {len(all_urls)}")
            logger.info(f"✅ All URLs saved incrementally to: {output_csv}")
            
            if all_urls:
                logger.info(f"\nSample URLs (first 5):")
                for i, url in enumerate(all_urls[:5], 1):
                    logger.info(f"  {i}. {url}")
            
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description='Collect Zillow property URLs (runs indefinitely until no more pages)')
    parser.add_argument('--city', type=str, required=True, help='City name (e.g., "Atlanta")')
    parser.add_argument('--state', type=str, required=True, help='State abbreviation (e.g., "GA")')
    parser.add_argument('--max_pages', type=int, default=None, help='Optional: Maximum pages to scrape (default: unlimited, runs until no more pages)')
    parser.add_argument('--delay', type=float, default=3.0, help='Delay between pages in seconds (default: 3.0)')
    parser.add_argument('--output', type=str, default='data/zillow_urls.csv', help='Output CSV file (default: data/zillow_urls.csv)')
    parser.add_argument('--headless', action='store_true', help='Run browser in headless mode')
    
    args = parser.parse_args()
    
    collect_urls(
        city=args.city,
        state=args.state,
        delay=args.delay,
        output_csv=args.output,
        headless=args.headless,
        max_pages=args.max_pages
    )


if __name__ == "__main__":
    main()

