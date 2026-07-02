#!/usr/bin/env python3

"""
Script to download ALL data and save as CSV files.
Usage: python download_all_data.py <api_token> <output_dir> [start_timestamp] [end_timestamp]
Example: python download_all_data.py your_token_here ./data 0 1776188812150
Example: python download_all_data.py your_token_here ./data - 1776188812150
Example: python download_all_data.py your_token_here ./data

Timestamps are Unix milliseconds:
- "-" as start_timestamp = use the automatic default (last saved - 3s)
- 0 = download from beginning
- Use https://www.epochconverter.com/ to convert dates to timestamps

Defaults (for incremental downloads):
- start_timestamp: the last saved timestamp - 3s, per participant/data type
  (0 if nothing has been downloaded yet). The 3s overlap is re-fetched so no
  rows are missed; run deduplicate_data.py afterwards to drop the duplicates.
- end_timestamp: the current time.

Downloads ALL data types for ALL participants from ALL data sources
"""

import json
import os
import sys
import time

import pandas as pd
import requests


def get_data_types(base_url, token):
    """Get the list of namespaced data types (<source_type>:<data_type>) in the study"""
    url = f"{base_url}/studies/api/data"
    headers = {"Authorization": f"Token {token}"}

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data.get("data_types", [])
    except requests.exceptions.RequestException as e:
        print(f"Error getting data types: {e}")
        sys.exit(1)


def get_participants(base_url, token, data_type):
    """Get the participant ids that have data for the given namespaced data type"""
    url = f"{base_url}/studies/api/participants"
    headers = {"Authorization": f"Token {token}"}
    params = {"data_type": data_type}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        return data.get("participants", [])
    except requests.exceptions.RequestException as e:
        print(f"Error getting participants: {e}")
        sys.exit(1)


def get_existing_max_timestamp(csv_file):
    """Return the largest timestamp already saved in csv_file, or None.

    Reads the file in chunks so memory stays bounded for large files.
    """
    if not os.path.exists(csv_file):
        return None
    max_ts = None
    try:
        for chunk in pd.read_csv(csv_file, usecols=["timestamp"], chunksize=100_000):
            ts = pd.to_numeric(chunk["timestamp"], errors="coerce").dropna()
            if ts.empty:
                continue
            chunk_max = int(ts.max())
            if max_ts is None or chunk_max > max_ts:
                max_ts = chunk_max
    except (ValueError, KeyError, pd.errors.EmptyDataError):
        return None
    return max_ts


def download_data_for_participant(
    base_url,
    token,
    participant_id,
    source_type,
    data_type,
    start_timestamp,
    end_timestamp,
    output_dir,
):
    """Download data for a single participant/data_type within timestamp range and save as CSV"""
    url = f"{base_url}/studies/api/data"
    headers = {"Authorization": f"Token {token}"}

    # Create participant directory if it doesn't exist
    participant_dir = os.path.join(output_dir, participant_id)
    os.makedirs(participant_dir, exist_ok=True)

    # CSV file path
    csv_file = os.path.join(participant_dir, f"{source_type}_{data_type}.csv")

    # Column order is locked on the first write so appended batches with
    # differing/missing columns stay aligned with the header.
    columns = None

    file_has_header = False
    if os.path.exists(csv_file):
        try:
            columns = list(pd.read_csv(csv_file, nrows=0).columns)
            file_has_header = True
        except pd.errors.EmptyDataError:
            pass

    if start_timestamp is None:
        # Default: resume from just before the last saved timestamp, re-fetching
        # a 3s overlap (deduplicate_data.py removes the resulting duplicates).
        existing_max = get_existing_max_timestamp(csv_file)
        if existing_max is not None:
            last_timestamp = max(0, existing_max - 3000)
            print(f"    Resuming from timestamp {last_timestamp} (last saved - 3s)")
        else:
            last_timestamp = 0
    else:
        last_timestamp = start_timestamp

    batch_count = 0
    total_rows = 0

    while True:
        batch_count += 1
        params = {
            "data_type": f"{source_type}:{data_type}",
            "participant_id": participant_id,
            "timestamp": last_timestamp,
            "limit": 1000,  # Fixed batch size for memory efficiency
            "advance": "true",
        }

        print(
            f"    Batch {batch_count}: Fetching data from timestamp {last_timestamp}..."
        )

        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()

            # Check for errors
            if "error" in data:
                raise RuntimeError(f"Server returned an error: {data['error']}")

            rows = data.get("data", [])
            row_count = len(rows)

            if row_count == 0:
                print(f"    No more data to download")
                return total_rows

            df = pd.DataFrame(rows)
            # Coerce timestamp to int; the API rejects non-integer timestamp
            # query params on the next request.
            df["timestamp"] = df["timestamp"].astype("int64")
            max_batch_timestamp = int(df["timestamp"].max())

            # Append the batch to the CSV (deduplication happens afterwards via
            # deduplicate_data.py).
            if columns is None:
                columns = list(df.columns)
            dropped_columns = [c for c in df.columns if c not in columns]
            if dropped_columns:
                print(
                    f"    WARNING: batch contains columns missing from the"
                    f" existing file and their values are NOT saved:"
                    f" {dropped_columns}. Re-download this data type into a"
                    f" fresh file to capture them."
                )
            df = df.reindex(columns=columns)
            df.to_csv(
                csv_file,
                mode="a",
                header=not file_has_header,
                index=False,
            )
            file_has_header = True

            total_rows += row_count
            print(
                f"    Downloaded {row_count} rows, max timestamp: {max_batch_timestamp}, saved to {csv_file}"
            )

            # Check if we've reached our end timestamp
            if max_batch_timestamp >= end_timestamp:
                print(f"    Reached end timestamp {end_timestamp}")
                return total_rows

            # Prepare for next batch. Advance past the max timestamp: the API
            # returns ALL rows sharing the last timestamp, so +1 won't skip any
            # rows and avoids re-fetching the same group forever.
            last_timestamp = max_batch_timestamp + 1

            # Small delay to avoid overwhelming the server
            time.sleep(0.1)

        except requests.exceptions.RequestException as e:
            # Include the error message from the response body, if any.
            message = str(e)
            if e.response is not None:
                try:
                    body = e.response.json()
                    if "error" in body:
                        message = f"{e}\nServer says: {body['error']}"
                except ValueError:
                    pass
            raise RuntimeError(f"Error downloading data: {message}") from e


