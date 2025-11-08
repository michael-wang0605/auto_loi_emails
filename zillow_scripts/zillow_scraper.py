"""
Zillow.com scraper with Playwright navigation and multi-fallback extraction.
Follows the same pattern as apartments scraper.
"""
import json
import logging
import random
import re
import time
from typing import Optional, Dict, List
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from src.store import Store, normalize_url

logger = logging.getLogger(__name__)

BASE_URL = "https://www.zillow.com"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def retry_goto(page: Page, url: str, max_retries: int = 3) -> bool:
    """
    Retry page.goto() with incremental backoff (1s, 2s, 4s).
    Uses more human-like navigation to avoid bot detection.
    Returns True if successful, False if all retries failed.
    """
    backoff_delays = [1, 2, 4]
    
    for attempt in range(max_retries):
        try:
            # Add random mouse movement before navigation (human-like behavior)
            try:
                page.mouse.move(random.randint(100, 500), random.randint(100, 500))
            except Exception:
                pass
            
            wait_strategies = ["domcontentloaded", "load"]  # Try domcontentloaded first (faster, less detectable)
            last_error = None
            
            for wait_strategy in wait_strategies:
                try:
                    # Use referer to make it look like we came from Zillow
                    page.goto(
                        url, 
                        wait_until=wait_strategy, 
                        timeout=90000,
                        referer='https://www.zillow.com/'
                    )
                    
                    # Random delay to mimic human reading time
                    wait_time = random.uniform(1.5, 3.0)
                    page.wait_for_timeout(int(wait_time * 1000))
                    
                    return True
                except Exception as e:
                    last_error = e
                    if wait_strategy == wait_strategies[-1]:
                        raise
                    continue
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
    Normalize phone number: strip non-digits, keep 10 or 11 digits (11 if starts with 1).
    Returns None if invalid.
    """
    if not phone:
        return None
    
    digits = re.sub(r'\D', '', phone)
    
    if len(digits) == 10:
        return digits
    elif len(digits) == 11 and digits[0] == '1':
        return digits
    
    return None


def parse_json_ld(soup: BeautifulSoup) -> Dict:
    """Parse all <script type="application/ld+json"> blocks."""
    result = {
        'address': None,
        'telephone': None,
        'name': None
    }
    
    json_ld_scripts = soup.find_all('script', type='application/ld+json')
    
    for script in json_ld_scripts:
        try:
            content = script.string
            if not content:
                continue
            
            data = json.loads(content)
            
            # Handle both single objects and arrays
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        data = item
                        break
            
            if not isinstance(data, dict):
                continue
            
            # Extract address
            if 'address' in data:
                addr_data = data['address']
                if isinstance(addr_data, dict):
                    # Build full address from components
                    parts = []
                    if addr_data.get('streetAddress'):
                        parts.append(addr_data['streetAddress'])
                    if addr_data.get('addressLocality'):
                        parts.append(addr_data['addressLocality'])
                    if addr_data.get('addressRegion'):
                        parts.append(addr_data['addressRegion'])
                    if addr_data.get('postalCode'):
                        parts.append(addr_data['postalCode'])
                    if parts:
                        result['address'] = ', '.join(parts)
                elif isinstance(addr_data, str):
                    result['address'] = addr_data
            
            # Extract telephone
            if 'telephone' in data:
                result['telephone'] = data['telephone']
            
            # Extract name
            if 'name' in data:
                result['name'] = data['name']
                
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"Error parsing JSON-LD: {e}")
            continue
    
    return result


def extract_phone_from_selectors(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract phone using selector fallbacks."""
    # Method 1: tel: links
    try:
        tel_links = page.query_selector_all('a[href^="tel:"]')
        for link in tel_links:
            try:
                href = link.get_attribute('href')
                if href:
                    phone = href.replace('tel:', '').replace('+1', '').strip()
                    normalized = normalize_phone(phone)
                    if normalized:
                        return normalized
            except Exception:
                continue
    except Exception:
        pass
    
    if soup:
        tel_links = soup.find_all('a', href=re.compile(r'^tel:'))
        for link in tel_links:
            href = link.get('href', '')
            phone = href.replace('tel:', '').replace('+1', '').strip()
            normalized = normalize_phone(phone)
            if normalized:
                return normalized
    
    # Method 2: Elements with phone-like text
    try:
        selectors = [
            'a[href*="phone"]',
            'a[href*="call"]',
            '[class*="phone"]',
            '[class*="contact"]',
            '[data-testid*="phone"]',
            '[data-testid*="contact"]',
        ]
        for selector in selectors:
            try:
                elements = page.query_selector_all(selector)
                for elem in elements:
                    try:
                        if elem.is_visible():
                            text = elem.inner_text()
                            if text:
                                phone_pattern = r'(?:\+?1[\s\-\.]?)?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}'
                                matches = re.findall(phone_pattern, text)
                                for match in matches:
                                    normalized = normalize_phone(match)
                                    if normalized:
                                        return normalized
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception:
        pass
    
    return None


