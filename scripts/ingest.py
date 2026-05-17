#!/usr/bin/env python3
"""Bulk-ingest a directory of .md / .txt files into Finrod via /memory/ingest."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx


def collect(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in suffixes]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dir", required=True, type=Path, help="root directory to walk")
    p.add_argument("--api-url", default="http://localhost:5000")
    p.add_argument("--api-key", required=True)
    p.add_argument("--suffixes", default=".md,.txt", help="comma-separated file suffixes")
    args = p.parse_args()

    if not args.dir.exists():
        print(f"directory not found: {args.dir}", file=sys.stderr)
        return 2

    suffixes = tuple(s.strip().lower() for s in args.suffixes.split(",") if s.strip())
    files = collect(args.dir, suffixes)
    if not files:
        print(f"no files matched {suffixes} under {args.dir}", file=sys.stderr)
        return 1

    headers = {"x-api-key": args.api_key}
    ok = fail = 0
    with httpx.Client(base_url=args.api_url, headers=headers, timeout=60.0) as client:
        for fp in files:
            doc_id = str(fp.relative_to(args.dir))
            text = fp.read_text(encoding="utf-8", errors="replace")
            try:
                resp = client.post(
                    "/memory/ingest",
                    json={"doc_id": doc_id, "text": text, "metadata": {"source": doc_id}},
                )
                resp.raise_for_status()
                print(f"OK   {doc_id}")
                ok += 1
            except Exception as e:
                print(f"FAIL {doc_id}: {e}", file=sys.stderr)
                fail += 1

    print(f"\n{ok} ingested, {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
