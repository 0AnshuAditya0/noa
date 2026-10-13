"""Roadmap finish-resolution and per-user progress isolation."""
import db
import roadmaps


def _swe_path():
    paths = roadmaps.load_roadmaps()
    for data in paths.values():
        if roadmaps.get_topic(data, "05") is not None:
            return data
    raise AssertionError("no path with topic '05' found in data/roadmaps")


def test_parent_id_expands_to_children():
    path = _swe_path()
    expected = [str(s["id"]) for s in roadmaps.get_topic(path, "05")["subtopics"]]
    got = roadmaps.resolve_ids_for_finish(path, "05")
    assert sorted(got) == sorted(expected)
    assert len(got) > 1
    assert all(tid.startswith("05.") for tid in got)


def test_leaf_id_resolves_to_itself():
    path = _swe_path()
    assert roadmaps.resolve_ids_for_finish(path, "05.2") == ["05.2"]


def test_unknown_ids_return_none():
    path = _swe_path()
    assert roadmaps.resolve_ids_for_finish(path, "99") is None
    assert roadmaps.resolve_ids_for_finish(path, "05.99") is None
    assert roadmaps.resolve_ids_for_finish(path, "") is None


def test_progress_isolated_per_user():
    db.create_user(41, "a")
    db.create_user(42, "b")
    assert db.mark_done(41, "swe", "05.2") is True
    assert db.is_done(41, "swe", "05.2") is True
    assert db.is_done(42, "swe", "05.2") is False
    assert db.get_done_ids(42, "swe") == set()
    assert db.get_done_ids(41, "swe") == {"05.2"}


def test_unmark_does_not_touch_other_user():
    db.create_user(43, "a")
    db.create_user(44, "b")
    db.mark_done(43, "swe", "05.2")
    db.mark_done(44, "swe", "05.2")
    assert db.unmark(43, "swe", "05.2") is True
    assert db.is_done(43, "swe", "05.2") is False
    assert db.is_done(44, "swe", "05.2") is True
