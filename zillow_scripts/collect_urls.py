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
    Collect URLs by clicking through property cards on the current page.
    Saves each URL to CSV immediately after collection.
    Returns list of new URLs collected.
    """
    collected_urls = []
    
    try:
        # Scroll to bottom multiple times to trigger all lazy loading
        logger.info("Scrolling to bottom to load ALL property cards...")
        
        # Keep scrolling until no new cards appear
        previous_card_count = 0
        scroll_attempts = 0
        max_scroll_attempts = 20  # Increase attempts for more thorough loading
        no_change_count = 0  # Count how many times card count didn't change
        
        while scroll_attempts < max_scroll_attempts:
            # First, check for "Load More" or "Show More" buttons and click them
            load_more_selectors = [
                'button:has-text("Load More")',
                'button:has-text("Show More")',
                'button:has-text("See More")',
                '[data-test*="load-more"]',
                '[data-testid*="load-more"]',
                'button[aria-label*="Load More"]',
                'button[aria-label*="Show More"]',
            ]
            
            for selector in load_more_selectors:
                try:
                    load_more_btn = page.query_selector(selector)
                    if load_more_btn and load_more_btn.is_visible():
                        # Check if button is disabled
                        is_disabled = (
                            load_more_btn.get_attribute('disabled') is not None or
                            load_more_btn.get_attribute('aria-disabled') == 'true' or
                            'disabled' in (load_more_btn.get_attribute('class') or '').lower()
                        )
                        if not is_disabled:
                            card_count_before = len(page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]'))
                            logger.info(f"  Found 'Load More' button, clicking... (cards before: {card_count_before})")
                            load_more_btn.click()
                            time.sleep(random.uniform(4.0, 5.5))  # Even longer wait for content to load
                            # Scroll to bottom after clicking
                            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            time.sleep(random.uniform(3.0, 4.0))
                            card_count_after = len(page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]'))
                            if card_count_after > card_count_before:
                                logger.info(f"  ✅ Load More worked! Cards increased from {card_count_before} to {card_count_after}")
                            break
                except Exception:
                    continue
            
            # Get current page dimensions
            current_scroll = page.evaluate("window.pageYOffset || window.scrollY")
            page_height = page.evaluate("document.body.scrollHeight")
            viewport_height = page.viewport_size['height']
            
            # Scroll VERY slowly in small increments to trigger all lazy loading
            scroll_position = current_scroll
            scroll_step = 200  # Smaller steps for more thorough scrolling
            max_scroll = page_height - viewport_height + 100  # Go slightly past bottom
            
            while scroll_position < max_scroll:
                scroll_position += scroll_step
                # Ensure we don't overshoot
                if scroll_position > max_scroll:
                    scroll_position = max_scroll
                
                page.evaluate(f"window.scrollTo(0, {scroll_position})")
                time.sleep(random.uniform(1.2, 2.0))  # Longer pause between scrolls
                
                # Check if page height increased (new content loaded)
                new_page_height = page.evaluate("document.body.scrollHeight")
                if new_page_height > page_height:
                    logger.info(f"  Page height increased: {page_height} -> {new_page_height}, continuing scroll...")
                    page_height = new_page_height
                    max_scroll = page_height - viewport_height + 100
            
            # Force scroll to absolute bottom multiple times
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(random.uniform(2.5, 3.5))
                
                # Check if page height increased
                new_page_height = page.evaluate("document.body.scrollHeight")
                if new_page_height > page_height:
                    page_height = new_page_height
                    logger.info(f"  Page height increased to {page_height}, scrolling again...")
            
            # Check current card count
            current_cards = page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]')
            current_card_count = len(current_cards)
            
            logger.info(f"  Scroll attempt {scroll_attempts + 1}: Found {current_card_count} cards (page height: {page_height})")
            
            # If no new cards appeared, increment no_change_count
            if current_card_count == previous_card_count:
                no_change_count += 1
                # Only stop if we've had 5 consecutive attempts with no change (more conservative)
                if no_change_count >= 5 and current_card_count > 0:
                    logger.info(f"  ✅ All cards loaded! Total: {current_card_count} (no change for {no_change_count} attempts)")
                    break
            else:
                no_change_count = 0  # Reset counter if we found new cards
                logger.info(f"  📈 Card count increased: {previous_card_count} -> {current_card_count}")
            
            previous_card_count = current_card_count
            scroll_attempts += 1
            
            # Scroll back up a bit occasionally (human behavior) - but less frequently
            if scroll_attempts % 5 == 0:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight - 2000)")
                time.sleep(random.uniform(1.0, 1.5))
        
        # Final aggressive scroll to very bottom multiple times
        logger.info("  Performing final aggressive scroll to bottom...")
        for final_scroll in range(5):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(random.uniform(2.0, 3.0))
            
            # Check for load more buttons one more time
            for selector in load_more_selectors:
                try:
                    load_more_btn = page.query_selector(selector)
                    if load_more_btn and load_more_btn.is_visible():
                        is_disabled = (
                            load_more_btn.get_attribute('disabled') is not None or
                            load_more_btn.get_attribute('aria-disabled') == 'true' or
                            'disabled' in (load_more_btn.get_attribute('class') or '').lower()
                        )
                        if not is_disabled:
                            card_count_before = len(page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]'))
                            logger.info(f"  Final scroll {final_scroll + 1}: Found 'Load More' button, clicking... (cards: {card_count_before})")
                            load_more_btn.click()
                            time.sleep(random.uniform(4.0, 5.0))
                            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            time.sleep(random.uniform(2.0, 3.0))
                except Exception:
                    continue
        
        # One more check for load more buttons
        for selector in load_more_selectors:
            try:
                load_more_btn = page.query_selector(selector)
                if load_more_btn and load_more_btn.is_visible():
                    is_disabled = (
                        load_more_btn.get_attribute('disabled') is not None or
                        load_more_btn.get_attribute('aria-disabled') == 'true' or
                        'disabled' in (load_more_btn.get_attribute('class') or '').lower()
                    )
                    if not is_disabled:
                        card_count_before = len(page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]'))
                        logger.info(f"  Found final 'Load More' button, clicking... (cards before: {card_count_before})")
                        load_more_btn.click()
                        time.sleep(random.uniform(3.0, 4.5))
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        time.sleep(random.uniform(2.0, 3.0))
                        card_count_after = len(page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]'))
                        if card_count_after > card_count_before:
                            logger.info(f"  ✅ Final Load More worked! Cards increased from {card_count_before} to {card_count_after}")
            except Exception:
                continue
        
        # Scroll back to top
        logger.info("Scrolling back to top to start collecting...")
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(random.uniform(1.0, 1.5))
        
        # Final comprehensive query of all property cards
        logger.info("Performing final card count...")
        
        # Query all cards using the primary selectors
        property_cards = page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]')
        
        # If we didn't find many, try alternative selectors
        if len(property_cards) < 10:
            logger.info(f"  Only found {len(property_cards)} cards with primary selectors, trying alternatives...")
            alt_cards = page.query_selector_all('article[data-test="property-card"], [class*="PropertyCard"]')
            # Combine and deduplicate by checking hrefs
            seen_hrefs = set()
            for card in property_cards:
                try:
                    link = card.query_selector('a[href*="homedetails"], a[href*="/b/"]')
                    if link:
                        href = link.get_attribute('href')
                        if href:
                            seen_hrefs.add(href)
                except Exception:
                    pass
            
            for card in alt_cards:
                try:
                    link = card.query_selector('a[href*="homedetails"], a[href*="/b/"]')
                    if link:
                        href = link.get_attribute('href')
                        if href and href not in seen_hrefs:
                            property_cards.append(card)
                            seen_hrefs.add(href)
                except Exception:
                    pass
        
        logger.info(f"✅ Found {len(property_cards)} property cards on page (ready to collect)")
        
        if not property_cards:
            logger.warning("No property cards found!")
            return collected_urls
        
        # Click through each card
        for i, card in enumerate(property_cards, 1):
            try:
                # Re-query the card to avoid stale element issues
                try:
                    # Scroll card into view (human-like) - use page scroll instead
                    box = card.bounding_box()
                    if box:
                        page.evaluate(f"window.scrollTo(0, {box['y'] - 200})")
                        time.sleep(random.uniform(0.3, 0.7))
                        
                        # Small random mouse movement (human behavior)
                        x = box['x'] + box['width'] / 2 + random.randint(-10, 10)
                        y = box['y'] + box['height'] / 2 + random.randint(-10, 10)
                        page.mouse.move(x, y)
                        time.sleep(random.uniform(0.2, 0.5))
                except Exception as e:
                    logger.debug(f"Could not scroll to card {i}: {e}")
                    # Continue anyway
                
                logger.info(f"Clicking card {i}/{len(property_cards)}...")
                
                # Click and collect URL (pass page for context)
                url = click_property_card_and_collect_url(context, card, seen_urls, page)
                
                if url:
                    collected_urls.append(url)
                    seen_urls.add(url)
                    
                    # Save to CSV immediately
                    save_url_to_csv(url, output_csv)
                    logger.info(f"  💾 Saved to CSV: {url}")
                
                # Random pause between clicks (human reading time)
                time.sleep(random.uniform(1.0, 2.5))
                
            except Exception as e:
                logger.warning(f"Error processing card {i}: {e}")
                continue
        
    except Exception as e:
        logger.error(f"Error collecting URLs from page: {e}")
    
    return collected_urls


def get_next_page_url(page: Page):
    """Get the next page URL from pagination by clicking the next button or numbered page links."""
    try:
        # Scroll to bottom where pagination usually is
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(random.uniform(1.0, 1.5))
        
        current_url = page.url
        
        # Strategy 1: Look for arrow/next button with various selectors
        next_selectors = [
            'a[aria-label="Next page"]',
            'a[aria-label="Next"]',
            'a[aria-label*="Next" i]',
            'a[data-test="pagination-next"]',
            'a[data-testid="pagination-next"]',
            'button[aria-label="Next page"]',
            'button[aria-label="Next"]',
            'button[aria-label*="Next" i]',
            'a.next',
            '[class*="Pagination"] a[aria-label*="Next" i]',
            'nav a[aria-label*="Next" i]',
            # Arrow icons
            'a[aria-label*="arrow" i]',
            'button[aria-label*="arrow" i]',
            '[class*="arrow"][class*="next" i]',
            '[class*="pagination"][class*="next" i]',
        ]
        
        for selector in next_selectors:
            try:
                next_button = page.query_selector(selector)
                if next_button:
                    # Check visibility
                    try:
                        if not next_button.is_visible():
                            continue
                    except Exception:
                        pass  # Continue anyway
                    
                    is_disabled = (next_button.get_attribute('aria-disabled') == 'true' or
                                 'disabled' in (next_button.get_attribute('class') or '').lower() or
                                 next_button.get_attribute('disabled') is not None)
                    
                    if not is_disabled:
                        # Try to get href first
                        href = next_button.get_attribute('href')
                        if href:
                            if href.startswith('/'):
                                full_url = f"{BASE_URL}{href}"
                            elif href.startswith('http'):
                                full_url = href
                            else:
                                full_url = None
                            
                            if full_url and full_url != current_url:
                                logger.info(f"Found next page href via arrow: {full_url}")
                                next_button.click()
                                time.sleep(random.uniform(2.0, 3.0))
                                return page.url
                        
                        # If no href, try clicking and checking URL change
                        next_button.click()
                        time.sleep(random.uniform(2.0, 3.0))
                        new_url = page.url
                        if new_url != current_url:
                            logger.info(f"Next page found via arrow click: {new_url}")
                            return new_url
            except Exception as e:
                logger.debug(f"Selector {selector} failed: {e}")
                continue
        
        # Strategy 2: Look for numbered pagination links (2, 3, 4, etc.)
        try:
            # Find all pagination links/buttons
            pagination_containers = page.query_selector_all(
                'nav[aria-label*="pagination" i], '
                '[class*="Pagination"], '
                '[class*="pagination"], '
                '[data-test*="pagination" i], '
                '[data-testid*="pagination" i]'
            )
            
            # Also try to find links that look like page numbers
            all_pagination_links = []
            
            # Get links from pagination containers
            for container in pagination_containers:
                links = container.query_selector_all('a, button')
                all_pagination_links.extend(links)
            
            # Also search for links/buttons with numeric text (page numbers)
            numeric_links = page.query_selector_all('a, button')
            for link in numeric_links:
                try:
                    text = link.inner_text().strip()
                    # Check if it's a number (2, 3, 4, etc.) and not disabled
                    if text.isdigit() and int(text) > 1:
                        # Check if it's in a pagination context
                        parent = link.evaluate('el => el.closest("nav, [class*=\"pagination\" i], [class*=\"Pagination\"]")')
                        if parent:
                            all_pagination_links.append(link)
                except Exception:
                    continue
            
            # Find the current page number
            current_page_num = 1
            try:
                # Look for active/current page indicator
                active_page = page.query_selector(
                    '[aria-current="page"], '
                    '[class*="active"][class*="page"], '
                    '[class*="current"][class*="page"], '
                    '[data-test*="current-page" i]'
                )
                if active_page:
                    active_text = active_page.inner_text().strip()
                    if active_text.isdigit():
                        current_page_num = int(active_text)
            except Exception:
                pass
            
            # Look for the next page number (current + 1)
            next_page_num = current_page_num + 1
            
            logger.info(f"Looking for page {next_page_num} (current: {current_page_num})")
            
            for link in all_pagination_links:
                try:
                    if not link.is_visible():
                        continue
                    
                    is_disabled = (
                        link.get_attribute('aria-disabled') == 'true' or
                        'disabled' in (link.get_attribute('class') or '').lower() or
                        link.get_attribute('disabled') is not None
                    )
                    
                    if is_disabled:
                        continue
                    
                    # Check if this link is for the next page
                    text = link.inner_text().strip()
                    href = link.get_attribute('href')
                    
                    # Determine if this is the next page link
                    is_next_page = False
                    match_reason = ""
                    
                    # Check by text (should be the next page number)
                    if text == str(next_page_num):
                        is_next_page = True
                        match_reason = f"text matches page {next_page_num}"
                    # Check by href (might contain page number)
                    elif href and (f'/{next_page_num}_p/' in href or f'page={next_page_num}' in href or f'/p{next_page_num}/' in href):
                        is_next_page = True
                        match_reason = f"href contains page {next_page_num}"
                    
                    if is_next_page:
                        logger.info(f"Found page {next_page_num} link by {match_reason}: {text or href}")
                        
                        # Get current card URLs/addresses before clicking to compare
                        cards_before = page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]')
                        card_count_before = len(cards_before)
                        card_urls_before = set()
                        for card in cards_before[:5]:  # Get first 5 card URLs
                            try:
                                link_elem = card.query_selector('a[href*="homedetails"], a[href*="/b/"]')
                                if link_elem:
                                    href = link_elem.get_attribute('href')
                                    if href:
                                        card_urls_before.add(href)
                            except Exception:
                                pass
                        
                        # Click the link
                        link.click()
                        time.sleep(random.uniform(4.0, 5.5))  # Even longer wait for SPA navigation
                        
                        # Scroll to trigger any lazy loading
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        time.sleep(random.uniform(2.0, 3.0))
                        page.evaluate("window.scrollTo(0, 0)")
                        time.sleep(random.uniform(1.0, 1.5))
                        
                        new_url = page.url
                        
                        # Check if URL changed OR content changed (for SPAs)
                        cards_after = page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]')
                        card_count_after = len(cards_after)
                        
                        # Get URLs from first few cards after click
                        card_urls_after = set()
                        for card in cards_after[:5]:  # Get first 5 card URLs
                            try:
                                link_elem = card.query_selector('a[href*="homedetails"], a[href*="/b/"]')
                                if link_elem:
                                    href = link_elem.get_attribute('href')
                                    if href:
                                        card_urls_after.add(href)
                            except Exception:
                                pass
                        
                        # Check if cards are different (different URLs)
                        # If the intersection is smaller than either set, cards are different
                        cards_different = (
                            len(card_urls_before) > 0 and 
                            len(card_urls_after) > 0 and 
                            card_urls_before != card_urls_after
                        )
                        
                        if new_url != current_url:
                            logger.info(f"Page changed! URL: {current_url} -> {new_url}, Cards: {card_count_before} -> {card_count_after}")
                            return new_url
                        elif card_count_after != card_count_before:
                            logger.info(f"Page content changed (SPA navigation)! Cards: {card_count_before} -> {card_count_after}")
                            return new_url  # Return current URL even if it didn't change
                        elif cards_different and len(card_urls_after) > 0:
                            logger.info(f"Page content changed (different property cards)! Card URLs changed")
                            return new_url  # Return current URL even if it didn't change
                        else:
                            logger.warning(f"No change detected after clicking page {next_page_num}. URL: {new_url}, Cards: {card_count_after}, Card URLs match: {not cards_different}")
                            # Continue to next link
                            
                except Exception as e:
                    logger.debug(f"Error checking pagination link: {e}")
                    continue
        except Exception as e:
            logger.debug(f"Numbered pagination search failed: {e}")
        
        # Strategy 3: Fallback - Try to find pagination by text content
        try:
            all_links = page.query_selector_all('a, button')
            for link in all_links:
                try:
                    text = link.inner_text().strip().lower()
                    if text in ['next', 'next page', '→', '›', '»']:
                        is_disabled = (link.get_attribute('aria-disabled') == 'true' or
                                     'disabled' in (link.get_attribute('class') or '').lower())
                        if not is_disabled:
                            current_url = page.url
                            link.click()
                            time.sleep(random.uniform(2.0, 3.0))
                            new_url = page.url
                            if new_url != current_url:
                                logger.info(f"Found next page via text/arrow search: {new_url}")
                                return new_url
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"Text-based pagination search failed: {e}")
            
    except Exception as e:
        logger.debug(f"Error finding next page: {e}")
    
    return None


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


def collect_urls(city: str, state: str, max_pages: int, delay: float, output_csv: str, headless: bool = False):
    """Collect property URLs from Zillow search pages by clicking through cards."""
    city_normalized = city.lower().replace(' ', '-').replace(',', '').replace("'", "")
    state_normalized = state.lower()
    
    all_urls = []
    seen_urls = load_existing_urls(output_csv)  # Load existing URLs
    # Use the houses-for-rent URL format
    search_url = f"{BASE_URL}/{city_normalized}-{state_normalized}/rent-houses/"
    
    logger.info("=" * 80)
    logger.info("ZILLOW URL COLLECTOR (Human-like)")
    logger.info("=" * 80)
    logger.info(f"City: {city}, State: {state}")
    logger.info(f"Max pages: {max_pages}")
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
            # Navigate to search page (already filtered for houses via URL)
            logger.info(f"\nNavigating to: {search_url}")
            page.goto(search_url, wait_until='domcontentloaded', timeout=60000)
            time.sleep(random.uniform(2.0, 3.5))
            
            # Wait for page to load and verify we're on houses page
            try:
                page.wait_for_selector('[data-test="property-card"], [data-testid="property-card"]', timeout=15000)
                logger.info("✅ Property cards loaded")
            except Exception:
                logger.warning("Property cards not found, but continuing...")
            
            page_num = 0
            
            while page_num < max_pages:
                page_num += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"PAGE {page_num}/{max_pages}")
                logger.info(f"{'='*80}")
                logger.info(f"Current URL: {page.url}")
                
                # Collect URLs by clicking through cards (saves to CSV incrementally)
                urls = collect_urls_from_page(context, page, seen_urls, output_csv)
                
                if urls:
                    all_urls.extend(urls)
                    logger.info(f"\n✅ Collected {len(urls)} new URLs from this page")
                    logger.info(f"📊 Total unique URLs so far: {len(all_urls)}")
                else:
                    logger.warning(f"No new URLs found on page {page_num}")
                
                # Try to go to next page
                if page_num < max_pages:
                    logger.info("\nLooking for next page...")
                    current_url_before = page.url  # Save URL before navigation
                    next_url = get_next_page_url(page)
                    if next_url:  # If we got a URL (even if same, it means navigation happened)
                        logger.info(f"✅ Next page found: {next_url}")
                        time.sleep(random.uniform(delay, delay + 1.0))
                        # Continue to next iteration to collect URLs from the new page
                    else:
                        logger.info("❌ No next page found, stopping")
                        break
                else:
                    break
            
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
    parser = argparse.ArgumentParser(description='Collect Zillow property URLs')
    parser.add_argument('--city', type=str, required=True, help='City name (e.g., "Atlanta")')
    parser.add_argument('--state', type=str, required=True, help='State abbreviation (e.g., "GA")')
    parser.add_argument('--max_pages', type=int, default=10, help='Maximum pages to scrape (default: 10)')
    parser.add_argument('--delay', type=float, default=3.0, help='Delay between pages in seconds (default: 3.0)')
    parser.add_argument('--output', type=str, default='zillow_urls.csv', help='Output CSV file (default: zillow_urls.csv)')
    parser.add_argument('--headless', action='store_true', help='Run browser in headless mode')
    
    args = parser.parse_args()
    
    collect_urls(
        city=args.city,
        state=args.state,
        max_pages=args.max_pages,
        delay=args.delay,
        output_csv=args.output,
        headless=args.headless
    )


if __name__ == "__main__":
    main()

