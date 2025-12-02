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
# Use a more recent Chrome user agent
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


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


def detect_and_handle_challenge(page: Page, headless: bool) -> bool:
    """
    Detect and handle Zillow's press-and-hold anti-bot challenge.
    Returns True if challenge was detected and handled, False otherwise.
    """
    try:
        # Wait a bit for page to fully load
        time.sleep(2)
        
        # Common selectors for Zillow's press-and-hold challenge
        challenge_selectors = [
            'button[data-testid*="challenge"]',
            'button:has-text("Press & Hold")',
            'button:has-text("Press and Hold")',
            '[class*="challenge"] button',
            '[id*="challenge"] button',
            'button[aria-label*="Press"]',
            'button[aria-label*="Hold"]',
            'button[class*="Press"]',
            'button[class*="Hold"]',
        ]
        
        # Check for challenge button
        challenge_button = None
        for selector in challenge_selectors:
            try:
                elements = page.query_selector_all(selector)
                for elem in elements:
                    if elem.is_visible():
                        challenge_button = elem
                        logger.warning("⚠️  Press-and-hold challenge detected!")
                        break
                if challenge_button:
                    break
            except Exception:
                continue
        
        # Also check page content for challenge indicators
        if not challenge_button:
            try:
                page_text = page.content().lower()
                page_title = page.title().lower()
                body_text = ""
                try:
                    body_text = page.inner_text('body').lower()
                except:
                    pass
                
                # Check for challenge keywords
                challenge_keywords = ['press', 'hold', 'verify', 'human', 'bot']
                has_challenge_text = any(kw in page_text or kw in page_title or kw in body_text for kw in challenge_keywords)
                
                if has_challenge_text:
                    # Look for any button on the page
                    all_buttons = page.query_selector_all('button')
                    for btn in all_buttons:
                        try:
                            if btn.is_visible():
                                btn_text = btn.inner_text().lower()
                                btn_aria = (btn.get_attribute('aria-label') or '').lower()
                                # Check if button text or aria-label contains challenge keywords
                                if any(kw in btn_text or kw in btn_aria for kw in ['press', 'hold', 'verify']):
                                    challenge_button = btn
                                    logger.warning("⚠️  Press-and-hold challenge detected via text!")
                                    break
                        except:
                            continue
            except Exception as e:
                logger.debug(f"Error checking page text: {e}")
        
        # Also check if no property cards are visible but page loaded (might be challenge blocking)
        if not challenge_button:
            try:
                cards = page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]')
                if not cards or len(cards) == 0:
                    # Check if page seems empty or blocked
                    body_text = page.inner_text('body')
                    if body_text and len(body_text.strip()) < 500:  # Very short content might indicate challenge
                        logger.warning("⚠️  Page appears to have very little content - might be blocked by challenge")
                        logger.warning("   Please check the browser window for any challenges")
            except:
                pass
        
        if challenge_button:
            if headless:
                logger.error("❌ Challenge detected but running in headless mode!")
                logger.error("   Please run WITHOUT --headless flag to manually solve the challenge.")
                logger.error("   Waiting 30 seconds...")
                time.sleep(30)
                return False
            else:
                logger.warning("=" * 80)
                logger.warning("⚠️  PRESS-AND-HOLD CHALLENGE DETECTED!")
                logger.warning("=" * 80)
                logger.warning("   Please manually solve the challenge in the browser window.")
                logger.warning("   Look for a button that says 'Press & Hold' or similar.")
                logger.warning("   Hold down the button until it completes.")
                logger.warning("   Waiting up to 120 seconds for you to complete it...")
                logger.warning("=" * 80)
                
                # Wait for challenge to be solved (check if button disappears or page changes)
                start_time = time.time()
                timeout = 120
                last_check = start_time
                
                while time.time() - start_time < timeout:
                    try:
                        elapsed = int(time.time() - start_time)
                        if elapsed % 10 == 0 and elapsed != int(last_check - start_time):
                            logger.info(f"   Still waiting... ({elapsed}/{timeout} seconds)")
                        last_check = time.time()
                        
                        # Check if challenge button is still visible
                        if challenge_button:
                            try:
                                if not challenge_button.is_visible():
                                    logger.info("✅ Challenge button disappeared - appears to be solved!")
                                    time.sleep(3)  # Wait a bit more for page to update
                                    break
                            except:
                                # Button might have been removed from DOM
                                logger.info("✅ Challenge button removed from DOM - appears to be solved!")
                                time.sleep(3)
                                break
                        
                        # Check if property cards have appeared (indicates challenge passed)
                        cards = page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]')
                        if cards and len(cards) > 0:
                            logger.info("✅ Challenge solved! Property cards are visible.")
                            return True
                        
                        # Check if page content changed significantly
                        try:
                            current_body = page.inner_text('body')
                            if current_body and len(current_body.strip()) > 1000:  # More content = likely passed
                                cards = page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]')
                                if cards:
                                    logger.info("✅ Challenge appears solved - page has more content now.")
                                    return True
                        except:
                            pass
                        
                        time.sleep(2)
                    except Exception as e:
                        logger.debug(f"Error checking challenge status: {e}")
                        time.sleep(2)
                
                # Final check
                cards = page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]')
                if cards and len(cards) > 0:
                    logger.info("✅ Challenge solved! Property cards are visible.")
                    return True
                else:
                    logger.warning("⚠️  Timeout waiting for challenge. Please check browser window manually.")
                    logger.warning("   If challenge is still visible, solve it and the script will continue.")
                    logger.warning("   Waiting additional 30 seconds...")
                    time.sleep(30)
                    return True
        
        return False
    except Exception as e:
        logger.debug(f"Error detecting challenge: {e}")
        return False


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
            
            # Log page title for debugging (but don't block based on it)
            try:
                page_title = new_page.title()
                logger.info(f"    → Page title: {page_title}")
            except Exception:
                pass
            
            # Collect URL regardless of page state (challenge, blocked, etc.)
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
                # Even if it doesn't match the exact format, if it's a zillow URL with zpid, collect it
                if normalized.startswith(BASE_URL) and 'zpid' in normalized:
                    logger.info(f"  ⚠️  Collecting URL that doesn't match standard format: {normalized}")
                    if normalized not in seen_urls:
                        logger.info(f"  ✅ Collected non-standard URL: {normalized}")
                        return normalized
                    else:
                        logger.info(f"  ⏭️  Already seen, skipping: {normalized}")
                        return None
                else:
                    logger.warning(f"  ❌ Invalid URL format: {normalized}")
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


