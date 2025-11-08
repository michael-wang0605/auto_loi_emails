#!/usr/bin/env python3
"""
Apartments.com Single-Family Rental (SFR) Scraper

Collects ~5 single-family rental listings from Apartments.com
and exports to CSV with phone, address, and manager information.
"""
import argparse
import logging
import random
import re
import sys
import time
from typing import Dict, List, Optional
from urllib.parse import urljoin

import pandas as pd
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
BASE_URL = "https://www.apartments.com"


def retry_page_goto(page: Page, url: str, max_retries: int = 3) -> bool:
    """
    Retry page.goto() with incremental backoff (1s, 2s, 4s).
    Returns True if successful, False if all retries failed.
    """
    backoff_delays = [1, 2, 4]  # seconds
    
    for attempt in range(max_retries):
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                logger.warning(f"Failed to load {url} (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {delay}s...")
                time.sleep(delay)
            else:
                logger.error(f"Failed to load {url} after {max_retries} attempts: {e}")
                return False
    
    return False


def normalize_phone(phone: str) -> Optional[str]:
    """
    Normalize phone number: strip non-digits, keep 10-11 digits.
    Returns None if invalid.
    """
    if not phone:
        return None
    
    # Remove all non-digit characters
    digits = re.sub(r'[^\d]', '', phone)
    
    # Remove leading 1 if present (US country code)
    if len(digits) == 11 and digits[0] == '1':
        digits = digits[1:]
    
    # Must be exactly 10 digits
    if len(digits) != 10:
        return None
    
    return digits


def extract_phone(page: Optional[Page] = None, soup: Optional[BeautifulSoup] = None, page_text: Optional[str] = None) -> Optional[str]:
    """
    Extract phone number using multiple fallback methods.
    Prioritizes Playwright selectors if page is provided, falls back to BeautifulSoup.
    """
    # If page is provided, use Playwright selectors first
    if page:
        try:
            # Method 1: tel: links (Playwright)
            tel_links = page.query_selector_all('a[href^="tel:"]')
            for link in tel_links:
                href = link.get_attribute('href')
                if href:
                    phone = href.replace('tel:', '').replace('+1', '').strip()
                    normalized = normalize_phone(phone)
                    if normalized:
                        return normalized
        except Exception as e:
            logger.debug(f"Error extracting tel: links with Playwright: {e}")
    
    # Fallback to BeautifulSoup if available
    if soup:
        # Method 1: tel: links (BeautifulSoup)
        tel_links = soup.find_all('a', href=re.compile(r'^tel:'))
        for link in tel_links:
            href = link.get('href', '')
            phone = href.replace('tel:', '').replace('+1', '').strip()
            normalized = normalize_phone(phone)
            if normalized:
                return normalized
    
    # Get page text if not provided
    if not page_text:
        if page:
            try:
                page_text = page.inner_text('body')
            except Exception:
                page_text = ''
        elif soup:
            page_text = soup.get_text()
        else:
            page_text = ''
    
    # Method 2: Phone number patterns in visible text (regex on page content)
    phone_patterns = [
        r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}',  # (XXX) XXX-XXXX
        r'\d{3}[-.\s]?\d{3}[-.\s]?\d{4}',        # XXX-XXX-XXXX
        r'\+?1[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}',  # +1 XXX-XXX-XXXX
    ]
    
    # Try to find phone in visible elements first (if using Playwright)
    # Search in likely elements that contain phone numbers (links, buttons, contact sections)
    if page:
        try:
            # Look for phone patterns in likely visible elements
            likely_selectors = [
                'a[href^="tel:"]',
                'a[href*="phone"]',
                'a[href*="call"]',
                'button',
                '[class*="contact"]',
                '[class*="phone"]',
                '[class*="call"]',
                '[id*="contact"]',
                '[id*="phone"]',
            ]
            
            for selector in likely_selectors:
                try:
                    elements = page.query_selector_all(selector)
                    for elem in elements:
                        try:
                            if elem.is_visible():
                                text = elem.inner_text()
                                if text:
                                    for pattern in phone_patterns:
                                        matches = re.findall(pattern, text)
                                        for match in matches:
                                            normalized = normalize_phone(match)
                                            if normalized:
                                                return normalized
                        except Exception:
                            continue
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"Error searching visible elements for phone: {e}")
    
    # Fallback: search entire page text
    for pattern in phone_patterns:
        matches = re.findall(pattern, page_text)
        for match in matches:
            normalized = normalize_phone(match)
            if normalized:
                return normalized
    
    return None


