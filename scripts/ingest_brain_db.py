#!/usr/bin/env python3
"""Ingest rows from a legacy openclaw brain.db SQLite into Finrod via /memory/ingest.

Schema expected:
    memories(id, category, key, value, source, confidence,
             created_at, updated_at, expires_at, tags)

Each row becomes one Finrod document with doc_id = "brain-db/{id}". Rows whose
expires_at is in the past are skipped.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx


def format_text(row: sqlite3.Row) -> str:
    parts = [f"[{row['category']}] {row['key']}", "", row["value"]]
    meta = []
    if row["source"]:
        meta.append(f"source={row['source']}")
    if row["confidence"] is not None:
        meta.append(f"confidence={row['confidence']}")
    if row["tags"]:
        meta.append(f"tags={row['tags']}")
    if row["created_at"]:
        meta.append(f"created={row['created_at']}")
    if meta:
        parts.extend(["", "(" + ", ".join(meta) + ")"])
    return "\n".join(parts)


def is_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        when = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return when < datetime.now(timezone.utc)
    except ValueError:
        return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True, type=Path, help="path to brain.db")
    p.add_argument("--api-url", default="http://localhost:5000")
    p.add_argument("--api-key", required=True)
    p.add_argument("--dry-run", action="store_true", help="print without posting")
    p.add_argument("--min-confidence", type=float, default=0.0)
    args = p.parse_args()

    if not args.db.exists():
        print(f"db not found: {args.db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM memories ORDER BY id"
    ).fetchall()
    conn.close()

    headers = {"x-api-key": args.api_key}
    ok = skipped = failed = 0
    with httpx.Client(base_url=args.api_url, headers=headers, timeout=60.0) as client:
        for row in rows:
            doc_id = f"brain-db/{row['id']}"
            if is_expired(row["expires_at"]):
                print(f"SKIP {doc_id} (expired {row['expires_at']})")
                skipped += 1
                continue
            if (row["confidence"] or 1.0) < args.min_confidence:
                print(f"SKIP {doc_id} (confidence {row['confidence']} < {args.min_confidence})")
                skipped += 1
                continue

            text = format_text(row)
            if args.dry_run:
                print(f"--- {doc_id} ---")
                print(text)
                print()
                ok += 1
                continue

            try:
                resp = client.post(
                    "/memory/ingest",
                    json={
                        "doc_id": doc_id,
                        "text": text,
                        "metadata": {
                            "category": row["category"],
                            "source": "brain-db-import",
                            "original_source": row["source"],
                        },
                    },
                )
                resp.raise_for_status()
                print(f"OK   {doc_id} [{row['category']}] {row['key']}")
                ok += 1
            except Exception as e:
                print(f"FAIL {doc_id}: {e}", file=sys.stderr)
                failed += 1

    print(f"\n{ok} ingested, {skipped} skipped, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
