import argparse
import ast
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config import DATA_DIR
from finngpu import load_gpu_models, match_gpu_model


CATEGORY_URL = "https://www.prisjakt.no/c/skjermkort"


def parse_category(html):
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script"):
        text = script.get_text()
        match = re.search(
            r"window\.__REACT_QUERY_STATE__\s*=\s*JSON\.parse\(('(?:\\.|[^'\\])*')\)",
            text,
        )
        if not match:
            continue
        market_match = re.search(r"window\.CLIENT_MARKET\s*=\s*", text)
        if not market_match:
            raise ValueError("Prisjakt did not identify its market")
        market, _ = json.JSONDecoder().raw_decode(text[market_match.end():])
        if market.get("currency") != "NOK":
            raise ValueError("Prisjakt returned a non-NOK market")
        # Decode a string literal, never execute the site's JavaScript.
        state = json.loads(ast.literal_eval(match.group(1)))
        for query in state["queries"]:
            collection = query.get("state", {}).get("data", {}).get("productCollection", {})
            for section in collection.get("slices", []):
                if section.get("__typename") == "ProductsSlice":
                    return section
    raise ValueError("Prisjakt product state is missing; the page may have changed or been blocked")


def product_identity(product, models):
    properties = {
        node["id"]: node.get("value")
        for node in (product.get("properties") or {}).get("nodes", [])
    }
    processor = properties.get("114854")
    memory = properties.get("6716")
    if not isinstance(processor, str) or not isinstance(memory, str):
        return None
    if not re.fullmatch(r"\d+\s*GB", memory, re.IGNORECASE):
        return None
    model, _, _, vram, _, _ = match_gpu_model(f"{processor} {memory}", models)
    if model in {"Unknown", "Multi", "Ignored"} or vram is None:
        return None
    url = urljoin(CATEGORY_URL, product["pathName"])
    if not url.startswith("https://www.prisjakt.no/product.php?"):
        raise ValueError("Unexpected Prisjakt product URL")
    return model, {
        "product_id": str(product["id"]),
        "product_name": product["name"],
        "url": url,
    }


def product_reference(product, models):
    if product.get("stockStatus") != "in_stock":
        return None
    summary = product.get("priceSummary") or {}
    price = summary.get("regular")
    if not isinstance(price, (int, float)) or isinstance(price, bool) or not math.isfinite(price) or price <= 0:
        return None
    identity = product_identity(product, models)
    if identity is None:
        return None
    model, entry = identity
    return model, {
        **entry,
        "price_nok": price,
        "availability": "product_in_stock",
        "shipping_included": False,
    }


def fetch_retail_prices(session, models, interval=1.0, max_pages=50):
    references = {}
    catalog = {}
    seen = set()
    offset = 0
    scanned = 0
    for page in range(max_pages):
        if page:
            time.sleep(interval)
        response = session.get(CATEGORY_URL, params={"offset": offset}, timeout=30)
        response.raise_for_status()
        section = parse_category(response.text)
        info = section["pageInfo"]
        products = section["products"]
        if info["offset"] != offset or not isinstance(info["limit"], int) or info["limit"] <= 0:
            raise ValueError("Prisjakt returned inconsistent pagination")
        total = section["productsCount"]
        if not products and offset < total:
            raise ValueError("Prisjakt ended pagination before all products were fetched")
        ids = [str(product["id"]) for product in products]
        if len(set(ids)) != len(ids) or seen.intersection(ids):
            raise ValueError("Prisjakt repeated products across pages; snapshot not saved")
        seen.update(ids)
        scanned += len(products)
        for product in products:
            identity = product_identity(product, models)
            if identity is not None:
                model, entry = identity
                catalog[entry["product_id"]] = dict(entry, model=model)
            reference = product_reference(product, models)
            if reference is None:
                continue
            model, entry = reference
            previous = references.get(model)
            count = previous["product_count"] + 1 if previous else 1
            if previous is None or entry["price_nok"] < previous["price_nok"]:
                references[model] = dict(entry, product_count=count)
            else:
                previous["product_count"] = count
        offset += info["limit"]
        if offset >= total:
            break
    else:
        raise ValueError("Prisjakt pagination limit reached; snapshot not saved")
    if not references:
        raise ValueError("Prisjakt returned no supported, priced products")
    return {
        "schema_version": 2,
        "source": "prisjakt.no",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "currency": "NOK",
        "price_basis": (
            "Lowest regular listed price across supported products marked in_stock; "
            "shipping excluded and individual offer availability not verified."
        ),
        "products_scanned": scanned,
        "products": catalog,
        "models": references,
    }


def main():
    parser = argparse.ArgumentParser(description="Fetch timestamped NOK retail references from Prisjakt")
    parser.add_argument("-o", "--output", type=Path, default=DATA_DIR / "retail_prices.json")
    args = parser.parse_args()
    with requests.Session() as session:
        snapshot = fetch_retail_prices(session, load_gpu_models())
    args.output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(snapshot['models'])} model references from {snapshot['products_scanned']} products to {args.output}")
    print(snapshot["price_basis"])


if __name__ == "__main__":
    main()
