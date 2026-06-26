#!/usr/bin/env python3

"""
Deduplicate the CSV data files produced by download_all_data.py.

Removes exact duplicate rows from each file, including duplicates between newly
downloaded rows and rows that already existed in the file (e.g. overlap at the
seam where a new download is appended to an existing file).

Each file is read in chunks and rewritten to a temporary file, which then
atomically replaces the original. Only a set of already-seen row keys is held in
memory, so memory use stays bounded by the number of UNIQUE rows rather than the
full file size.

Usage: python deduplicate_data.py <path> [chunk_size]

<path> may be a single CSV file or a directory (every .csv under it is processed).
Example: python deduplicate_data.py ./data
"""

import os
import sys

import pandas as pd

DEFAULT_CHUNK_SIZE = 100_000


def dedupe_file(path, chunk_size):
    """Rewrite `path` in place with exact duplicate rows removed.

    Returns (rows_in, rows_out).
    """
    tmp_path = path + ".dedup.tmp"
    seen = set()
    rows_in = 0
    rows_out = 0
    header_written = False

    try:
        reader = pd.read_csv(
            path, chunksize=chunk_size, dtype=str, keep_default_na=False
        )
        for chunk in reader:
            rows_in += len(chunk)

            # Exact per-row key: every column value tab-joined. dtype=str +
            # keep_default_na=False guarantees plain strings (empty for blanks).
            keys = chunk.agg("\t".join, axis=1)

            # Drop rows seen in earlier chunks (isin) and repeats within this
            # chunk (duplicated keeps the first occurrence).
            is_dup = keys.isin(seen) | keys.duplicated()
            unique = chunk[~is_dup]

            seen.update(keys[~is_dup])
            rows_out += len(unique)

            unique.to_csv(
                tmp_path,
                mode="w" if not header_written else "a",
                header=not header_written,
                index=False,
            )
            header_written = True
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    if not header_written:
        # Source had no data rows; leave the original untouched.
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return rows_in, rows_out

    os.replace(tmp_path, path)
    return rows_in, rows_out


def find_csv_files(path):
    """Return the list of CSV files for `path` (a single file or a directory)."""
    if os.path.isfile(path):
        return [path]
    csv_files = []
    for root, _dirs, files in os.walk(path):
        for name in sorted(files):
            if name.endswith(".csv"):
                csv_files.append(os.path.join(root, name))
    return csv_files


def main():
    if len(sys.argv) not in (2, 3):
        print(f"Usage: {sys.argv[0]} <path> [chunk_size]")
        print(f"\nExample: {sys.argv[0]} ./data")
        print(
            "\n<path> is a CSV file or a directory of CSV files produced by"
            " download_all_data.py. Exact duplicate rows are removed in place."
        )
        sys.exit(1)

    path = sys.argv[1]
    chunk_size = DEFAULT_CHUNK_SIZE
    if len(sys.argv) == 3:
        try:
            chunk_size = int(sys.argv[2])
            if chunk_size < 1:
                raise ValueError
        except ValueError:
            print("Error: chunk_size must be a positive integer")
            sys.exit(1)

    if not os.path.exists(path):
        print(f"Error: path '{path}' does not exist")
        sys.exit(1)

    csv_files = find_csv_files(path)
    if not csv_files:
        print(f"Error: No CSV files found under '{path}'")
        sys.exit(1)

    print("=== Data Deduplication ===")
    print(f"Files to process: {len(csv_files)}")
    print(f"Chunk size: {chunk_size}")
    print()

    total_in = 0
    total_removed = 0

    for csv_file in csv_files:
        print(f"Deduplicating {csv_file}")
        try:
            rows_in, rows_out = dedupe_file(csv_file, chunk_size)
        except Exception as e:
            print(f"    Error: {e}")
            continue

        removed = rows_in - rows_out
        total_in += rows_in
        total_removed += removed
        print(f"    {rows_in} rows -> {rows_out} rows ({removed} duplicates removed)")

    print()
    print("=== Summary ===")
    print(f"Total rows processed: {total_in}")
    print(f"Total duplicates removed: {total_removed}")


if __name__ == "__main__":
    main()
