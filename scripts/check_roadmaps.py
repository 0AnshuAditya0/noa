"""Standalone dev check for roadmap JSON files. No bot code is imported.

Usage:
    python scripts/check_roadmaps.py

Validates schema + IDs, then checks every resource URL (async, max 10
concurrent, 10s timeout, follows redirects, HEAD with GET fallback).
403/429 count as "unverified", not broken.

Exit code is non-zero when schema errors or broken links exist.
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
ROADMAPS_DIR = ROOT / "data" / "roadmaps"
CONCURRENCY = 10
TIMEOUT = 10.0
UNVERIFIED_STATUSES = {403, 429}
HEAD_FALLBACK_STATUSES = {400, 404, 405, 410, 500, 501, 502, 503}


def validate_file(path: Path) -> tuple[dict | None, list[str], list[tuple[str, str, str]]]:
    """Returns (data, schema_errors, url_refs as (subtopic_id, name, url))."""
    errors: list[str] = []
    refs: list[tuple[str, str, str]] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return None, [f"{path.name}: unreadable JSON ({exc})"], refs
    if not isinstance(data, dict):
        return None, [f"{path.name}: top level must be an object"], refs

    pid = data.get("id") or data.get("name")
    if not pid or not str(pid).strip():
        errors.append(f"{path.name}: missing path id/name")
    if not str(data.get("description", "")).strip():
        errors.append(f"{path.name}: missing description")

    topics = data.get("topics")
    if not isinstance(topics, list) or not topics:
        errors.append(f"{path.name}: 'topics' must be a non-empty list")
        return data, errors, refs

    seen_topics: set[str] = set()
    seen_subs: set[str] = set()
    for i, topic in enumerate(topics):
        where = f"{path.name} topic[{i}]"
        if not isinstance(topic, dict):
            errors.append(f"{where}: must be an object")
            continue
        tid = str(topic.get("id", "")).strip()
        title = str(topic.get("title", "")).strip()
        if not tid:
            errors.append(f"{where}: missing id")
        elif tid in seen_topics:
            errors.append(f"{where}: duplicate topic id '{tid}'")
        else:
            seen_topics.add(tid)
        if not title:
            errors.append(f"{where} ({tid or '?'}): empty title")

        subs = topic.get("subtopics")
        if not isinstance(subs, list) or not subs:
            errors.append(f"{where} ({tid or '?'}): 'subtopics' must be a non-empty list")
            continue
        for j, sub in enumerate(subs):
            swhere = f"{path.name} subtopic[{tid}.{j}]"
            if not isinstance(sub, dict):
                errors.append(f"{swhere}: must be an object")
                continue
            sid = str(sub.get("id", "")).strip()
            stitle = str(sub.get("title", "")).strip()
            if not sid:
                errors.append(f"{swhere}: missing id")
            else:
                if sid in seen_subs:
                    errors.append(f"{swhere}: duplicate subtopic id '{sid}'")
                else:
                    seen_subs.add(sid)
                if tid and not sid.startswith(tid + "."):
                    errors.append(f"{swhere}: id '{sid}' not prefixed by parent topic id '{tid}'")
            if not stitle:
                errors.append(f"{swhere} ({sid or '?'}): empty title")
            resources = sub.get("resources")
            if not isinstance(resources, list) or len(resources) < 2:
                errors.append(f"{swhere} ({sid or '?'}): need at least 2 resources")
                continue
            for r in resources:
                if not isinstance(r, dict) or not str(r.get("name", "")).strip():
                    errors.append(f"{swhere} ({sid or '?'}): resource missing name")
                url = str((r or {}).get("url", "")).strip()
                if not url.startswith(("http://", "https://")):
                    errors.append(f"{swhere} ({sid or '?'}): bad url '{url}'")
                elif sid:
                    refs.append((sid, str(r.get("name", "")).strip(), url))
    return data, errors, refs


async def check_url(client: httpx.AsyncClient, sem: asyncio.Semaphore, url: str) -> tuple[str, str]:
    """Returns (verdict, detail): verdict in {'ok', 'broken', 'unverified'}."""
    async with sem:
        try:
            resp = await client.head(url)
            status = resp.status_code
            if status in HEAD_FALLBACK_STATUSES:
                resp = await client.get(url)
                status = resp.status_code
        except (httpx.TimeoutException, httpx.RequestError):
            try:
                resp = await client.get(url)
                status = resp.status_code
            except httpx.TimeoutException:
                return "broken", "timeout"
            except httpx.RequestError as exc:
                return "broken", f"connection error ({type(exc).__name__})"
            except Exception as exc:  # noqa: BLE001 - report, don't crash the run
                return "broken", f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001
            return "broken", f"{type(exc).__name__}: {exc}"
        if status in UNVERIFIED_STATUSES:
            return "unverified", f"HTTP {status}"
        if 200 <= status < 400:
            return "ok", f"HTTP {status}"
        return "broken", f"HTTP {status}"


async def main() -> int:
    files = sorted(ROADMAPS_DIR.glob("*.json"))
    if not files:
        print(f"No JSON files in {ROADMAPS_DIR}")
        return 1

    all_errors: list[str] = []
    refs_by_file: dict[str, list[tuple[str, str, str]]] = {}
    for path in files:
        _, errors, refs = validate_file(path)
        all_errors.extend(errors)
        refs_by_file[path.name] = refs

    if all_errors:
        print("== SCHEMA ERRORS ==")
        for err in all_errors:
            print(f"  X {err}")

    unique_urls = sorted({url for refs in refs_by_file.values() for _, _, url in refs})
    print(f"\nChecked {len(files)} file(s); {len(unique_urls)} unique URL(s).")
    print(f"Checking links (concurrency={CONCURRENCY}, timeout={TIMEOUT}s)...")

    results: dict[str, tuple[str, str]] = {}
    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "Noa-linkcheck"}) as client:
        verdicts = await asyncio.gather(*(check_url(client, sem, url) for url in unique_urls))
    results = dict(zip(unique_urls, verdicts))

    broken: dict[str, list[str]] = defaultdict(list)  # file -> lines
    unverified_count = 0
    for fname, refs in refs_by_file.items():
        by_sub: dict[str, list[str]] = defaultdict(list)
        for sid, name, url in refs:
            verdict, detail = results[url]
            if verdict == "broken":
                by_sub[sid].append(f"{name} <{url}> ({detail})")
            elif verdict == "unverified":
                unverified_count += 1
        for sid in sorted(by_sub):
            broken[fname].append(f"  [{sid}]")
            broken[fname].extend(f"    X {line}" for line in by_sub[sid])

    ok_count = sum(1 for v, _ in results.values() if v == "ok")
    broken_count = sum(1 for v, _ in results.values() if v == "broken")

    if broken:
        print("\n== BROKEN LINKS ==")
        for fname in sorted(broken):
            print(f"{fname}:")
            print("\n".join(broken[fname]))

    print("\n== SUMMARY ==")
    n_topics = n_subs = 0
    for fname in files:
        try:
            data = json.loads(fname.read_text(encoding="utf-8"))
            topics = data.get("topics", [])
            n_topics += len(topics)
            n_subs += sum(len(t.get("subtopics", [])) for t in topics if isinstance(t, dict))
        except (json.JSONDecodeError, OSError):
            pass
    print(f"Files: {len(files)} | Topics: {n_topics} | Subtopics: {n_subs} | URLs: {len(unique_urls)}")
    print(f"OK: {ok_count} | Broken: {broken_count} | Unverified (403/429): {unverified_count}")
    print(f"Schema errors: {len(all_errors)}")

    if all_errors or broken_count:
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