def collect_urls_from_all_pages(context, seen_urls: Set[str], output_csv: str) -> List[str]:
    """
    Check all pages in the context and collect any property URLs from them.
    This includes pages that might have opened due to challenges or redirects.
    Returns list of new URLs collected.
    """
    collected_urls = []
    try:
        # Get all pages in the context
        all_pages = context.pages
        
        for page in all_pages:
            try:
                # Get the URL from this page
                url = page.url
                if not url:
                    continue
                
                # Normalize it
                normalized = normalize_url(url)
                
                # Check if it's a valid property URL
                is_valid_detail_page = (
                    normalized.startswith(BASE_URL) and
                    ('/homedetails/' in normalized or '/b/' in normalized) and
                    '/browse/' not in normalized and
                    'zpid' in normalized
                )
                
                # Also collect if it's any zillow URL with zpid (even if format is non-standard)
                if normalized.startswith(BASE_URL) and 'zpid' in normalized:
                    if normalized not in seen_urls:
                        logger.info(f"  🔍 Found URL from open page: {normalized}")
                        collected_urls.append(normalized)
                        seen_urls.add(normalized)
                        save_url_to_csv(normalized, output_csv)
                        logger.info(f"  ✅ Collected and saved: {normalized}")
            except Exception as e:
                logger.debug(f"Error checking page for URLs: {e}")
                continue
                
    except Exception as e:
        logger.debug(f"Error collecting URLs from all pages: {e}")
    
    return collected_urls


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
        
        logger.info("Step 1: Scrolling through entire page to load all property cards...")
        
        # First, scroll through the entire page to ensure all cards are loaded
        viewport_height = page.viewport_size['height']
        page_height = page.evaluate("document.body.scrollHeight")
        current_scroll = 0
        scroll_step = 300  # Scroll in small increments
        max_scroll = page_height - viewport_height + 100
        
        # Scroll through entire page first
        while current_scroll < max_scroll:
            # Scroll down slowly
            current_scroll += scroll_step
            if current_scroll > max_scroll:
                current_scroll = max_scroll
            
            page.evaluate(f"window.scrollTo(0, {current_scroll})")
            time.sleep(random.uniform(0.8, 1.2))  # Faster scroll for initial load
            
            # Check if page height increased (new content loaded)
            new_page_height = page.evaluate("document.body.scrollHeight")
            if new_page_height > page_height:
                logger.info(f"  Page height increased: {page_height} -> {new_page_height}, continuing scroll...")
                page_height = new_page_height
                max_scroll = page_height - viewport_height + 100
        
        # Final scroll to bottom to ensure everything is loaded
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(random.uniform(2.0, 3.0))
        
        # Get final page height after all scrolling
        final_page_height = page.evaluate("document.body.scrollHeight")
        logger.info(f"Finished scrolling. Final page height: {final_page_height}")
        
        # Now collect all unique card hrefs from the entire page
        logger.info("Step 2: Collecting all property cards from the page...")
        all_cards = page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]')
        logger.info(f"Found {len(all_cards)} total property cards on this page")
        
        # Build a list of cards with their hrefs and positions
        cards_to_process = []
        for card in all_cards:
            try:
                link = card.query_selector('a[href*="homedetails"], a[href*="/b/"]')
                if not link:
                    continue
                
                href = link.get_attribute('href')
                if not href:
                    continue
                
                # Skip if we've already processed this card
                if href in processed_card_hrefs:
                    continue
                
                # Get card position
                box = card.bounding_box()
                if not box:
                    continue
                
                cards_to_process.append((card, href, box))
            except Exception as e:
                logger.debug(f"Error extracting card info: {e}")
                continue
        
        logger.info(f"Step 3: Processing {len(cards_to_process)} unique cards...")
        
        # Now process each card one by one
        card_count = 0
        for card, href, box in cards_to_process:
            try:
                # Mark as processed immediately to avoid duplicates
                processed_card_hrefs.add(href)
                card_count += 1
                
                # Scroll card into view
                card_top = box['y']
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
                
                logger.info(f"Clicking card {card_count}/{len(cards_to_process)}...")
                
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


