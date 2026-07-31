"""captain.toml travels with a clone, so its values are untrusted input."""

import pytest

from tsubasa import config
from tsubasa.adapters.gitlog import _safe_ref


def write(root, body):
    d = root / config.TSUBASA_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / config.CONFIG_FILE).write_text(body)
    return root


def test_branch_that_git_would_parse_as_an_option_is_rejected(tmp_path):
    # --upload-pack runs a command; --output writes a file. Both are documented
    # git options, and both sit where the branch name goes.
    for hostile in ("--upload-pack=/tmp/x.sh", "--output=/tmp/pwned", "-x"):
        write(tmp_path, f'[[sources]]\nadapter = "git"\npath = "."\nbranch = "{hostile}"\n')
        with pytest.raises(RuntimeError, match="not a valid ref"):
            config.load(tmp_path)


def test_ordinary_branch_names_still_load(tmp_path):
    for ok in ("main", "release/1.2", "feat/fix-thing", "v2.0.0"):
        write(tmp_path, f'[[sources]]\nadapter = "git"\npath = "."\nbranch = "{ok}"\n')
        assert config.load(tmp_path).sources[0].options["branch"] == ok


def test_source_path_cannot_escape_the_captain_root(tmp_path):
    # Path("/repo") / "/etc" == "/etc": an absolute path silently escapes, and
    # the adapter would excerpt what it read into a graph the user pushes.
    for escape in ("/etc", "../../elsewhere", str(tmp_path.parent)):
        write(tmp_path, f'[[sources]]\nadapter = "doc"\npath = "{escape}"\nglob = "*.md"\n')
        with pytest.raises(RuntimeError, match="outside the captain root"):
            config.load(tmp_path)


def test_workspace_relative_paths_still_load(tmp_path):
    for ok in (".", "repo", "nested/repo"):
        write(tmp_path, f'[[sources]]\nadapter = "doc"\npath = "{ok}"\n')
        assert config.load(tmp_path).sources[0].path == ok


def test_watermarks_are_commit_ids_not_options():
    assert config.valid_sha("0cf3f500cdd8219f250c0ca3bfe97a3ad30ba4a5")
    assert config.valid_sha("0cf3f50")
    assert not config.valid_sha("--output=/tmp/pwned")
    assert not config.valid_sha("main")  # a ref is not a watermark
    assert not config.valid_sha("")


def test_safe_ref_drops_options_and_keeps_refs():
    # The second gate: refs arriving from state or from git itself.
    assert _safe_ref("main") == "main"
    assert _safe_ref("origin/main") == "origin/main"
    assert _safe_ref("--upload-pack=/tmp/x.sh") == ""
    assert _safe_ref("") == ""
