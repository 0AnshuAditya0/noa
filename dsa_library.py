"""Loader for the DSA reference library in data/dsa/.

One JSON file per category; adding entries needs no code change. Everything
is loaded fresh on each call so content edits apply without a restart.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DSA_DIR = Path(__file__).parent / "data" / "dsa"

LEVELS = ("beginner", "intermediate", "advanced", "expert")


def load_dsa() -> dict[str, dict[str, Any]]:
    """Read every *.json in data/dsa/. Returns {category: file_data}."""
    library: dict[str, dict[str, Any]] = {}
    if not DSA_DIR.is_dir():
        return library
    for file in sorted(DSA_DIR.glob("*.json")):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        category = str(data.get("category") or file.stem)
        library[category] = data
    return library


def entries_of(category_data: dict[str, Any]) -> list[dict[str, Any]]:
    entries = category_data.get("entries", [])
    return entries if isinstance(entries, list) else []


def index_by_id(library: dict[str, dict[str, Any]] | None = None) -> dict[str, tuple[str, dict[str, Any]]]:
    """Map entry id -> (category, entry) across the whole library."""
    library = library if library is not None else load_dsa()
    index: dict[str, tuple[str, dict[str, Any]]] = {}
    for category, data in library.items():
        for entry in entries_of(data):
            if isinstance(entry, dict) and entry.get("id"):
                index[str(entry["id"])] = (category, entry)
    return index


def get_entry(entry_id: str) -> tuple[str, dict[str, Any]] | None:
    """Find one entry by id. Returns (category, entry) or None."""
    return index_by_id().get(entry_id)


def category_of(entry_id: str) -> str | None:
    hit = get_entry(entry_id)
    return hit[0] if hit else None


def search_entries(term: str, limit: int = 25) -> list[tuple[str, dict[str, Any]]]:
    """Match title (best), tags, then summary. Case-insensitive substring."""
    want = term.strip().lower()
    if not want:
        return []
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for category, entry in index_by_id().values():
        title = str(entry.get("title", ""))
        tags = entry.get("tags", [])
        summary = str(entry.get("summary", ""))
        tag_text = " ".join(str(t) for t in tags) if isinstance(tags, list) else ""
        if want in title.lower():
            scored.append((0, category, entry))
        elif want in tag_text.lower():
            scored.append((1, category, entry))
        elif want in summary.lower():
            scored.append((2, category, entry))
    scored.sort(key=lambda item: (item[0], str(item[2].get("title", "")).lower()))
    return [(category, entry) for _, category, entry in scored[:limit]]
