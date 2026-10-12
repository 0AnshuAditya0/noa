"""Standalone dev check for the DSA library. No bot code is imported.

Usage:
    python scripts/check_dsa.py

Validates schema, unique ids, required fields, related-id existence,
snippet length (<= 25 lines) and non-empty lists. Prints a summary and
exits non-zero on any error.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DSA_DIR = ROOT / "data" / "dsa"

LEVELS = ("beginner", "intermediate", "advanced", "expert")
REQUIRED = ("id", "title", "level", "summary", "tags", "recognize",
            "how_it_works", "complexity", "example", "pitfalls", "tips", "related")
LIST_FIELDS = ("tags", "recognize", "pitfalls", "tips", "related")
MAX_SNIPPET_LINES = 25


def main() -> int:
    files = sorted(DSA_DIR.glob("*.json"))
    if not files:
        print(f"No JSON files in {DSA_DIR}")
        return 1

    errors: list[str] = []
    seen_ids: dict[str, str] = {}
    entry_count = 0

    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"{path.name}: unreadable JSON ({exc})")
            continue
        if not isinstance(data, dict):
            errors.append(f"{path.name}: top level must be an object")
            continue
        if not str(data.get("category", "")).strip():
            errors.append(f"{path.name}: missing category")
        entries = data.get("entries")
        if not isinstance(entries, list) or not entries:
            errors.append(f"{path.name}: 'entries' must be a non-empty list")
            continue
        for i, entry in enumerate(entries):
            where = f"{path.name} entries[{i}]"
            if not isinstance(entry, dict):
                errors.append(f"{where}: must be an object")
                continue
            for field in REQUIRED:
                if field not in entry:
                    errors.append(f"{where}: missing field '{field}'")
            eid = str(entry.get("id", "")).strip()
            if not eid:
                errors.append(f"{where}: empty id")
                continue
            if eid in seen_ids:
                errors.append(f"{where}: duplicate id '{eid}' (also in {seen_ids[eid]})")
            else:
                seen_ids[eid] = path.name
            if entry.get("level") not in LEVELS:
                errors.append(f"{where} ({eid}): bad level '{entry.get('level')}'")
            for field in ("title", "summary", "how_it_works", "example"):
                if field in entry and not str(entry[field]).strip():
                    errors.append(f"{where} ({eid}): empty '{field}'")
            for field in LIST_FIELDS:
                if field in entry and (not isinstance(entry[field], list) or not entry[field]):
                    errors.append(f"{where} ({eid}): '{field}' must be a non-empty list")
            complexity = entry.get("complexity")
            if isinstance(complexity, dict):
                for part in ("time", "space"):
                    if not str(complexity.get(part, "")).strip():
                        errors.append(f"{where} ({eid}): complexity.{part} missing/empty")
            elif "complexity" in entry:
                errors.append(f"{where} ({eid}): complexity must be an object with time/space")
            example = entry.get("example")
            if isinstance(example, str) and example.strip():
                lines = example.strip().splitlines()
                if len(lines) > MAX_SNIPPET_LINES:
                    errors.append(f"{where} ({eid}): example has {len(lines)} lines (max {MAX_SNIPPET_LINES})")
            entry_count += 1

    # Related ids must exist (second pass: all ids known now).
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for entry in data.get("entries", []) if isinstance(data, dict) else []:
            if not isinstance(entry, dict):
                continue
            eid = str(entry.get("id", ""))
            related = entry.get("related")
            if isinstance(related, list):
                for rid in related:
                    if str(rid) not in seen_ids:
                        errors.append(f"{path.name} ({eid}): related id '{rid}' does not exist")

    if errors:
        print("== ERRORS ==")
        for err in errors:
            print(f"  X {err}")

    print("\n== SUMMARY ==")
    print(f"Files: {len(files)} | Entries: {entry_count} | Unique ids: {len(seen_ids)}")
    print(f"Errors: {len(errors)}")
    if errors:
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
