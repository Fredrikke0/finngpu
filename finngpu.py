import requests
from bs4 import BeautifulSoup
from bs4.element import NavigableString, TemplateString
import pandas as pd
import json
import re
import argparse
import subprocess
import time
from pathlib import Path
from datetime import datetime
from typing import NamedTuple
from functools import lru_cache
from tqdm import tqdm
from config import API, BLACKLIST_FILE, IGNORED_MODELS_FILE, MODELS_FILE, OUTPUT_DIR, OUTPUT_FORMAT, SKIP_TERMS


class GPUMatch(NamedTuple):
    model: str
    brand: str | None
    model_number: str | list[str] | None
    vram: str | None
    matched_number: str | None
    match_count: int


def gpu_brand(text: str) -> str | None:
    for brand, prefixes in (
        ("NVIDIA", ("geforce", "nvidia", "rtx", "gtx")),
        ("AMD", ("radeon", "amd", "rx")),
        ("Intel", ("intel", "arc")),
    ):
        if any(prefix in text for prefix in prefixes):
            return brand
    return None

class RateLimiter:
    """Simple time-based rate limiter"""
    def __init__(self, max_calls_per_second: int):
        self.min_interval = 1.0 / max_calls_per_second
        self.last_call = 0

    def __call__(self):
        now = time.time()
        time_since_last = now - self.last_call
        if time_since_last < self.min_interval:
            time.sleep(self.min_interval - time_since_last)
        self.last_call = time.time()

