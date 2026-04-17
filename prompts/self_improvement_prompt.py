# This file is adapted from https://github.com/jennyzzt/dgm.

import json
import os

from utils.common_utils import load_json_file, read_file
from utils.docker_utils import safe_log

coding_agent_summary = """# Coding Agent Summary

- **Main File**: `coding_agent.py`
  - Primary Class: `AgenticSystem`
  - The `forward()` function is the central entry point.
  - Prompts are located either within the `forward()` function or in the `prompts/` directory.
- **Tools**: `tools/`
  - The `tools/` directory contains various tools that LLMs can use to perform specific tasks.
  - Each tool must have a `tool_info()` function that returns a JSON object containing 'name', 'description', and 'input_schema'. The 'input_schema' should be a JSON object containing 'type', 'properties', and 'required'.
  - Each tool must have a `tool_function()` function that takes the arguments defined in input_schema, performs the tool's task, and returns a string.
  - See other tools for reference.
- **Utilities**: `utils/`
  - The `utils/` directory contains utility functions used across the codebase.

- **Additional Details**:
  - The agent is very good at automatically utilizing the right available tools at the right time. So do not have an agentic flow that explicitly forces a tool's usage.
  - Common tools, such as file editing and bash commands, are easy for the agent to recognize and use appropriately. However, more complex and niche tools may require explicit instructions in the prompt.
  - Tools should be designed to be as general as possible, ensuring they work across any GitHub repository. Avoid hardcoding repository-specific details or behaviors (e.g., paths).
  - Do not use 'while True' loops in the agent's code. This can cause the agent to get stuck and not respond.
  - Verify the implementation details of helper functions prior to usage to ensure proper integration and expected behavior.
  - Do not install additional packages or dependencies directly. Update `requirements.txt` if new dependencies are required and install them using `pip install -r requirements.txt`.
\n\n"""

coding_agent_summary_polyglot = (
    """# Coding Agent Summary

- **Main File**: `coding_agent.py`
  - Primary Class: `AgenticSystem`
  - The `forward()` function is the central entry point.
  - Prompts are located either within the `forward()` function or in the `prompts/` directory.
- **Tools**: `tools/`
  - The `tools/` directory contains various tools that LLMs can use to perform specific tasks.
  - Each tool must have a `tool_info()` function that returns a JSON object containing 'name', 'description', and 'input_schema'. The 'input_schema' should be a JSON object containing 'type', 'properties', and 'required'.
  - Each tool must have a `tool_function()` function that takes the arguments defined in input_schema, performs the tool's task, and returns a string.
  - See other tools for reference.
- **Utilities**: `utils/`
  - The `utils/` directory contains utility functions used across the codebase.

- **Additional Details**:
  - The coding agent trying to solve a programming task. A task is in one programming language, but the coding agent needs to deal with different languages including C++, Go, Java, JavaScript, Python, and Rust.
  - The agent is very good at automatically utilizing the right available tools at the right time. So do not have an agentic flow that explicitly forces a tool's usage.
  - Be detailed in the prompt about what steps (e.g. implementing tests, refining solutions, etc.) you would like the agent to execute.
  - Common tools, such as file editing and bash commands, are easy for the agent to recognize and use appropriately. However, more complex and niche tools may require explicit instructions in the prompt.
  - Tools should be designed to be as general as possible, ensuring they work across any task. Avoid hardcoding task-specific details or behaviors (e.g., paths or solutions).
  - DO NOT use 'while True' loops in the agent's code IN ANY CASE!! This can cause the agent to get stuck and not respond.
  - Verify the implementation details of helper functions prior to usage to ensure proper integration and expected behavior.
  - **DO NOT create parsing errors tools or functions, collecting raw error messages and letting the agent analyze them will be more efficient.**
\n\n
"""
    + """ 
### DOC: tool function schema

Carefully consider whether to add/enhance the current tool or edit the workflow in forward()

Pay special attention to making sure that "required" and "type" are always at the correct level of nesting. For example, "required" should be at the same level as "properties", not inside it.
Make sure that every property, no matter how short, has a type and description correctly nested inside it.
Other arguments than you have seen are not permitted. For example, in "edit_line_ranges" with "type": "array", arguments like "minItems" and "maxItems" are not permitted.
\n\n
"""
)

diagnose_system_message = """Here is the implementation of the coding agent.

# Coding Agent Implementation
----- Coding Agent Implementation Start -----
{code}
----- Coding Agent Implementation End -----

Your task is to identify ONE detailed plan that would improve the agent's coding ability. The improvement should not be specific to any particular GitHub issue or repository. Focus on general improvements that can enhance the agent's overall coding capabilities.
"""

swe_issue_prompt = (
    "Here is the log for the coding agent trying to solve the GitHub issues but failed."
)
polyglot_issue_prompt = "Here is the log for the coding agent trying to solve a programming task. A task is in one programming language, but the coding agent needs to deal with different languages including C++, Go, Java, JavaScript, Python, and Rust."

