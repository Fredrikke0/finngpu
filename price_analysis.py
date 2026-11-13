import pandas as pd
import argparse
import re
from datetime import datetime
from pathlib import Path

from config import BASE_DIR, DATA_DIR, OUTPUT_DIR
from deal_analysis import analyze_deals

def parse_args():
    parser = argparse.ArgumentParser(
        description="Compare GPU deals with Finn medians, current retail prices and launch metadata.",
        epilog="Example usage:\n"
               "  %(prog)s                                                     # Use latest listings and all three signals\n"
               "  %(prog)s --brand nvidia --max-price 5000                      # Filter after computing market medians\n"
               "  %(prog)s --sort retail --all-listings                         # Compare every listing with retail\n"
               "  %(prog)s -p performance.csv                                  # Legacy FPS mode\n",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("-f", "--file",
                       help="Path to specific CSV file containing GPU listings. If not provided, uses most recent finn_gpu_listings_*.csv")
    parser.add_argument("-p", "--performance",
                       help="Use legacy FPS analysis with this performance CSV instead of deal signals")
    parser.add_argument("--metadata", type=Path, default=BASE_DIR / "gpu_metadata.json")
    parser.add_argument("--retail", type=Path, default=DATA_DIR / "retail_prices.json")
    parser.add_argument("--history", type=Path,
                       help="Historical-low snapshot (default: data/historical_prices.json when available)")
    parser.add_argument("--history-max-age-days", type=float, default=7,
                       help="Suppress historical references older than this (default: 7 days)")
    parser.add_argument("--min-samples", type=int, default=5,
                       help="Minimum unique same-model listings for a median discount (default: 5)")
    parser.add_argument("--retail-max-age-days", type=float, default=7,
                       help="Suppress retail discounts older than this (default: 7 days)")
    parser.add_argument("--all-listings", action="store_true",
                       help="Show all eligible listings, not only the cheapest per model")
    parser.add_argument("--sort", choices=["median", "retail", "price"], default="median")
    parser.add_argument("-c", "--csv", dest="output_csv", metavar="OUTPUT_FILE",
                       help="Output results to a CSV file instead of console")

    # Add new filtering arguments
    filter_group = parser.add_argument_group('filtering options')
    filter_group.add_argument("--brand",
                            help="Filter by brand (e.g., 'nvidia' or 'amd')",
                            type=str.lower)
    filter_group.add_argument("--min-price",
                            help="Minimum price to include",
                            type=float)
    filter_group.add_argument("--max-price",
                            help="Maximum price to include",
                            type=float)
    filter_group.add_argument("--min-fps",
                            help="Minimum 1440p FPS to include",
                            type=float)
    filter_group.add_argument("--max-fps",
                            help="Maximum 1440p FPS to include",
                            type=float)
    filter_group.add_argument("--exclude-models",
                            help="Comma-separated literal model fragments to exclude (e.g., '3060,6700')",
                            type=str)
    return parser.parse_args()

def find_latest_gpu_listings():
    files = {
        file
        for directory in {Path.cwd(), DATA_DIR, OUTPUT_DIR}
        for file in directory.glob("finn_gpu_listings_*.csv")
    }

    if not files:
        raise FileNotFoundError("No GPU listing files found matching pattern 'finn_gpu_listings_*.csv'")

    dated_files = []
    for file in files:
        date_match = re.fullmatch(r'finn_gpu_listings_(\d{4}-\d{2}-\d{2})\.csv', file.name)
        if not date_match:
            continue
        try:
            file_date = datetime.strptime(date_match.group(1), '%Y-%m-%d')
        except ValueError:
            continue
        dated_files.append((file_date, str(file)))

    if not dated_files:
        raise ValueError("No valid dated GPU listing files found")

    latest_file = max(dated_files)[1]
    print(f"Using most recent GPU listings file: {latest_file}")
    return latest_file

def parse_gpu_listings(filename):
    # Read the CSV file
    df = pd.read_csv(filename)

    # Keep ignored cards and multi-card listings out of price comparisons.
    df = df[~df['model'].isin(['Unknown', 'Multi', 'Ignored'])]

    # Find the index of the minimum price for each model
    idx = df.groupby('model')['price'].idxmin()

    # Select the rows with the minimum price for each model
    cheapest_prices = df.loc[idx, ['model', 'price', 'canonical_url']].reset_index(drop=True)

    return cheapest_prices

def apply_filters(df, args):
    """Apply filters based on command line arguments."""
    filtered_df = df.copy()

    if args.brand:
        # Convert model names to lowercase for case-insensitive comparison
        brand = "geforce" if args.brand == "nvidia" else "radeon" if args.brand == "amd" else args.brand
        filtered_df = filtered_df[filtered_df['model'].str.lower().str.contains(brand, regex=False)]

    if args.min_price is not None:
        filtered_df = filtered_df[filtered_df['price'] >= args.min_price]

    if args.max_price is not None:
        filtered_df = filtered_df[filtered_df['price'] <= args.max_price]

    if args.min_fps is not None:
        filtered_df = filtered_df[filtered_df['1440p Ultra FPS'] >= args.min_fps]

    if args.max_fps is not None:
        filtered_df = filtered_df[filtered_df['1440p Ultra FPS'] <= args.max_fps]

    if args.exclude_models:
        exclude_patterns = [
            re.escape(pattern.strip())
            for pattern in args.exclude_models.split(',') if pattern.strip()
        ]
        if exclude_patterns:
            mask = ~filtered_df['model'].str.contains('|'.join(exclude_patterns), case=False)
            filtered_df = filtered_df[mask]

    return filtered_df

def load_and_process_data(listings_file, performance_file, args):
    # Get the cheapest price for each GPU model
    cheapest_df = parse_gpu_listings(listings_file)

    # Load the 1440p performance data
    performance_df = pd.read_csv(performance_file)

    # Merge the dataframes based on exact model names
    merged_df = pd.merge(cheapest_df, performance_df,
                        left_on='model',
                        right_on='Graphics Card',
                        how='inner')

    # Print unmatched models for debugging
    unmatched_models = cheapest_df[~cheapest_df['model'].isin(merged_df['model'])]
    if not unmatched_models.empty:
        print("\nWarning: The following models from listings didn't match performance data:")
        for model in unmatched_models['model']:
            print(f"- {model}")

    # Calculate price per FPS and round to 1 decimal place
    merged_df['Price per FPS'] = (merged_df['price'] / merged_df['1440p Ultra FPS']).round(1)

    # Apply filters
    filtered_df = apply_filters(merged_df, args)

    # Sort by Price per FPS
    sorted_df = filtered_df.sort_values('Price per FPS')

    # Clean up the result
    result_df = sorted_df[['model', 'price', '1440p Ultra FPS', 'Price per FPS', 'canonical_url']]

    return result_df

def display_results(df, output_csv=None):
    if df.empty:
        print("\nNo results found matching the specified filters.")
        return

    if output_csv:
        # Output to CSV file
        df.to_csv(output_csv, index=False)
        print(f"Results have been saved to {output_csv}")
    else:
        # Print the table header
        print("\nResults:")
        print(f"{'Model':<40}{'Price':>10}{'1440p FPS':>15}{'Price per FPS':>20}{'URL':<100}")
        print("-" * 185)

        # Print each row of the table with Price per FPS rounded to 1 decimal
        for _, row in df.iterrows():
            print(f"{row['model']:<40}{row['price']:>10.2f}{row['1440p Ultra FPS']:>15.2f}{row['Price per FPS']:>20.1f}{row['canonical_url']:<100}")

def main():
    args = parse_args()

    # Get the input file, either from argument or by finding the latest
    try:
        input_file = args.file if args.file else find_latest_gpu_listings()
        # Load and process the data
        if args.performance:
            result_df = load_and_process_data(input_file, args.performance, args)

            display_results(result_df, args.output_csv)
        else:
            if args.min_fps is not None or args.max_fps is not None:
                raise ValueError("FPS filters require --performance")
            history = args.history or DATA_DIR / "historical_prices.json"
            if not history.exists() and args.history is None:
                print("Warning: historical prices unavailable; run historical_prices.py to populate Historical low")
                history = None
            result_df = analyze_deals(
                input_file, args.metadata, args.retail, args.min_samples,
                args.retail_max_age_days, args.all_listings,
                history_file=history, history_max_age_days=args.history_max_age_days,
            )
            result_df = apply_filters(result_df, args)
            sort_column = {"median": "median_discount_pct", "retail": "retail_discount_pct", "price": "price"}[args.sort]
            result_df = result_df.sort_values(
                [sort_column, "model"], ascending=[args.sort == "price", True], na_position="last"
            )
            if args.output_csv:
                result_df.to_csv(args.output_csv, index=False)
                print(f"Results have been saved to {args.output_csv}")
            else:
                print(result_df[[
                    "model", "price", "median_nok", "sample_count",
                    "median_discount_pct", "retail_nok", "retail_discount_pct",
                    "historical_low_nok", "release_date", "msrp_usd", "vram_assumed", "price_review_required",
                ]].to_string(index=False))
            print(
                f"\n{len(result_df)} results. Median discounts require {args.min_samples} unique listings. "
                "Retail prices are NOK, exclude shipping and use product-level stock status; "
                "launch MSRP is USD and is not directly compared with NOK."
            )
            print(f"Median availability: {result_df['median_status'].value_counts().to_dict()}")
            print(f"Retail availability: {result_df['retail_status'].value_counts().to_dict()}")
            print(f"Historical low availability: {result_df['historical_low_status'].value_counts().to_dict()}")
            print(f"Launch metadata: {result_df['metadata_status'].value_counts().to_dict()}")

    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return 1

    return 0

if __name__ == "__main__":
    exit(main())