def extract_phone_from_regex(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract phone using regex fallback on page text."""
    page_text = ""
    
    try:
        page_text = page.inner_text('body')
    except Exception:
        if soup:
            page_text = soup.get_text()
    
    if not page_text:
        return None
    
    phone_pattern = r'(?:\+?1[\s\-\.]?)?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}'
    matches = re.findall(phone_pattern, page_text)
    
    for match in matches:
        normalized = normalize_phone(match)
        if normalized:
            return normalized
    
    return None


def extract_phone(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract phone using JSON-LD → selectors → regex fallback order."""
    # Method 1: JSON-LD
    json_ld_data = parse_json_ld(soup)
    if json_ld_data.get('telephone'):
        normalized = normalize_phone(json_ld_data['telephone'])
        if normalized:
            return normalized
    
    # Method 2: Selectors
    phone = extract_phone_from_selectors(page, soup)
    if phone:
        return phone
    
    # Method 3: Regex fallback
    phone = extract_phone_from_regex(page, soup)
    if phone:
        return phone
    
    return None


def normalize_address(address: str) -> str:
    """Normalize address: title case, collapse whitespace."""
    if not address:
        return ""
    
    address = ' '.join(address.split())
    
    words = address.split()
    normalized_words = []
    for word in words:
        if word.upper() in ['ST', 'AVE', 'RD', 'BLVD', 'LN', 'CT', 'DR', 'WAY', 'PL', 'PKWY']:
            normalized_words.append(word.upper())
        elif word.upper() in ['N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW']:
            normalized_words.append(word.upper())
        else:
            normalized_words.append(word.title())
    
    return ' '.join(normalized_words)


def extract_address_from_selectors(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract address using multiple selector fallbacks."""
    # Method 1: meta itemprop=streetAddress
    if soup:
        street_elem = soup.find('meta', itemprop='streetAddress')
        if street_elem and street_elem.get('content'):
            addr = street_elem.get('content').strip()
            if addr:
                return addr
    
    try:
        meta_elem = page.query_selector('meta[itemprop="streetAddress"]')
        if meta_elem:
            content = meta_elem.get_attribute('content')
            if content and content.strip():
                return content.strip()
    except Exception:
        pass
    
    # Method 2: Zillow-specific address selectors
    address_selectors = [
        'h1[data-test="property-card-addr"]',
        '[data-test="property-card-addr"]',
        '.PropertyHeaderContainer h1',
        'h1.address',
        '[data-testid="address"]',
        '[class*="address" i]',
        '[class*="Address"]',
    ]
    
    for selector in address_selectors:
        try:
            elem = page.query_selector(selector)
            if elem:
                text = elem.inner_text().strip()
                if text and len(text) > 10 and len(text) < 200:
                    if re.search(r'^\d+', text):
                        lines = text.split('\n')
                        addr = lines[0].split(',')[0].strip()
                        if addr:
                            return addr
        except Exception:
            continue
    
    # Method 3: address tag
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
    
    return None


def extract_address_from_regex(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract address using regex fallback."""
    page_text = ""
    
    try:
        page_text = page.inner_text('body')
    except Exception:
        if soup:
            page_text = soup.get_text()
    
    if not page_text:
        return None
    
    street_pattern = r'\d+\s+[A-Za-z0-9\s]+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Blvd|Boulevard|Ln|Lane|Ct|Court|Way|Pl|Place|Pkwy|Parkway)'
    matches = re.findall(street_pattern, page_text, re.IGNORECASE)
    if matches:
        addr = matches[0].strip()
        addr = ' '.join(addr.split())
        return addr
    
    return None


def extract_address(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract address using JSON-LD → selectors → regex fallback order."""
    # Method 1: JSON-LD
    json_ld_data = parse_json_ld(soup)
    if json_ld_data.get('address'):
        addr = json_ld_data['address']
        if addr:
            normalized = normalize_address(addr)
            return normalized
    
    # Method 2: Selectors
    address = extract_address_from_selectors(page, soup)
    if address:
        return normalize_address(address)
    
    # Method 3: Regex fallback
    address = extract_address_from_regex(page, soup)
    if address:
        return normalize_address(address)
    
    return None


def extract_manager_name_from_selectors(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract manager name using selector fallbacks."""
    page_text = ""
    
    try:
        page_text = page.inner_text('body')
    except Exception:
        if soup:
            page_text = soup.get_text()
    
    # Method 1: Look for labels
    manager_keywords = [
        r'Managed by[:\s]+([A-Z][a-zA-Z\s&,.-]+)',
        r'Leasing Office[:\s]+([A-Z][a-zA-Z\s&,.-]+)',
        r'Property Management[:\s]+([A-Z][a-zA-Z\s&,.-]+)',
        r'Agent[:\s]+([A-Z][a-zA-Z\s&,.-]+)',
        r'Listing Agent[:\s]+([A-Z][a-zA-Z\s&,.-]+)',
        r'Contact[:\s]+([A-Z][a-zA-Z\s&,.-]+)',
    ]
    
    for pattern in manager_keywords:
        match = re.search(pattern, page_text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            name = re.sub(r'\s+(LLC|Inc|Corp|Management|Properties).*$', '', name, flags=re.IGNORECASE)
            if len(name) > 2 and len(name) < 80:
                return name
    
    # Method 2: Zillow-specific selectors
    manager_selectors = [
        '[data-test="agent-name"]',
        '[data-testid="agent-name"]',
        '[class*="agent" i]',
        '[class*="Agent"]',
        '[class*="manager" i]',
        '[class*="Manager"]',
    ]
    
    for selector in manager_selectors:
        try:
            elems = page.query_selector_all(selector)
            for elem in elems:
                try:
                    if elem.is_visible():
                        text = elem.inner_text().strip()
                        if text and len(text) > 2 and len(text) < 80:
                            if (not re.search(r'\d{5}', text) and
                                'zillow.com' not in text.lower() and
                                not re.search(r'\d+\s+[A-Za-z\s]+(?:St|Street|Ave|Avenue)', text)):
                                return text
                except Exception:
                    continue
        except Exception:
            continue
    
    # Method 3: H1/H2
    try:
        h1_elements = page.query_selector_all('h1')
        for h1 in h1_elements:
            try:
                if h1.is_visible():
                    text = h1.inner_text().strip()
                    if (text and len(text) > 2 and len(text) < 80 and
                        not re.search(r'\d{5}', text) and
                        'zillow.com' not in text.lower() and
                        not re.search(r'^\d+\s+[A-Za-z\s]+(?:St|Street|Ave|Avenue)', text)):
                        return text
            except Exception:
                continue
    except Exception:
        pass
    
    if soup:
        headers = soup.find_all(['h1', 'h2'])
        for header in headers:
            text = header.get_text(strip=True)
            if (text and len(text) > 2 and len(text) < 80 and
                not re.search(r'\d{5}', text) and
                'zillow.com' not in text.lower() and
                not re.search(r'^\d+\s+[A-Za-z\s]+(?:St|Street|Ave|Avenue)', text)):
                return text
    
    return None


def extract_manager_name_from_regex(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract manager name using regex fallback."""
    title_text = None
    try:
        title_text = page.title()
    except Exception:
        if soup:
            title = soup.find('title')
            if title:
                title_text = title.get_text()
    
    if title_text:
        match = re.search(r'^([^-|]+)', title_text)
        if match:
            name = match.group(1).strip()
            name = re.sub(r'^(Apartments?|Rentals?|Homes?|Properties?)\s+', '', name, flags=re.IGNORECASE)
            if len(name) > 2 and len(name) < 80 and 'zillow.com' not in name.lower():
                return name
    
    return None


def extract_manager_name(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract manager name using JSON-LD → selectors → regex fallback order."""
    # Method 1: JSON-LD
    json_ld_data = parse_json_ld(soup)
    if json_ld_data.get('name'):
        name = json_ld_data['name']
        if name and len(name) > 2 and len(name) < 80:
            return name
    
    # Method 2: Selectors
    name = extract_manager_name_from_selectors(page, soup)
    if name:
        return name
    
    # Method 3: Regex fallback
    name = extract_manager_name_from_regex(page, soup)
    if name:
        return name
    
    return None


def get_listing_urls_from_search_page(page: Page, url: str) -> List[str]:
    """
    Extract listing detail page URLs from a Zillow search results page.
    Returns empty list if all retries fail.
    """
    listing_urls = []
    
    if not retry_goto(page, url, max_retries=3):
        logger.warning(f"Failed to load search page {url} after all retries, continuing to next page")
        return listing_urls
    
    try:
        try:
            page.wait_for_load_state('domcontentloaded', timeout=10000)
        except Exception:
            pass
        
        page.wait_for_timeout(3000)
        
        # Wait for property cards to appear
        search_selectors = [
            "[data-test='property-card']",
            "[data-testid='property-card']",
            "article[data-test='property-card']",
        ]
        
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
        
        # Scroll multiple times to trigger lazy loading
        for scroll_step in range(8):
            scroll_position = scroll_step * 1000
            page.evaluate(f"window.scrollTo(0, {scroll_position})")
            page.wait_for_timeout(1500)
        
        # Scroll to bottom and back up to trigger more loading
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(3000)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight - 2000)")
        page.wait_for_timeout(2000)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(3000)
        
        # Method 1: Extract from JSON-LD structured data (if available)
        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        
        json_ld_scripts = soup.find_all('script', type='application/ld+json')
        for script in json_ld_scripts:
            try:
                content = script.string
                if not content:
                    continue
                
                data = json.loads(content)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            data = item
                            break
                
                if isinstance(data, dict):
                    # Look for URL in JSON-LD
                    if 'url' in data:
                        url_val = data['url']
                        if isinstance(url_val, str) and '/b/' in url_val:
                            if url_val not in listing_urls:
                                listing_urls.append(url_val)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        
        # Method 2: Extract from property cards using Playwright (most reliable)
        try:
            property_cards = page.query_selector_all('[data-test="property-card"], [data-testid="property-card"]')
            logger.debug(f"Found {len(property_cards)} property cards")
            
            for card in property_cards:
                try:
                    # Find link within the card
                    link = card.query_selector('a[href*="/b/"]')
                    if link:
                        href = link.get_attribute('href')
                        if href:
                            if href.startswith('/'):
                                full_url = urljoin(BASE_URL, href)
                            elif href.startswith('http'):
                                full_url = href
                            else:
                                continue
                            
                            # Normalize URL
                            normalized = normalize_url(full_url)
                            
                            # Filter for valid detail pages: /b/slug/ format, exclude /browse/
                            if (normalized not in listing_urls and
                                normalized.startswith(BASE_URL) and
                                '/b/' in normalized and
                                '/browse/' not in normalized and
                                normalized.count('/b/') == 1):  # Only one /b/ segment
                                listing_urls.append(normalized)
                except Exception as e:
                    logger.debug(f"Error extracting link from card: {e}")
                    continue
        except Exception as e:
            logger.debug(f"Error finding property cards with Playwright: {e}")
        
        # Method 3: Fallback to BeautifulSoup HTML parsing
        if not listing_urls:
            # Find all links with /b/ pattern
            all_links = soup.find_all('a', href=re.compile(r'/b/[^/]+/'))
            seen_urls = set()
            
            for link in all_links:
                href = link.get('href')
                if href:
                    if href.startswith('/'):
                        full_url = urljoin(BASE_URL, href)
                    elif href.startswith('http'):
                        full_url = href
                    else:
                        continue
                    
                    normalized = normalize_url(full_url)
                    
                    if (normalized not in seen_urls and
                        normalized.startswith(BASE_URL) and
                        '/b/' in normalized and
                        '/browse/' not in normalized and
                        normalized.count('/b/') == 1):
                        seen_urls.add(normalized)
                        listing_urls.append(normalized)
        
        # Also try direct Playwright link search as final fallback
        if not listing_urls:
            try:
                playwright_links = page.query_selector_all('a[href*="/b/"]')
                seen_urls = set()
                
                for link in playwright_links:
                    href = link.get_attribute('href')
                    if href:
                        if href.startswith('/'):
                            full_url = urljoin(BASE_URL, href)
                        elif href.startswith('http'):
                            full_url = href
                        else:
                            continue
                        
                        normalized = normalize_url(full_url)
                        
                        if (normalized not in seen_urls and
                            normalized.startswith(BASE_URL) and
                            '/b/' in normalized and
                            '/browse/' not in normalized and
                            normalized.count('/b/') == 1):
                            seen_urls.add(normalized)
                            listing_urls.append(normalized)
            except Exception as e:
                logger.debug(f"Error finding links with Playwright fallback: {e}")
        
        # Remove duplicates while preserving order
        seen = set()
        unique_urls = []
        for url in listing_urls:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)
        
        logger.info(f"Found {len(unique_urls)} unique listings on search page")
        if unique_urls:
            logger.info(f"Sample URLs collected: {unique_urls[:3]}")
        return unique_urls
        
    except Exception as e:
        logger.error(f"Error extracting listing URLs from search page {url}: {e}")
    
    return listing_urls


def get_next_page_url(page: Page) -> Optional[str]:
    """
    Get the next page URL from pagination on the current page.
    Returns None if no next page found.
    """
    try:
        # Zillow pagination - try multiple selectors
        next_selectors = [
            'a[aria-label="Next page"]',
            'a[aria-label="Next"]',
            'a[data-test="pagination-next"]',
            'a[data-testid="pagination-next"]',
            'button[aria-label="Next page"]',
            'button[aria-label="Next"]',
            'a.next',
            '.pagination a:last-child',
            '[class*="Pagination"] a[aria-label*="Next"]',
        ]
        
        for selector in next_selectors:
            try:
                next_link = page.query_selector(selector)
                if next_link:
                    # Check if it's enabled (not disabled)
                    is_disabled = next_link.get_attribute('disabled') or \
                                 next_link.get_attribute('aria-disabled') == 'true' or \
                                 'disabled' in (next_link.get_attribute('class') or '')
                    
                    if not is_disabled:
                        href = next_link.get_attribute('href')
                        if href:
                            if href.startswith('/'):
                                return urljoin(BASE_URL, href)
                            elif href.startswith('http'):
                                return href
                        # If no href, might be a button that triggers navigation
                        # Try clicking and checking URL change (but we'll skip this for now)
            except Exception:
                continue
        
        # Fallback: try to find pagination links in HTML
        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        
        # Look for pagination container
        pagination_containers = soup.find_all(['nav', 'div'], class_=re.compile(r'[Pp]agination'))
        for container in pagination_containers:
            # Look for "Next" link
            next_links = container.find_all('a', string=re.compile(r'Next', re.I))
            if next_links:
                href = next_links[0].get('href')
                if href:
                    if href.startswith('/'):
                        return urljoin(BASE_URL, href)
                    elif href.startswith('http'):
                        return href
        
        # Alternative: look for numbered pagination links and find the next one
        pagination_links = soup.find_all('a', href=re.compile(r'/\d+_p/|page=\d+'))
        if pagination_links:
            # Try to find the current page and get the next one
            current_url = page.url
            for link in pagination_links:
                href = link.get('href')
                if href:
                    full_href = urljoin(BASE_URL, href) if href.startswith('/') else href
                    # If this link's number is higher than current, it might be next
                    # This is a simple heuristic
                    if full_href != current_url and '/browse/' not in full_href:
                        return full_href
        
    except Exception as e:
        logger.debug(f"Error finding next page URL: {e}")
    
    return None


def scrape_listing_detail(page: Page, url: str, store: Store) -> Optional[Dict]:
    """
    Scrape a single Zillow listing detail page and extract data.
    Returns None if all retries fail or no phone found.
    """
    normalized_url = normalize_url(url)
    
    # Skip if already crawled
    if store.is_url_crawled(normalized_url):
        logger.debug(f"Skipping already crawled URL: {normalized_url}")
        return None
    
    logger.info(f"🔵 ATTEMPTING TO NAVIGATE TO: {url}")
    logger.info(f"   Current page URL before navigation: {page.url}")
    
    # Navigate to the detail page
    navigation_success = retry_goto(page, url, max_retries=3)
    
    # Check what happened
    current_url = page.url
    logger.info(f"   Navigation success: {navigation_success}")
    logger.info(f"   Current page URL after navigation: {current_url}")
    
    if not navigation_success:
        logger.warning(f"❌ FAILED to load listing detail page {url} after all retries, skipping")
        store.mark_url_crawled(normalized_url)  # Mark as crawled to avoid retrying
        return None
    
    # Verify we actually navigated to the right place
    if url not in current_url and normalized_url not in current_url:
        logger.warning(f"⚠️  Navigation may have failed - expected URL containing '{url}', but got '{current_url}'")
        # Check if we got redirected to a blocked page
        page_title = page.title()
        if 'denied' in page_title.lower() or 'blocked' in page_title.lower():
            logger.warning(f"   Page appears to be blocked: {page_title}")
            store.mark_url_crawled(normalized_url)
            return None
        # Continue anyway, might be a redirect to a valid page
    else:
        logger.info(f"✅ Successfully navigated to detail page")
    
    try:
        # Wait for page to load - Zillow uses different selectors
        detail_selectors = [
            'h1',
            'address',
            '[data-test="property-card-addr"]',
            '[data-testid="address"]',
            '[class*="PropertyHeader"]',
            '[class*="AddressContainer"]',
            'body',  # Fallback - body should always exist
        ]
        
        waited = False
        for selector in detail_selectors:
            try:
                page.wait_for_selector(selector, timeout=10000, state='visible')
                waited = True
                logger.debug(f"Found selector: {selector}")
                break
            except PlaywrightTimeoutError:
                continue
        
        if not waited:
            logger.warning(f"None of the detail selectors found on {url}, but continuing anyway")
        
        # Additional wait for dynamic content
        page.wait_for_timeout(3000)
        
        # Check if we're on a blocked/error page
        page_title = page.title()
        page_text = page.inner_text('body')[:200] if page.inner_text('body') else ''
        
        if 'denied' in page_title.lower() or 'blocked' in page_title.lower() or 'bot' in page_text.lower():
            logger.warning(f"Page appears to be blocked: {page_title}")
            store.mark_url_crawled(normalized_url)
            return None
        
        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        
        logger.debug(f"Page loaded, HTML length: {len(html)}")
        
        # Extract phone (required)
        phone = extract_phone(page, soup)
        if not phone:
            logger.debug(f"No phone found for {url} - trying to see what's on the page...")
            # Debug: log a snippet of the page to see what we got
            page_snippet = page_text[:500] if page_text else "No text found"
            logger.debug(f"Page text snippet: {page_snippet}")
            store.mark_url_crawled(normalized_url)
            return None
        
        # Extract address (best-effort)
        address = extract_address(page, soup)
        
        # Extract manager name (best-effort)
        manager_name = extract_manager_name(page, soup)
        
        # Mark URL as crawled
        store.mark_url_crawled(normalized_url)
        
        logger.info(f"Extracted: {phone} - {address or 'N/A'} - {manager_name or 'N/A'}")
        
        return {
            'phone': phone,
            'address': address or '',
            'manager_name': manager_name or ''
        }
        
    except Exception as e:
        logger.error(f"Error scraping listing {url}: {e}", exc_info=True)
        store.mark_url_crawled(normalized_url)
        return None


def export_to_csv_incremental(store: Store, output_path: str):
    """
    Export data to CSV incrementally (called after each extraction).
    This ensures we have constant updates even if interrupted.
    """
    try:
        import pandas as pd
        
        phones_data = store.get_all_phones()
        
        if not phones_data:
            return
        
        records = []
        for data in phones_data:
            addresses_str = '; '.join(sorted(data['addresses'])) if data['addresses'] else ''
            
            records.append({
                'phone': data['phone'],
                'manager_name': data['manager_name'] or '',
                'addresses': addresses_str,
                'units': data['units']
            })
        
        df = pd.DataFrame(records)
        df = df.sort_values(by='phone').reset_index(drop=True)
        df.to_csv(output_path, index=False)
        
    except Exception as e:
        logger.debug(f"Could not export CSV incrementally: {e}")


def scrape_city(
    city: str,
    state: str,
    max_pages: int,
    delay: float,
    target_phones: int,
    headless: bool,
    proxy: Optional[str],
    store: Store,
    output_path: str = "zillow_sfr.csv"
) -> None:
    """
    Main scraping function using Playwright.
    Uses one browser + one context for the whole run.
    """
    logger.info(f"Starting scrape for {city}, {state} (max {max_pages} pages, target {target_phones} phones)")

    with sync_playwright() as p:
        browser_options = {
            'headless': headless,
            'args': [
                '--disable-blink-features=AutomationControlled',
                '--disable-features=IsolateOrigins,site-per-process',
                '--disable-site-isolation-trials',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-web-security',
                '--disable-features=VizDisplayCompositor',
            ]
        }
        if proxy:
            browser_options['proxy'] = {'server': proxy}

        browser = p.chromium.launch(**browser_options)
        logger.info("Chromium browser launched")

        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={'width': 1920, 'height': 1080},
            locale='en-US',
            timezone_id='America/New_York',
            java_script_enabled=True,
            bypass_csp=True,
            ignore_https_errors=False,
            extra_http_headers={
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0',
                'Referer': 'https://www.zillow.com/',
            },
        )
        logger.info("Browser context created")

        # Enhanced stealth script to avoid bot detection
        context.add_init_script("""
            // Hide webdriver
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            
            // Override plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            
            // Override languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
            
            // Override permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
            
            // Chrome runtime
            window.chrome = {
                runtime: {}
            };
            
            // Override permissions
            Object.defineProperty(navigator, 'permissions', {
                get: () => ({
                    query: async () => ({ state: 'granted' })
                })
            });
        """)

        results_page = context.new_page()
        detail_page = context.new_page()
        logger.info("Pages created - reusing for entire run")

        try:
            all_listing_urls = []
            city_normalized = city.lower().replace(' ', '-').replace(',', '').replace("'", "")
            state_normalized = state.lower()

            # Start with page 1 URL
            # Zillow rentals URL format: https://www.zillow.com/{city}-{state}/rentals/
            current_url = f"{BASE_URL}/{city_normalized}-{state_normalized}/rentals/"
            page_num = 0
            consecutive_empty_pages = 0

            while page_num < max_pages:
                if store.get_unique_phones_count() >= target_phones:
                    logger.info(f"Reached target of {target_phones} phones, stopping")
                    break

                page_num += 1
                logger.info(f"Fetching page {page_num}: {current_url}")

                listing_urls = get_listing_urls_from_search_page(results_page, current_url)

                if listing_urls:
                    all_listing_urls.extend(listing_urls)
                    consecutive_empty_pages = 0
                    logger.info(f"Found {len(listing_urls)} listings on page {page_num}, total so far: {len(all_listing_urls)}")
                else:
                    consecutive_empty_pages += 1
                    logger.warning(f"No listings found on page {page_num} (consecutive empty: {consecutive_empty_pages})")
                    
                    # If we've had 2 consecutive empty pages, likely no more results
                    if consecutive_empty_pages >= 2:
                        logger.info("Multiple consecutive empty pages, stopping pagination")
                        break

                # Get next page URL
                if page_num < max_pages:
                    next_url = get_next_page_url(results_page)
                    if next_url:
                        current_url = next_url
                        logger.info(f"Next page URL found: {next_url}")
                    else:
                        # Fallback: try to construct next page URL manually
                        # Zillow might use query parameters or different formats
                        # Try common patterns
                        if page_num == 1:
                            # Try page 2 with different formats
                            fallback_urls = [
                                f"{BASE_URL}/{city_normalized}-{state_normalized}/rentals/2_p/",
                                f"{BASE_URL}/{city_normalized}-{state_normalized}/rentals/?page=2",
                            ]
                            logger.info("Trying fallback pagination URLs...")
                            for fallback_url in fallback_urls:
                                # Quick test if this URL exists by checking if we can find listings
                                test_urls = get_listing_urls_from_search_page(results_page, fallback_url)
                                if test_urls:
                                    current_url = fallback_url
                                    logger.info(f"Fallback URL works: {fallback_url}")
                                    break
                            else:
                                logger.info("No next page found and fallback URLs didn't work, stopping pagination")
                                break
                        else:
                            logger.info("No next page found, stopping pagination")
                            break
                    
                    jitter = random.uniform(-0.6, 0.6)
                    wait_time = max(0.1, delay + jitter)
                    time.sleep(wait_time)
                else:
                    break

            logger.info(f"Total listing URLs collected: {len(all_listing_urls)}")
            
            if not all_listing_urls:
                logger.warning("No listing URLs found! Check if the search page selectors are correct.")
                return

            # Scrape each listing detail page
            for i, listing_url in enumerate(all_listing_urls, 1):
                if store.get_unique_phones_count() >= target_phones:
                    logger.info(f"Reached target of {target_phones} phones, stopping")
                    break

                normalized_listing_url = normalize_url(listing_url)
                if store.is_url_crawled(normalized_listing_url):
                    logger.debug(f"Skipping already crawled URL: {normalized_listing_url}")
                    continue

                logger.info(f"\n{'='*80}")
                logger.info(f"Scraping listing {i}/{len(all_listing_urls)}: {listing_url}")
                logger.info(f"{'='*80}")

                listing_data = scrape_listing_detail(detail_page, listing_url, store)

                if listing_data:
                    phone = listing_data['phone']
                    address = listing_data['address']
                    manager_name = listing_data['manager_name']

                    logger.info(f"✓ Successfully extracted data from {listing_url}")
                    logger.info(f"  Phone: {phone}")
                    logger.info(f"  Address: {address or 'N/A'}")
                    logger.info(f"  Manager: {manager_name or 'N/A'}")

                    store.upsert_phone(phone, manager_name)

                    if address:
                        store.add_address(phone, address)

                    logger.info(f"Progress: {store.get_unique_phones_count()}/{target_phones} unique phones")

                    # Export to CSV incrementally
                    try:
                        export_to_csv_incremental(store, output_path)
                        logger.debug(f"CSV updated with {store.get_unique_phones_count()} phones")
                    except Exception as e:
                        logger.debug(f"Could not export CSV incrementally: {e}")
                else:
                    logger.warning(f"✗ Failed to extract data from {listing_url} (no phone found or navigation failed)")

                if i < len(all_listing_urls):
                    jitter = random.uniform(-0.6, 0.6)
                    wait_time = max(0.1, delay + jitter)
                    time.sleep(wait_time)

        finally:
            results_page.close()
            detail_page.close()
            browser.close()

    logger.info("Scraping completed")