diagnose_prompt = """
# GitHub Issue
The GitHub issue that the agent is trying to solve.
----- GitHub Issue Start -----
{github_issue}
----- GitHub Issue End -----

# Predicted Patch
The agent's predicted patch to solve the issue.
----- Predicted Patch Start -----
{predicted_patch}
----- Predicted Patch End -----

# Private Test Patch
SWE-bench's official private tests to detect whether the issue is solved. This is not available to the agent during evaluation. The agent should try to implement its own tests.
----- Private Test Patch Start -----
{test_patch}
----- Private Test Patch End -----

# Issue Test Results
The test results from SWE-bench using the above official private tests.
----- Issue Test Results Start -----
{eval_log}
----- Issue Test Results End -----

Respond precisely in the following format including the JSON start and end markers:

```json
<JSON>
```

In <JSON>, provide a JSON response with the following fields:
- "log_summarization": Analyze the above logs and summarize how the agent tried to solve the GitHub issue. Note which tools and how they are used, the agent's problem-solving approach, and any issues encountered.
- "potential_improvements": Identify potential improvements to the coding agent that could enhance its coding capabilities. Focus on the agent's general coding abilities (e.g., better or new tools usable across any repository) rather than issue-specific fixes (e.g., tools only usable in one framework). All necessary dependencies and environment setup have already been handled, so do not focus on these aspects.
- "improvement_proposal": Choose ONE high-impact improvement from the identified potential improvements and describe it in detail. This should be a focused and comprehensive plan to enhance the agent's overall coding ability.
- "implementation_suggestion": Referring to the coding agent's summary and implementation, think critically about what feature or tool could be added or improved to best implement the proposed improvement. If the proposed feature can be implemented by modifying the existing tools, describe the modifications needed, instead of suggesting a new tool.
- "problem_description": Phrase the improvement proposal and implementation suggestion as a GitHub issue description. It should clearly describe the feature so that a software engineer viewing the issue and the repository can implement it.

Your response will be automatically parsed, so ensure that the string response is precisely in the correct format. Do NOT include the `<JSON>` tag in your output."""

diagnose_prompt_emptypatches = """There are some empty patches when attempting to solve GitHub issues. Since the coding agent is stochastic, it may not always produce a patch. Handle cases where the coding agent fails to generate a patch or generates one that only modifies the test cases without editing the primary source code. For example, the simplest solution is to ask the agent to try again.

Respond precisely in the following format including the JSON start and end markers:

```json
<JSON>
```

In <JSON>, provide a JSON response with the following fields:
- "potential_improvements": Identify potential improvements to the coding agent's system. All necessary dependencies and environment setup have already been handled, so do not focus on these aspects.
- "improvement_proposal": Choose ONE high-impact improvement from the identified potential improvements and describe it in detail. This should be a focused and comprehensive plan to enhance the agent's overall coding ability.
- "implementation_suggestion": Referring to the coding agent's summary and implementation, think critically about what feature could be added or improved to best implement the proposed improvement.
- "problem_description": Phrase the improvement proposal and implementation suggestion as a GitHub issue description. It should clearly describe the feature so that a software engineer viewing the issue and the repository can implement it.

Your response will be automatically parsed, so ensure that the string response is precisely in the correct format. Do NOT include the `<JSON>` tag in your output."""

diagnose_prompt_stochasticity = """Since the coding agent is stochastic, it may not produce the correct patch for the given problem statement on the first try. Take into account the agent's stochastic nature and provide a solution to handle such cases. For example, one solution could be to ask the agent to try multiple times and select the best patch. The file `utils/eval_utils.py` contains helper functions to evaluate the generated patches. Giving previous attempts as context to the agent may also help.

Respond precisely in the following format including the JSON start and end markers:

```json
<JSON>
```

In <JSON>, provide a JSON response with the following fields:
- "potential_improvements": Identify potential improvements to the coding agent's system. All necessary dependencies and environment setup have already been handled, so do not focus on these aspects.
- "improvement_proposal": Choose ONE high-impact improvement from the identified potential improvements and describe it in detail. This should be a focused and comprehensive plan to enhance the agent's overall coding ability.
- "implementation_suggestion": Referring to the coding agent's summary and implementation, think critically about what feature could be added or improved to best implement the proposed improvement.
- "problem_description": Phrase the improvement proposal and implementation suggestion as a GitHub issue description. It should clearly describe the feature so that a software engineer viewing the issue and the repository can implement it.

Your response will be automatically parsed, so ensure that the string response is precisely in the correct format. Do NOT include the `<JSON>` tag in your output."""

diagnose_prompt_contextlength = """While the coding agent is attempting to solve GitHub issues, it encounters an error due to the input being too long for the requested model. This error is likely due to the context length exceeding the model's maximum input size. Handle cases where the input is too long for the model. The coding agent is mainly using the file `llm_withtools.py`. LLMs typically have a context window of 200k tokens. Handle context length only if the context window limit is reached and caught as an exception; otherwise, it is okay to leave it as is.

<error_message>
Error in get_response_withtools: Error code: 400 - {'message': 'Input is too long for requested model.'}
</error_message>

Respond precisely in the following format including the JSON start and end markers:

```json
<JSON>
```

In <JSON>, provide a JSON response with the following fields:
- "potential_improvements": Identify potential improvements to the coding agent's system. All necessary dependencies and environment setup have already been handled, so do not focus on these aspects.
- "improvement_proposal": Choose ONE high-impact improvement from the identified potential improvements and describe it in detail. This should be a focused and comprehensive plan to enhance the agent's overall coding ability.
- "implementation_suggestion": Referring to the coding agent's summary and implementation, think critically about what feature could be added or improved to best implement the proposed improvement.
- "problem_description": Phrase the improvement proposal and implementation suggestion as a GitHub issue description. It should clearly describe the feature and details so that a software engineer viewing the issue and the repository can implement it.

Your response will be automatically parsed, so ensure that the string response is precisely in the correct format. Do NOT include the `<JSON>` tag in your output."""

problem_description_prompt = (
    """# To Implement\n\n{implementation_suggestion}\n\n{problem_description}"""
)

# ---------------------------------------------------------------------------
# Strategy B: two different tasks, same agent commit.
# Goal: cross-task pattern mining to find systematic weaknesses.
# ---------------------------------------------------------------------------

STRATEGY_B_INTRO_SWE = """You are given TWO runs of the coding agent on **different** GitHub issues using the **same agent codebase**. At least one of the runs failed. Your goal is to identify a **systematic weakness** in the agent that manifests across different tasks, rather than a fix for one specific issue.

Analyze both scenarios carefully:
- Look for **common patterns**: Do both runs share similar failure modes (e.g., poor tool usage, wrong debugging strategy, incomplete understanding of the codebase)?
- Look for **contrasting signals**: If one run succeeded and the other failed, what capability was present in the successful run but missing in the failed one?
- Focus on the agent's **general approach and decision-making**, not on repository-specific knowledge.

"""