def main():
    if not 3 <= len(sys.argv) <= 5:
        print(
            f"Usage: {sys.argv[0]} <api_token> <output_dir> [start_timestamp] [end_timestamp]"
        )
        print(f"Example: {sys.argv[0]} your_token_here ./data 0 1776188812150")
        print(f"Example: {sys.argv[0]} your_token_here ./data - 1776188812150")
        print(f"Example: {sys.argv[0]} your_token_here ./data")
        print("\nTimestamps are Unix milliseconds (use 0 for beginning).")
        print('Pass "-" as start_timestamp to use the automatic default.')
        print(
            "start_timestamp defaults to the last saved timestamp - 3s, per"
            " participant/data type (0 if nothing downloaded yet)."
        )
        print("end_timestamp defaults to the current time.")
        print("Downloads ALL data types for ALL participants from ALL data sources")
        sys.exit(1)

    token = sys.argv[1]
    output_dir = sys.argv[2]
    try:
        # "-" keeps the automatic start (last saved - 3s) while still allowing
        # an explicit end_timestamp.
        start_timestamp = (
            int(sys.argv[3]) if len(sys.argv) >= 4 and sys.argv[3] != "-" else None
        )
        end_timestamp = (
            int(sys.argv[4]) if len(sys.argv) >= 5 else int(time.time() * 1000)
        )
    except ValueError:
        print("Error: timestamps must be integers (Unix milliseconds)")
        sys.exit(1)

    base_url = "http://localhost:8000"

    print("=== Data Download Workflow ===")
    if start_timestamp is None:
        print("Start timestamp: auto (last saved - 3s, per participant/data type)")
    else:
        print(f"Start timestamp: {start_timestamp}")
    print(f"End timestamp: {end_timestamp}")
    print(f"Output directory: {output_dir}")
    print("Will download ALL data types for ALL participants from ALL data sources")
    print()

    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    # Step 1: Get the list of available data types
    print("Step 1: Getting data type list...")
    data_types = get_data_types(base_url, token)

    if not data_types:
        print("Error: No data types found")
        sys.exit(1)

    print(f"Found {len(data_types)} data types: {', '.join(data_types)}")
    print()

    # Step 2: Process each data type for every participant that has it
    total_downloaded = 0

    for namespaced_type in data_types:
        source_type, _, data_type = namespaced_type.partition(":")
        if not data_type:
            print(f"Skipping malformed data type: {namespaced_type}")
            continue

        participants = get_participants(base_url, token, namespaced_type)
        if not participants:
            print(f"Data type {namespaced_type}: No participants found")
            continue

        print(f"Processing {namespaced_type} for {len(participants)} participants")

        for participant_id in participants:
            print(
                f"  Downloading {namespaced_type} for participant {participant_id}..."
            )
            downloaded = download_data_for_participant(
                base_url,
                token,
                participant_id,
                source_type,
                data_type,
                start_timestamp,
                end_timestamp,
                output_dir,
            )
            total_downloaded += downloaded
            print(
                f"  Total downloaded for {participant_id} {namespaced_type}: {downloaded} rows"
            )

        print()

    # Summary
    print("=== Summary ===")
    print(f"Total rows downloaded: {total_downloaded}")
    print(f"Data saved to: {output_dir}")
    print(
        f"Directory structure: {output_dir}/<participant_id>/<source_type>_<data_type>.csv"
    )


if __name__ == "__main__":
    main()
