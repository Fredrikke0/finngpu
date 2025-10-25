#!/bin/bash

# Base directories
BASE_DIR="$(dirname "$(dirname "$(realpath "$0")")")"
WWW_DIR="/var/www/finngpu"
PUBLIC_DIR="$WWW_DIR/public"
DATA_DIR="$WWW_DIR/data"
VENV_DIR="$BASE_DIR/.venv"
EMAIL="email@mail.com"
REQUIREMENTS="$BASE_DIR/requirements.txt"

# Analysis files
ANALYSIS_FILE="$PUBLIC_DIR/analysis.csv"
PREV_ANALYSIS_FILE="$PUBLIC_DIR/analysis_prev.csv"

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check dependencies
DEPENDENCIES=("python3" "mail")
for dep in "${DEPENDENCIES[@]}"; do
    if ! command_exists "$dep"; then
        echo "Error: '$dep' is not installed. Please install it and rerun the script."
        exit 1
    fi
done

# Function to setup virtual environment
setup_venv() {
    if [ ! -d "$VENV_DIR" ]; then
        echo "Creating virtual environment..."
        python3 -m venv "$VENV_DIR"
        source "$VENV_DIR/bin/activate"
        echo "Installing requirements..."
        pip install -r "$REQUIREMENTS"
    else
        source "$VENV_DIR/bin/activate"
    fi
}

# Ensure directories exist
mkdir -p "$DATA_DIR" "$PUBLIC_DIR"

# Setup and activate virtual environment
setup_venv

# Set umask for file creation
umask 002

# Run scraper to generate new data
python3 finngpu.py -b blacklist.txt -w whitelist.txt

# Find the newest scraped data file
newest_csv=$(ls -t "$DATA_DIR"/finn_gpu_listings_*.csv 2>/dev/null | head -n 1)

# Check if the newest CSV file exists
if [[ -z "$newest_csv" ]]; then
    echo "Error: No CSV file found in $DATA_DIR."
    exit 1
fi

# Run price analysis
python3 price_analysis.py \
    -f "$newest_csv" \
    -p "$BASE_DIR/1440p-ultra-performance.csv" \
    -c "$ANALYSIS_FILE" \
    --min-fps 10

# Generate HTML table snippet for web (served at /finngpu/data)
cd "$WWW_DIR"
python3 -c "from csv_to_html import csv_to_html; open('$PUBLIC_DIR/table.html','w').write(csv_to_html('$ANALYSIS_FILE'))"

# Check for differences in top ten ads
if [[ -f "$ANALYSIS_FILE" ]]; then
    if [[ -f "$PREV_ANALYSIS_FILE" ]]; then
        # Extract top ten ads (excluding the header)
        CURRENT_TOP_TEN=$(head -n 11 "$ANALYSIS_FILE" | tail -n 10)
        PREV_TOP_TEN=$(head -n 11 "$PREV_ANALYSIS_FILE" | tail -n 10)

        # Compare the top ten entries
        if [[ "$CURRENT_TOP_TEN" != "$PREV_TOP_TEN" ]]; then
            echo "Top 10 ads have changed. Sending notification."
#            mail -s "GPU Price/Performance Update" "$EMAIL" < "$ANALYSIS_FILE"
        else
            echo "No changes in the top 10 ads."
        fi
    else
        echo "Previous analysis file not found. Skipping comparison."
    fi

    # Update the previous analysis file
    cp "$ANALYSIS_FILE" "$PREV_ANALYSIS_FILE"
else
    echo "Current analysis file not found. Exiting."
    exit 1
fi