STRATEGY_B_INTRO_POLYGLOT = """You are given TWO runs of the coding agent on **different** programming tasks using the **same agent codebase**. At least one of the runs failed. Your goal is to identify a **systematic weakness** in the agent that manifests across different tasks and languages.

Analyze both scenarios:
- Look for **common patterns** in how the agent approaches problems across languages.
- If one run succeeded and the other failed, identify what capability made the difference.
- Focus on **general coding strategies** that work across C++, Go, Java, JavaScript, Python, and Rust.

"""

STRATEGY_B_JSON_BLOCK = """
Respond precisely in the following format including the JSON start and end markers:

```json
<JSON>
```

In <JSON>, provide a JSON response with the following fields:
- "task_1_analysis": Analyze how the agent approached Task 1. Summarize the tools used, the problem-solving strategy, and the outcome (success or failure). If it failed, identify the specific failure mode.
- "task_2_analysis": Analyze how the agent approached Task 2. Same structure as above.
- "common_weakness": Identify the **systematic weakness** that connects the two scenarios. This should be a general agent limitation (e.g., poor error recovery, lack of incremental testing, weak codebase navigation) rather than a task-specific issue. If one task succeeded and the other failed, explain why the successful approach did not transfer.
- "potential_improvements": List potential improvements to the coding agent that address the identified common weakness. Focus on general agent capabilities rather than fixing one repository-specific issue.
- "improvement_proposal": Choose ONE high-impact improvement from the list that best addresses the common weakness across both tasks. Describe it in detail.
- "implementation_suggestion": Referring to the coding agent's summary and implementation, describe what feature or tool to add or change. Prefer extending existing tools over inventing redundant ones.
- "problem_description": Phrase the improvement proposal and implementation suggestion as a GitHub issue description so an engineer can implement it.

Your response will be automatically parsed, so ensure that the string response is precisely in the correct format. Do NOT include the `<JSON>` tag in your output."""

# ---------------------------------------------------------------------------
# Strategy C: same (or overlapping) task, two different agent versions.
# Goal: differential analysis -- learn from how different versions handle the
# same problem, especially when one succeeds and the other fails.
# ---------------------------------------------------------------------------

STRATEGY_C_INTRO_BOTH_FAILED_SWE = """You are given **one** GitHub issue attempted by **two different versions** of the coding agent. **Both versions failed** to solve this issue.

Logs are labeled:
- **Primary** (commit: `{primary_commit}`, overall accuracy: {primary_utility:.1%}) -- the version you should optimize.
- **Context** (commit: `{context_commit}`, overall accuracy: {context_utility:.1%}) -- for comparison only.

Your goal is a **differential analysis**: compare how the two versions approached the same problem, identify which got closer to a solution and why, and propose one improvement for the Primary version.

"""

STRATEGY_C_INTRO_ONE_SUCCEEDED_SWE = """You are given **one** GitHub issue attempted by **two different versions** of the coding agent. One version **succeeded** and the other **failed**.

Logs are labeled:
- **Primary** (commit: `{primary_commit}`, overall accuracy: {primary_utility:.1%}) -- the version you should optimize.
- **Context** (commit: `{context_commit}`, overall accuracy: {context_utility:.1%}) -- for comparison.

This is a powerful diagnostic signal: by comparing a successful and failed attempt on the **same** issue, you can pinpoint exactly what capability or approach made the difference. Focus on extracting a **transferable lesson** -- not a task-specific fix, but a general improvement.

"""

STRATEGY_C_INTRO_BOTH_FAILED_POLYGLOT = """You are given **one** programming task attempted by **two different versions** of the coding agent. **Both versions failed**.

Logs are labeled:
- **Primary** (commit: `{primary_commit}`, overall accuracy: {primary_utility:.1%}) -- optimize this version.
- **Context** (commit: `{context_commit}`, overall accuracy: {context_utility:.1%}) -- for comparison.

Compare how each version approached the same task. Identify which got closer and why, then propose one improvement for the Primary version.

"""

STRATEGY_C_INTRO_ONE_SUCCEEDED_POLYGLOT = """You are given **one** programming task attempted by **two different versions** of the coding agent. One version **succeeded** and the other **failed**.

Logs are labeled:
- **Primary** (commit: `{primary_commit}`, overall accuracy: {primary_utility:.1%}) -- optimize this version.
- **Context** (commit: `{context_commit}`, overall accuracy: {context_utility:.1%}) -- for comparison.

Analyze what the successful version did differently and extract a **transferable lesson** that applies beyond this specific task.

"""

STRATEGY_C_JSON_BLOCK = """
Respond precisely in the following format including the JSON start and end markers:

```json
<JSON>
```

In <JSON>, provide a JSON response with the following fields:
- "primary_approach": Analyze how the Primary version approached the task. Summarize tools used, problem-solving strategy, and outcome.
- "context_approach": Analyze how the Context version approached the same task. Same structure.
- "differential_insight": Compare the two approaches side-by-side. What specific differences in behavior, tool usage, or strategy led to the different outcomes? If both failed, which got closer and why?
- "transferable_lesson": Extract a generalizable lesson from this comparison that applies beyond this specific task. What general capability or strategy should the Primary agent adopt?
- "potential_improvements": Based on the differential analysis, list potential improvements for the Primary agent.
- "improvement_proposal": Choose ONE high-impact improvement. Describe it in detail as a unified plan to enhance the Primary agent.
- "implementation_suggestion": Referring to the coding agent's summary and implementation, describe what feature or tool to add or change. Prefer extending existing tools over inventing redundant ones.
- "problem_description": Phrase the improvement proposal and implementation suggestion as a GitHub issue description so an engineer can implement it.

Your response will be automatically parsed, so ensure that the string response is precisely in the correct format. Do NOT include the `<JSON>` tag in your output."""


def _clip_text_for_joint(s, max_len=30000):
    if s is None:
        return ""
    if len(s) <= max_len:
        return s
    half = max_len // 2
    return s[:half] + "\n\n... [log truncated: middle omitted] ...\n\n" + s[-half:]


