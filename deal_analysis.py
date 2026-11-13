import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


UNSUITABLE_TITLE = (
    r"\b(?:defekt|defective|broken|ødelagt|reservedeler|delekort|waterblock|"
    r"vannblokk|laptop|stasjonær|pc)\b|"
    r"\b(?:virker ikke|fungerer ikke|for parts|kun eske|kun boks|uten skjermkort|uten gpu)\b|"
    r"\bek[- ]quantum\s+vector|^\W*(?:bytte|bytter)\b|"
    r"^\W*(?:eske|boks|emballasje)\s+(?:til|for)\b|"
    r"\b(?:krever|trenger)\s+(?:enkel\s+)?(?:reparasjon|reperasjon|repair)\b"
)


def load_listings(path):
    listings = pd.read_csv(path, dtype={"ad_id": "string"})
    required = {"model", "price", "canonical_url"}
    if not required.issubset(listings.columns):
        raise ValueError(f"Listings require columns: {', '.join(sorted(required))}")
    listings["price"] = pd.to_numeric(listings["price"], errors="coerce")
    valid_price = listings["price"].gt(0) & listings["price"].lt(math.inf)
    if not valid_price.all():
        print(f"Warning: excluding {(~valid_price).sum()} listings with invalid/nonpositive prices")
    listings = listings[valid_price & listings["model"].notna() & ~listings["model"].isin(["Unknown", "Multi", "Ignored"])].copy()
    if "heading" in listings:
        unsuitable = listings["heading"].fillna("").str.contains(UNSUITABLE_TITLE, case=False, regex=True)
        if unsuitable.any():
            print(f"Excluding {unsuitable.sum()} unsuitable listings by title (broken cards, PCs or accessories)")
        listings = listings[~unsuitable].copy()
    else:
        print("Warning: no heading column; condition/accessory filtering is unavailable")
    urls = listings["canonical_url"].fillna("").astype(str).str.strip()
    identities = listings["ad_id"].fillna("").str.strip() if "ad_id" in listings else urls
    identities = identities.where(identities.ne(""), urls)
    if identities.eq("").any():
        raise ValueError("Every priced listing needs an ad_id or canonical_url for deduplication")
    listings["_identity"] = identities
    before = len(listings)
    listings = listings.drop_duplicates("_identity", keep="last").drop(columns="_identity")
    if before != len(listings):
        print(f"Deduplicated {before - len(listings)} repeated listings")
    listings["vram_assumed"] = listings["vram"].isna() if "vram" in listings else True
    return listings


def load_retail_snapshot(path, now, max_age_days, label="Retail"):
    snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
    if snapshot.get("currency") != "NOK":
        raise ValueError(f"{label} snapshot must use NOK; MSRP stays separately in USD")
    fetched_at = datetime.fromisoformat(snapshot["fetched_at"])
    if fetched_at.tzinfo is None:
        raise ValueError(f"{label} snapshot timestamp must include a timezone")
    age = (now - fetched_at).total_seconds() / 86400
    if age < -1 / 24:
        raise ValueError(f"{label} snapshot timestamp is in the future")
    stale = age > max_age_days
    if stale:
        print(f"Warning: {label.lower()} snapshot is {age:.1f} days old; its price references will be unavailable")
    for model, reference in snapshot["models"].items():
        price = reference["price_nok"]
        if not isinstance(price, (int, float)) or isinstance(price, bool) or not math.isfinite(price) or price <= 0:
            raise ValueError(f"Invalid retail price for {model}")
        if not reference.get("url"):
            raise ValueError(f"Retail source URL missing for {model}")
    return snapshot, stale


def load_metadata(path):
    metadata = json.loads(Path(path).read_text(encoding="utf-8"))["gpus"]
    for model, facts in metadata.items():
        release = facts.get("release_date")
        if release is not None:
            if not isinstance(release, str) or not re.fullmatch(r"\d{4}(?:-\d{2}){0,2}", release):
                raise ValueError(f"Invalid release date for {model}")
            datetime.strptime(release, {4: "%Y", 7: "%Y-%m", 10: "%Y-%m-%d"}[len(release)])
        msrp = facts.get("msrp_usd")
        if msrp is not None and (
            not isinstance(msrp, (int, float)) or isinstance(msrp, bool)
            or not math.isfinite(msrp) or msrp <= 0
        ):
            raise ValueError(f"Invalid launch MSRP for {model}")
        sources = facts.get("sources", [])
        if (release is not None or msrp is not None) and (
            not isinstance(sources, list) or not sources
            or not all(isinstance(url, str) and url.startswith(("https://", "http://")) for url in sources)
        ):
            raise ValueError(f"Launch metadata needs source URLs for {model}")
    return metadata


