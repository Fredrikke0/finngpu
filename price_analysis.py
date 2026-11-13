import argparse
import re
from datetime import datetime
from pathlib import Path

from config import BASE_DIR, DATA_DIR
from deal_analysis import analyze_deals


def parse_args():
    parser = argparse.ArgumentParser(description="Compare Finn GPU prices with retail and historical references.")
    parser.add_argument("-f", "--file", help="Listings CSV; defaults to the latest dated snapshot")
    parser.add_argument("--metadata", type=Path, default=BASE_DIR / "gpu_metadata.json")
    parser.add_argument("--retail", type=Path, default=DATA_DIR / "retail_prices.json")
    parser.add_argument("--history", type=Path, help="Historical-low snapshot")
    parser.add_argument("--history-max-age-days", type=float, default=7)
    parser.add_argument("--retail-max-age-days", type=float, default=7)
    parser.add_argument("--min-samples", type=int, default=5)
    parser.add_argument("--all-listings", action="store_true")
    parser.add_argument("--sort", choices=["median", "retail", "price"], default="median")
    parser.add_argument("-c", "--csv", dest="output_csv", type=Path)
    parser.add_argument("--brand", type=str.lower)
    parser.add_argument("--min-price", type=float)
    parser.add_argument("--max-price", type=float)
    parser.add_argument("--exclude-models", help="Comma-separated literal model fragments")
    return parser.parse_args()


def find_latest_gpu_listings():
    dated_files = []
    for file in DATA_DIR.glob("finn_gpu_listings_*.csv"):
        match = re.fullmatch(r"finn_gpu_listings_(\d{4}-\d{2}-\d{2})\.csv", file.name)
        if not match:
            continue
        try:
            date = datetime.strptime(match.group(1), "%Y-%m-%d")
        except ValueError:
            continue
        dated_files.append((date, str(file)))
    if not dated_files:
        raise FileNotFoundError("No valid dated finn_gpu_listings_*.csv snapshots found")
    latest = max(dated_files)[1]
    print(f"Using most recent GPU listings file: {latest}")
    return latest


def apply_filters(df, args):
    filtered = df.copy()
    if args.brand:
        brand = {"nvidia": "geforce", "amd": "radeon"}.get(args.brand, args.brand)
        filtered = filtered[filtered["model"].str.lower().str.contains(brand, regex=False)]
    if args.min_price is not None:
        filtered = filtered[filtered["price"] >= args.min_price]
    if args.max_price is not None:
        filtered = filtered[filtered["price"] <= args.max_price]
    if args.exclude_models:
        patterns = [re.escape(value.strip()) for value in args.exclude_models.split(",") if value.strip()]
        if patterns:
            filtered = filtered[~filtered["model"].str.contains("|".join(patterns), case=False)]
    return filtered


def main():
    args = parse_args()
    try:
        history = args.history or DATA_DIR / "historical_prices.json"
        if args.history is None and not history.exists():
            print("Warning: historical prices unavailable; run historical_prices.py")
            history = None
        result = analyze_deals(
            args.file or find_latest_gpu_listings(), args.metadata, args.retail,
            args.min_samples, args.retail_max_age_days, args.all_listings,
            history_file=history, history_max_age_days=args.history_max_age_days,
        )
        result = apply_filters(result, args)
        sort_column = {"median": "median_discount_pct", "retail": "retail_discount_pct", "price": "price"}[args.sort]
        result = result.sort_values(
            [sort_column, "model"], ascending=[args.sort == "price", True], na_position="last"
        )
        if args.output_csv:
            result.to_csv(args.output_csv, index=False)
            print(f"Results saved to {args.output_csv}")
        else:
            print(result[[
                "model", "price", "median_nok", "sample_count",
                "retail_nok", "historical_low_nok", "release_date", "vram_assumed",
            ]].to_string(index=False))
        for label, column in (
            ("Median", "median_status"), ("Retail", "retail_status"),
            ("Historical low", "historical_low_status"), ("Launch metadata", "metadata_status"),
        ):
            print(f"{label} availability: {result[column].value_counts().to_dict()}")
    except (OSError, ValueError, KeyError) as error:
        print(f"Error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