def _swe_code_bundle(root_dir, patch_files):
    code_files = ["coding_agent.py", "tools/", "utils/"]
    exclude_files = [
        "utils/evo_utils.py",
        "utils/docker_utils.py",
        "utils/swe_log_parsers.py",
        "prompts/self_improvement_prompt.py",
    ]
    return get_current_code(
        root_dir, code_files, patch_files=patch_files, exclude_files=exclude_files
    )


def _polyglot_code_bundle(root_dir, patch_files, is_polyglot):
    code_files = ["coding_agent.py", "tools/", "utils/"]
    exclude_files = [
        "utils/evo_utils.py",
        "utils/docker_utils.py",
        "utils/swe_log_parsers.py",
        "utils/eval_utils.py",
        "prompts/self_improvement_prompt.py",
    ]
    return get_current_code(
        root_dir,
        code_files,
        patch_files=patch_files,
        exclude_files=exclude_files,
        is_polyglot=is_polyglot,
    )


def _collect_swe_entry_context(entry_id, commit, out_dir, dataset):
    (
        md_logs,
        eval_logs,
        predicted_patches,
        eval_results,
    ) = find_selfimprove_eval_logs(entry_id, out_dir, commit_id=commit)
    md_log, eval_log, predicted_patch, eval_result = process_selfimprove_eval_logs(
        md_logs, eval_logs, predicted_patches, eval_results
    )
    md_log = _clip_text_for_joint(md_log)
    eval_log = _clip_text_for_joint(eval_log)
    predicted_patch = _clip_text_for_joint(predicted_patch, max_len=20000)

    entry = next((e for e in dataset if e["instance_id"] == entry_id), None)
    if not entry:
        raise ValueError(f"Could not find entry with id {entry_id} in dataset.")
    answer_patch = entry["patch"]
    test_patch = entry["test_patch"]
    github_issue = entry["problem_statement"]
    return {
        "md_log": md_log,
        "eval_log": eval_log,
        "predicted_patch": predicted_patch,
        "eval_result": eval_result,
        "answer_patch": answer_patch,
        "test_patch": test_patch,
        "github_issue": github_issue,
    }


def _format_swe_scenario(instance_id, ctx, label="Scenario"):
    return f"""
## {label} (instance_id: {instance_id})

# GitHub Issue
----- GitHub Issue Start -----
{ctx["github_issue"]}
----- GitHub Issue End -----

# Predicted Patch
----- Predicted Patch Start -----
{ctx["predicted_patch"]}
----- Predicted Patch End -----

# Private Test Patch
----- Private Test Patch Start -----
{ctx["test_patch"]}
----- Private Test Patch End -----

# Issue Test Results
----- Issue Test Results Start -----
{ctx["eval_log"]}
----- Issue Test Results End -----
"""


def build_joint_diagnose_user_prompt_swe_two_entries(
    entry_id_a, entry_id_b, commit, out_dir, dataset
):
    ctx_a = _collect_swe_entry_context(entry_id_a, commit, out_dir, dataset)
    ctx_b = _collect_swe_entry_context(entry_id_b, commit, out_dir, dataset)
    body = (
        STRATEGY_B_INTRO_SWE
        + swe_issue_prompt
        + _format_swe_scenario(entry_id_a, ctx_a, label="Task 1")
        + _format_swe_scenario(entry_id_b, ctx_b, label="Task 2")
        + STRATEGY_B_JSON_BLOCK
    )
    return body


def _collect_polyglot_entry_context(entry_id, commit, out_dir, dataset):
    md_logs, eval_logs, predicted_patches, eval_results = find_selfimprove_eval_logs(
        entry_id, out_dir, commit_id=commit
    )
    md_log, eval_log, predicted_patch, eval_result = process_selfimprove_eval_logs(
        md_logs, eval_logs, predicted_patches, eval_results
    )
    md_log = _clip_text_for_joint(md_log)
    eval_log = _clip_text_for_joint(eval_log)
    predicted_patch = _clip_text_for_joint(predicted_patch, max_len=20000)

    entry = next((e for e in dataset if e["instance_id"] == entry_id), None)
    if not entry:
        raise ValueError(f"Could not find entry with id {entry_id} in dataset.")
    is_polyglot = "language" in entry
    answer_patch = entry["patch"] if not is_polyglot else entry["reference_answers"]
    test_patch = entry["test_patch"] if not is_polyglot else entry["reference_tests"]
    github_issue = entry["problem_statement"]
    return {
        "md_log": md_log,
        "eval_log": eval_log,
        "predicted_patch": predicted_patch,
        "eval_result": eval_result,
        "answer_patch": answer_patch,
        "test_patch": test_patch,
        "github_issue": github_issue,
        "is_polyglot": is_polyglot,
    }


def _format_polyglot_failure_scenario(instance_id, ctx):
    return f"""
## Failure scenario (instance_id: {instance_id})

# Task / problem statement
----- Task Start -----
{ctx["github_issue"]}
----- Task End -----

# Predicted Patch
----- Predicted Patch Start -----
{ctx["predicted_patch"]}
----- Predicted Patch End -----

# Reference solution (not seen by agent)
----- Reference Answers Start -----
{ctx["answer_patch"]}
----- Reference Answers End -----

# Reference tests (not seen by agent)
----- Reference Tests Start -----
{ctx["test_patch"]}
----- Reference Tests End -----

# Evaluation / test results
----- Evaluation Results Start -----
{ctx["eval_log"]}
----- Evaluation Results End -----
"""


def build_joint_diagnose_user_prompt_polyglot_two_entries(
    entry_id_a, entry_id_b, commit, out_dir, dataset
):
    ctx_a = _collect_polyglot_entry_context(entry_id_a, commit, out_dir, dataset)
    ctx_b = _collect_polyglot_entry_context(entry_id_b, commit, out_dir, dataset)
    body = (
        STRATEGY_B_INTRO_POLYGLOT
        + polyglot_issue_prompt
        + _format_polyglot_failure_scenario(entry_id_a, ctx_a)
        + _format_polyglot_failure_scenario(entry_id_b, ctx_b)
        + STRATEGY_B_JSON_BLOCK
    )
    return body


