"""Consent, export and bot_meta: policy constants, roundtrips, isolation."""
import json
import re
from datetime import datetime, timedelta, timezone

import db
import policy


def test_policy_version_format():
    assert re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", policy.POLICY_VERSION), policy.POLICY_VERSION


def test_privacy_text_covers_required_topics():
    text = policy.PRIVACY_TEXT.lower()
    for phrase in ("deleteme", "export", "sold", "codeforces", "leetcode", "atcoder", "bio", "streak"):
        assert phrase in text, phrase


def test_consent_embed_contents():
    embed = policy.build_consent_embed(">")
    assert "minimum age" in (embed.description or "").lower()
    assert f"Policy version {policy.POLICY_VERSION}" in (embed.footer.text or "")
    assert ">privacy" in (embed.description or "")


def test_consent_roundtrip():
    assert db.get_consent(12301) == (None, None)
    assert db.needs_consent(12301, policy.POLICY_VERSION) is True
    db.create_user(12301, "u12301", active_path="swe")
    assert db.needs_consent(12301, policy.POLICY_VERSION) is True
    db.set_consent(12301, policy.POLICY_VERSION, username="u12301")
    version, at = db.get_consent(12301)
    assert version == policy.POLICY_VERSION
    assert at is not None
    assert db.needs_consent(12301, policy.POLICY_VERSION) is False
    assert db.needs_consent(12301, "2099-01-1") is True


def test_set_consent_does_not_clobber_profile():
    db.create_user(12302, "keepname", active_path="swe", goal="Placement/Job")
    db.set_consent(12302, policy.POLICY_VERSION, username="other")
    user = db.get_user(12302)
    assert user["username"] == "keepname"
    assert user["active_path"] == "swe"
    assert user["goal"] == "Placement/Job"
    assert user["consent_version"] == policy.POLICY_VERSION


def test_set_consent_creates_minimal_row():
    db.set_consent(12303, policy.POLICY_VERSION, username="fresh")
    assert db.is_registered(12303) is True
    assert db.get_consent(12303)[0] == policy.POLICY_VERSION


def test_min_age_check():
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    old = now - timedelta(days=400)
    new = now - timedelta(days=3)
    assert policy.meets_min_age(old, 30, now) is True
    assert policy.meets_min_age(new, 30, now) is False
    assert policy.meets_min_age(new, 0, now) is True  # off
    assert policy.meets_min_age(old.replace(tzinfo=None), 30, now) is True  # naive tolerated
    assert isinstance(policy.MIN_ACCOUNT_AGE_DAYS, int)


def test_export_contains_only_own_data():
    db.create_user(12304, "mine", active_path="swe")
    db.mark_done(12304, "swe", "05.2")
    db.create_pending_link(12304, "codeforces", "mine-handle", "t")
    db.create_user(12305, "theirs", active_path="swe")
    db.mark_done(12305, "swe", "05.3")
    db.create_pending_link(12305, "codeforces", "theirs-handle", "t")
    data = db.export_user_data(12304)
    assert "users" in data and "progress" in data and "linked_accounts" in data
    assert {r["topic_id"] for r in data["progress"]} == {"05.2"}
    assert {r["handle"] for r in data["linked_accounts"]} == {"mine-handle"}
    blob = json.dumps(data)
    assert "theirs-handle" not in blob
    assert "mine-handle" in blob
    for table, rows in data.items():
        for row in rows:
            assert row.get("user_id") == 12304, (table, row)


def test_export_empty_user():
    db.create_user(12306, "empty")
    data = db.export_user_data(12306)
    assert data["users"] and len(data["users"]) == 1
    assert all(v == [] for k, v in data.items() if k != "users")


def test_bot_meta_roundtrip():
    assert db.get_meta("no-such-key") is None
    db.set_meta("host_reminder_at", "2026-10-12T00:00:00+00:00")
    assert db.get_meta("host_reminder_at") == "2026-10-12T00:00:00+00:00"
    db.set_meta("host_reminder_at", "2026-10-13T00:00:00+00:00")
    assert db.get_meta("host_reminder_at") == "2026-10-13T00:00:00+00:00"