def extract_address(page: Optional[Page] = None, soup: Optional[BeautifulSoup] = None, page_text: Optional[str] = None) -> Optional[str]:
    """
    Extract street address using multiple fallback methods.
    Prioritizes Playwright selectors if page is provided, falls back to BeautifulSoup.
    """
    # Get page text if not provided
    if not page_text:
        if page:
            try:
                page_text = page.inner_text('body')
            except Exception:
                page_text = ''
        elif soup:
            page_text = soup.get_text()
        else:
            page_text = ''
    
    # Method 1: itemprop=streetAddress (meta tags)
    if soup:
        street_elem = soup.find('meta', itemprop='streetAddress')
        if street_elem and street_elem.get('content'):
            addr = street_elem.get('content').strip()
            if addr:
                return addr
    
    if page:
        try:
            # Try to find meta tags with Playwright
            meta_elem = page.query_selector('meta[itemprop="streetAddress"]')
            if meta_elem:
                content = meta_elem.get_attribute('content')
                if content and content.strip():
                    return content.strip()
        except Exception:
            pass
    
    # Method 2: itemprop=addressLocality (meta tags)
    if soup:
        locality_elem = soup.find('meta', itemprop='addressLocality')
        if locality_elem and locality_elem.get('content'):
            addr = locality_elem.get('content').strip()
            if addr:
                return addr
    
    # Method 3: address tag
    if page:
        try:
            address_tags = page.query_selector_all('address')
            for tag in address_tags:
                try:
                    text = tag.inner_text()
                    if text and len(text) > 10:
                        lines = [line.strip() for line in text.split('\n') if line.strip()]
                        if lines:
                            addr = lines[0].split(',')[0].strip()
                            if addr:
                                return addr
                except Exception:
                    continue
        except Exception:
            pass
    
    if soup:
        address_tags = soup.find_all('address')
        for tag in address_tags:
            text = tag.get_text(strip=True)
            if text and len(text) > 10:
                lines = [line.strip() for line in text.split('\n') if line.strip()]
                if lines:
                    addr = lines[0].split(',')[0].strip()
                    if addr:
                        return addr
    
    # Method 4: Regex fallback - look for street address pattern
    street_pattern = r'\d+\s+[A-Za-z\s]+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Blvd|Boulevard|Ln|Lane|Ct|Court|Pl|Place|Pkwy|Parkway)'
    matches = re.findall(street_pattern, page_text, re.IGNORECASE)
    if matches:
        return matches[0].strip()
    
    return None


