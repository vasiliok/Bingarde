"""scripts/bingarde-upload.py, run against the app through WebTest."""
import hashlib
import importlib.util
import io
import os

import pytest

from bingarde.model import Build
from tests.conftest import HERE

spec = importlib.util.spec_from_file_location(
    "bingarde_upload", os.path.join(HERE, "scripts", "bingarde-upload.py"))
upload_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upload_script)

TOKEN = "bg_unused_the_test_app_sends_its_own"


class WebTestClient(upload_script.Client):
    """The script's client, with HTTP replaced by the in-process test app."""

    def __init__(self, test_app, fail_on=None):
        super().__init__("http://bingarde.test", TOKEN)
        self.test_app = test_app
        self.fail_on = fail_on

    def send(self, method, path, body, headers):
        if self.fail_on and path.endswith(self.fail_on):
            raise upload_script.ApiError("simulated failure on " + path)
        if body is not None and not isinstance(body, bytes):
            body = b"".join(body)
        resp = self.test_app.request(path, method=method, body=body or b"", expect_errors=True,
                                     content_type=headers.get("Content-Type"))
        return resp.status_int, resp.body


@pytest.fixture
def build_dir(tmp_path):
    files = {"Game.exe": b"MZ" + b"\0" * 100, "Game.pdb": b"PDB" * 50, "build.log": b"ok\n"}
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)
    return tmp_path


def args_for(build_dir, *extra, names=("Game.exe", "Game.pdb", "build.log")):
    return upload_script.parse_args(
        ["--url", "http://bingarde.test", "--token", TOKEN,
         "--project", "game", "--branch", "main", "--platform", "windows",
         "--build", "42", "--config", "release", "--arch", "x86_64",
         "--revision", "3f9c2a1", "--ci-url", "https://ci.example.com/job/42",
         "--property", "compiler=msvc 19.44", *extra]
        + [str(build_dir / n) for n in names])


def run(app, args, **client_kw):
    out = io.StringIO()
    build = upload_script.run(WebTestClient(app, **client_kw), args, out=out)
    return build, out.getvalue()


def test_upload_creates_complete_build_with_roles_and_checksums(app, db, build_dir):
    build, out = run(app, args_for(build_dir))

    stored = db.get(Build, build["id"])
    assert stored.status == "complete"
    assert (stored.revision, stored.ci_url) == ("3f9c2a1", "https://ci.example.com/job/42")
    assert (stored.config, stored.arch) == ("release", "x86_64")
    assert stored.properties == {"compiler": "msvc 19.44"}
    roles = {f.display_name: f.role for f in stored.files}
    assert roles == {"Game.exe": "artifact", "Game.pdb": "symbols", "build.log": "log"}
    for f in stored.files:
        expected = hashlib.sha256((build_dir / f.display_name).read_bytes()).hexdigest()
        assert f.sha256 == expected
    assert "created build #%d" % build["id"] in out
    assert out.strip().endswith("complete: http://bingarde.test/builds/%d" % build["id"])


def test_rerun_reuses_the_build_and_skips_identical_files(app, db, build_dir):
    first, _ = run(app, args_for(build_dir))
    second, out = run(app, args_for(build_dir))

    assert second["id"] == first["id"]
    assert "reusing build #%d" % first["id"] in out
    assert out.count("(already stored)") == 3
    assert len(db.get(Build, first["id"]).files) == 3


def test_role_option_sets_the_role_of_all_files(app, db, build_dir):
    build, _ = run(app, args_for(build_dir, "--role", "other"))
    assert {f.role for f in db.get(Build, build["id"]).files} == {"other"}


def test_status_failed_keeps_the_logs_of_a_broken_build(app, db, build_dir):
    build, out = run(app, args_for(build_dir, "--status", "failed", names=("build.log",)))
    stored = db.get(Build, build["id"])
    assert stored.status == "failed"
    assert [f.display_name for f in stored.files] == ["build.log"]
    assert "failed: http://bingarde.test/builds/" in out


def test_a_failed_upload_marks_the_build_failed(app, db, build_dir):
    with pytest.raises(upload_script.ApiError, match="simulated failure"):
        run(app, args_for(build_dir), fail_on="/files")
    stored = db.query(Build).one()
    assert stored.status == "failed"
    assert stored.files == []


def test_api_errors_are_raised_with_the_server_message(app, build_dir):
    args = args_for(build_dir)
    args.ci_url = "javascript:alert(1)"
    with pytest.raises(upload_script.ApiError, match="ci_url must be an http"):
        run(app, args)


def test_create_fields_leave_out_empty_values(build_dir):
    args = upload_script.parse_args(
        ["--url", "u", "--token", "t", "--project", "p", "--branch", "b",
         "--platform", "linux", "--build", "7"])
    assert upload_script.create_fields(args) == {
        "project": "p", "branch": "b", "platform": "linux", "build": 7, "status": "uploading"}


def test_properties_are_sent_as_an_object(build_dir):
    fields = upload_script.create_fields(args_for(build_dir, "--property", "sdk=15.1"))
    assert fields["properties"] == {"compiler": "msvc 19.44", "sdk": "15.1"}


@pytest.mark.parametrize("argv, message", [
    (["--property", "novalue"], "KEY=VALUE"),
    (["/no/such/file.zip"], "not a file"),
    (["--build", "abc"], "invalid int"),
])
def test_bad_arguments_stop_with_a_message(argv, message, capsys):
    base = ["--url", "u", "--token", "t", "--project", "p", "--branch", "b",
            "--platform", "linux", "--build", "7"]
    with pytest.raises(SystemExit):
        upload_script.parse_args(base + argv)
    assert message in capsys.readouterr().err


def test_url_and_token_are_required(monkeypatch, capsys):
    monkeypatch.delenv("BINGARDE_URL", raising=False)
    monkeypatch.delenv("BINGARDE_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        upload_script.parse_args(["--project", "p", "--branch", "b", "--platform", "x", "--build", "1"])
    assert "BINGARDE_URL" in capsys.readouterr().err


def test_multipart_length_matches_the_streamed_body(build_dir):
    body = upload_script.MultipartFile({"role": "symbols"}, "file", str(build_dir / "Game.pdb"))
    data = b"".join(body.chunks(size=7))
    assert len(data) == body.length
    assert (build_dir / "Game.pdb").read_bytes() in data
