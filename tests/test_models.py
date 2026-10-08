"""Model serialization."""
from bingarde.model import FilterPreset, Build, BuildFile, User


def test_build_as_dict_contains_all_public_fields(db):
    build = Build(name="build1", properties={"sdk": "34"}, project="Alpha",
                branch="main", platform="ios", build=1000, history="initial commit")
    db.add(build)
    db.commit()
    d = build.as_dict()
    assert d["id"] == build.id
    assert (d["name"], d["properties"]) == ("build1", {"sdk": "34"})
    assert (d["project"], d["branch"], d["platform"]) == ("Alpha", "main", "ios")
    assert d["build"] == 1000
    assert d["history"] == "initial commit"
    assert "T" in d["created_at"] and "T" in d["updated_at"]          # ISO 8601


def test_build_file_as_dict(db):
    build = Build(name="p", project="Alpha", branch="b", platform="ios", build=1)
    db.add(build)
    db.flush()
    f = BuildFile(build_id=build.id, path="2026/10/p1/hello.txt", size=5)
    db.add(f)
    db.commit()
    d = f.as_dict()
    assert (d["id"], d["build_id"]) == (f.id, build.id)
    assert d["name"] == "hello.txt"
    assert "path" not in d          # where it is stored stays inside the server
    assert d["size"] == 5
    assert "T" in d["uploaded_at"]
    assert d["download_url"] == "/files/%d" % f.id


def test_filter_preset_as_dict_with_user(db):
    user = User(email="alice@example.com", name="Alice")
    db.add(user)
    db.flush()
    preset = FilterPreset(name="mine", user_id=user.id, version=1, notify=True,
                          data={"filter": None})
    db.add(preset)
    db.commit()
    d = preset.as_dict()
    assert d["name"] == "mine"
    assert d["user"] == "alice@example.com"
    assert d["notify"] is True
    assert d["data"] == {"filter": None}


def test_filter_preset_as_dict_without_user(db):
    preset = FilterPreset(name="shared", version=1, notify=False, data={"filter": None})
    db.add(preset)
    db.commit()
    assert preset.as_dict()["user"] is None
    assert preset.as_dict()["notify"] is False


def test_session_cleanup_removes_only_old_files(tmp_path):
    import os
    import time
    from bingarde.lib.session_cleanup import remove_expired
    old, fresh = tmp_path / "a" / "old.cache", tmp_path / "b" / "fresh.cache"
    for path in (old, fresh):
        path.parent.mkdir()
        path.write_text("x")
    now = time.time()
    os.utime(old, (now - 7200, now - 7200))
    assert remove_expired(str(tmp_path), 3600, now=now) == 1
    assert not old.exists() and fresh.exists()