def extract_manager_name(page: Optional[Page] = None, soup: Optional[BeautifulSoup] = None, page_text: Optional[str] = None) -> Optional[str]:
    """
    Extract manager/community name using multiple fallback methods.
    Prioritizes Playwright selectors if page is provided, falls back to BeautifulSoup.
    """
    # Get page text if not provided
    if not page_text:
        if page:
            try:
                page_text = page.inner_text('body')
            except Exception:
                page_text = ''
        elif soup:
            page_text = soup.get_text()
        else:
            page_text = ''
    
    # Method 1: Look for labels "Managed by", "Leasing Office", "Property Management", "Community"
    manager_keywords = [
        r'Managed by[:\s]+([A-Z][a-zA-Z\s&,.-]+)',
        r'Leasing Office[:\s]+([A-Z][a-zA-Z\s&,.-]+)',
        r'Property Management[:\s]+([A-Z][a-zA-Z\s&,.-]+)',
        r'Community[:\s]+([A-Z][a-zA-Z\s&,.-]+)',
        r'Management[:\s]+([A-Z][a-zA-Z\s&,.-]+)',
    ]
    
    for pattern in manager_keywords:
        match = re.search(pattern, page_text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            # Clean up common suffixes
            name = re.sub(r'\s+(LLC|Inc|Corp|Management|Properties).*$', '', name, flags=re.IGNORECASE)
            if len(name) > 2 and len(name) < 100:
                return name
    
    # Method 2: H1 or main header near the top of the listing
    if page:
        try:
            # Look for h1 elements
            h1_elements = page.query_selector_all('h1')
            for h1 in h1_elements:
                try:
                    if h1.is_visible():
                        text = h1.inner_text().strip()
                        # If it looks like a name (not an address, not too long, not apartments.com)
                        if (text and len(text) > 2 and len(text) < 100 and 
                            not re.search(r'\d{5}', text) and  # Not a zip code
                            'apartments.com' not in text.lower() and
                            not re.search(r'\d+\s+[A-Za-z\s]+(?:St|Street|Ave|Avenue)', text)):  # Not an address
                            return text
                except Exception:
                    continue
        except Exception:
            pass
    
    if soup:
        headers = soup.find_all(['h1', 'h2'])
        for header in headers:
            text = header.get_text(strip=True)
            # If it looks like a name (not an address, not too long)
            if (text and len(text) > 2 and len(text) < 100 and 
                not re.search(r'\d{5}', text) and  # Not a zip code
                'apartments.com' not in text.lower() and
                not re.search(r'\d+\s+[A-Za-z\s]+(?:St|Street|Ave|Avenue)', text)):  # Not an address
                return text
    
    # Method 3: Page title chunks (often contains community/manager name)
    title_text = None
    if page:
        try:
            title_text = page.title()
        except Exception:
            pass
    
    if not title_text and soup:
        title = soup.find('title')
        if title:
            title_text = title.get_text()
    
    if title_text:
        # Look for patterns like "Name - Apartments.com" or "Name | Apartments"
        match = re.search(r'^([^-|]+)', title_text)
        if match:
            name = match.group(1).strip()
            # Remove common prefixes
            name = re.sub(r'^(Apartments?|Rentals?|Homes?|Properties?)\s+', '', name, flags=re.IGNORECASE)
            if len(name) > 2 and len(name) < 100 and 'apartments.com' not in name.lower():
                return name
    
    return None


def get_listing_urls_from_search_page(page: Page, url: str) -> List[str]:
    """
    Extract listing detail page URLs from a search results page.
    Uses retry logic with incremental backoff.
    Returns empty list if all retries fail (caller will continue to next page).
    """
    listing_urls = []
    
    # Use retry helper for page.goto()
    if not retry_page_goto(page, url, max_retries=3):
        logger.warning(f"Failed to load search page {url} after all retries, continuing to next page")
        return listing_urls
    
    try:
        # Wait for DOM to render - try multiple selectors
        search_selectors = [
            "a[href*='/property/']",
            ".placard",
            "[data-testid*='placard']",
            'article.placard',
            'a.property-link',
        ]
        
        # Try to wait for at least one of the selectors
        waited = False
        for selector in search_selectors:
            try:
                page.wait_for_selector(selector, timeout=10000, state='visible')
                waited = True
                break
            except PlaywrightTimeoutError:
                continue
        
        if not waited:
            logger.warning(f"None of the search selectors found on {url}, but continuing anyway")
        
        # Additional wait for dynamic content
        page.wait_for_timeout(1000)
        
        # Get page content
        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        
        # Find listing links - multiple selector patterns
        link_selectors = [
            'article.placard a.property-link',
            'a.property-link',
            'a[href*="/apartments/"]',
            'a[href*="/property/"]',
            'article[class*="placard"] a',
        ]
        
        for selector in link_selectors:
            links = soup.select(selector)
            for link in links:
                href = link.get('href')
                if href:
                    full_url = urljoin(BASE_URL, href)
                    # Filter for detail pages (not search pages)
                    if (full_url not in listing_urls and 
                        full_url.startswith(BASE_URL) and
                        ('/apartments/' in full_url or '/property/' in full_url) and
                        '/houses/' not in full_url.split('/')[-2:]):
                        listing_urls.append(full_url)
        
        logger.info(f"Found {len(listing_urls)} listings on search page")
        
    except Exception as e:
        logger.error(f"Error extracting listing URLs from search page {url}: {e}")
    
    return listing_urls


def scrape_listing_detail(page: Page, url: str) -> Optional[Dict]:
    """
    Scrape a single listing detail page and extract data.
    Uses retry logic with incremental backoff.
    Returns None if all retries fail (caller will skip this listing).
    """
    # Use retry helper for page.goto()
    if not retry_page_goto(page, url, max_retries=3):
        logger.warning(f"Failed to load listing detail page {url} after all retries, skipping")
        return None
    
    try:
        # Wait for page to load - wait for any content that indicates page is loaded
        # Try waiting for common detail page elements
        detail_selectors = [
            'address',
            'h1',
            '[data-testid*="address"]',
            '.property-header',
            '.listing-details',
        ]
        
        waited = False
        for selector in detail_selectors:
            try:
                page.wait_for_selector(selector, timeout=10000, state='visible')
                waited = True
                break
            except PlaywrightTimeoutError:
                continue
        
        if not waited:
            logger.warning(f"None of the detail selectors found on {url}, but continuing anyway")
        
        # Additional wait for dynamic content
        page.wait_for_timeout(1000)
        
        # Get page content for BeautifulSoup fallback
        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        
        # Extract phone (required) - use Playwright page first, fallback to soup
        phone = extract_phone(page=page, soup=soup)
        if not phone:
            logger.debug(f"No phone found for {url}")
            return None
        
        # Extract address (best-effort)
        address = extract_address(page=page, soup=soup)
        if not address:
            logger.debug(f"No address found for {url}")
            # Still return if we have phone, but address will be empty
        
        # Extract manager name (best-effort)
        manager_name = extract_manager_name(page=page, soup=soup)
        
        logger.info(f"Extracted: {phone} - {address or 'N/A'} - {manager_name or 'N/A'}")
        
        return {
            'phone': phone,
            'address': address or '',
            'manager_name': manager_name or ''
        }
        
    except Exception as e:
        logger.error(f"Error scraping listing {url}: {e}")
        return None


def scrape_city(city: str, state: str, max_pages: int, delay: float, target_rows: int = 5) -> Dict:
    """
    Main scraping function using Playwright.
    Uses one browser, one context, and reuses separate pages for search and detail.
    Returns aggregated data dict keyed by phone.
    """
    # Aggregate data: phone -> {addresses: set, manager_name: str, units: int}
    aggregated: Dict[str, Dict] = {}
    
    logger.info(f"Starting scrape for {city}, {state} (max {max_pages} pages, target {target_rows} rows)")
    
    with sync_playwright() as p:
        # Launch browser with one context for the whole run
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={'width': 1920, 'height': 1080}
        )
        
        # Create two pages: one for search pages, one for detail pages
        search_page = context.new_page()
        detail_page = context.new_page()
        
        try:
            # Collect listing URLs from search pages
            all_listing_urls = []
            city_normalized = city.lower().replace(' ', '-').replace(',', '')
            state_normalized = state.lower()
            
            for page_num in range(1, max_pages + 1):
                # Stop only when we've collected enough phones OR finished all pages
                if len(aggregated) >= target_rows:
                    logger.info(f"Reached target of {target_rows} rows, stopping early")
                    break
                
                # Build search URL
                if page_num == 1:
                    search_url = f"{BASE_URL}/houses/{city_normalized}-{state_normalized}/"
                else:
                    search_url = f"{BASE_URL}/houses/{city_normalized}-{state_normalized}/{page_num}/"
                
                logger.info(f"Fetching page {page_num}: {search_url}")
                
                # Reuse search_page for all search pages
                # If search page fails, get_listing_urls_from_search_page returns empty list and logs warning
                # We continue to next page instead of stopping
                listing_urls = get_listing_urls_from_search_page(search_page, search_url)
                
                # Never treat a single failed page as "no more listings"
                # Only continue processing if we have URLs to process
                if listing_urls:
                    all_listing_urls.extend(listing_urls)
                    logger.info(f"Found {len(listing_urls)} listings on page {page_num}, total so far: {len(all_listing_urls)}")
                else:
                    logger.warning(f"No listings found on page {page_num}, but continuing to next page (may be temporary failure)")
                
                # Rate limiting with jitter using wait_for_timeout
                if page_num < max_pages:
                    jitter = random.uniform(-0.4, 0.4)
                    wait_time = max(0.1, delay + jitter)
                    search_page.wait_for_timeout(int(wait_time * 1000))
            
            logger.info(f"Total listing URLs collected: {len(all_listing_urls)}")
            
            # Scrape each listing detail page using the detail_page
            for i, listing_url in enumerate(all_listing_urls, 1):
                # Stop only when we've collected enough phones
                if len(aggregated) >= target_rows:
                    logger.info(f"Reached target of {target_rows} rows, stopping")
                    break
                
                logger.info(f"Scraping listing {i}/{len(all_listing_urls)}: {listing_url}")
                
                # Navigate detail_page instead of creating new tabs
                # If detail page fails, scrape_listing_detail returns None and logs warning
                # We skip this listing and continue to next one
                listing_data = scrape_listing_detail(detail_page, listing_url)
                
                if listing_data:
                    phone = listing_data['phone']
                    address = listing_data['address']
                    manager_name = listing_data['manager_name']
                    
                    if phone in aggregated:
                        # Existing phone: append address if unique, increment units
                        if address and address not in aggregated[phone]['addresses']:
                            aggregated[phone]['addresses'].add(address)
                            aggregated[phone]['units'] += 1
                        # Update manager_name if we found one and didn't have one before
                        if manager_name and not aggregated[phone]['manager_name']:
                            aggregated[phone]['manager_name'] = manager_name
                    else:
                        # New phone: create entry
                        aggregated[phone] = {
                            'addresses': {address} if address else set(),
                            'manager_name': manager_name,
                            'units': 1
                        }
                else:
                    logger.debug(f"Skipping listing {i} (no data extracted), continuing to next listing")
                
                # Rate limiting with jitter using wait_for_timeout
                if i < len(all_listing_urls):
                    jitter = random.uniform(-0.4, 0.4)
                    wait_time = max(0.1, delay + jitter)
                    detail_page.wait_for_timeout(int(wait_time * 1000))
        
        finally:
            # Clean up pages and browser
            search_page.close()
            detail_page.close()
            browser.close()
    
    return aggregated


