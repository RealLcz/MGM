
========== User Instruction ==========
I have uploaded a Python code repository in the directory /testbed/. Help solve the following problem.

<problem_description>
Support defining specific `:private-members:` for autodoc
**Is your feature request related to a problem? Please describe.**
Currently, if I'm using autodoc, the `:private-members:` option does not allow specification of which private members to document. The current behavior is to document all private members, but what if I would only like to document 1 or 2?

**Describe the solution you'd like**
For `:private-members:` to take arguments, similarly to how `:members:` currently works

**Describe alternatives you've considered**
The current best way to do this is to explicitly list each class in a module and use `:autoattribute:`

- Some prior discussion: https://github.com/sphinx-doc/sphinx/issues/8009



</problem_description>

<test_description>
The tests in the repository can be run with the bash command `cd /testbed/ && : '>>>>> End Test Output' <specific test files>`. If no specific test files are provided, all tests will be run. The given command-line options must be used EXACTLY as specified. Do not use any other command-line options.
</test_description>

Your task is to make changes to the files in the /testbed/ directory to address the <problem_description>. I have already taken care of the required dependencies.

# IMPORTANT: Test-Driven Development Workflow

You MUST follow a structured test-driven development workflow to ensure your changes are validated:

## Step 1: Analyze Test Requirements
Before making any code changes, you MUST:
1. Use the `test_analysis` tool to identify relevant test files
2. Analyze test expectations and requirements
3. Understand what the code should do based on the tests

## Step 2: Establish Baseline
After understanding the requirements, you MUST:
1. Use the `run_tests` tool to run all tests and establish a baseline
2. Identify which tests are currently failing
3. Document the failure patterns

## Step 3: Implement Fix
Based on the test analysis and baseline failures, you MUST:
1. Make targeted code changes to address the issues
2. Reference the test expectations when making changes
3. Ensure your changes are minimal and focused

## Step 4: Validate Changes
After making code changes, you MUST:
1. Use the `run_tests` tool again to verify that tests now pass
2. If tests still fail, analyze the new output
3. Repeat Steps 3-4 until all relevant tests pass

## Step 5: Final Verification
Once tests pass, you MUST:
1. Run tests one final time to confirm stability
2. Document what was changed and why
3. Confirm that the fix addresses the <problem_description>

Remember: Do not skip any of these steps. Each step builds on the previous one. The `test_analysis` and `run_tests` tools are specifically designed to help you work systematically and avoid speculative patching.

Available tools you MUST use:
- `test_analysis`: To analyze test files and understand requirements BEFORE coding
- `run_tests`: To run tests and validate changes AFTER coding
- `editor`: To view and edit files
- `bash`: To run commands if needed

Follow this workflow rigorously to ensure reliable and validated fixes.

Failed to import tools.signature_tracer: No module named 'sphinx'
Error in chat_with_agent_open_router: No module named 'sphinx'