def _get_strategy_c_intro(meta, is_polyglot):
    if meta is None:
        meta = {}
    primary_resolved = meta.get("primary_resolved_task", False)
    context_resolved = meta.get("context_resolved_task", False)
    one_succeeded = primary_resolved != context_resolved

    fmt_kwargs = {
        "primary_commit": meta.get("primary_commit", "unknown"),
        "context_commit": meta.get("context_commit", "unknown"),
        "primary_utility": meta.get("primary_utility", 0.0),
        "context_utility": meta.get("context_utility", 0.0),
    }

    if is_polyglot:
        template = STRATEGY_C_INTRO_ONE_SUCCEEDED_POLYGLOT if one_succeeded else STRATEGY_C_INTRO_BOTH_FAILED_POLYGLOT
    else:
        template = STRATEGY_C_INTRO_ONE_SUCCEEDED_SWE if one_succeeded else STRATEGY_C_INTRO_BOTH_FAILED_SWE

    return template.format(**fmt_kwargs)


def build_joint_diagnose_user_prompt_swe_two_commits(
    task_id,
    commit_primary,
    commit_context,
    out_dir,
    dataset,
    primary_label="Primary",
    context_label="Context",
    strategy_c_meta=None,
):
    ctx_p = _collect_swe_entry_context(task_id, commit_primary, out_dir, dataset)
    ctx_c = _collect_swe_entry_context(task_id, commit_context, out_dir, dataset)

    meta = dict(strategy_c_meta or {})
    meta.setdefault("primary_commit", commit_primary)
    meta.setdefault("context_commit", commit_context)
    intro = _get_strategy_c_intro(meta, is_polyglot=False)

    body = (
        intro
        + swe_issue_prompt
        + f"\n## {primary_label} ({commit_primary})\n"
        + _format_swe_scenario(task_id, ctx_p, label=primary_label)
        + f"\n## {context_label} ({commit_context})\n"
        + _format_swe_scenario(task_id, ctx_c, label=context_label)
        + STRATEGY_C_JSON_BLOCK
    )
    return body


def build_joint_diagnose_user_prompt_polyglot_two_commits(
    task_id,
    commit_primary,
    commit_context,
    out_dir,
    dataset,
    primary_label="Primary",
    context_label="Context",
    strategy_c_meta=None,
):
    ctx_p = _collect_polyglot_entry_context(task_id, commit_primary, out_dir, dataset)
    ctx_c = _collect_polyglot_entry_context(task_id, commit_context, out_dir, dataset)

    meta = dict(strategy_c_meta or {})
    meta.setdefault("primary_commit", commit_primary)
    meta.setdefault("context_commit", commit_context)
    intro = _get_strategy_c_intro(meta, is_polyglot=True)

    body = (
        intro
        + polyglot_issue_prompt
        + f"\n## {primary_label} ({commit_primary})\n"
        + _format_polyglot_failure_scenario(task_id, ctx_p)
        + f"\n## {context_label} ({commit_context})\n"
        + _format_polyglot_failure_scenario(task_id, ctx_c)
        + STRATEGY_C_JSON_BLOCK
    )
    return body


def get_diagnose_prompt_swe_two_entries(
    entry_ids, commit, root_dir, out_dir, dataset, patch_files=None
):
    if patch_files is None:
        patch_files = []
    if len(entry_ids) != 2:
        raise ValueError("entry_ids must contain exactly two instance ids")
    if entry_ids[0] == entry_ids[1]:
        raise ValueError("joint SWE diagnosis requires two distinct instance ids")
    user_prompt = build_joint_diagnose_user_prompt_swe_two_entries(
        entry_ids[0], entry_ids[1], commit, out_dir, dataset
    )
    code_text = _swe_code_bundle(root_dir, patch_files)
    diagnose_system_message_out = coding_agent_summary + diagnose_system_message.format(
        code=code_text
    )
    return diagnose_system_message_out, user_prompt


def get_diagnose_prompt_polyglot_two_entries(
    entry_ids, commit, root_dir, out_dir, dataset, patch_files=None
):
    if patch_files is None:
        patch_files = []
    if len(entry_ids) != 2:
        raise ValueError("entry_ids must contain exactly two instance ids")
    if entry_ids[0] == entry_ids[1]:
        raise ValueError("joint polyglot diagnosis requires two distinct instance ids")
    user_prompt = build_joint_diagnose_user_prompt_polyglot_two_entries(
        entry_ids[0], entry_ids[1], commit, out_dir, dataset
    )
    ent0 = next((e for e in dataset if e["instance_id"] == entry_ids[0]), None)
    ent1 = next((e for e in dataset if e["instance_id"] == entry_ids[1]), None)
    is_polyglot = (ent0 and "language" in ent0) or (ent1 and "language" in ent1)
    code_text = _polyglot_code_bundle(root_dir, patch_files, is_polyglot)
    safe_log(
        f"[joint polyglot two entries] code len={len(code_text)}, entry0={entry_ids[0]}, entry1={entry_ids[1]}"
    )
    diagnose_system_message_out = (
        coding_agent_summary_polyglot + diagnose_system_message.format(code=code_text)
    )
    return diagnose_system_message_out, user_prompt


def get_diagnose_prompt_swe_shared_task_two_commits(
    task_id,
    commit_primary,
    commit_context,
    root_dir,
    out_dir,
    dataset,
    patch_files=None,
    primary_label="Primary",
    context_label="Context",
    strategy_c_meta=None,
):
    if patch_files is None:
        patch_files = []
    if commit_primary == commit_context:
        raise ValueError("shared-task diagnosis requires two distinct commits")
    user_prompt = build_joint_diagnose_user_prompt_swe_two_commits(
        task_id,
        commit_primary,
        commit_context,
        out_dir,
        dataset,
        primary_label=primary_label,
        context_label=context_label,
        strategy_c_meta=strategy_c_meta,
    )
    code_text = _swe_code_bundle(root_dir, patch_files)
    diagnose_system_message_out = coding_agent_summary + diagnose_system_message.format(
        code=code_text
    )
    return diagnose_system_message_out, user_prompt