def analyze_deals(listings_file, metadata_file, retail_file, min_samples=5,
                  retail_max_age_days=7, all_listings=False, now=None,
                  history_file=None, history_max_age_days=7):
    if min_samples < 2 or retail_max_age_days <= 0:
        raise ValueError("min_samples must be at least 2 and retail_max_age_days must be positive")
    now = now or datetime.now(timezone.utc)
    listings = load_listings(listings_file)
    if listings.empty:
        raise ValueError("No eligible priced GPU listings remain after filtering")
    metadata = load_metadata(metadata_file)
    snapshot, stale = load_retail_snapshot(retail_file, now, retail_max_age_days)
    if history_max_age_days <= 0:
        raise ValueError("history_max_age_days must be positive")
    history, history_stale = (
        load_retail_snapshot(history_file, now, history_max_age_days, label="Historical")
        if history_file is not None else (None, False)
    )
    listings["price_review_required"] = listings["price"].eq(1)
    references = listings[~listings["price_review_required"]]
    groups = references.groupby("model").agg(
        median_nok=("price", "median"),
        sample_count=("price", "size"),
        assumed_vram_count=("vram_assumed", "sum"),
    )
    listings = listings.join(groups, on="model")
    listings[["sample_count", "assumed_vram_count"]] = listings[["sample_count", "assumed_vram_count"]].fillna(0).astype(int)
    # Extreme prices may be trade placeholders or accessories the title filter missed.
    listings["price_review_required"] |= (
        listings["sample_count"].ge(min_samples)
        & listings["price"].lt(listings["median_nok"] * 0.2)
    )
    if not all_listings:
        listings = listings.sort_values(["price_review_required", "price"]).groupby("model", sort=False).head(1).copy()
    listings["median_status"] = "insufficient_samples"
    listings.loc[listings["sample_count"].ge(min_samples), "median_status"] = "available"
    listings.loc[listings["price_review_required"], "median_status"] = "price_review_required"
    listings["median_discount_pct"] = (
        (1 - listings["price"] / listings["median_nok"]) * 100
    ).where(listings["median_status"].eq("available")).round(1)
    rows = []
    for _, listing in listings.iterrows():
        row = listing.to_dict()
        model = row["model"]
        reference = snapshot["models"].get(model)
        row.update({
            "retail_status": "stale" if stale and reference else "available" if reference else "unavailable",
            "retail_nok": reference["price_nok"] if reference and not stale else None,
            "retail_discount_pct": (
                round((1 - row["price"] / reference["price_nok"]) * 100, 1)
                if reference and not stale and not row["price_review_required"] else None
            ),
            "retail_url": reference["url"] if reference else "",
            "retail_product": reference["product_name"] if reference else "",
            "retail_fetched_at": snapshot["fetched_at"],
            "retail_price_basis": snapshot["price_basis"],
        })
        historical = history["models"].get(model) if history else None
        row.update({
            "historical_low_nok": historical["price_nok"] if historical and not history_stale else None,
            "historical_low_status": "stale" if historical and history_stale else "available" if historical else "unavailable",
            "historical_low_url": historical["url"] if historical else "",
            "historical_low_recorded_at": historical["recorded_at"] if historical else "",
            "historical_low_product": historical["product_name"] if historical else "",
            "historical_low_fetched_at": history["fetched_at"] if history else "",
            "historical_low_basis": history["price_basis"] if history else "",
        })
        facts = metadata.get(model)
        row.update({
            "release_date": facts.get("release_date") if facts else None,
            "msrp_usd": facts.get("msrp_usd") if facts else None,
            "metadata_status": (
                "available" if facts and facts.get("release_date") and facts.get("msrp_usd") is not None
                else "partial" if facts else "missing"
            ),
            "metadata_sources": " | ".join(facts.get("sources", [])) if facts else "",
            "metadata_notes": facts.get("notes", "") if facts else "",
        })
        rows.append(row)
    columns = [
        "model", "price", "median_nok", "sample_count", "median_discount_pct",
        "median_status", "price_review_required", "vram_assumed", "assumed_vram_count", "retail_nok",
        "retail_discount_pct", "retail_status",
        "historical_low_nok", "historical_low_status", "historical_low_url",
        "historical_low_recorded_at", "historical_low_product",
        "historical_low_fetched_at", "historical_low_basis", "release_date", "msrp_usd",
        "canonical_url", "retail_url", "retail_product", "retail_fetched_at",
        "retail_price_basis", "metadata_status", "metadata_sources", "metadata_notes",
    ]
    return pd.DataFrame(rows, columns=columns)
