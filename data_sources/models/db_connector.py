import time

import mysql.connector
from django.conf import settings

# Simple in-memory cache for get_aware_tables: { device_label: (timestamp, tables_list) }
_aware_tables_cache = {}


def get_device_ids_for_label(device_label):
    """Gets a list of device_ids associated with the given device_label."""
    if not device_label:
        print("Invalid AWARE device label provided.", device_label)
        return []

    device_ids = []
    try:
        database = mysql.connector.connect(
            host=settings.AWARE_DB_HOST,
            port=settings.AWARE_DB_PORT,
            user=settings.AWARE_DB_RO_USER,
            password=settings.AWARE_DB_RO_PASSWORD,
            database=settings.AWARE_DB_NAME,
        )
        cursor = database.cursor()

        query = "SELECT device_id FROM aware_device WHERE label = %s"
        cursor.execute(query, (device_label,))
        rows = cursor.fetchall()
        device_ids = [row[0] for row in rows]

        cursor.close()
        database.close()
        return device_ids

    except mysql.connector.Error as e:
        print(f"Error in get_device_ids_for_label: {e}")
        return []


def get_aware_tables(device_label):
    """Gets a list of available tables that have data for the given device_label."""
    if not device_label:
        print("Invalid AWARE device label provided.", device_label)
        return []
    device_ids = get_device_ids_for_label(device_label)
    if not device_ids:
        return []

    # Return cached result when available and fresh (60s)
    cached = _aware_tables_cache.get(device_label)
    if cached:
        ts, tables = cached
        if time.time() - ts < 60:
            return tables

    tables_with_data = []
    try:
        database = mysql.connector.connect(
            host=settings.AWARE_DB_HOST,
            port=settings.AWARE_DB_PORT,
            user=settings.AWARE_DB_RO_USER,
            password=settings.AWARE_DB_RO_PASSWORD,
            database=settings.AWARE_DB_NAME,
        )
        cursor = database.cursor()

        cursor.execute("SHOW TABLES")
        all_tables = [table[0] for table in cursor.fetchall()]
        # Only consider transformed tables; researchers must see processed data only
        for table_name in all_tables:
            if not table_name.endswith("_transformed"):
                continue
            try:
                table_name_without_suffix = table_name.replace("_transformed", "")
                # Find the device_lookup ids that correspond to the device_ids
                device_id_format = ",".join(["%s"] * len(device_ids))
                query_string = f"SELECT id FROM device_lookup WHERE device_uuid IN ({device_id_format})"
                cursor.execute(query_string, tuple(device_ids))

                rows = cursor.fetchall()
                device_uids = [
                    row[0] for row in rows if isinstance(row, tuple) and len(row) > 0
                ]
                if not device_uids:
                    continue

                device_uid_format = ",".join(["%s"] * len(device_uids))
                query = f"SELECT 1 FROM `{table_name}` WHERE device_uid IN ({device_uid_format}) LIMIT 1"
                cursor.execute(query, tuple(device_uids))

                if cursor.fetchone():
                    tables_with_data.append(table_name_without_suffix)

            except mysql.connector.Error:
                continue

        cursor.close()
        database.close()
        # Cache the result for a short period to avoid repeated introspection
        try:
            _aware_tables_cache[device_label] = (time.time(), tables_with_data)
        except Exception:
            pass
        return tables_with_data

    except mysql.connector.Error as e:
        print(f"Error in get_aware_tables: {e}")
        return []


def _run_aware_table_query(
    cursor,
    base_select,
    table_name,
    id_column,
    id_values,
    start_date=None,
    end_date=None,
    limit=None,
    offset=0,
):
    """Build and run a parametrized query against an AWARE table.

    - `base_select` should be the SELECT prefix (e.g. "SELECT *" or "SELECT COUNT(*) as row_count").
    - `id_column` is the column to filter on (device_id or device_uid).
    - `id_values` is a non-empty list of parameter values to use in the IN(...) clause.
    Returns the fetched rows (list).
    """
    if not id_values:
        return []

    is_aggregate = (
        str(base_select).strip().upper().startswith(("SELECT COUNT", "SELECT MAX"))
    )
    id_placeholders = ",".join(["%s"] * len(id_values))
    query_str = (
        f"{base_select} FROM `{table_name}` WHERE {id_column} IN ({id_placeholders})"
    )
    params = list(id_values)

    if start_date:
        query_str += " AND timestamp >= %s"
        params.append(int(start_date.timestamp() * 1000))
    if end_date:
        query_str += " AND timestamp <= %s"
        params.append(int(end_date.timestamp() * 1000))

    if not is_aggregate:
        query_str += " ORDER BY timestamp DESC"
        if limit is not None:
            try:
                limit_val = int(limit)
            except (TypeError, ValueError):
                limit_val = None
            if limit_val and limit_val > 0:
                if offset and int(offset) > 0:
                    query_str += " LIMIT %s OFFSET %s"
                    params.extend([limit_val, int(offset)])
                else:
                    query_str += " LIMIT %s"
                    params.append(limit_val)

    cursor.execute(query_str, tuple(params))
    return cursor.fetchall()