def get_diagnose_prompt_polyglot_shared_task_two_commits(
    task_id,
    commit_primary,
    commit_context,
    root_dir,
    out_dir,
    dataset,
    patch_files=None,
    primary_label="Primary",
    context_label="Context",
    strategy_c_meta=None,
):
    if patch_files is None:
        patch_files = []
    if commit_primary == commit_context:
        raise ValueError("shared-task diagnosis requires two distinct commits")
    user_prompt = build_joint_diagnose_user_prompt_polyglot_two_commits(
        task_id,
        commit_primary,
        commit_context,
        out_dir,
        dataset,
        primary_label=primary_label,
        context_label=context_label,
        strategy_c_meta=strategy_c_meta,
    )
    entry = next((e for e in dataset if e["instance_id"] == task_id), None)
    if not entry:
        raise ValueError(f"Could not find entry with id {task_id} in dataset.")
    is_polyglot = "language" in entry
    code_text = _polyglot_code_bundle(root_dir, patch_files, is_polyglot)
    safe_log(
        f"[joint polyglot two commits] code len={len(code_text)}, task={task_id}, primary={commit_primary}, context={commit_context}"
    )
    diagnose_system_message_out = (
        coding_agent_summary_polyglot + diagnose_system_message.format(code=code_text)
    )
    return diagnose_system_message_out, user_prompt


def get_problem_description_prompt(response_json, is_polyglot=False):
    if is_polyglot:
        return coding_agent_summary_polyglot + problem_description_prompt.format(
            implementation_suggestion=response_json["implementation_suggestion"],
            problem_description=response_json["problem_description"],
        )
    else:
        return coding_agent_summary + problem_description_prompt.format(
            implementation_suggestion=response_json["implementation_suggestion"],
            problem_description=response_json["problem_description"],
        )


def read_mdlog_file(filepath, filter=True):
    if not filter:
        return read_file(filepath)

    # Filter out unwanted strings from the log file
    filter_content = [
        "Error in get_response_withtools",
    ]
    filtered_lines = []
    with open(filepath, "r") as f:
        for line in f:
            # Check if line contains any of the unwanted strings
            if not any(line.startswith(fc) for fc in filter_content):
                filtered_lines.append(line.rstrip("\n"))
    # Join the remaining lines with a newline and return
    return "\n".join(filtered_lines).strip()


def find_selfimprove_eval_logs(entry, out_dir, commit_id="initial", filter=True):
    predictions_dir = os.path.join(out_dir, commit_id, "predictions")
    all_preds_folders = [
        f
        for f in os.listdir(predictions_dir)
        if os.path.isdir(os.path.join(predictions_dir, f))
    ]
    prediction_log_files = [
        os.path.join(predictions_dir, f, f"{entry}.md") for f in all_preds_folders
    ]
    prediction_json_files = [
        os.path.join(predictions_dir, f, f"{entry}.json") for f in all_preds_folders
    ]
    prediction_log_files = [f for f in prediction_log_files if os.path.exists(f)]
    prediction_json_files = [f for f in prediction_json_files if os.path.exists(f)]
    try_eval_logs = [
        os.path.join(predictions_dir, f, f"{entry}_eval.md") for f in all_preds_folders
    ]
    try_eval_logs = [f for f in try_eval_logs if os.path.exists(f)]
    # Read the evaluation log files and convert markdown to text
    md_logs = []
    for file in prediction_log_files:
        md_logs.append(read_mdlog_file(file, filter=filter))
    # Read the predicted patches
    predicted_patches = []
    eval_results = []
    for json_file in prediction_json_files:
        prediction_data = load_json_file(json_file)
        predicted_patch = prediction_data.get("model_patch", "")
        predicted_patches.append(predicted_patch)
        eval_result = prediction_data.get("eval_result", "")
        eval_results.append(eval_result)
    if not try_eval_logs:
        # Find evaluation log under out_dir/logs/run_evaluation/{f}/{f}/
        # NOTE: it is {f}/{f}/ because of how swe_bench/report.py is reusing code from SWE-bench
        eval_log_files = [
            os.path.join(
                out_dir, commit_id, f"logs/run_evaluation/", f, f, entry, "report.json"
            )
            for f in all_preds_folders
        ]
        eval_log_files = [f for f in eval_log_files if os.path.exists(f)]
        eval_logs = []
        for file in eval_log_files:
            eval_json = load_json_file(file)
            eval_logs.append(get_eval_log_text(eval_json))
    else:
        eval_logs = []
        for file in try_eval_logs:
            print(file)
            eval_logs.append(read_file(file))
    return md_logs, eval_logs, predicted_patches, eval_results


def process_selfimprove_eval_logs(md_logs, eval_logs, predicted_patches, eval_results):
    # NOTE: using only the first logs
    md_log = md_logs[0] if md_logs else "No logs available."
    eval_log = (
        eval_logs[0]
        if eval_logs
        else "No test results available. Assume all tests failed."
    )
    predicted_patch = (
        predicted_patches[0]
        if predicted_patches
        else "No predicted patch available. Assume the agent failed."
    )

    # truncate logs if too long
    if len(md_log) > 100000:
        md_log = md_log[:100000] + "\n<log clipped>"
    # if len(eval_log) > 100000:
    #     eval_log = eval_log[:100000] + "\n<log clipped>"
    # if len(predicted_patch) > 100000:
    #     predicted_patch = predicted_patch[:100000] + "\n<patch clipped>"

    eval_result = (
        eval_results[0]
        if eval_results
        else "No evaluation result available. Assume the agent failed."
    )
    return md_log, eval_log, predicted_patch, eval_result


