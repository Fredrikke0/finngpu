import argparse
import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import DATA_DIR
from finngpu import load_gpu_models


HISTORY_URL = "https://www.prisjakt.no/api/price-history"
PRICE_BASIS = (
    "Lowest active, non-membership price in Prisjakt ALL history across discovered "
    "board products of this GPU/VRAM variant. Shipping and used products excluded "
    "by Prisjakt's price-history definition; not an exhaustive catalogue of every SKU."
)


def timestamp(value):
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ValueError("Historical price timestamps must include a timezone")
    return date


def parse_history(payload):
    history = payload.get("priceHistory")
    if not isinstance(history, dict) or not isinstance(history.get("historyItems"), list):
        raise ValueError("Prisjakt did not return priceHistory.historyItems")
    items = history["historyItems"]
    dates = [timestamp(item["date"]) for item in items]
    oldest = history.get("oldestHistoryItem")
    if oldest is not None:
        if not dates or (min(dates) - timestamp(oldest)).total_seconds() > 86400:
            raise ValueError("Prisjakt history does not cover its oldest recorded date")
    elif items:
        raise ValueError("Prisjakt omitted the oldest-history timestamp")
    eligible = []
    for item in items:
        price = item.get("price")
        if not isinstance(item.get("active"), bool):
            raise ValueError("Prisjakt history omitted an active/inactive flag")
        if item.get("active") is not True:
            continue
        if not isinstance(price, (int, float)) or isinstance(price, bool) or not math.isfinite(price):
            raise ValueError("Prisjakt history contains an invalid active price")
        if not isinstance(item.get("isMembershipPrice"), bool) or not isinstance(item.get("isPaidMembership"), bool):
            raise ValueError("Prisjakt history omitted membership-price flags")
        if item["isMembershipPrice"] or item["isPaidMembership"]:
            continue
        if price > 0:
            eligible.append(item)
    low = min(eligible, key=lambda item: (item["price"], timestamp(item["date"]))) if eligible else None
    return {
        "oldest_recorded_at": oldest,
        "newest_recorded_at": history.get("newestHistoryItem"),
        "record_count": len(items),
        "low": {
            # Prisjakt's history API returns ore, unlike category prices in NOK.
            "price_nok": round(low["price"] / 100, 2),
            "recorded_at": low["date"],
            "shop": low.get("shopName", ""),
        } if low is not None else None,
    }


def atomic_json(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as file:
            temporary = Path(file.name)
            json.dump(data, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def fetch_histories(session, catalog, previous, models, max_age_hours=24, interval=1.0, now=None):
    if max_age_hours <= 0 or interval < 0:
        raise ValueError("Cache age must be positive and request interval nonnegative")
    now = now or datetime.now(timezone.utc)
    if not isinstance(catalog.get("products"), dict) or not catalog["products"]:
        raise ValueError("Retail snapshot lacks product identities; rerun retail_prices.py")
    if catalog.get("currency") != "NOK" or (previous and previous.get("currency") != "NOK"):
        raise ValueError("Historical price inputs must use NOK")
    supported = set(models)
    old_products = previous.get("products", {}) if previous else {}
    products = {
        key: {name: item[name] for name in ("model", "product_id", "product_name", "url")}
        for key, item in old_products.items() if item["model"] in supported
    }
    products.update({
        key: item for key, item in catalog["products"].items() if item["model"] in supported
    })
    records = {}
    requests_made = 0
    for product_id, product in sorted(products.items()):
        if not product_id.isdigit() or str(product["product_id"]) != product_id:
            raise ValueError("Historical product identity is not a Prisjakt product ID")
        if product["url"] != f"https://www.prisjakt.no/product.php?p={product_id}":
            raise ValueError("Unexpected historical product source URL")
        cached = old_products.get(product_id)
        age = (now - timestamp(cached["checked_at"])).total_seconds() / 3600 if cached else math.inf
        if age < -1:
            raise ValueError("Historical cache timestamp is in the future")
        if cached and age < max_age_hours and cached["model"] == product["model"]:
            records[product_id] = dict(cached, **product)
            continue
        if requests_made:
            time.sleep(interval)
        response = session.post(HISTORY_URL, json={
            "productId": int(product_id), "timeRange": "ALL", "shopIds": [], "variantIds": [],
        }, timeout=30)
        response.raise_for_status()
        try:
            history = parse_history(response.json())
        except ValueError as error:
            raise ValueError(f"Invalid history for product {product_id}: {error}") from error
        records[product_id] = dict(product, **history, checked_at=now.isoformat())
        requests_made += 1
        print(f"History {len(records)}/{len(products)}: {product['product_name']}", flush=True)

    references = {}
    for product in records.values():
        if product["low"] is None:
            continue
        model = product["model"]
        entry = {
            **product["low"],
            "product_name": product["product_name"], "url": product["url"],
            "checked_at": product["checked_at"],
        }
        if model not in references or entry["price_nok"] < references[model]["price_nok"]:
            references[model] = entry
    if not references:
        raise ValueError("No eligible historical prices found; previous snapshot not replaced")
    return {
        "schema_version": 1, "source": "prisjakt.no", "currency": "NOK",
        "fetched_at": now.isoformat(), "price_basis": PRICE_BASIS,
        "products_checked": len(records), "http_requests": requests_made,
        "products": records, "models": references,
    }


def main():
    parser = argparse.ArgumentParser(description="Cache lowest recorded Prisjakt prices in NOK")
    parser.add_argument("--retail", type=Path, default=DATA_DIR / "retail_prices.json")
    parser.add_argument("-o", "--output", type=Path, default=DATA_DIR / "historical_prices.json")
    parser.add_argument("--max-age-hours", type=float, default=24)
    args = parser.parse_args()
    catalog = json.loads(args.retail.read_text(encoding="utf-8"))
    previous = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else None
    with requests.Session() as session:
        snapshot = fetch_histories(session, catalog, previous, load_gpu_models(), args.max_age_hours)
    atomic_json(args.output, snapshot)
    print(f"Saved historical lows for {len(snapshot['models'])} GPU variants to {args.output}")
    print(snapshot["price_basis"])


if __name__ == "__main__":
    main()
