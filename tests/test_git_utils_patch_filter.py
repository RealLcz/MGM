import subprocess
import sys
import types

sys.modules.setdefault("git", types.ModuleType("git"))
from utils.git_utils import filter_patch_by_files, remove_patch_by_files


def _init_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "config", "user.email", "you@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "user"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "coding_agent.py").write_text("old\n")
    subprocess.run(["git", "add", "coding_agent.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "base"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.PIPE,
    )


def test_remove_patch_by_files_preserves_applyable_trailing_newline(tmp_path):
    _init_repo(tmp_path)
    patch = "\n".join(
        [
            "diff --git a/coding_agent.py b/coding_agent.py",
            "index 3367afd..3e75765 100644",
            "--- a/coding_agent.py",
            "+++ b/coding_agent.py",
            "@@ -1 +1 @@",
            "-old",
            "+new",
        ]
    )

    filtered = remove_patch_by_files(patch)

    assert filtered.endswith("\n")
    result = subprocess.run(
        ["git", "apply", "--check", "-"],
        cwd=tmp_path,
        input=filtered,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_filter_patch_by_files_keeps_complete_matching_block():
    patch = "\n".join(
        [
            "diff --git a/coding_agent.py b/coding_agent.py",
            "--- a/coding_agent.py",
            "+++ b/coding_agent.py",
            "@@ -1 +1 @@",
            "-old",
            "+new",
            "diff --git a/polyglot/tmp.py b/polyglot/tmp.py",
            "--- a/polyglot/tmp.py",
            "+++ b/polyglot/tmp.py",
            "@@ -1 +1 @@",
            "-x",
            "+y",
        ]
    )

    filtered = filter_patch_by_files(patch, ["coding_agent.py"])

    assert "coding_agent.py" in filtered
    assert "polyglot/tmp.py" not in filtered
    assert filtered.endswith("\n")