def load_gpu_models():
    """Load GPU models from JSON file"""
    try:
        with open(MODELS_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: {MODELS_FILE} not found")
        return []

def load_filter_list(filename: Path, list_type: str):
    """Load blacklist or whitelist from file"""
    filter_list = {'patterns': [], 'ids': []}
    try:
        with open(filename) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    if line.isdigit():
                        filter_list['ids'].append(line)
                    else:
                        filter_list['patterns'].append(line.lower())
    except FileNotFoundError:
        print(f"Warning: {list_type} file {filename} not found, proceeding without it")
    return filter_list

@lru_cache(maxsize=1)
def ignored_gpu_patterns():
    with open(IGNORED_MODELS_FILE, encoding="utf-8") as file:
        families = json.load(file)["families"]
    patterns = []
    for family in families:
        prefixes = "|".join(
            r"\s*".join(re.escape(word) for word in prefix.split())
            for prefix in sorted(family["prefixes"], key=len, reverse=True)
        )
        for model in family["models"]:
            number = r"\s*".join(re.escape(word) for word in model.split())
            pattern = re.compile(rf"\b(?:{prefixes})\s*{number}\b", re.IGNORECASE)
            patterns.append((pattern, family["brand"], f'{family["name"]} {model}'.strip(), model))
    return patterns


def match_ignored_gpu_models(text: str) -> list[GPUMatch]:
    candidates = []
    for pattern, brand, name, number in ignored_gpu_patterns():
        for match in pattern.finditer(text):
            memory = re.match(r"[\s-]*(\d+)\s*g(?:b)?\b", text[match.end():], re.IGNORECASE)
            candidates.append((
                match.start(), match.end(),
                GPUMatch(name, brand, number or name, memory.group(1) if memory else None, number or name, 1),
            ))
    matches = {}
    for start, end, result in candidates:
        if any(other_start <= start and other_end > end for other_start, other_end, _ in candidates):
            continue
        matches[result.model] = result
    return list(matches.values())


def match_gpu_model(text: str, gpu_models: list, debug=False) -> GPUMatch:
    supported = _match_supported_gpu_model(text, gpu_models, debug=debug)
    supported_families = {re.sub(r"\s+\d+\s+GB$", "", model) for model in gpu_models}
    ignored = [match for match in match_ignored_gpu_models(text) if match.model not in supported_families]
    if not ignored:
        return supported
    if debug:
        print("Recognized out-of-scope GPUs:", [match.model for match in ignored])
    if supported.model == "Unknown" and len(ignored) == 1:
        return ignored[0]._replace(model="Ignored")
    numbers = [match.model_number for match in ignored if isinstance(match.model_number, str)]
    if supported.model == "Multi" and isinstance(supported.model_number, list):
        numbers.extend(supported.model_number)
    elif supported.matched_number is not None:
        numbers.append(supported.matched_number)
    return GPUMatch("Multi", None, numbers, None, numbers[0], len(ignored) + supported.match_count)


def _match_supported_gpu_model(text: str, gpu_models: list, debug=False) -> GPUMatch:
    """Match GPU model with multiple GPU detection"""
    text = text.lower()
    if debug:
        print(f"\nProcessing text: {text}")

    pattern = (
        r'\b(?:(?:geforce|nvidia|rtx|gtx|radeon|amd|rx|intel|arc)[\s\u2122\u00ae-]*)*'
        r'([ab]\s*-?\s*\d{3}|\d{3,4})[\s-]*'
        r'(ti(?:[\s-]*(?:super|s))?|xtx|xt|gre|super|s|d(?:[\s-]*v2)?)?'
        r'(?:[\s-]*(\d+)\s*g(?:b)?\b)?\b'
    )
    matches = list(re.finditer(pattern, text))

    if debug:
        print(f"Regex matches found: {len(matches)}")
        for m in matches:
            print(f"Match groups: {m.groups()}")
            print(f"Full match: '{m.group(0)}'")
            print(f"Context: '...{text[max(0, m.start()-20):m.end()+20]}...'")

    if not matches:
        return GPUMatch("Unknown", None, None, None, None, 0)

    # Process all matches
    all_matched_gpus = []

    for index, match in enumerate(matches):
        number = re.sub(r'[\s-]+', '', match.group(1))
        suffix = match.group(2) or ''
        vram = match.group(3)
        if not vram:
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            # Board names can separate the GPU number from its memory capacity.
            context = re.split(r'[.;\n]', text[match.end():end], maxsplit=1)[0]
            memory = re.search(r'\b(\d+)\s*g(?:b)?\b(?!\s*(?:ram|ddr[345])\b)', context)
            if memory and not re.search(r'\b(?:ram|ssd|hdd|nvme)\b', context[:memory.start()]):
                vram = memory.group(1)
        suffix = re.sub(r'[\s-]+', ' ', suffix)
        suffix = re.sub(r'^ti\s*(?:super|s)$', 'ti super', suffix)
        regional_variant = number in {'4090', '5090'} and suffix in {'d', 'dv2', 'd v2'}
        if regional_variant:
            suffix = ''

        # Standardize 's' to 'super'
        if suffix.lower() == 's':
            suffix = 'super'

        prefix = text[match.start():match.start(1)].strip()
        brand = gpu_brand(prefix)

        model_number = f"{number} {suffix}".strip()

        if debug:
            print(f"\nProcessing match:")
            print(f"  Number: {number}")
            print(f"  Suffix: {suffix}")
            print(f"  Brand: {brand}")
            print(f"  VRAM: {vram}")
            print(f"  Model number: {model_number}")

        model_pattern = re.compile(rf'\b{re.escape(model_number)}(?=\s+\d+\s+gb$)')
        for gpu in gpu_models:
            gpu_lower = gpu.lower()
            if debug:
                print(f"\nChecking against GPU: {gpu_lower}")

            candidate_brand = gpu_brand(gpu_lower)
            if brand and candidate_brand and brand != candidate_brand:
                continue
            if not model_pattern.search(gpu_lower):
                continue
            if vram and not regional_variant and not re.search(rf'\b{vram}\s*gb\b', gpu_lower):
                continue
            all_matched_gpus.append(GPUMatch(gpu, brand, model_number, vram, model_number, 1))
            if not vram:
                # Catalogue order puts the preferred VRAM variant first.
                break

    if debug:
        print("\nAll matched GPUs:", all_matched_gpus)

    if not all_matched_gpus:
        return GPUMatch("Unknown", None, None, None, None, 0)

    # If we found multiple different GPUs, return Multi
    if len({gpu.model for gpu in all_matched_gpus}) > 1:
        matched_models = [gpu.matched_number for gpu in all_matched_gpus if gpu.matched_number is not None]
        return GPUMatch("Multi", None, matched_models, None, matched_models[0], len(all_matched_gpus))

    # If all matches are for the same GPU, return that one
    return all_matched_gpus[0]

def memory_variants(model: str, gpu_models: list) -> list:
    family = re.sub(r'\s+\d+\s+GB$', '', model)
    return [gpu for gpu in gpu_models if re.sub(r'\s+\d+\s+GB$', '', gpu) == family]

def needs_description(title_match: GPUMatch, gpu_models: list) -> bool:
    return title_match.model == "Unknown" or (
        title_match.model not in {"Multi", "Ignored"} and title_match.vram is None
        and len(memory_variants(title_match.model, gpu_models)) > 1
    )

def refine_gpu_match(title_match: GPUMatch, description: str, gpu_models: list, debug=False) -> GPUMatch:
    """Resolve missing title VRAM using deterministic description evidence."""
    if not description or not needs_description(title_match, gpu_models):
        return title_match
    if title_match.model == "Unknown":
        description_match = match_gpu_model(description, gpu_models, debug=debug)
        return description_match if description_match.model != "Unknown" else title_match

    variants = memory_variants(title_match.model, gpu_models)
    targeted = match_gpu_model(description, variants, debug=debug)
    if targeted.model == "Multi":
        return title_match
    if targeted.model not in {"Unknown", "Ignored"} and targeted.vram is not None:
        return title_match._replace(model=targeted.model, vram=targeted.vram)

    description_match = match_gpu_model(description, gpu_models, debug=debug)
    if description_match.model == "Multi" or (
        description_match.model != "Unknown" and description_match.model not in variants
    ):
        return title_match

    capacities = set()
    text = description.lower()
    for memory in re.finditer(r'\b(\d+)\s*g(?:b)?\b', text):
        before = text[max(0, memory.start() - 40):memory.start()]
        after = text[memory.end():memory.end() + 30]
        gpu_memory = re.match(r'\s*gddr[5-7]x?\b', after) or re.search(
            r'\b(?:vram|grafikkminne|skjermminne)\s*[:=-]?\s*$', before
        )
        other_memory = re.search(
            r'\b(?:ram|systemminne|system memory|ssd|hdd|nvme|ddr[345])\s*[:=-]?\s*$', before
        ) or re.match(r'\s*(?:ram|ddr[345]|ssd|hdd|nvme)\b', after)
        if gpu_memory or not other_memory:
            capacities.add(memory.group(1))

    if len(capacities) != 1:
        return title_match
    capacity = capacities.pop()
    for variant in variants:
        if re.search(rf'\s{capacity}\s+GB$', variant):
            return title_match._replace(model=variant, vram=capacity)
    return title_match

def fetch_page(page: int = 1) -> dict:
    """Fetch listing JSON using curl; requests is challenged by Finn."""
    params = API["params"].copy()
    params["page"] = page
    url = requests.Request("GET", API["base_url"], params=params).prepare().url
    command = [
        "curl", "--silent", "--show-error", "--fail",
        "--connect-timeout", "10", "--max-time", "30",
    ]
    for name, value in API["headers"].items():
        command.extend(["--header", f"{name}: {value}"])
    command.extend(["--url", url])
    try:
        response = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            check=True, timeout=35,
        )
    except FileNotFoundError as error:
        raise RuntimeError("Finn fetching requires curl to be installed") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"Timed out fetching Finn page {page}") from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"Error fetching Finn page {page}: {error.stderr.strip()}") from error

    try:
        data = json.loads(response.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Finn page {page} did not return valid JSON") from error
    if not isinstance(data, dict) or not isinstance(data.get("docs"), list):
        raise RuntimeError(f"Finn page {page} did not return a docs list")
    return data

def fetch_listings() -> list:
    """Fetch every page, failing rather than returning a partial snapshot."""
    items = []
    rate_limit = RateLimiter(API["rate_limit"])
    with tqdm(desc="Fetching pages") as pbar:
        page = 1
        while True:
            rate_limit()
            page_items = fetch_page(page)["docs"]
            if not page_items:
                return items
            items.extend(page_items)
            page += 1
            pbar.update(1)

def fetch_description(url: str) -> str:
    """Fetch and parse item description"""
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        about_section = soup.find('section', class_='about-section')
        if about_section:
            # Finn's server-rendered description may be inside a template.
            text = about_section.get_text(
                separator=' ', strip=True, types=(NavigableString, TemplateString)
            )
            # Replace multiple spaces with single space
            text = re.sub(r'\s+', ' ', text)
            return text
        print(f"Warning: no description section found at {url}")
        return ""
    except requests.RequestException as error:
        print(f"Warning: could not fetch description from {url}: {error}")
        return ""

def process_listings(items: list, gpu_models: list, blacklist: dict, whitelist: dict, verbose: bool = False) -> pd.DataFrame:
    """Process raw listings into structured data with progress tracking"""
    processed_items = []
    skipped_count = 0
    skip_patterns = [re.compile(rf'\b{re.escape(term)}\b', re.IGNORECASE) for term in SKIP_TERMS]

    pending_descriptions = []

    for item in tqdm(items, desc="Processing listings"):
        heading = item.get('heading', '')
        ad_id = str(item.get('id', ''))

        # If whitelist exists, only process whitelisted items
        if whitelist['patterns'] or whitelist['ids']:
            if ad_id not in whitelist['ids'] and \
               not any(pattern in heading.lower() for pattern in whitelist['patterns']):
                continue
            elif verbose:
                print(f"\nProcessing whitelisted item {ad_id}:")
                print(f"Title: {heading}")
        else:
            # If no whitelist, apply blacklist and skip terms
            if ad_id in blacklist['ids'] or \
               any(pattern in heading.lower() for pattern in blacklist['patterns']):
                continue

            # Improved skip term matching with whole-word check
            if any(pattern.search(heading) for pattern in skip_patterns):
                if verbose:
                    print(f"Skipping item {ad_id} containing: {heading}")
                skipped_count += 1
                continue

        # Extract GPU info from title
        title_match = match_gpu_model(heading, gpu_models, debug=verbose)

        if verbose:
            print(f"Matched model: {title_match.model}")
            if title_match.brand:
                print(f"Brand: {title_match.brand}")
            if title_match.model_number:
                print(f"Model number: {title_match.model_number}")
            if title_match.vram:
                print(f"VRAM: {title_match.vram}GB")
            print("-" * 80)

        record = {
            "heading": heading,
            "price": item.get("price", {}).get("amount", "N/A"),
            **title_match._asdict(),
            "match_source": "unknown" if title_match.model == "Unknown" else "title",
            "description_checked": False,
            "location": item.get("location", "N/A"),
            "ad_id": ad_id,
            "canonical_url": item.get("canonical_url", "")
        }
        processed_items.append(record)
        if needs_description(title_match, gpu_models):
            pending_descriptions.append((record, title_match))

    print(f"\nSkipped {skipped_count} items containing: {', '.join(SKIP_TERMS)}")

    # Fetch descriptions for unknown models and ambiguous memory variants.
    if pending_descriptions:
        rate_limit = RateLimiter(API["rate_limit"])
        print(f"\nFetching descriptions for {len(pending_descriptions)} unmatched or VRAM-ambiguous items...")
        desc_pbar = tqdm(pending_descriptions, desc="Fetching descriptions")

        for item, title_match in desc_pbar:
            desc_pbar.set_postfix_str(f"Processing: {item['ad_id']}...")
            rate_limit()
            description = fetch_description(item["canonical_url"])
            item["description_checked"] = True
            if not description:
                continue
            if verbose:
                print(f"\nTrying to match from description for {item['ad_id']}:")
                print(f"Description: {description[:200]}...")
            resolved = refine_gpu_match(title_match, description, gpu_models, debug=verbose)
            if resolved != title_match:
                item.update(resolved._asdict())
                item["match_source"] = "description"

    return pd.DataFrame(processed_items)

def main():
    parser = argparse.ArgumentParser(description="Fetch and process GPU listings from Finn.no")
    parser.add_argument("-f", "--file", type=str, help="Process existing CSV file")
    parser.add_argument("-b", "--blacklist", type=Path, default=BLACKLIST_FILE, help="Blacklist file path")
    parser.add_argument("-w", "--whitelist", type=Path, help="Whitelist file path")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("-o", "--output", type=Path, help="Override the output CSV path")
    args = parser.parse_args()

    # Load GPU models and filter lists
    gpu_models = load_gpu_models()
    blacklist = load_filter_list(args.blacklist, "blacklist")
    whitelist = load_filter_list(args.whitelist, "whitelist") if args.whitelist else {'patterns': [], 'ids': []}

    if args.verbose:
        print("\nLoaded configuration:")
        if whitelist['ids']:
            print(f"Whitelist IDs: {', '.join(whitelist['ids'])}")
        if whitelist['patterns']:
            print(f"Whitelist patterns: {', '.join(whitelist['patterns'])}")
        print(f"Skip terms: {', '.join(SKIP_TERMS)}")
        print("-" * 80)

    if args.file:
        # Process existing file
        try:
            df = pd.read_csv(args.file)
        except Exception as e:
            print(f"Error reading file: {e}")
            return
    else:
        df = process_listings(fetch_listings(), gpu_models, blacklist, whitelist, verbose=args.verbose)

    # Save results
    output_path = args.output or OUTPUT_DIR / OUTPUT_FORMAT.format(date=datetime.now().strftime("%Y-%m-%d"))
    df.to_csv(output_path, index=False)
    print(f"\nData saved to {output_path}")

    # Print statistics
    total = len(df)
    if "model" in df.columns:
        matched = len(df[~df['model'].isin(['Unknown', 'Multi', 'Ignored'])])
        print(f"\nStatistics:")
        print(f"Total listings: {total}")
        print(f"Successfully matched: {matched} ({matched/total:.1%})")

        if args.verbose:
            print("\nModel distribution:")
            print(df['model'].value_counts())
            print("\nFinal results by ID:")
            for _, row in df.iterrows():
                print(f"\nAd ID: {row['ad_id']}")
                print(f"Title: {row['heading']}")
                print(f"Model: {row['model']}")
                print(f"Price: {row['price']}")
                print(f"URL: {row['canonical_url']}")
    else:
        print(f"\nWarning: 'model' column not found in DataFrame")
        print(f"Available columns: {', '.join(df.columns)}")

if __name__ == "__main__":
    main()
