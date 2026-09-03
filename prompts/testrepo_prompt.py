# This file is adapted from https://github.com/jennyzzt/dgm.

def get_test_command(eval_script):
    test_hint = ""
    # test_command is the 2nd last line in eval_script
    lines = eval_script.strip().split("\n")
    test_command = lines[-2].strip()
    # Remove trailing arguments specifying filepaths
    parts = test_command.split()
    if "." in parts[-1] and not parts[-1].endswith(".py"):
        # Get the test hint
        test_hint = "If the target test file path is tests/some_folder/some_file.py, then <specific test files> should be `some_folder.some_file`."
    while parts and "." in parts[-1]:
        parts.pop()
    # Reconstruct the command
    test_command = " ".join(parts)
    return f"cd /testbed/ && {test_command} <specific test files>", test_hint


def get_test_description(eval_script="", swerepo=False, polyglot=False):
    assert not (swerepo and polyglot), "swerepo and polyglot cannot both be True"
    if swerepo:  # SWE repo
        swe_prompt = """The tests in the repository can be run with the bash command `{test_command}`. If no specific test files are provided, all tests will be run. The given command-line options must be used EXACTLY as specified. Do not use any other command-line options. {test_hint}"""
        test_command, test_hint = get_test_command(eval_script)
        description = swe_prompt.format(test_command=test_command, test_hint=test_hint)
    elif polyglot:
        description = (
            "In the repository folder, the tests can be run with the following "
            "bash command(s):\n\n```{eval_script}```\n\n"
            "IMPORTANT NOTES ABOUT THE TESTS:\n"
            "1. The test files are INTENTIONALLY HIDDEN from you. They are not "
            "present anywhere under /testbed, and they are not stored in any "
            "build-image cache directory either. Do NOT waste time searching "
            "for them with `find`, `ls`, or `git log`.\n"
            "2. Do NOT install or invoke test runners yourself (no `npm "
            "install`, `npx jest`, `pip install`, `cargo install`, etc.). The "
            "evaluator will apply the hidden tests and run them for you after "
            "you finish. Your only job is to implement the solution code.\n"
            "3. Only edit the solution/source files in /testbed. Do not create "
            "new test files, babel/config files, or package-lock files.\n"
            "4. You MAY run the test command above yourself if a test runner "
            "is already installed in the image (e.g. `npm run test`, "
            "`cargo test`, `go test ./...`), but expect it to report no tests "
            "found — that is normal and does not mean your solution is wrong.\n"
        ).format(eval_script=eval_script)
    else:  # hgm repo
        description = "The tests in the repository can be run with the bash command `cd /hgm/ && pytest -rA <specific test files>`. If no specific test files are provided, all tests will be run. The given command-line options must be used EXACTLY as specified. Do not use any other command-line options. ONLY test tools and utils. NEVER try to test or run agentic_system.forward()."

    return description.strip()
