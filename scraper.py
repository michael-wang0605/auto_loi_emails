"""
Zillow Property Manager Scraper
Scrapes active listings from Zillow and extracts property manager phone numbers.
"""
import time
import re
import csv
from typing import List, Dict, Set
from urllib.parse import quote_plus, urljoin
import requests
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import config


class PropertyManagerScraper:
    def __init__(self):
        self.driver = None
        self.property_managers: List[Dict] = []
        self.phones_found: Set[str] = set()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': config.USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        })

    def init_driver(self):
        """Initialize Chrome driver with undetected-chromedriver to avoid detection"""
        def create_options():
            options = uc.ChromeOptions()
            if config.HEADLESS:
                options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument(f'user-agent={config.USER_AGENT}')
            return options
        
        # Try with explicit Chrome version 141 (matching current Chrome browser version)
        try:
            self.driver = uc.Chrome(options=create_options(), version_main=141)
        except Exception as e1:
            print(f"Warning: Could not initialize with version 141: {e1}")
            # Try without specifying version (let it auto-detect)
            try:
                self.driver = uc.Chrome(options=create_options())
            except Exception as e2:
                print(f"Error: Could not initialize Chrome driver: {e2}")
                raise
        
        self.driver.set_page_load_timeout(config.PAGE_LOAD_TIMEOUT)

    def close_driver(self):
        """Close the browser driver"""
        if self.driver:
            self.driver.quit()
            self.driver = None

    def search_zillow_listings(self, location: str) -> List[str]:
        """
        Search Zillow for active rental listings in the specified location
        Returns list of listing URLs
        """
        listing_urls = []
        
        # Construct Zillow search URL
        # Zillow uses different URL patterns for rentals vs sales
        # For rentals: https://www.zillow.com/{location}/rentals/
        # Try a simpler URL format first
        location_encoded = location.replace(' ', '-').replace(',', '').strip()
        base_url = f"https://www.zillow.com/{location_encoded.lower()}/rentals/"
        
        # Alternative: try with query parameters
        # base_url = f"https://www.zillow.com/homes/for_rent/{location_encoded}/"
        
        print(f"Searching Zillow for rentals in: {location}")
        print(f"URL: {base_url}")
        
        try:
            if not self.driver:
                self.init_driver()
            
            print(f"Loading page: {base_url}")
            self.driver.get(base_url)
            
            # Wait for page to load with explicit wait
            try:
                WebDriverWait(self.driver, 15).until(
                    lambda d: d.execute_script('return document.readyState') == 'complete'
                )
            except:
                pass
            
            time.sleep(5)  # Additional wait for dynamic content
            
            # Debug: Save page title to verify we're on the right page
            try:
                page_title = self.driver.title
                print(f"Page title: {page_title}")
                
                # Debug: Check page source length
                page_source = self.driver.page_source
                print(f"Page source length: {len(page_source)} characters")
                
                # Check if we got blocked or redirected
                current_url = self.driver.current_url
                if 'zillow.com' not in current_url.lower():
                    print(f"Warning: Redirected to {current_url}")
                elif 'captcha' in page_source.lower() or 'bot' in page_source.lower():
                    print("Warning: Possible bot detection or CAPTCHA present")
            except Exception as debug_error:
                print(f"Debug info error: {debug_error}")
            
            # Handle potential cookie banners or modals
            try:
                # Try to dismiss any modals or cookie banners
                close_buttons = self.driver.find_elements(
                    By.CSS_SELECTOR, 
                    'button[aria-label*="close" i], button[aria-label*="dismiss" i], .close, [data-test*="close"]'
                )
                for btn in close_buttons[:3]:  # Try first few close buttons
                    try:
                        btn.click()
                        time.sleep(1)
                    except:
                        pass
            except:
                pass
            
            # Try to find and click "Show more" or scroll to load more listings
            scroll_attempts = 0
            max_scrolls = 8
            
            while scroll_attempts < max_scrolls and len(listing_urls) < config.MAX_LISTINGS:
                # Scroll gradually to trigger lazy loading
                scroll_position = 0
                for i in range(3):
                    scroll_position += 500
                    self.driver.execute_script(f"window.scrollTo(0, {scroll_position});")
                    time.sleep(1)
                
                # Scroll to bottom
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(4)  # Wait longer for listings to load
                
                # Try multiple selectors for listing links
                selectors = [
                    'a[data-test="property-card-link"]',
                    'a[href*="/homedetails/"]',
                    'a[href*="/b/"]',
                    'article a[href*="/homedetails/"]',
                    '[data-test="property-card"] a',
                    '.property-card-data a',
                    'a[href^="/homedetails/"]'
                ]
                
                for selector in selectors:
                    try:
                        listing_elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        for element in listing_elements:
                            href = element.get_attribute('href')
                            if href:
                                # Only include actual property listing pages, not browse pages
                                if '/homedetails/' in href and '/browse/' not in href:
                                    full_url = urljoin('https://www.zillow.com', href) if not href.startswith('http') else href
                                    # Make sure it's not a duplicate and is a real listing URL
                                    if full_url not in listing_urls and '/homedetails/' in full_url:
                                        listing_urls.append(full_url)
                                        if len(listing_urls) >= config.MAX_LISTINGS:
                                            break
                        if len(listing_urls) >= config.MAX_LISTINGS:
                            break
                    except:
                        continue
                
                scroll_attempts += 1
                print(f"Found {len(listing_urls)} listings so far...")
                
                # If we found some listings, scroll a bit more to see if more load
                if len(listing_urls) > 0:
                    # Scroll up a bit then down to trigger lazy loading
                    self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight - 1000);")
                    time.sleep(2)
                    self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(2)
            
            print(f"Total listings found: {len(listing_urls)}")
            return listing_urls[:config.MAX_LISTINGS]
            
        except Exception as e:
            print(f"Error searching Zillow: {e}")
            return listing_urls

    def extract_phone_numbers(self, text: str) -> Set[str]:
        """Extract phone numbers from text using multiple patterns"""
        phones = set()
        
        # Pattern 1: (XXX) XXX-XXXX or XXX-XXX-XXXX or XXX.XXX.XXXX
        pattern1 = r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'
        matches = re.findall(pattern1, text)
        for match in matches:
            # Clean and normalize phone number
            cleaned = re.sub(r'[^\d]', '', match)
            if len(cleaned) == 10:  # Valid US phone number length
                phones.add(cleaned)
        
        # Pattern 2: +1 XXX XXX XXXX or 1-XXX-XXX-XXXX
        pattern2 = r'\+?1[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'
        matches = re.findall(pattern2, text)
        for match in matches:
            cleaned = re.sub(r'[^\d]', '', match)
            if cleaned.startswith('1') and len(cleaned) == 11:
                phones.add(cleaned[1:])  # Remove leading 1
            elif len(cleaned) == 10:
                phones.add(cleaned)
        
        return phones
    
    def normalize_phone(self, phone: str) -> str:
        """Normalize phone number to (XXX) XXX-XXXX format"""
        # Remove all non-digits
        digits = re.sub(r'[^\d]', '', phone)
        
        # Remove leading 1 if present
        if len(digits) == 11 and digits[0] == '1':
            digits = digits[1:]
        
        # Format as (XXX) XXX-XXXX
        if len(digits) == 10:
            return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
        return phone  # Return original if can't normalize

    def scrape_listing_page(self, url: str) -> Dict:
        """
        Scrape individual listing page for property manager phone numbers
        Returns dictionary with property manager details
        """
        result = {
            'listing_url': url,
            'property_address': '',
            'phone_number': '',
            'property_manager': '',
            'company': '',
            'listing_price': ''
        }
        
        try:
            print(f"Scraping: {url}")
            
            if not self.driver:
                self.init_driver()
            
            self.driver.get(url)
            time.sleep(3)  # Wait for page to load
            
            # Get page source
            page_source = self.driver.page_source
            soup = BeautifulSoup(page_source, 'lxml')
            page_text = soup.get_text()
            
            # Try to find property address
            address_selectors = [
                'h1[data-test="property-card-addr"]',
                '.PropertyHeaderContainer h1',
                'h1.address',
                '[data-testid="address"]',
                'address'
            ]
            for selector in address_selectors:
                try:
                    address_elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                    result['property_address'] = address_elem.text.strip()
                    break
                except:
                    continue
            
            # Try to find listing price
            price_selectors = [
                '[data-test="price"]',
                '.ds-price',
                '[class*="Price"]',
                '[data-testid="price"]'
            ]
            for selector in price_selectors:
                try:
                    price_elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                    result['listing_price'] = price_elem.text.strip()
                    break
                except:
                    continue
            
            # Collect phone numbers from multiple sources
            all_phones = set()
            
            # Source 1: Look for tel: links
            try:
                tel_links = self.driver.find_elements(By.CSS_SELECTOR, 'a[href^="tel:"]')
                for link in tel_links:
                    href = link.get_attribute('href')
                    if href:
                        phone = href.replace('tel:', '').replace('+1', '').strip()
                        cleaned = re.sub(r'[^\d]', '', phone)
                        if len(cleaned) == 10:
                            all_phones.add(self.normalize_phone(cleaned))
            except:
                pass
            
            # Source 2: Look for contact/agent sections
            contact_selectors = [
                'div[data-test="contact-box"]',
                '[data-test="contact-info"]',
                '.contact-info',
                '.agent-info',
                '.property-manager',
                '[class*="Contact"]',
                '[class*="Agent"]',
                '[class*="Manager"]',
                'div[class*="leasing"]',
                '[id*="contact"]',
                '[id*="agent"]'
            ]
            
            contact_texts = []
            for selector in contact_selectors:
                try:
                    contact_elems = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for elem in contact_elems:
                        contact_texts.append(elem.text)
                except:
                    continue
            
            # Extract phones from contact sections
            for text in contact_texts:
                phones = self.extract_phone_numbers(text)
                all_phones.update(phones)
            
            # Source 3: Extract from entire page text
            page_phones = self.extract_phone_numbers(page_text)
            all_phones.update(page_phones)
            
            # Source 4: Look for click-to-call buttons
            try:
                call_buttons = self.driver.find_elements(
                    By.CSS_SELECTOR,
                    'button[data-test*="call"], button[aria-label*="call" i], a[aria-label*="call" i]'
                )
                for button in call_buttons:
                    text = button.text
                    phones = self.extract_phone_numbers(text)
                    all_phones.update(phones)
            except:
                pass
            
            # Normalize and format phone numbers
            normalized_phones = []
            for phone in all_phones:
                normalized = self.normalize_phone(phone)
                normalized_phones.append(normalized)
                self.phones_found.add(phone)
            
            # Store the first (primary) phone number
            if normalized_phones:
                result['phone_number'] = normalized_phones[0]
                if len(normalized_phones) > 1:
                    # If multiple phones found, note it
                    result['phone_number'] += f" (found {len(normalized_phones)} total)"
            
            # Try to find property manager name or company
            manager_keywords = ['property manager', 'leasing', 'management', 'manager', 'agent', 'landlord']
            for keyword in manager_keywords:
                pattern = re.compile(rf'{keyword}[:\s]+([A-Z][a-zA-Z\s&,.-]+)', re.IGNORECASE)
                match = pattern.search(page_text)
                if match:
                    name = match.group(1).strip()
                    # Clean up the name (remove common suffixes)
                    name = re.sub(r'\s+(LLC|Inc|Corp|Management).*$', '', name, flags=re.IGNORECASE)
                    result['property_manager'] = name[:100]  # Limit length
                    break
            
            # Extract company name
            company_patterns = [
                r'([A-Z][a-zA-Z\s&,.-]+)\s*(?:LLC|Inc|Corp|Management|Properties|Real Estate)',
                r'(?:LLC|Inc|Corp|Management|Properties|Real Estate)\s+([A-Z][a-zA-Z\s&,.-]+)'
            ]
            for pattern_str in company_patterns:
                match = re.search(pattern_str, page_text)
                if match:
                    result['company'] = match.group(1).strip()[:100]  # Limit length
                    break
            
            time.sleep(config.DELAY_BETWEEN_REQUESTS)  # Be respectful with rate limiting
            
        except TimeoutException:
            print(f"Timeout loading page: {url}")
        except Exception as e:
            print(f"Error scraping {url}: {e}")
        
        return result

    def run_scraper(self, location: str = None):
        """Main method to run the scraper"""
        location = location or config.LOCATION
        
        try:
            print("=" * 60)
            print("Property Manager Scraper - Starting")
            print("=" * 60)
            
            # Initialize driver
            self.init_driver()
            
            # Search for listings
            listing_urls = self.search_zillow_listings(location)
            
            if not listing_urls:
                print("No listings found. You may need to adjust your search location.")
                return
            
            # Scrape each listing
            print(f"\nScraping {len(listing_urls)} listings for property manager phone numbers...")
            for i, url in enumerate(listing_urls, 1):
                print(f"\n[{i}/{len(listing_urls)}]", end=' ')
                pm_info = self.scrape_listing_page(url)
                # Include listings with phone numbers
                if pm_info.get('phone_number'):
                    self.property_managers.append(pm_info)
                    print(f"✓ Found phone: {pm_info.get('phone_number')}")
                else:
                    print("✗ No phone found")
            
            # Save results
            self.save_results()
            
            print("\n" + "=" * 60)
            print("Scraping Complete!")
            print(f"Total listings with phone numbers: {len(self.property_managers)}")
            print(f"Unique phone numbers found: {len(self.phones_found)}")
            print("=" * 60)
            
        finally:
            self.close_driver()

    def save_results(self):
        """Save results to CSV file"""
        if self.property_managers:
            with open(config.OUTPUT_FILE, 'w', newline='', encoding='utf-8') as f:
                fieldnames = ['property_address', 'phone_number', 'property_manager', 
                             'company', 'listing_price', 'listing_url']
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                
                # Write rows, ensuring all fields are present
                for pm in self.property_managers:
                    row = {field: pm.get(field, '') for field in fieldnames}
                    writer.writerow(row)
            
            print(f"\nResults saved to: {config.OUTPUT_FILE}")
            print(f"CSV contains {len(self.property_managers)} entries with phone numbers")
        else:
            print("\nNo phone numbers found to save.")


if __name__ == "__main__":
    scraper = PropertyManagerScraper()
    scraper.run_scraper()