def human_like_browsing_start(page: Page):
    """
    Start browsing session in a human-like way by visiting Google first,
    then navigating to Zillow. This helps avoid bot detection.
    """
    try:
        logger.info("Starting human-like browsing pattern...")
        logger.info("Step 1: Visiting Google...")
        
        # Visit Google first
        page.goto("https://www.google.com", wait_until='domcontentloaded', timeout=30000)
        time.sleep(random.uniform(2.0, 3.5))
        
        # Simulate human behavior: move mouse, scroll a bit
        try:
            # Random mouse movements
            for _ in range(random.randint(2, 4)):
                page.mouse.move(random.randint(100, 800), random.randint(100, 600))
                time.sleep(random.uniform(0.3, 0.7))
            
            # Scroll down a bit
            page.evaluate("window.scrollTo(0, 300)")
            time.sleep(random.uniform(0.8, 1.5))
            
            # Scroll back up
            page.evaluate("window.scrollTo(0, 100)")
            time.sleep(random.uniform(0.5, 1.0))
        except Exception:
            pass
        
        logger.info("Step 2: Waiting a moment (human reading time)...")
        time.sleep(random.uniform(3.0, 5.0))
        
        # Sometimes visit another page to make it more realistic
        if random.random() < 0.3:  # 30% chance
            try:
                logger.info("Step 2.5: Visiting another page (more realistic browsing)...")
                page.goto("https://www.google.com/search?q=real+estate", wait_until='domcontentloaded', timeout=30000)
                time.sleep(random.uniform(2.0, 3.5))
                
                # More mouse movements
                for _ in range(random.randint(1, 3)):
                    page.mouse.move(random.randint(200, 700), random.randint(200, 500))
                    time.sleep(random.uniform(0.3, 0.6))
            except Exception:
                pass
        
        logger.info("Step 3: Now navigating to Zillow...")
        time.sleep(random.uniform(1.0, 2.0))
        return True
    except Exception as e:
        logger.warning(f"Error in human-like browsing start: {e}")
        return False


