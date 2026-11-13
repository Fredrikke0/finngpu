import argparse
import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def generate_review(dataset_path, csv_path, output_path):
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    with csv_path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    ids = [row["ad_id"] for row in rows]
    if not ids or len(ids) != len(set(ids)) or set(ids) != {
        case["ad_id"] for case in dataset["cases"]
    }:
        raise ValueError("Review CSV must contain exactly one row per dataset case")
    cases = {case["ad_id"]: case for case in dataset["cases"]}
    if any(
        row["title"] != cases[row["ad_id"]]["title"]
        or row["expected_model"] != cases[row["ad_id"]]["expected_model"]
        for row in rows
    ):
        raise ValueError("Review CSV is stale; rerun the benchmark validator")
    payload = {
        "dataset": dataset,
        "rows": rows,
        "models": ["Unknown", "Multi", "Ignored"] + json.loads(
            (ROOT / "gpus.json").read_text(encoding="utf-8")
        ),
    }
    # Keep listing text inert even if it contains an HTML script terminator.
    serialized = json.dumps(payload, ensure_ascii=True).replace("<", "\\u003c")
    template = (Path(__file__).with_name("review_template.html")).read_text(encoding="utf-8")
    output_path.write_text(template.replace("__REVIEW_DATA__", serialized), encoding="utf-8")
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="Generate a standalone GPU benchmark review page")
    parser.add_argument("--dataset", type=Path, default=ROOT / "utils/current_listings.json")
    parser.add_argument("--csv", type=Path, default=ROOT / "data/current_listings_review.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "data/current_listings_review.html")
    args = parser.parse_args()
    count = generate_review(args.dataset, args.csv, args.output)
    print(f"Review page with {count} listings saved to {args.output}")


if __name__ == "__main__":
    main()
