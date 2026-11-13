import argparse
import csv
import math
import re
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent


def release_key(row):
    date = row.get("release_date", "")
    if not re.fullmatch(r"\d{4}(?:-\d{2}){0,2}", date):
        return (0, 0, 0)
    parts = [int(value) for value in date.split("-")]
    return tuple(parts + [0] * (3 - len(parts)))


def number(value, suffix="", decimals=0):
    if value == "" or value is None:
        return "&mdash;"
    amount = float(value)
    if not math.isfinite(amount):
        return "&mdash;"
    return f"{amount:,.{decimals}f}".replace(",", " ") + escape(suffix)


def link(url, label, title=None):
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        return label
    tooltip = f' title="{escape(title, quote=True)}"' if title else ""
    return f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer"{tooltip}>{label}</a>'


def render_row(row):
    year = release_key(row)[0]
    memory = re.fullmatch(r"(.+)\s+(\d+)\s+GB", row["model"])
    model = memory.group(1) if memory else row["model"]
    capacity = memory.group(2) if memory else "&mdash;"
    retail = (
        '<span class="retail-current">'
        + link(row.get("retail_url", ""), number(row.get("retail_nok"), " kr"))
        + '</span>'
        + '<span class="historical-low">'
        + number(row.get("historical_low_nok"), " kr")
        + '</span>'
    )
    cells = [
        escape(str(year)) if year else "Ukjent",
        "<strong>" + escape(model) + "</strong>",
        capacity,
        link(row.get("canonical_url", ""), number(row["price"], " kr")),
        retail,
    ]
    search = " ".join([row["model"], str(year) if year else "unknown"]).lower()
    return (
        f'<tr data-year="{year}" data-search="{escape(search, quote=True)}">'
        + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"
    )


def generate_results(csv_path, output_path):
    with csv_path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        required = {"model", "price", "release_date", "retail_nok"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Input must be a three-signal deal analysis CSV")
        rows = list(reader)
    rows.sort(key=lambda row: row["model"])
    rows.sort(key=release_key, reverse=True)
    updated = datetime.fromtimestamp(csv_path.stat().st_mtime).astimezone().strftime("%d.%m.%Y %H:%M")
    headers = [
        "&Aring;r &#8595;", "GPU", "GB", "finn.no", "Butikkpris<br><small>(n&aring; vs lavest)</small>",
    ]
    html = """<!doctype html>
<html lang="no"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FinnGPU - Rimeligste GPUer fra finn.no</title>
<style>
body { font-family: Arial, sans-serif; font-size: 14px; max-width: 1024px; margin: 0 auto; padding: 10px; color: #000; background: #f0f0f0; }
h1 { color: #000080; text-align: center; font-size: 24px; border-bottom: 2px solid #000080; padding-bottom: 5px; }
a { color: #000080; text-decoration: none; }
a:hover { text-decoration: underline; }
.timestamp { color: #666; font-size: 12px; text-align: center; margin: 5px 0; }
.controls { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 10px 0; }
input { font: inherit; padding: 4px 6px; width: 240px; max-width: 100%; box-sizing: border-box; border: 1px solid #808080; }
#count { font-size: 12px; color: #666; }
.table-wrap { width: fit-content; max-width: 100%; overflow: auto; max-height: 76vh; }
table { border-collapse: collapse; background: white; font-size: 14px; }
th, td { border: 1px solid #808080; padding: 4px 6px; text-align: center; white-space: nowrap; font-variant-numeric: tabular-nums; }
th:nth-child(2), td:nth-child(2) { text-align: left; }
.retail-current, .historical-low { display: block; }
.historical-low { color: #666; font-size: 12px; margin-top: 2px; }
th { position: sticky; top: 0; background: #d3d3d3; font-weight: bold; z-index: 1; }
tbody tr { background: white; }
tbody tr:nth-child(even) { background: #f8f8f8; }
[hidden] { display: none !important; }
:focus-visible { outline: 3px solid #d88a25; outline-offset: 3px; }
@media (max-width: 1024px) {
  body { max-width: 100%; padding: 5px; }
  th:first-child, td:first-child { position: sticky; left: 0; background: inherit; }
  th:first-child { background: #d3d3d3; z-index: 2; }
}
</style></head><body>
<h1>FinnGPU - Rimeligste GPUer fra finn.no</h1>
"""
    html += f'<div class="timestamp">Oppdatert: {escape(updated)}</div>'
    html += '<div class="controls"><label for="search">Filtrer:</label><input id="search" type="search" placeholder="GPU, merke eller &aring;r">'
    html += f'<span id="count" aria-live="polite">{len(rows)} av {len(rows)} resultater</span></div>'
    html += '<div class="table-wrap" tabindex="0" role="region" aria-label="GPU results"><table><thead><tr>'
    html += "".join(f'<th scope="col">{header}</th>' for header in headers)
    html += "</tr></thead><tbody>" + "".join(render_row(row) for row in rows) + "</tbody></table></div>"
    html += """<p id="empty" hidden>Ingen treff.</p>
<script>
"use strict";
const rows = [...document.querySelectorAll("tbody tr")];
document.getElementById("search").addEventListener("input", event => {
  const query = event.target.value.toLowerCase().trim();
  let visible = 0;
  for (const row of rows) {
    row.hidden = !row.dataset.search.includes(query);
    if (!row.hidden) visible++;
  }
  document.getElementById("count").textContent = `${visible} av ${rows.length} resultater`;
  document.getElementById("empty").hidden = visible !== 0;
});
</script></body></html>
"""
    output_path.write_text(html, encoding="utf-8")
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="Create a standalone deal results page, newest release year first")
    parser.add_argument("--csv", type=Path, default=ROOT / "data/deals.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "data/deals.html")
    args = parser.parse_args()
    count = generate_results(args.csv, args.output)
    print(f"Saved {count} results, newest release year first, to {args.output}")


if __name__ == "__main__":
    main()
