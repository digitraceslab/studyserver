#!/usr/bin/env python3

"""
Script to cancel ALL pending deletable marks so no further data is deleted.

Usage: python unmark_data_deletable.py <api_token>

Calls the unmark-deletable endpoint, which for every active consent removes
pending deletion requests and clears the deletable watermarks. Data that has
already been deleted cannot be restored.

Example: python unmark_data_deletable.py your_token_here
"""

import sys

import requests


def unmark_data_deletable(base_url, token):
    """Cancel all pending deletable marks and return the endpoint's response"""
    url = f"{base_url}/studies/api/data/unmark-deletable"
    headers = {"Authorization": f"Token {token}"}

    try:
        response = requests.post(url, headers=headers)
        response.raise_for_status()
        return response.json()
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
        raise RuntimeError(f"Error unmarking data as deletable: {message}") from e


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <api_token>")
        print(f"\nExample: {sys.argv[0]} your_token_here")
        print(
            "\nCancels ALL pending deletable marks for every participant and"
            " data source, so no further data is deleted."
        )
        sys.exit(1)

    token = sys.argv[1]
    base_url = "http://localhost:8000"

    print("=== Cancel Pending Deletions ===")
    print("Cancelling all pending deletable marks...")
    print()

    result = unmark_data_deletable(base_url, token)

    for item in result.get("results", []):
        participant_id = item.get("participant_id")
        source_type = item.get("source_type")
        if item.get("unmarked"):
            print(
                f"  {participant_id} ({source_type}): unmarked,"
                f" {item.get('watermarks_cleared', 0)} watermarks cleared"
            )
        else:
            detail = item.get("detail")
            reason = item.get("reason", "unknown reason")
            if detail:
                reason = f"{reason}: {detail}"
            print(f"  {participant_id} ({source_type}): FAILED ({reason})")

    print()
    print("=== Summary ===")
    print(f"Data sources unmarked: {result.get('unmarked_count', 0)}")


if __name__ == "__main__":
    main()
