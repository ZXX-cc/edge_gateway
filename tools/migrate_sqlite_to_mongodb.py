#!/usr/bin/env python3
"""把历史 SQLite 全量迁移到网关运行时 MongoDB，并逐行校验。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple
from urllib.parse import quote_plus

import yaml

try:
    from bson.binary import Binary
    from pymongo import MongoClient, ReplaceOne
except ImportError:  # pragma: no cover - handled as a command-line error
    Binary = None
    MongoClient = None
    ReplaceOne = None


MIGRATION_META_FIELD = "_sqlite_migration"


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def user_tables(connection: sqlite3.Connection) -> List[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def table_info(connection: sqlite3.Connection, table: str) -> List[sqlite3.Row]:
    return connection.execute(
        f"PRAGMA table_info({quote_identifier(table)})"
    ).fetchall()


def primary_key_columns(columns: Sequence[sqlite3.Row]) -> List[str]:
    return [
        str(column["name"])
        for column in sorted(columns, key=lambda item: int(item["pk"]))
        if int(column["pk"]) > 0
    ]


def json_columns(columns: Sequence[sqlite3.Row]) -> set[str]:
    return {
        str(column["name"])
        for column in columns
        if "JSON" in str(column["type"] or "").upper()
    }


def transform_value(value: Any, is_json: bool) -> Any:
    if value is None:
        return None
    if is_json and isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    if isinstance(value, bytes):
        return Binary(value) if Binary is not None else value
    return value


def document_id(table: str, row: Mapping[str, Any], pk_columns: Sequence[str]) -> str:
    if pk_columns:
        identity = [row[column] for column in pk_columns]
    else:
        identity = sorted(row.items())
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(f"{table}:{encoded}".encode("utf-8")).hexdigest()


def row_to_document(
    table: str,
    row: sqlite3.Row,
    pk_columns: Sequence[str],
    json_column_names: set[str],
) -> Dict[str, Any]:
    raw = dict(row)
    document = {
        key: transform_value(value, key in json_column_names)
        for key, value in raw.items()
    }
    document["_id"] = document_id(table, raw, pk_columns)
    document[MIGRATION_META_FIELD] = {
        "source_table": table,
        "primary_key": {name: raw[name] for name in pk_columns},
    }
    return document


def iter_documents(
    connection: sqlite3.Connection, table: str
) -> Iterable[Dict[str, Any]]:
    columns = table_info(connection, table)
    pk_columns = primary_key_columns(columns)
    json_column_names = json_columns(columns)
    cursor = connection.execute(f"SELECT * FROM {quote_identifier(table)}")
    for row in cursor:
        yield row_to_document(table, row, pk_columns, json_column_names)


def canonical(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"$binary_hex": bytes(value).hex()}
    if isinstance(value, Mapping):
        return {key: canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    return value


def migrate(
    sqlite_connection: sqlite3.Connection,
    mongo_database: Any,
    batch_size: int,
) -> int:
    total = 0
    for table in user_tables(sqlite_connection):
        if table not in mongo_database.list_collection_names():
            mongo_database.create_collection(table)
        collection = mongo_database[table]
        operations = []
        migrated_ids: List[str] = []
        table_total = 0
        for document in iter_documents(sqlite_connection, table):
            migrated_ids.append(document["_id"])
            operations.append(
                ReplaceOne({"_id": document["_id"]}, document, upsert=True)
            )
            if len(operations) >= batch_size:
                collection.bulk_write(operations, ordered=False)
                operations.clear()
            table_total += 1
        if operations:
            collection.bulk_write(operations, ordered=False)
        stale_query: Dict[str, Any] = {
            f"{MIGRATION_META_FIELD}.source_table": table,
        }
        if migrated_ids:
            stale_query["_id"] = {"$nin": migrated_ids}
        stale_deleted = collection.delete_many(stale_query).deleted_count
        print(f"MIGRATED table={table} rows={table_total}")
        if stale_deleted:
            print(f"REMOVED_STALE table={table} rows={stale_deleted}")
        total += table_total
    print(f"MIGRATION_OK tables={len(user_tables(sqlite_connection))} rows={total}")
    return total


def verify(sqlite_connection: sqlite3.Connection, mongo_database: Any) -> bool:
    failures: List[str] = []
    total = 0
    tables = user_tables(sqlite_connection)
    expected_collections = set(tables)
    actual_collections = {
        name
        for name in mongo_database.list_collection_names()
        if not name.startswith("system.")
    }

    missing_collections = expected_collections - actual_collections
    if missing_collections:
        failures.append(f"missing collections: {sorted(missing_collections)}")

    for table in tables:
        expected_documents = {
            document["_id"]: document for document in iter_documents(sqlite_connection, table)
        }
        collection = mongo_database[table]
        mongo_count = collection.count_documents(
            {f"{MIGRATION_META_FIELD}.source_table": table}
        )
        if mongo_count != len(expected_documents):
            failures.append(
                f"{table}: count differs sqlite={len(expected_documents)} mongo={mongo_count}"
            )

        mismatches = 0
        for document_id, expected in expected_documents.items():
            actual = collection.find_one({"_id": document_id})
            if actual is None or canonical(actual) != canonical(expected):
                mismatches += 1
                if mismatches <= 5:
                    failures.append(f"{table}: document mismatch _id={document_id}")
        if mismatches > 5:
            failures.append(f"{table}: {mismatches - 5} additional mismatches")

        print(
            f"VERIFIED table={table} sqlite={len(expected_documents)} "
            f"mongo={mongo_count} mismatches={mismatches}"
        )
        total += len(expected_documents)

    if failures:
        print("VERIFICATION_FAILED", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return False

    print(f"VERIFICATION_OK tables={len(tables)} rows={total}")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("migrate", "verify", "migrate-and-verify"))
    parser.add_argument("--sqlite", required=True, type=Path, help="SQLite database file")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/gateway.yaml"),
        help="Gateway YAML used for MongoDB defaults",
    )
    parser.add_argument(
        "--mongo-uri",
        default=os.getenv("MONGODB_URI") or os.getenv("MONGO_URI"),
        help="MongoDB URI; overrides config and MONGODB_URI/MONGO_URI",
    )
    parser.add_argument("--mongo-db", help="MongoDB database name; defaults to gateway config")
    parser.add_argument("--batch-size", type=int, default=1000)
    return parser.parse_args()


def resolve_mongo_settings(args: argparse.Namespace) -> Tuple[str, str]:
    config: Dict[str, Any] = {}
    if args.config.is_file():
        config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    mongo = (config.get("database") or {}).get("mongodb") or {}
    database_name = args.mongo_db or os.getenv("MONGODB_DATABASE") or mongo.get("database", "edge_gateway")
    if args.mongo_uri:
        return args.mongo_uri, str(database_name)

    host = os.getenv("MONGODB_HOST", str(mongo.get("host", "127.0.0.1")))
    port = int(os.getenv("MONGODB_PORT", str(mongo.get("port", 27017))))
    username = os.getenv("MONGODB_USERNAME", str(mongo.get("username", "")))
    password = os.getenv("MONGODB_PASSWORD", str(mongo.get("password", "")))
    auth_source = os.getenv("MONGODB_AUTH_SOURCE", str(mongo.get("auth_source", "admin")))
    credentials = ""
    if username:
        credentials = f"{quote_plus(username)}:{quote_plus(password)}@"
    uri = f"mongodb://{credentials}{host}:{port}/?authSource={quote_plus(auth_source)}"
    return uri, str(database_name)


def main() -> int:
    args = parse_args()
    if MongoClient is None:
        print("pymongo is required: python -m pip install pymongo", file=sys.stderr)
        return 2
    mongo_uri, mongo_database_name = resolve_mongo_settings(args)
    if not args.sqlite.is_file():
        print(f"SQLite database not found: {args.sqlite}", file=sys.stderr)
        return 2
    if args.batch_size < 1:
        print("--batch-size must be greater than zero", file=sys.stderr)
        return 2

    sqlite_connection = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)
    sqlite_connection.row_factory = sqlite3.Row
    mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        mongo_client.admin.command("ping")
        database = mongo_client[mongo_database_name]
        if args.action in ("migrate", "migrate-and-verify"):
            migrate(sqlite_connection, database, args.batch_size)
        if args.action in ("verify", "migrate-and-verify"):
            return 0 if verify(sqlite_connection, database) else 1
        return 0
    finally:
        sqlite_connection.close()
        mongo_client.close()


if __name__ == "__main__":
    raise SystemExit(main())
