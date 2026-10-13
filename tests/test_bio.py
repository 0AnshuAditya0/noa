"""Bio: sanitize rules, 150-char cap, set/clear roundtrip, migration column."""
import db
from cogs.account import BIO_MAX, sanitize_bio


def test_mentions_stripped():
    assert sanitize_bio("Hi <@123456> and <@!789>!") == "Hi and !"


def test_role_and_channel_mentions_stripped():
    assert sanitize_bio("<@&111> see <#222>") == "see"


def test_links_stripped():
    assert sanitize_bio("see https://example.com/x and http://a.b") == "see and"


def test_mass_mentions_stripped():
    assert sanitize_bio("hi @everyone and @HERE") == "hi and"


def test_whitespace_collapsed():
    assert sanitize_bio("  a\n\t b  ") == "a b"


def test_cap_at_150_chars():
    assert sanitize_bio("x" * 500) == "x" * BIO_MAX
    assert BIO_MAX == 150


def test_empty_and_none():
    assert sanitize_bio("") == ""
    assert sanitize_bio("   ") == ""


def test_bio_column_exists():
    with db._connect() as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(users)")]
    assert "bio" in cols


def test_set_and_clear_roundtrip():
    db.create_user(71, "u71")
    assert db.update_user(71, bio="Hello") is True
    assert db.get_user(71)["bio"] == "Hello"
    assert db.update_user(71, bio=None) is True
    assert db.get_user(71)["bio"] is None


def test_bio_survives_other_updates():
    db.create_user(72, "u72", active_path="swe")
    db.update_user(72, bio="Keep me")
    db.update_user(72, goal="Placement/Job")
    assert db.get_user(72)["bio"] == "Keep me"
