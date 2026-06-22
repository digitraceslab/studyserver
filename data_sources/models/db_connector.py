import mysql.connector
from django.conf import settings
import time

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
            database=settings.AWARE_DB_NAME
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
    """ Gets a list of available tables that have data for the given device_label. """
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
            database=settings.AWARE_DB_NAME
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
                device_uids = [row[0] for row in rows if isinstance(row, tuple) and len(row) > 0]
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


def _run_aware_table_query(cursor, base_select, table_name, id_column, id_values, start_date=None, end_date=None, limit=None, offset=0):
    """Build and run a parametrized query against an AWARE table.

    - `base_select` should be the SELECT prefix (e.g. "SELECT *" or "SELECT COUNT(*) as row_count").
    - `id_column` is the column to filter on (device_id or device_uid).
    - `id_values` is a non-empty list of parameter values to use in the IN(...) clause.
    Returns the fetched rows (list).
    """
    if not id_values:
        return []

    is_aggregate = str(base_select).strip().upper().startswith(("SELECT COUNT", "SELECT MAX"))
    id_placeholders = ",".join(["%s"] * len(id_values))
    query_str = (
        f"{base_select} FROM `{table_name}` "
        f"WHERE {id_column} IN ({id_placeholders})"
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


def query_aware_data(base_query, device_label, table_name, limit=None, start_date=None, end_date=None, offset=0):
    """
    Runs a data query against the AWARE database. The query parameter should be either "SELECT COUNT(*)" or "SELECT *".
    """
    if not device_label:
        print("Invalid AWARE device label provided.", device_label)
        return []

    device_ids = get_device_ids_for_label(device_label)
    if not device_ids:
        return []
    results = []

    try:
        database = mysql.connector.connect(
            host=settings.AWARE_DB_HOST,
            port=settings.AWARE_DB_PORT,
            user=settings.AWARE_DB_RO_USER,
            password=settings.AWARE_DB_RO_PASSWORD,
            database=settings.AWARE_DB_NAME
        )
        cursor = database.cursor()

        cursor.execute("SHOW TABLES")
        all_tables = [table[0] for table in cursor.fetchall()]
        transformed_table_name = f"{table_name}_transformed"
        if transformed_table_name not in all_tables:
            print(f"Transformed table {transformed_table_name} does not exist in AWARE database.")
            return []
        cursor.close()

        cursor = database.cursor(dictionary=True)
        # Map device_lookup.id (device_uid) -> device_uuid (the original device id)
        device_id_format = ",".join(["%s"] * len(device_ids))
        query_string = f"SELECT id, device_uuid FROM device_lookup WHERE device_uuid IN ({device_id_format})"
        cursor.execute(query_string, tuple(device_ids))
        rows = cursor.fetchall()
        device_uids = [row['id'] for row in rows if isinstance(row, dict) and len(row) > 0]
        device_uid_to_device_id = {row['id']: row['device_uuid'] for row in rows if isinstance(row, dict)}

        if device_uids:
            # Query only the transformed table (processed data only)
            results_transformed = _run_aware_table_query(cursor, base_query, transformed_table_name, 'device_uid', device_uids, start_date, end_date, limit, offset)
            for row in results_transformed:
                if isinstance(row, dict):
                    row['device_id'] = device_uid_to_device_id.get(row.get('device_uid'), None)
                    row.pop('device_uid', None)
            results.extend(results_transformed)

        cursor.close()
        database.close()

        return results
    
    except mysql.connector.Error as e:
        print(f"Error querying Aware data: {e}")
        return results



def get_aware_data(device_label, table_name='battery', limit=1000, start_date=None, end_date=None, offset=0):
    """
    Connects to the AWARE DB and fetches the latest records for a specific
    AWARE device ID. Returns a list of dictionaries.
    """
    return query_aware_data(
        "SELECT *", device_label, table_name, limit, start_date, end_date, offset
    )


def get_aware_count(device_label, table_name='battery', start_date=None, end_date=None):
    """Return the number of rows available for the given AWARE data_type.

    Counts rows in the original and transformed tables (if present) for the
    provided device_label and optional time range.
    """
    rows = query_aware_data(
        "SELECT COUNT(*) as row_count", device_label, table_name, None, start_date, end_date, 0
    )
    if not rows:
        return 0
    return rows[0].get('row_count', 0)


def get_aware_max_timestamp(device_label, table_name='battery', start_date=None, end_date=None):
    """Return the newest row timestamp (Unix ms) for the given AWARE data_type, or None.

    Queries MAX(timestamp) in the transformed table for the device_label and optional
    time range. Returns None when there is no data.
    """
    rows = query_aware_data(
        "SELECT MAX(timestamp) as max_ts", device_label, table_name, None, start_date, end_date, 0
    )
    if not rows:
        return None
    value = rows[0].get('max_ts')
    return int(value) if value is not None else None