def _connect_and_resolve_device_uids(device_label):
    """Open a MySQL connection and resolve device_uuid -> device_uid mappings.

    Validates ``device_label``, fetches device IDs, opens the AWARE MySQL
    connection, then resolves ``device_uuid`` -> ``device_uid`` via
    ``device_lookup``.

    Returns ``(database, cursor, device_uids, device_uid_to_device_id)`` on
    success where ``cursor`` is a dictionary cursor.  The caller is responsible
    for closing ``cursor`` and ``database`` in all code paths.

    Returns ``(None, None, [], {})`` when there are no matching devices (caller
    should return early) or raises ``mysql.connector.Error`` on connection
    failure.
    """
    if not device_label:
        print("Invalid AWARE device label provided.", device_label)
        return None, None, [], {}

    device_ids = get_device_ids_for_label(device_label)
    if not device_ids:
        return None, None, [], {}

    database = mysql.connector.connect(
        host=settings.AWARE_DB_HOST,
        port=settings.AWARE_DB_PORT,
        user=settings.AWARE_DB_RO_USER,
        password=settings.AWARE_DB_RO_PASSWORD,
        database=settings.AWARE_DB_NAME,
    )
    cursor = database.cursor(dictionary=True)

    device_id_format = ",".join(["%s"] * len(device_ids))
    lookup_query = (
        f"SELECT id, device_uuid FROM device_lookup "
        f"WHERE device_uuid IN ({device_id_format})"
    )
    cursor.execute(lookup_query, tuple(device_ids))
    rows = cursor.fetchall()
    device_uids = [row["id"] for row in rows if isinstance(row, dict)]
    device_uid_to_device_id = {
        row["id"]: row["device_uuid"] for row in rows if isinstance(row, dict)
    }

    return database, cursor, device_uids, device_uid_to_device_id


def query_aware_data(
    base_query,
    device_label,
    table_name,
    limit=None,
    start_date=None,
    end_date=None,
    offset=0,
):
    """
    Runs a data query against the AWARE database. The query parameter should be either "SELECT COUNT(*)" or "SELECT *".
    """
    results = []
    database = None
    cursor = None
    try:
        database, cursor, device_uids, device_uid_to_device_id = (
            _connect_and_resolve_device_uids(device_label)
        )
        if database is None:
            return []

        # Verify the transformed table exists (use a plain cursor for SHOW TABLES)
        plain_cursor = database.cursor()
        plain_cursor.execute("SHOW TABLES")
        all_tables = [table[0] for table in plain_cursor.fetchall()]
        plain_cursor.close()

        transformed_table_name = f"{table_name}_transformed"
        if transformed_table_name not in all_tables:
            print(
                f"Transformed table {transformed_table_name} does not exist in AWARE database."
            )
            cursor.close()
            database.close()
            return []

        if device_uids:
            # Query only the transformed table (processed data only)
            results_transformed = _run_aware_table_query(
                cursor,
                base_query,
                transformed_table_name,
                "device_uid",
                device_uids,
                start_date,
                end_date,
                limit,
                offset,
            )
            for row in results_transformed:
                if isinstance(row, dict):
                    row["device_id"] = device_uid_to_device_id.get(
                        row.get("device_uid"), None
                    )
                    row.pop("device_uid", None)
            results.extend(results_transformed)

        cursor.close()
        database.close()

        return results

    except mysql.connector.Error as e:
        print(f"Error querying Aware data: {e}")
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if database is not None:
            try:
                database.close()
            except Exception:
                pass
        return results


def get_aware_data(device_label, table_name="battery", timestamp=0, limit=1000):
    """Fetch rows from the AWARE DB using a timestamp cursor.

    Returns rows from ``<table_name>_transformed`` whose ``timestamp`` field is
    >= ``timestamp`` (Unix time in milliseconds), ordered by ``timestamp`` ASC.
    Applies the soft-limit rule: if exactly ``limit`` rows were returned, all
    rows sharing the last timestamp are fetched and merged in so that the caller
    can safely advance its cursor to ``max(timestamp) + 1``.

    Each returned dict has ``device_id`` (the original device UUID) and no
    ``device_uid`` field.
    """
    database = None
    cursor = None
    try:
        database, cursor, device_uids, device_uid_to_device_id = (
            _connect_and_resolve_device_uids(device_label)
        )
        if database is None:
            return []

        if not device_uids:
            cursor.close()
            database.close()
            return []

        transformed_table_name = f"{table_name}_transformed"
        device_uid_format = ",".join(["%s"] * len(device_uids))

        # Primary cursor query
        primary_query = (
            f"SELECT * FROM `{transformed_table_name}` "
            f"WHERE device_uid IN ({device_uid_format}) "
            f"AND timestamp >= %s "
            f"ORDER BY timestamp ASC "
            f"LIMIT %s"
        )
        primary_params = tuple(device_uids) + (int(timestamp), int(limit))
        cursor.execute(primary_query, primary_params)
        rows = cursor.fetchall()

        # Soft-limit: if we got exactly `limit` rows, fetch all rows at last_ts
        if len(rows) == int(limit) and rows:
            last_ts = rows[-1]["timestamp"]
            # Trim trailing rows sharing last_ts from the primary result
            trimmed = [r for r in rows if r["timestamp"] != last_ts]
            # Fetch the complete group at last_ts
            tail_query = (
                f"SELECT * FROM `{transformed_table_name}` "
                f"WHERE device_uid IN ({device_uid_format}) "
                f"AND timestamp = %s "
                f"ORDER BY timestamp ASC"
            )
            tail_params = tuple(device_uids) + (last_ts,)
            cursor.execute(tail_query, tail_params)
            tail_rows = cursor.fetchall()
            rows = trimmed + tail_rows

        cursor.close()
        database.close()

        # Map device_uid -> device_id and strip device_uid
        for row in rows:
            row["device_id"] = device_uid_to_device_id.get(row.get("device_uid"))
            row.pop("device_uid", None)
        return rows

    except mysql.connector.Error as e:
        print(f"Error in get_aware_data: {e}")
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if database is not None:
            try:
                database.close()
            except Exception:
                pass
        return []


