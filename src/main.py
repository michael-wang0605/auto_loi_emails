"""
Apartments.com Single-Family Rental (SFR) Scraper - Entry Point

CLI usage:
    python -m src.main --city "Atlanta" --state "GA" --max_pages 40 --target_phones 200 --delay 3.0 --headless true --output apartments_sfr.csv
"""
import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from src.scraper import scrape_city
from src.store import Store

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def parse_bool(value: str) -> bool:
    """Parse string boolean value."""
    return value.lower() in ('true', '1', 'yes', 'on')


def export_to_csv(store: Store, output_path: str):
    """
    Export aggregated data to CSV.
    Columns: phone, manager_name, addresses, units
    """
    logger.info("Exporting data to CSV...")
    
    # Get all phones with their data
    phones_data = store.get_all_phones()
    
    if not phones_data:
        logger.warning("No data to export")
        return

    # Prepare data for CSV
    records = []
    for data in phones_data:
        # Join addresses with semicolon, deterministic order (sorted)
        addresses_str = '; '.join(sorted(data['addresses'])) if data['addresses'] else ''
        
        records.append({
            'phone': data['phone'],
            'manager_name': data['manager_name'] or '',
            'addresses': addresses_str,
            'units': data['units']
        })

    # Create DataFrame and sort by phone ascending
    df = pd.DataFrame(records)
    df = df.sort_values(by='phone').reset_index(drop=True)

    # Write to CSV
    df.to_csv(output_path, index=False)
    logger.info(f"Exported {len(records)} records to {output_path}")
    
    # Print summary
    print("\n" + "=" * 80)
    print("SCRAPING SUMMARY")
    print("=" * 80)
    print(f"Unique phones found: {len(records)}")
    
    total_addresses = sum(len(data['addresses']) for data in phones_data)
    print(f"Total addresses aggregated: {total_addresses}")
    
    print("\nPreview (first 5 rows):")
    print("-" * 80)
    print(df.head(5).to_string(index=False))
    print("=" * 80)
    print(f"\nFull output saved to: {output_path}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Scrape Apartments.com for single-family rental listings',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.main --city "Atlanta" --state "GA" --max_pages 40 --target_phones 200 --delay 3.0 --headless true --output apartments_sfr.csv
  python -m src.main --city "Atlanta" --state "GA" --max_pages 50 --target_phones 200 --delay 3.0 --headless false
        """
    )
    
    parser.add_argument(
        '--city',
        type=str,
        required=True,
        help='City name (e.g., "Atlanta")'
    )
    parser.add_argument(
        '--state',
        type=str,
        required=True,
        help='State abbreviation (e.g., "GA")'
    )
    parser.add_argument(
        '--max_pages',
        type=int,
        default=50,
        help='Maximum number of search result pages to scrape (default: 50)'
    )
    parser.add_argument(
        '--target_phones',
        type=int,
        default=200,
        help='Stop when this many unique phone keys are stored (default: 200)'
    )
    parser.add_argument(
        '--delay',
        type=float,
        default=3.0,
        help='Base seconds between navigations; adds ±0.6s jitter (default: 3.0)'
    )
    parser.add_argument(
        '--headless',
        type=str,
        default='true',
        help='Run browser in headless mode: true|false (default: true)'
    )
    parser.add_argument(
        '--proxy',
        type=str,
        default='',
        help='Optional HTTP proxy string; if empty, no proxy (default: empty)'
    )
    parser.add_argument(
        '--output',
        type=str,
        default='apartments_sfr.csv',
        help='Output CSV file path (default: apartments_sfr.csv)'
    )
    
    args = parser.parse_args()
    
    # Parse headless boolean
    headless = parse_bool(args.headless)
    
    # Parse proxy (empty string means no proxy)
    proxy = args.proxy.strip() if args.proxy.strip() else None
    
    # Validate inputs
    if args.max_pages < 1:
        logger.error("--max_pages must be at least 1")
        sys.exit(1)
    
    if args.target_phones < 1:
        logger.error("--target_phones must be at least 1")
        sys.exit(1)
    
    if args.delay < 0:
        logger.error("--delay must be non-negative")
        sys.exit(1)
    
    # Initialize store
    db_path = "data.db"
    store = Store(db_path)
    
    try:
        logger.info("=" * 80)
        logger.info("APARTMENTS.COM SFR SCRAPER")
        logger.info("=" * 80)
        logger.info(f"City: {args.city}")
        logger.info(f"State: {args.state}")
        logger.info(f"Max pages: {args.max_pages}")
        logger.info(f"Target phones: {args.target_phones}")
        logger.info(f"Delay: {args.delay}s (±0.6s jitter)")
        logger.info(f"Headless: {headless}")
        logger.info(f"Proxy: {proxy or 'None'}")
        logger.info(f"Output: {args.output}")
        logger.info(f"Database: {db_path}")
        logger.info("=" * 80)
        
        # Check existing progress
        existing_phones = store.get_unique_phones_count()
        if existing_phones > 0:
            logger.info(f"Resuming: Found {existing_phones} phones in database")
        
        # Run scraper
        scrape_city(
            city=args.city,
            state=args.state,
            max_pages=args.max_pages,
            delay=args.delay,
            target_phones=args.target_phones,
            headless=headless,
            proxy=proxy,
            store=store,
            output_path=args.output
        )
        
        # Export to CSV
        export_to_csv(store, args.output)
        
        logger.info("Scraping completed successfully")
        
    except KeyboardInterrupt:
        logger.info("\nScraping interrupted by user")
        logger.info("Progress saved to database. Re-run to resume.")
        # Export partial results
        export_to_csv(store, args.output)
        sys.exit(0)
    except Exception as e:
        logger.error(f"Scraping failed: {e}", exc_info=True)
        # Export partial results on error
        export_to_csv(store, args.output)
        sys.exit(1)
    finally:
        store.close()


if __name__ == "__main__":
    main()