diagnose_prompt_emptypatches_polyglot = """There are some empty patches when attempting to solve GitHub issues. Since the coding agent is stochastic, it may not always produce a patch. Handle cases where the coding agent fails to generate a patch or generates one that only modifies the test cases without editing the primary source code. For example, the simplest solution is to change the prompt to specifically make sure it called the edit tool.

Respond precisely in the following format including the JSON start and end markers:

```json
<JSON>
```

In <JSON>, provide a JSON response with the following fields:
- "potential_improvements": Identify potential improvements to the coding agent's system. All necessary dependencies and environment setup have already been handled, so do not focus on these aspects.
- "improvement_proposal": Choose ONE high-impact improvement from the identified potential improvements and describe it in detail. This should be a focused and comprehensive plan to enhance the agent's overall coding ability.
- "implementation_suggestion": Referring to the coding agent's summary and implementation, think critically about what feature could be added or improved to best implement the proposed improvement.
- "problem_description": Phrase the improvement proposal and implementation suggestion as a GitHub issue description. It should clearly describe the feature so that a software engineer viewing the issue and the repository can implement it.

Your response will be automatically parsed, so ensure that the string response is precisely in the correct format. Do NOT include the `<JSON>` tag in your output."""

diagnose_prompt_stochasticity_polyglot = """Since the coding agent is stochastic, it may not produce the correct patch for the given problem statement on the first try. 
Take into account the agent's stochastic nature and provide a solution to handle such cases. 
For example, one solution could be to ask the agent to try multiple times and select the best patch according to the test results. Scale the reflection times is also a good idea.
Giving previous attempts and test results as context to the agent may also help.
The tests for tasks are not provided in the repo, and the agent needs workflow design to implement them.
Agent's own tests that validate the solution may not cover all the cases that will be checked by the private tests during official scoring. So the quality of implemented tests are crucial.

Respond precisely in the following format including the JSON start and end markers:

```json
<JSON>
```

In <JSON>, provide a JSON response with the following fields:
- "potential_improvements": Identify potential improvements to the coding agent's system. All necessary dependencies and environment setup have already been handled, so do not focus on these aspects.
- "improvement_proposal": Choose ONE high-impact improvement from the identified potential improvements and describe it in detail. This should be a focused and comprehensive plan to enhance the agent's overall coding ability.
- "implementation_suggestion": Referring to the coding agent's summary and implementation, think critically about what feature could be added or improved to best implement the proposed improvement.
- "problem_description": Phrase the improvement proposal and implementation suggestion as a GitHub issue description. It should clearly describe the feature so that a software engineer viewing the issue and the repository can implement it.

Your response will be automatically parsed, so ensure that the string response is precisely in the correct format. Do NOT include the `<JSON>` tag in your output."""


def get_diagnose_prompt_swe(
    entry_id, commit, root_dir, out_dir, dataset, patch_files=[]
):
    if entry_id == "solve_empty_patches":
        # Get user prompt for solving empty patches
        diagnose_prompt_out = diagnose_prompt_emptypatches
    elif entry_id == "solve_stochasticity":
        # Get user prompt for solving stochasticity
        diagnose_prompt_out = diagnose_prompt_stochasticity
    elif entry_id == "solve_contextlength":
        # Get user prompt for solving context length
        diagnose_prompt_out = diagnose_prompt_contextlength
    else:
        # Get user prompt for the entry
        (
            md_logs,
            eval_logs,
            predicted_patches,
            eval_results,
        ) = find_selfimprove_eval_logs(entry_id, out_dir, commit_id=commit)
        md_log, eval_log, predicted_patch, eval_result = process_selfimprove_eval_logs(
            md_logs, eval_logs, predicted_patches, eval_results
        )
        entry = next((e for e in dataset if e["instance_id"] == entry_id), None)
        answer_patch = entry["patch"]
        test_patch = entry["test_patch"]
        github_issue = entry["problem_statement"]
        diagnose_prompt_out = swe_issue_prompt + diagnose_prompt.format(
            eval_log=eval_log,
            predicted_patch=predicted_patch,
            answer_patch=answer_patch,
            test_patch=test_patch,
            github_issue=github_issue,
        )

    # Get system prompt
    code_files = ["coding_agent.py", "tools/", "utils/"]
    exclude_files = [
        "utils/evo_utils.py",
        "utils/docker_utils.py",
        "utils/swe_log_parsers.py",
        "prompts/self_improvement_prompt.py",
    ]
    code_text = get_current_code(
        root_dir, code_files, patch_files=patch_files, exclude_files=exclude_files
    )
    diagnose_system_message_out = coding_agent_summary + diagnose_system_message.format(
        code=code_text
    )

    return diagnose_system_message_out, diagnose_prompt_out


