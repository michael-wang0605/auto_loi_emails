"""
Master CSV Combiner

Combines data from both Apartments.com and Zillow scrapers into a single master CSV.
Indicates which source each phone came from (or "both" if it appears in both sources).

Usage:
    python -m src.combine --apartments apartments_sfr.csv --zillow zillow_sfr.csv --output master_sfr.csv
"""
import argparse
import logging
from pathlib import Path
from typing import Dict, Set

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_csv(file_path: str) -> pd.DataFrame:
    """Load CSV file and return DataFrame."""
    if not Path(file_path).exists():
        logger.warning(f"File not found: {file_path}, skipping")
        return pd.DataFrame()
    
    try:
        df = pd.read_csv(file_path)
        logger.info(f"Loaded {len(df)} rows from {file_path}")
        return df
    except Exception as e:
        logger.error(f"Error loading {file_path}: {e}")
        return pd.DataFrame()


def combine_sources(
    apartments_csv: str,
    zillow_csv: str,
    output_csv: str
) -> None:
    """
    Combine data from both sources into a master CSV.
    Adds a 'source' column indicating 'apartments', 'zillow', or 'both'.
    """
    logger.info("=" * 80)
    logger.info("MASTER CSV COMBINER")
    logger.info("=" * 80)
    
    # Load both CSVs
    apartments_df = load_csv(apartments_csv)
    zillow_df = load_csv(zillow_csv)
    
    if apartments_df.empty and zillow_df.empty:
        logger.error("No data found in either CSV file")
        return
    
    # Track which phones come from which source
    phone_sources: Dict[str, Set[str]] = {}
    phone_data: Dict[str, Dict] = {}
    
    # Process Apartments.com data
    if not apartments_df.empty:
        logger.info(f"Processing {len(apartments_df)} rows from Apartments.com")
        for _, row in apartments_df.iterrows():
            phone = str(row['phone']).strip()
            if not phone:
                continue
            
            phone_sources.setdefault(phone, set()).add('apartments')
            
            # Store data (prefer first occurrence, but merge addresses)
            if phone not in phone_data:
                phone_data[phone] = {
                    'phone': phone,
                    'manager_name': str(row.get('manager_name', '') or '').strip(),
                    'addresses': set(),
                    'units': 0
                }
            
            # Merge addresses
            addresses_str = str(row.get('addresses', '') or '').strip()
            if addresses_str:
                addresses = [addr.strip() for addr in addresses_str.split(';') if addr.strip()]
                phone_data[phone]['addresses'].update(addresses)
            
            # Update manager_name if we don't have one
            if not phone_data[phone]['manager_name']:
                phone_data[phone]['manager_name'] = str(row.get('manager_name', '') or '').strip()
    
    # Process Zillow data
    if not zillow_df.empty:
        logger.info(f"Processing {len(zillow_df)} rows from Zillow")
        for _, row in zillow_df.iterrows():
            phone = str(row['phone']).strip()
            if not phone:
                continue
            
            phone_sources.setdefault(phone, set()).add('zillow')
            
            # Store data (merge with existing if phone already exists)
            if phone not in phone_data:
                phone_data[phone] = {
                    'phone': phone,
                    'manager_name': str(row.get('manager_name', '') or '').strip(),
                    'addresses': set(),
                    'units': 0
                }
            else:
                # Merge manager_name if we don't have one
                if not phone_data[phone]['manager_name']:
                    phone_data[phone]['manager_name'] = str(row.get('manager_name', '') or '').strip()
            
            # Merge addresses
            addresses_str = str(row.get('addresses', '') or '').strip()
            if addresses_str:
                addresses = [addr.strip() for addr in addresses_str.split(';') if addr.strip()]
                phone_data[phone]['addresses'].update(addresses)
    
    # Build final records
    records = []
    for phone, data in sorted(phone_data.items()):
        sources = phone_sources.get(phone, set())
        
        # Determine source string
        if 'apartments' in sources and 'zillow' in sources:
            source_str = 'both'
        elif 'apartments' in sources:
            source_str = 'apartments'
        elif 'zillow' in sources:
            source_str = 'zillow'
        else:
            source_str = 'unknown'
        
        # Calculate units (number of unique addresses)
        addresses_list = sorted(data['addresses'])
        units = len(addresses_list)
        
        # Join addresses with semicolon
        addresses_str = '; '.join(addresses_list) if addresses_list else ''
        
        records.append({
            'phone': phone,
            'manager_name': data['manager_name'] or '',
            'addresses': addresses_str,
            'units': units,
            'source': source_str
        })
    
    # Create DataFrame and sort by phone
    df = pd.DataFrame(records)
    df = df.sort_values(by='phone').reset_index(drop=True)
    
    # Write to CSV
    df.to_csv(output_csv, index=False)
    logger.info(f"Exported {len(records)} records to {output_csv}")
    
    # Print summary
    print("\n" + "=" * 80)
    print("COMBINED SUMMARY")
    print("=" * 80)
    print(f"Total unique phones: {len(records)}")
    
    apartments_only = sum(1 for r in records if r['source'] == 'apartments')
    zillow_only = sum(1 for r in records if r['source'] == 'zillow')
    both_sources = sum(1 for r in records if r['source'] == 'both')
    
    print(f"  - Apartments.com only: {apartments_only}")
    print(f"  - Zillow only: {zillow_only}")
    print(f"  - Both sources: {both_sources}")
    
    total_addresses = sum(len(r['addresses'].split(';')) if r['addresses'] else 0 for r in records)
    print(f"Total addresses aggregated: {total_addresses}")
    
    print("\nPreview (first 10 rows):")
    print("-" * 80)
    print(df.head(10).to_string(index=False))
    print("=" * 80)
    print(f"\nFull output saved to: {output_csv}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Combine Apartments.com and Zillow CSV files into a master CSV',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.combine --apartments apartments_sfr.csv --zillow zillow_sfr.csv --output master_sfr.csv
  python -m src.combine --apartments apartments_sfr.csv --zillow zillow_sfr.csv
        """
    )
    
    parser.add_argument(
        '--apartments',
        type=str,
        default='apartments_sfr.csv',
        help='Path to Apartments.com CSV file (default: apartments_sfr.csv)'
    )
    parser.add_argument(
        '--zillow',
        type=str,
        default='zillow_sfr.csv',
        help='Path to Zillow CSV file (default: zillow_sfr.csv)'
    )
    parser.add_argument(
        '--output',
        type=str,
        default='master_sfr.csv',
        help='Output master CSV file path (default: master_sfr.csv)'
    )
    
    args = parser.parse_args()
    
    try:
        combine_sources(
            apartments_csv=args.apartments,
            zillow_csv=args.zillow,
            output_csv=args.output
        )
        logger.info("Combining completed successfully")
    except Exception as e:
        logger.error(f"Combining failed: {e}", exc_info=True)
        import sys
        sys.exit(1)


if __name__ == "__main__":
    main()

