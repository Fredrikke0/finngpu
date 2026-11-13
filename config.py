from pathlib import Path

# Base paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# API settings — same search endpoint as bedrefinn/populate_db.py.
API = {
    "base_url": "https://www.finn.no/map/podium-resource/content/api/search/recommerce/SEARCH_ID_BAP_COMMON",
    "headers": {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
        "Referer": "https://www.finn.no/",
    },
    "rate_limit": 4,  # requests per second
    "params": {
        "rows": 300,
        "product_category": [
            "2.93.3215.8368",
            "2.93.3215.46",
            "2.93.3215.44",
        ],
        "computer_components_type": 2,
        "price_from": 1,
        "sort": "NEWEST",
        "trade_type": 1,
    },
}

# File paths
MODELS_FILE = BASE_DIR / "gpus.json"
IGNORED_MODELS_FILE = BASE_DIR / "ignored_gpus.json"
BLACKLIST_FILE = BASE_DIR / "blacklist.txt"
WHITELIST_FILE = BASE_DIR / "whitelist.txt"

# Listing processing
SKIP_TERMS = ['pc', 'stasjonær', 'mining', 'mac', 'laptop', 'dell', 'gamingpoint', 'datamaskin', 'server', 'windows', 'lenovo', 'desktop', 'kjøler', 'VANNBLOKK', 'CPU']

# Output settings
OUTPUT_DIR = Path("/var/www/finngpu/data")
OUTPUT_FORMAT = "finn_gpu_listings_{date}.csv"
