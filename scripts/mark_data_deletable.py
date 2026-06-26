#!/usr/bin/env python3

"""
Script to mark downloaded data as deletable based on the CSV files produced by
download_all_data.py.

Usage: python mark_data_deletable.py <api_token> <data_dir>

For each downloaded CSV file, the script:
1. Reads the namespaced data type (<source_type>:<data_type>) and the max
   timestamp actually downloaded (from the CSV contents).
2. Calculates current time - 1 day.
3. Uses the LOWER of (max downloaded timestamp, current_time - 1 day) as the cutoff.
4. Marks data up to that timestamp as deletable.

Works with ALL data source types (aware, survey, etc.) — no assumptions about
data types are made, everything is discovered from the CSV files.

Example: python mark_data_deletable.py your_token_here ./data
"""

import csv
import json
import os
import sys
from datetime import datetime, timedelta

import requests


def get_one_day_ago_timestamp():
    """Get current time minus 1 day in Unix milliseconds"""
    one_day_ago = datetime.now() - timedelta(days=1)
    return int(one_day_ago.timestamp() * 1000)


def read_csv_summary(csv_file):
    """Read a downloaded CSV and return (namespaced_data_type, max_timestamp).

    Returns (None, None) if the file is empty or has no usable rows.
    """
    data_type = None
    max_timestamp = None

    with open(csv_file, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if data_type is None:
                data_type = (row.get("data_type") or "").strip() or None
            ts = row.get("timestamp")
            if ts is None or ts == "":
                continue
            try:
                ts = int(ts)
            except (TypeError, ValueError):
                continue
            if max_timestamp is None or ts > max_timestamp:
                max_timestamp = ts

    return data_type, max_timestamp


def mark_data_deletable(base_url, token, data_type, through_timestamp):
    """Mark data as deletable up to through_timestamp"""
    url = f"{base_url}/studies/api/data/mark-deletable"
    headers = {"Content-Type": "application/json", "Authorization": f"Token {token}"}

    payload = {"data_type": data_type, "through": through_timestamp}

    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error marking data as deletable: {e}")
        return None


def find_csv_files(data_dir):
    """Yield every .csv file under data_dir (recursively)"""
    for root, _dirs, files in os.walk(data_dir):
        for name in sorted(files):
            if name.endswith(".csv"):
                yield os.path.join(root, name)


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <api_token> <data_dir>")
        print(f"\nExample: {sys.argv[0]} your_token_here ./data")
        print(
            "\n<data_dir> is the directory produced by download_all_data.py."
            " For each downloaded CSV the cutoff is"
            " min(max downloaded timestamp, current_time - 1 day)."
        )
        print("Works with ALL data source types")
        sys.exit(1)

    token = sys.argv[1]
    data_dir = sys.argv[2]
    base_url = "http://localhost:8000"

    if not os.path.isdir(data_dir):
        print(f"Error: data directory '{data_dir}' does not exist")
        sys.exit(1)

    print("=== Data Deletion Marking Workflow ===")
    print("Strategy: for each downloaded CSV, mark up to")
    print("min(max downloaded timestamp, current_time - 1 day)")
    print("Works with ALL data source types (aware, survey, etc.)")
    print()

    # Calculate one-day-ago timestamp once (same for all)
    one_day_ago = get_one_day_ago_timestamp()
    print(f"Current time - 1 day: {one_day_ago}")
    print()

    csv_files = list(find_csv_files(data_dir))
    if not csv_files:
        print(f"Error: No CSV files found under '{data_dir}'")
        sys.exit(1)

    print(f"Found {len(csv_files)} CSV files")
    print()

    total_marked = 0

    for csv_file in csv_files:
        print(f"Processing {csv_file}")

        data_type, max_timestamp = read_csv_summary(csv_file)

        if not data_type:
            print(f"    Could not determine data type, skipping")
            continue
        if max_timestamp is None:
            print(f"    No timestamped rows, skipping")
            continue

        print(f"    Data type: {data_type}")
        print(f"    Max downloaded timestamp: {max_timestamp}")
        print(f"    Current - 1 day: {one_day_ago}")

        # Use the lower of the two timestamps
        through_timestamp = min(max_timestamp, one_day_ago)
        print(f"    Using cutoff: {through_timestamp}")

        # Mark as deletable
        result = mark_data_deletable(base_url, token, data_type, through_timestamp)

        if result:
            marked_any = any(
                item.get("marked") for item in result.get("results", [])
            )
            if marked_any:
                total_marked += 1
                print(
                    f"    Successfully marked as deletable up to {through_timestamp}"
                )
            else:
                print(f"    Nothing marked (already deletable or not downloaded)")
        else:
            print(f"    Failed to mark as deletable")

        print()

    # Summary
    print("=== Summary ===")
    print(f"CSV files that resulted in newly marked data: {total_marked}")
    print("For each, used min(max downloaded timestamp, current_time - 1 day)")
    print("Data can now be deleted by the aware_migrations daemon")


if __name__ == "__main__":
    main()