def collect_urls(city: str, state: str, delay: float, output_csv: str, headless: bool = False, max_pages: int = None, start_page: int = 1):
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
    logger.info(f"Start page: {start_page}")
    logger.info(f"Max pages: {'Unlimited (runs until no more pages)' if max_pages is None else max_pages}")
    logger.info(f"Output: {output_csv}")
    logger.info(f"Headless: {headless}")
    logger.info("=" * 80)
    
    with sync_playwright() as p:
        # Use installed Chrome instead of Chromium for better anti-bot evasion
        browser = p.chromium.launch(
            headless=headless,
            channel="chrome",  # Use installed Chrome browser instead of bundled Chromium
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
            ]
        )
        
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={'width': 1920, 'height': 1080},
            locale='en-US',
            timezone_id='America/New_York',
            permissions=['geolocation'],
            geolocation={'latitude': 33.7490, 'longitude': -84.3880},  # Atlanta coordinates
            color_scheme='light',
            # Add extra HTTP headers to look more realistic
            extra_http_headers={
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0',
            }
        )
        
        # Add comprehensive stealth scripts to avoid detection
        context.add_init_script("""
            // Remove webdriver property
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            
            // Override plugins to look like real Chrome
            Object.defineProperty(navigator, 'plugins', {
                get: () => {
                    const plugins = [];
                    for (let i = 0; i < 5; i++) {
                        plugins.push({
                            0: {type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format'},
                            description: 'Portable Document Format',
                            filename: 'internal-pdf-viewer',
                            length: 1,
                            name: 'Chrome PDF Plugin'
                        });
                    }
                    return plugins;
                }
            });
            
            // Override languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
            
            // Chrome runtime (make it look like real Chrome)
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };
            
            // Override permissions API
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
            
            // Override getBattery to return realistic values
            if (navigator.getBattery) {
                navigator.getBattery = () => Promise.resolve({
                    charging: true,
                    chargingTime: 0,
                    dischargingTime: Infinity,
                    level: 1
                });
            }
            
            // Override platform
            Object.defineProperty(navigator, 'platform', {
                get: () => 'Win32'
            });
            
            // Add missing properties that real Chrome has
            Object.defineProperty(navigator, 'hardwareConcurrency', {
                get: () => 8
            });
            
            Object.defineProperty(navigator, 'deviceMemory', {
                get: () => 8
            });
        """)
        
        page = context.new_page()
        
        try:
            # Start with human-like browsing pattern (visit Google first)
            human_like_browsing_start(page)
            
            # Store base URL for pagination (page 1 doesn't have /1_p, it's just the base URL)
            base_url = search_url.rstrip('/')
            
            page_num = start_page - 1  # Start from specified page (will be incremented before first use)
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
                    # Add human-like mouse movement before navigation
                    try:
                        page.mouse.move(random.randint(50, 200), random.randint(50, 200))
                        time.sleep(random.uniform(0.3, 0.7))
                    except Exception:
                        pass
                    
                    # Set referrer to Google for page 1 only (looks like user came from search)
                    referrer = "https://www.google.com/" if page_num == 1 else None
                    
                    page.goto(
                        page_url, 
                        wait_until='domcontentloaded', 
                        timeout=60000,
                        referer=referrer
                    )
                    
                    # Human-like behavior after page load
                    time.sleep(random.uniform(2.0, 3.5))
                    
                    # Random mouse movements to simulate human interaction
                    try:
                        for _ in range(random.randint(1, 3)):
                            x = random.randint(100, 1800)
                            y = random.randint(100, 900)
                            page.mouse.move(x, y)
                            time.sleep(random.uniform(0.2, 0.5))
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning(f"Error navigating to page {page_num}: {e}")
                    consecutive_empty_pages += 1
                    if consecutive_empty_pages >= max_empty_pages:
                        logger.info("❌ Too many navigation errors, stopping...")
                        break
                    continue
                
                # Check for and handle anti-bot challenge (but continue regardless)
                challenge_handled = detect_and_handle_challenge(page, headless)
                if challenge_handled:
                    time.sleep(random.uniform(2.0, 3.0))  # Wait after challenge
                
                # Collect URLs from any pages that might have opened (challenge pages, redirects, etc.)
                try:
                    urls_from_pages = collect_urls_from_all_pages(context, seen_urls, output_csv)
                    if urls_from_pages:
                        all_urls.extend(urls_from_pages)
                        logger.info(f"  📋 Collected {len(urls_from_pages)} URLs from open pages")
                except Exception as e:
                    logger.debug(f"Error collecting URLs from open pages: {e}")
                
                # Additional human-like behaviors: random scrolling and mouse movements
                try:
                    # Random scroll to simulate reading
                    scroll_amount = random.randint(200, 600)
                    page.evaluate(f"window.scrollTo(0, {scroll_amount})")
                    time.sleep(random.uniform(0.5, 1.0))
                    
                    # Scroll back up a bit (human behavior)
                    page.evaluate(f"window.scrollTo(0, {scroll_amount - 100})")
                    time.sleep(random.uniform(0.3, 0.7))
                    
                    # More mouse movements
                    for _ in range(random.randint(2, 4)):
                        x = random.randint(200, 1700)
                        y = random.randint(200, 800)
                        page.mouse.move(x, y)
                        time.sleep(random.uniform(0.2, 0.4))
                except Exception:
                    pass
                
                # Additional check: if no cards found, wait longer and check again for challenge
                try:
                    cards_check = page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]')
                    if not cards_check or len(cards_check) == 0:
                        if not headless:
                            logger.warning("⚠️  No property cards found - this might indicate a challenge is blocking the page.")
                            logger.warning("   Please check the browser window and solve any challenges you see.")
                            logger.warning("   Waiting 20 seconds for you to interact with the page...")
                            time.sleep(20)
                            # Check challenge again after wait
                            detect_and_handle_challenge(page, headless)
                            time.sleep(random.uniform(2.0, 3.0))
                except Exception as e:
                    logger.debug(f"Error checking cards: {e}")
                
                # Wait for page to load (with challenge check) - longer wait for first page
                wait_time = random.uniform(3.0, 5.0) if page_num == start_page else random.uniform(1.5, 2.5)
                time.sleep(wait_time)
                
                # Wait for page to load and check if property cards exist
                # But continue regardless - we'll collect whatever URLs we can find
                cards_found = False
                try:
                    page.wait_for_selector('[data-test="property-card"], [data-testid="property-card"]', timeout=15000)
                    logger.info("✅ Property cards loaded")
                    cards_found = True
                    consecutive_empty_pages = 0  # Reset counter if we found cards
                except Exception:
                    # Check again for challenge - might have appeared after initial load
                    if not challenge_handled:
                        detect_and_handle_challenge(page, headless)
                        time.sleep(random.uniform(2.0, 3.0))
                        # Try waiting for cards again
                        try:
                            page.wait_for_selector('[data-test="property-card"], [data-testid="property-card"]', timeout=10000)
                            logger.info("✅ Property cards loaded after challenge")
                            cards_found = True
                            consecutive_empty_pages = 0
                        except Exception:
                            logger.warning("Property cards not found on this page - will still try to collect URLs")
                            # Don't increment consecutive_empty_pages yet - we'll check after trying to collect
                    else:
                        logger.warning("Property cards not found on this page - will still try to collect URLs")
                    # Still try to collect in case cards load slowly or page is a property detail page
                
                # Check if current page URL is itself a property URL (might have been redirected)
                try:
                    current_url = page.url
                    if current_url:
                        normalized = normalize_url(current_url)
                        if (normalized.startswith(BASE_URL) and 
                            ('/homedetails/' in normalized or '/b/' in normalized) and
                            '/browse/' not in normalized and
                            'zpid' in normalized and
                            normalized not in seen_urls):
                            logger.info(f"  🔍 Current page is a property URL: {normalized}")
                            all_urls.append(normalized)
                            seen_urls.add(normalized)
                            save_url_to_csv(normalized, output_csv)
                            logger.info(f"  ✅ Collected current page URL: {normalized}")
                            consecutive_empty_pages = 0
                except Exception as e:
                    logger.debug(f"Error checking current page URL: {e}")
                
                logger.info(f"Current URL: {page.url}")
                
                # Collect URLs by clicking through cards (saves to CSV incrementally)
                # Continue even if there are challenges - collect whatever we can
                try:
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
                except Exception as e:
                    logger.warning(f"Error collecting URLs from page {page_num}: {e}")
                    # Continue anyway - don't stop on errors
                    # Check for URLs from any open pages
                    try:
                        urls_from_pages = collect_urls_from_all_pages(context, seen_urls, output_csv)
                        if urls_from_pages:
                            all_urls.extend(urls_from_pages)
                            logger.info(f"  📋 Collected {len(urls_from_pages)} URLs from open pages after error")
                            consecutive_empty_pages = 0
                        else:
                            consecutive_empty_pages += 1
                    except:
                        consecutive_empty_pages += 1
                    
                    if consecutive_empty_pages >= max_empty_pages:
                        logger.info("❌ Too many errors, stopping...")
                        break
                
                # Final check for URLs from any pages that might have opened
                try:
                    urls_from_pages = collect_urls_from_all_pages(context, seen_urls, output_csv)
                    if urls_from_pages:
                        all_urls.extend(urls_from_pages)
                        logger.info(f"  📋 Final check: Collected {len(urls_from_pages)} URLs from open pages")
                except Exception as e:
                    logger.debug(f"Error in final URL collection check: {e}")
                
                # Small delay before next page with more human-like behavior
                wait_before_next = random.uniform(delay, delay + 2.0)
                
                # Sometimes add extra delay (human might get distracted)
                if random.random() < 0.2:  # 20% chance
                    extra_delay = random.uniform(3.0, 8.0)
                    logger.info(f"Taking a longer break ({extra_delay:.1f}s) - simulating human behavior...")
                    wait_before_next += extra_delay
                
                # Random mouse movements during wait
                try:
                    for _ in range(random.randint(1, 3)):
                        x = random.randint(100, 1800)
                        y = random.randint(100, 900)
                        page.mouse.move(x, y)
                        time.sleep(random.uniform(0.5, 1.5))
                except Exception:
                    pass
                
                time.sleep(wait_before_next)
            
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
    parser.add_argument('--start_page', type=int, default=1, help='Page number to start from (default: 1)')
    parser.add_argument('--max_pages', type=int, default=None, help='Optional: Maximum pages to scrape (default: unlimited, runs until no more pages)')
    parser.add_argument('--delay', type=float, default=3.0, help='Delay between pages in seconds (default: 3.0)')
    parser.add_argument('--output', type=str, default='data/zillow_urls.csv', help='Output CSV file (default: data/zillow_urls.csv)')
    parser.add_argument('--headless', action='store_true', help='Run browser in headless mode (NOT recommended - you cannot solve challenges in headless mode)')
    
    args = parser.parse_args()
    
    collect_urls(
        city=args.city,
        state=args.state,
        delay=args.delay,
        output_csv=args.output,
        headless=args.headless,
        max_pages=args.max_pages,
        start_page=args.start_page
    )


if __name__ == "__main__":
    main()

