# This file is adapted from https://github.com/jennyzzt/dgm.

import subprocess

import git


GENERATED_DIFF_EXCLUDES = (
    "self_evo.md",
    "model_patch.diff",
    "*.log",
    ".pytest_cache/",
    "__pycache__/",
    "*.pyc",
    "*.backup",
    ".coverage",
    "coverage.xml",
    "htmlcov/",
    "logs/",
    "output_mgm/",
    "output_polyglot/",
    "initial_polyglot_evaluation/",
    "target/",
    "build/",
    ".gradle/",
    "node_modules/",
    ".npm/",
    ".cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "dist/",
)


def get_git_commit_hash(repo_path="."):
    try:
        # Load the repository
        repo = git.Repo(repo_path)
        # Get the current commit hash
        commit_hash = repo.head.commit.hexsha
        return commit_hash
    except Exception as e:
        print("Error while getting git commit hash:", e)
        return None


def apply_patch(git_dname, patch_str):
    """
    Apply a patch to the repository at `git_dname`.
    """
    cmd = ["git", "-C", git_dname, "apply", "--reject", "-"]
    result = subprocess.run(
        cmd,
        input=patch_str,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    # Check if the patch was applied successfully
    if result.returncode != 0:
        print(
            f"apply_patch error: Patch did not fully apply. Return code: {result.returncode}, stdout: {result.stdout}, stderr: {result.stderr}"
        )
    else:
        print("apply_patch successful")


def diff_versus_commit(git_dname, commit):
    """
    Take a diff of `git_dname` current contents versus the `commit`, including untracked files,
    while excluding local run artifacts that should never become model patches.
    """
    pathspecs = ["."] + [f":(exclude){pattern}" for pattern in GENERATED_DIFF_EXCLUDES]

    # Make untracked source files visible to git diff without staging content.
    # This avoids hand-assembling --no-index hunks, which is easy to corrupt when
    # files lack trailing newlines.
    untracked_files_cmd = [
        "git",
        "-C",
        git_dname,
        "ls-files",
        "--others",
        "--exclude-standard",
    ]
    result = subprocess.run(
        untracked_files_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    untracked_files = result.stdout.decode("utf-8", errors="replace").splitlines()
    if untracked_files:
        subprocess.run(
            ["git", "-C", git_dname, "add", "--intent-to-add", "--", *untracked_files],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    diff_cmd = [
        "git",
        "-C",
        git_dname,
        "diff",
        "--binary",
        commit,
        "--",
        *pathspecs,
    ]
    result = subprocess.run(
        diff_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )
    return result.stdout.decode("utf-8", errors="replace")


def reset_to_commit(git_dname, commit):
    """
    Reset the repository at `git_dname` to the given `commit`.
    """
    # Step 1: Hard-reset tracked files
    reset_cmd = ["git", "-C", git_dname, "reset", "--hard", commit]
    result_reset = subprocess.run(
        reset_cmd, capture_output=True, text=True, check=False
    )
    if result_reset.returncode != 0:
        print(
            f"reset_to_commit error: Failed to reset {git_dname} to commit '{commit}'. STDOUT: {result_reset.stdout} STDERR: {result_reset.stderr}"
        )
    else:
        print(f"reset_to_commit successful: {commit}")

    # Step 2: Clean untracked files (the "new files") and directories
    clean_cmd = ["git", "-C", git_dname, "clean", "-fd"]
    result_clean = subprocess.run(
        clean_cmd, capture_output=True, text=True, check=False
    )
    if result_clean.returncode != 0:
        print(
            f"reset_to_commit clean error: Failed to clean {git_dname}. STDOUT: {result_clean.stdout} STDERR: {result_clean.stderr}"
        )
    else:
        print(f"reset_to_commit clean successful: {commit}")


def filter_patch_by_files(patch_str, target_files):
    """
    Filters out the diff blocks related to any of the target_files in a patch string.

    Args:
        patch_str (str): The complete patch text.
        target_files (list[str]): A list of filenames for which to extract changes (e.g. ['affine_cipher.py', 'other.py']).

    Returns:
        str: A string containing only the diff blocks for the specified target files.
    """
    lines = patch_str.splitlines(keepends=True)
    filtered_lines = []
    include_block = False

    for line in lines:
        # When we encounter a new diff block header, check if the block is for any of the target files.
        if line.startswith("diff --git"):
            include_block = any(
                f"a/{target}" in line and f"b/{target}" in line
                for target in target_files
            )
        if include_block:
            filtered_lines.append(line)
    return _ensure_patch_trailing_newline("".join(filtered_lines))


def remove_patch_by_files(patch_str, keyword="polyglot"):
    """
    Removes diff blocks related to files containing the keyword from a patch string.

    Args:
        patch_str (str): The complete patch text.
        keyword (str): Keyword to match in filenames for removal (default: 'polyglot').

    Returns:
        str: A string containing the patch with diff blocks for matching files removed.
    """
    lines = patch_str.splitlines(keepends=True)
    filtered_lines = []
    include_block = True

    for line in lines:
        # When we encounter a new diff block header, check if the block contains the keyword
        if line.startswith("diff --git"):
            include_block = keyword.lower() not in line.lower()
        if include_block:
            filtered_lines.append(line)

    return _ensure_patch_trailing_newline("".join(filtered_lines))


def _ensure_patch_trailing_newline(patch_str):
    if patch_str and not patch_str.endswith("\n"):
        return patch_str + "\n"
    return patch_str


if __name__ == "__main__":
    print(diff_versus_commit("./", "(root-commit)"))
