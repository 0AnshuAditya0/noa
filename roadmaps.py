"""Loader for roadmap JSON files in data/roadmaps/.

Adding a new file (e.g. data/roadmaps/frontend.json) automatically adds a new
path — no code change needed. Commands call load_roadmaps() fresh each time
so new files are picked up without a restart.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROADMAPS_DIR = Path(__file__).parent / "data" / "roadmaps"

PathData = dict[str, Any]


def load_roadmaps() -> dict[str, PathData]:
    """Read every *.json in data/roadmaps/. Returns {path_id: path_data}."""
    paths: dict[str, PathData] = {}
    if not ROADMAPS_DIR.is_dir():
        return paths
    for file in sorted(ROADMAPS_DIR.glob("*.json")):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        pid = str(data.get("id") or data.get("name") or file.stem)
        paths[pid] = data
    return paths


def _norm(s: str) -> str:
    return s.strip().lower()


def find_path(paths: dict[str, PathData], name: str) -> tuple[str, PathData] | None:
    """Match by id, name, or title (case-insensitive)."""
    want = _norm(name)
    for pid, data in paths.items():
        candidates = {pid, str(data.get("id", "")), str(data.get("name", "")), str(data.get("title", ""))}
        if any(_norm(c) == want for c in candidates if c):
            return pid, data
    return None


def iter_subtopics(path: PathData):
    """Yield (topic, subtopic) for every leaf subtopic."""
    for topic in path.get("topics", []):
        for sub in topic.get("subtopics", []):
            yield topic, sub


def all_leaf_ids(path: PathData) -> list[str]:
    return [str(sub["id"]) for _, sub in iter_subtopics(path)]


def total_count(path: PathData) -> int:
    return len(all_leaf_ids(path))


def get_topic(path: PathData, topic_id: str) -> dict | None:
    """Find a parent topic by id like '05'."""
    for topic in path.get("topics", []):
        if str(topic.get("id")) == topic_id:
            return topic
    return None


def get_subtopic(path: PathData, topic_id: str) -> tuple[dict, dict] | None:
    """Find a leaf subtopic by id like '05.2'. Returns (topic, sub)."""
    for topic, sub in iter_subtopics(path):
        if str(sub.get("id")) == topic_id:
            return topic, sub
    return None


def resolve_ids_for_finish(path: PathData, topic_id: str) -> list[str] | None:
    """Expand an id to leaf ids. Parent '05' -> all its children. Leaf -> [itself]. None if unknown."""
    topic = get_topic(path, topic_id)
    if topic is not None:
        return [str(s["id"]) for s in topic.get("subtopics", [])]
    found = get_subtopic(path, topic_id)
    if found is not None:
        return [topic_id]
    return None


def find_owner_paths(paths: dict[str, PathData], topic_id: str) -> list[str]:
    """Return path_ids where topic_id exists (as parent or leaf)."""
    owners = []
    for pid, data in paths.items():
        if get_topic(data, topic_id) is not None or get_subtopic(data, topic_id) is not None:
            owners.append(pid)
    return owners