def get_diagnose_prompt_polyglot(
    entry_id, commit, root_dir, out_dir, dataset, patch_files=[]
):
    md_logs, eval_logs, predicted_patches, eval_results = find_selfimprove_eval_logs(
        entry_id, out_dir, commit_id=commit
    )
    md_log, eval_log, predicted_patch, eval_result = process_selfimprove_eval_logs(
        md_logs, eval_logs, predicted_patches, eval_results
    )

    entry = next((e for e in dataset if e["instance_id"] == entry_id), None)
    assert entry, f"Could not find entry with id {entry_id} in dataset."
    is_polyglot = "language" in entry
    answer_patch = entry["patch"] if not is_polyglot else entry["reference_answers"]
    test_patch = entry["test_patch"] if not is_polyglot else entry["reference_tests"]
    github_issue = entry["problem_statement"]

    code_files = ["coding_agent.py", "tools/", "utils/"]
    exclude_files = [
        "utils/evo_utils.py",
        "utils/docker_utils.py",
        "utils/swe_log_parsers.py",
        "utils/eval_utils.py",
        "prompts/self_improvement_prompt.py",
    ]
    code_text = get_current_code(
        root_dir,
        code_files,
        patch_files=patch_files,
        exclude_files=exclude_files,
        is_polyglot=is_polyglot,
    )
    # if len(code_text) > 100000:
    #     code_text = code_text[:100000] + "\n<code clipped>"
    safe_log(
        f"Code text length: {len(code_text)}, md_log length: {len(md_log)}, eval_log length: {len(eval_log)}, predicted_patch length: {len(predicted_patch)}, answer_patch length: {len(answer_patch)}, test_patch length: {len(test_patch)}, github_issue length: {len(github_issue)}, "
    )
    import random

    if random.random() < 0.25:
        # Get user prompt for solving stochasticity
        return coding_agent_summary_polyglot + diagnose_system_message.format(
            code=code_text
        ), diagnose_prompt_stochasticity_polyglot
    if "empty_patch" in eval_result:
        return coding_agent_summary_polyglot + diagnose_system_message.format(
            code=code_text
        ), diagnose_prompt_emptypatches_polyglot
    return coding_agent_summary_polyglot + diagnose_system_message.format(
        code=code_text
    ), polyglot_issue_prompt + diagnose_prompt.format(
        eval_log=eval_log,
        predicted_patch=predicted_patch,
        answer_patch=answer_patch,
        test_patch=test_patch,
        github_issue=github_issue,
    )


def get_eval_log_text(eval_json, test_status=None):
    if not test_status:
        first_key = next(iter(eval_json))
        tests_status = eval_json[first_key].get("tests_status", {})

    # Initialize result parts
    result_parts = []

    # Handle FAIL_TO_PASS tests
    result_parts.append("## New tests for the issue")
    result_parts.append(
        "These test whether the coding agent fixed the requested issue."
    )
    fail_to_pass = tests_status.get("FAIL_TO_PASS", {})
    if fail_to_pass.get("success"):
        result_parts.append(f"Successfully fixed {len(fail_to_pass['success'])}:")
        for test in fail_to_pass["success"]:
            result_parts.append(f"  ✓ {test}")
    if fail_to_pass.get("failure"):
        result_parts.append(f"Failed to fix {len(fail_to_pass['failure'])} tests:")
        for test in fail_to_pass["failure"]:
            result_parts.append(f"  ✗ {test}")
    else:
        result_parts.append(f"Pass All New Tests!")

    # Handle PASS_TO_PASS tests
    result_parts.append("## Previous tests from the repo")
    result_parts.append(
        "These test whether the modification that coding agent made break the previous tests"
    )
    pass_to_pass = tests_status.get("PASS_TO_PASS", {})
    if pass_to_pass.get("success"):
        result_parts.append(
            f"\nMaintained {len(pass_to_pass['success'])} passing tests"
        )
    if pass_to_pass.get("failure"):
        result_parts.append(
            f"Regression in {len(pass_to_pass['failure'])} previously passing tests:"
        )
        for test in pass_to_pass["failure"]:
            result_parts.append(f"  ✗ {test}")
    else:
        result_parts.append(f"Pass All Previous Tests!")

    return (
        "\n".join(result_parts)
        if result_parts
        else "No test results available. Assume all tests failed."
    )


def get_current_code(
    current_dir, code_files, patch_files=None, exclude_files=None, is_polyglot=False
):
    """
    Retrieves the contents of specified Python files/directories, optionally
    applying patches. Also allows excluding specific files from the result.

    :param current_dir: Root directory to resolve paths against.
    :param code_files: List of files or directories to include.
    :param patch_files: List of patch files to include at the end of the output.
    :param exclude_files: List of files (relative paths to current_dir) to exclude.
    :return: A string containing all requested code (and patches).
    """
    if patch_files is None:
        patch_files = []
    if exclude_files is None:
        exclude_files = []

    # Convert exclude_files to a set for faster lookup
    exclude_set = set(exclude_files)

    code_text = []

    for file_path in code_files:
        full_path = os.path.join(current_dir, file_path)

        # Check if this exact file_path is excluded
        if file_path in exclude_set:
            continue

        if os.path.isfile(full_path):
            # If it's a file, check if it's excluded based on its relative path
            rel_path = os.path.relpath(full_path, current_dir)
            if rel_path not in exclude_set:
                # Handle polyglot case
                if is_polyglot and "coding_agent.py" in file_path:
                    full_path = full_path.replace(
                        "coding_agent.py", f"coding_agent_polyglot.py"
                    )

                code_text.append(f"# {rel_path}")
                code_text.append(read_file(full_path))

        elif os.path.isdir(full_path):
            # If it's a directory, walk through it
            for root, _, files in os.walk(full_path):
                for f in files:
                    if f.endswith(".py"):
                        file_full_path = os.path.join(root, f)
                        rel_path = os.path.relpath(file_full_path, current_dir)
                        # Check if this specific file is excluded
                        if rel_path not in exclude_set:
                            code_text.append(f"# {rel_path}")
                            code_text.append(read_file(file_full_path))

    # Add patch files (filter out non-code diffs like self_evo.md)
    skip_diff_prefixes = ("diff --git a/self_evo.md", "diff --git a/self_evo_")
    for i, patch_file in enumerate(patch_files):
        rel_path = os.path.relpath(patch_file, current_dir)
        if rel_path not in exclude_set:
            raw_patch = read_file(patch_file)
            filtered_hunks = []
            current_hunk = []
            skip = False
            for line in raw_patch.split("\n"):
                if line.startswith("diff --git"):
                    if current_hunk and not skip:
                        filtered_hunks.extend(current_hunk)
                    current_hunk = [line]
                    skip = any(line.startswith(p) for p in skip_diff_prefixes)
                else:
                    current_hunk.append(line)
            if current_hunk and not skip:
                filtered_hunks.extend(current_hunk)
            filtered_patch = "\n".join(filtered_hunks)
            if filtered_patch.strip():
                code_text.append(f"# Patch {i+1}: {rel_path}")
                code_text.append(filtered_patch)

    return "\n".join(code_text)