def get_aware_count(device_label, table_name="battery", start_date=None, end_date=None):
    """Return the number of rows available for the given AWARE data_type.

    Counts rows in the original and transformed tables (if present) for the
    provided device_label and optional time range.
    """
    rows = query_aware_data(
        "SELECT COUNT(*) as row_count",
        device_label,
        table_name,
        None,
        start_date,
        end_date,
        0,
    )
    if not rows:
        return 0
    return rows[0].get("row_count", 0)


def get_aware_max_timestamp(
    device_label, table_name="battery", start_date=None, end_date=None
):
    """Return the newest row timestamp (Unix ms) for the given AWARE data_type, or None.

    Queries MAX(timestamp) in the transformed table for the device_label and optional
    time range. Returns None when there is no data.
    """
    rows = query_aware_data(
        "SELECT MAX(timestamp) as max_ts",
        device_label,
        table_name,
        None,
        start_date,
        end_date,
        0,
    )
    if not rows:
        return None
    value = rows[0].get("max_ts")
    return int(value) if value is not None else None


def insert_deletion_request(device_label, table_name, delete_before):
    """Insert deletion requests for all devices matching device_label.

    Args:
        device_label: The AWARE device label to resolve device_uids for
        table_name: The base table name (will be suffixed with '_transformed')
        delete_before: Unix timestamp in milliseconds (exclusive upper bound)

    Returns:
        Number of device_uids that were written to deletion_requests
    """
    database = None
    cursor = None
    device_uids = []

    try:
        # Resolve device_uids using existing connection helper
        database, cursor, device_uids, device_uid_to_device_id = (
            _connect_and_resolve_device_uids(device_label)
        )
        if database is None or not device_uids:
            return 0

        # Build the transformed table name
        transformed_table_name = f"{table_name}_transformed"
        requested_at = int(time.time() * 1000)  # Current Unix timestamp in milliseconds

        # Insert deletion requests for each device_uid. delete_before follows
        # the latest request in both directions: a lower value retracts a
        # pending request so data past the new bound is no longer deleted
        # (already-deleted data cannot be restored).
        for device_uid in device_uids:
            insert_query = (
                "INSERT INTO deletion_requests (table_name, device_uid, delete_before, requested_at, processed_at) "
                "VALUES (%s, %s, %s, %s, NULL) "
                "ON DUPLICATE KEY UPDATE delete_before = VALUES(delete_before), processed_at = NULL"
            )
            cursor.execute(
                insert_query,
                (transformed_table_name, device_uid, delete_before, requested_at),
            )

        database.commit()
        return len(device_uids)

    except mysql.connector.Error:
        # Propagate so callers report the failure instead of a false success.
        if database is not None:
            try:
                database.rollback()
            except Exception:
                pass
        raise

    finally:
        # Ensure resources are cleaned up
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if database is not None:
            try:
                database.close()
            except Exception:
                pass


def remove_deletion_requests(device_label):
    """Remove all unprocessed deletion requests for devices matching device_label.

    Args:
        device_label: The AWARE device label to resolve device_uids for

    Returns:
        Number of deletion requests removed
    """
    database = None
    cursor = None
    device_uids = []

    try:
        # Resolve device_uids using existing connection helper
        database, cursor, device_uids, device_uid_to_device_id = (
            _connect_and_resolve_device_uids(device_label)
        )
        if database is None or not device_uids:
            return 0

        # Only remove pending requests; processed rows are kept as an audit record.
        placeholders = ", ".join(["%s"] * len(device_uids))
        delete_query = (
            f"DELETE FROM deletion_requests WHERE device_uid IN ({placeholders}) "
            "AND processed_at IS NULL"
        )
        cursor.execute(delete_query, tuple(device_uids))

        database.commit()
        return cursor.rowcount

    except mysql.connector.Error:
        # Propagate so callers report the failure instead of a false success.
        if database is not None:
            try:
                database.rollback()
            except Exception:
                pass
        raise

    finally:
        # Ensure resources are cleaned up
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if database is not None:
            try:
                database.close()
            except Exception:
                pass