def export_to_csv(aggregated: Dict, filename: str = "apartments_sfr.csv") -> None:
    """Export aggregated data to CSV, sorted by phone ascending."""
    records = []
    
    for phone in sorted(aggregated.keys()):
        data = aggregated[phone]
        addresses = sorted(data['addresses']) if data['addresses'] else []
        addresses_str = '; '.join(addresses) if addresses else ''
        
        records.append({
            'phone': phone,
            'manager_name': data['manager_name'] or '',
            'addresses': addresses_str,
            'units': data['units']
        })
    
    df = pd.DataFrame(records)
    df.to_csv(filename, index=False)
    logger.info(f"Exported {len(records)} records to {filename}")
    
    # Print preview
    print("\n" + "=" * 80)
    print("CSV Preview (first 10 rows):")
    print("=" * 80)
    print(df.head(10).to_string(index=False))
    print("=" * 80)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Scrape Apartments.com for single-family rental listings'
    )
    parser.add_argument('--city', type=str, required=True, help='City name (e.g., "Atlanta")')
    parser.add_argument('--state', type=str, required=True, help='State abbreviation (e.g., "GA")')
    parser.add_argument('--max_pages', type=int, default=3, help='Maximum pages to scrape (default: 3)')
    parser.add_argument('--delay', type=float, default=1.5, help='Delay between requests in seconds (default: 1.5)')
    
    args = parser.parse_args()
    
    try:
        # Run scraper
        aggregated = scrape_city(args.city, args.state, args.max_pages, args.delay, target_rows=5)
        
        # Export results
        export_to_csv(aggregated)
        
        logger.info("Scraping completed successfully")
        
    except KeyboardInterrupt:
        logger.info("Scraping interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Scraping failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

