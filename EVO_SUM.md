# EVO_SUM：self_evo 进化摘要与 To Implement 汇编

本文档由仓库内 `output_hgm/*/self_evo.md`（共 **24** 次 agent 自进化运行日志）自动抽取 `# To Implement` 下两段说明，并结合 `metadata.json` 中的 `run_id` / `self_improve_strategy` / `parent_commit` 整理而成。

---

## 一、进化在做什么（内容维度）

各轮 `problem_statement` 的共性结构是：先复述 **Coding Agent Summary**（`coding_agent.py` / `tools/` / `utils/` 约束），再在 **# To Implement** 用两段话描述本轮要改的能力：

1. **第一段（偏「做什么」）**：通常写具体改动形态——新增哪个 tool、改哪个文件的哪个函数、增加哪种模式或流程钩子。
2. **第二段（偏「为什么」）**：用失败症状 + 根因叙事解释 SWE-bench 上暴露的问题（漏改测试、无关改动、过度复杂、框架语义误判等），并回扣第一段方案。

从 24 轮文本的粗统计（两段合并做关键词命中，可多标签）来看：

- **新工具/new tool**：15 轮提及/相关
- **结构化/映射**：15 轮提及/相关
- **Sphinx**：10 轮提及/相关
- **coding_agent/forward**：9 轮提及/相关
- **Django**：9 轮提及/相关
- **edit\.py/editor**：7 轮提及/相关
- **test_analysis/run_tests**：5 轮提及/相关
- **linkcheck**：1 轮提及/相关

整体上，进化方向高度集中在 **「给 agent 增加可调用的新工具」** 与 **「把流程写进 `coding_agent` / 工具行为里」**，用结构化输出约束盲目改代码；少数轮次针对 **Django/Sphinx 领域语义**（字段关系、linkcheck 本地链接）做专用工具，体现 diagnose 从通用工作流滑向 **任务族特化** 的倾向。

## 二、进化是怎么发生的（机制维度）

- **代际链接**：`metadata.json` 中 `parent_commit` 将各 `run_id` 连成链；每一轮的 `problem_statement` 即下一代容器内 `/hgm` 要完成的「元任务」。
- **策略标签**：`self_improve_strategy` 在本批日志中为 **A: 1 轮，B: 17 轮，C: 6 轮**（仅计数，具体含义以 `hgm.py`/配置为准）。
- **执行方式**：`self_evo.md` 主体是「在 Docker `/hgm` 里改代码 + 跑 pytest」的完整轨迹；常见模式是仿照已有 `tools/*.py` 写 `tool_info`/`tool_function`，再改 `tools/__init__.py` 与 `coding_agent.py` 的 `forward()` 文案。
- **张力与规律**：模板里同时写「agent 能自动选对工具」与「复杂工具要在 prompt 里显式要求」——进化任务往往通过 **新增工具 + 在 forward 里写死步骤（MUST）** 解决 under-spec 问题；这与「不要过度强制工具」存在设计张力，后期多轮用「validate / analyze / requirement map」等措辞把这种强制包装成可测试的工作流。

## 三、特点与发现（归纳）

本节把通读 24 份 `self_evo.md` 后形成的**稳定特点**写清楚；其中**第一节**的关键词统计仅覆盖 To Implement 两段，本节则结合整份日志的常见结构来概括。

### 3.1 叙事与任务形态

- **固定「约束复述 + 两段式 To Implement」**：`problem_statement` 前半几乎总是 **Coding Agent Summary**（工具契约、`while True` 禁令、依赖安装方式等），后半用 **# To Implement** 两段完成「规格说明」：第一段偏 **做什么/长什么样**，第二段偏 **失败症状—根因—为何该方案能对症下药**。这种同质化有利于把 SWE-bench 上的诊断直接编译成可在 `/hgm` 落地的实现任务。
- **失败类型在第二段里高度复现**：反复出现的母题包括——**空 patch / 只分析不改**、**整文件替换误伤**、**改动与 issue 不对齐或漏改测试**、**过度复杂或引入无关依赖**、**框架边界条件（None vs 空串、builder 名等）未穷尽**、**Django/Sphinx 内部语义误判**。进化任务往往针对其中一两类点名修补。

### 3.2 技术路线：主航道与张力的特点

- **主航道是「新工具 +（必要时）改 `forward()` 文案」**：多数轮次在容器里新增 `tools/*.py`（`tool_info` / `tool_function`）、注册到 `tools/__init__.py`，再在 `coding_agent.py` 里插入步骤或强调 MUST；用 **结构化字符串/JSON 式报告** 把探索从「自由闲聊」收窄到可检查的中间产物。
- **与模板内建建议的张力**：Summary 里常见「agent 能自动选对工具、不要过度强制某一种工具」；而 To Implement 又常写 **必须在某阶段调用某工具 / 禁止在未 X 前改代码**。实际进化倾向于用 **「可测试工作流」（analyze、validate、requirement map）** 包装这种强制，使其看起来像流程质量门而非单纯堆工具。
- **由通专用的时间感**（按 `run_id` 粗读，非严格分期）：早期偏重 **保证有 patch、编辑粒度、proto_test 小实验** 等「闭环能否跑完」；中后期 To Implement 更常出现 **需求—改动映射、issue 结构化分析、条件/边界枚举、签名渲染追踪、linkcheck 本地校验、Django 字段语义** 等「诊断更细、更贴任务族」的能力。与此同时，**完整 User Instruction** 里往往已叠好 **TDD、`test_analysis` / `run_tests` / `validate_test_fix`**，这些内容**不一定**重复出现在 To Implement 两段里。

### 3.3 元数据与代际

- **代际链清晰**：每轮 `metadata.json` 的 `parent_commit` 指向前身 `run_id`（或 `initial`），`problem_statement` 即下一代镜像里要完成的元任务；`self_improve_strategy`（本批 **A:1 / B:17 / C:6**）标记采样或分叉策略，具体语义以 `hgm.py` 与配置为准。
- **数据范围**：本汇编仅包含 `output_hgm/<run_id>/self_evo.md` 共 24 份；`output_hgm/initial` 等路径下若无同名文件则未纳入。

### 3.4 阅读日志时的易错点

- **自我引用与「伪二次 Instruction」**：轨迹中常见 agent 用 `editor`/`grep` 打开 `/hgm/self_evo.md`，工具返回里带 **`cat -n` 行号前缀**，正文里会出现形似第二段 User Instruction 的块；**唯一可信的会话起点**仍是文件**行首顶格**的 `========== User Instruction ==========` 及其后**第一个** `<problem_description>…</problem_description>`（本 `EVO_SUM.md` 第四节即按此规则抽取）。

---

## 四、To Implement 全文汇编（两段 + ID）

**序号**：按 `run_id` 时间排序（与 `output_hgm` 目录名一致）。**ID**：`run_id`。

### 1. `20260409_134545_289259`

- **self_improve_strategy**: B
- **parent_commit**: initial
- **entries**: `django__django-16661`, `django__django-10973`

**段落一（To Implement 下第一段）**

Add a new tool in the tools/ directory called 'django_field_semantics.py' that provides functions to analyze Django field relationships. This tool should include functions like 'is_concrete_inheritance(field)' and 'is_primary_key_one_to_one(field)' that help the agent distinguish between different field semantics. The tool would query Django's model metadata system to determine field relationships and return structured information about the field's role in the model hierarchy. This would help the agent make more informed decisions when modifying code that handles field lookups.

**段落二（To Implement 下第二段）**

The coding agent lacks a systematic way to understand Django field semantics, particularly in distinguishing between OneToOneField primary keys, concrete inheritance, and regular relationships. This leads to incorrect assumptions about field behavior when modifying Django admin code. Add a 'django_field_semantics' tool that can analyze Django model metadata to determine whether a field represents concrete inheritance, a primary key OneToOneField, or a regular relationship. This tool should query Django's model introspection APIs and return structured information about field semantics that the agent can use to make correct decisions when modifying code that handles field lookups and relationships.

### 2. `20260409_144428_843008`

- **self_improve_strategy**: B
- **parent_commit**: initial
- **entries**: `django__django-13279`, `django__django-10999`

**段落一（To Implement 下第一段）**

Enhance the 'editor' tool in tools/edit.py to implement a 'diff-based editing' approach. Instead of the current 'view/create/edit' commands that require full file content, add a 'patch' command that takes a file path and a diff-style change specification. This would allow the agent to make targeted modifications without reading or replacing entire files. Alternatively, modify the 'edit' command to automatically read the file first, parse the existing content, and only replace the specified sections while preserving the rest.

**段落二（To Implement 下第二段）**

The coding agent's file editing tool has a critical flaw: it replaces entire file content rather than making targeted modifications. This causes two problems: 1) In tasks where no fix is needed or the agent hasn't fully understood the problem, the agent may accidentally overwrite working code, and 2) When the agent does make changes, it must read the entire file first to preserve existing content, but the current implementation doesn't do this. This was evident in Task 1 where no fix was implemented, and Task 2 where the entire file was accidentally overwritten before recovery. The agent needs a file editing system that makes minimal, targeted changes while preserving all existing code.

### 3. `20260409_163759_925057`

- **self_improve_strategy**: B
- **parent_commit**: initial
- **entries**: `sphinx-doc__sphinx-10466`, `django__django-11999`

**段落一（To Implement 下第一段）**

Based on the coding agent implementation, the `forward()` method should be modified to include an explicit 'generate_patch' phase after the LLM conversation. This phase would use the existing `edit.py` tool to make the identified changes, or a new `generate_patch` tool that outputs the diff directly. The key change is to ensure the agent cannot exit without producing a concrete patch. Specifically, after the chat_with_agent call, the system should call a new method that generates the patch based on the analysis.

**段落二（To Implement 下第二段）**

The coding agent successfully understands and analyzes code problems but fails to produce concrete patches in the final step. When given a problem like 'Cannot override get_FOO_display() in Django 2.2+', the agent can trace through the codebase, identify the root cause (the change from `if self.choices:` to `if self.choices is not None:` in commit 16a5a2a2c8), and understand the solution (check if method exists before overwriting), yet it produces no actual code changes. Similarly, for duplicate locations in Sphinx, the agent produces no output at all. The agent needs a guaranteed final phase that forces concrete patch generation. This should be implemented as a mandatory step in the `forward()` method that ensures the agent cannot exit without producing a diff that can be applied to fix the reported issue.

### 4. `20260409_175456_779774`

- **self_improve_strategy**: B
- **parent_commit**: initial
- **entries**: `sphinx-doc__sphinx-10466`, `django__django-12050`

**段落一（To Implement 下第一段）**

Modify the `forward()` function in `coding_agent.py` to include structured phases with explicit checks. After the LLM generates a solution, the agent should: (1) extract the proposed code changes, (2) use the `editor` tool to apply the changes, (3) run relevant tests using the `bash` tool, and (4) verify the changes with the `test_description` criteria. This can be implemented by adding post-LLM-processing steps that call existing tools to complete the cycle, rather than stopping at chat history generation.

**段落二（To Implement 下第二段）**

The coding agent often fails to complete tasks fully—either not starting (empty logs) or stopping prematurely after analysis without implementing or testing changes. The agent demonstrates strong codebase navigation and root cause identification capabilities but lacks robustness in executing the full problem-solving cycle (analyze → implement → test → verify). This inconsistency manifests across different tasks, where some fail to start while others analyze correctly but don't produce working code changes or test results. Implement a structured task completion framework that enforces all four phases (analysis, code modification, testing, verification) before termination, ensuring the agent follows through with the complete problem-solving cycle.

### 5. `20260409_190738_370957`

- **self_improve_strategy**: B
- **parent_commit**: initial
- **entries**: `django__django-11999`, `sphinx-doc__sphinx-10466`

**段落一（To Implement 下第一段）**

Add a new tool called 'proto_test' that allows the agent to execute small Python code snippets in the testbed environment to verify hypotheses. This tool should: (1) accept Python code as input, (2) execute it in the repository context, (3) return output and any exceptions, (4) support importing existing modules. This complements the existing 'bash' tool but is specifically for Python code testing rather than shell commands. The agent should be prompted to use this tool after identifying potential code locations to verify assumptions before making changes.

**段落二（To Implement 下第二段）**

The coding agent lacks a systematic approach to validate its understanding of code issues before making changes. In some cases (e.g., Django issue), it explores extensively without intermediate verification, while in other cases (e.g., Sphinx issue), it fails to explore at all. The agent needs a structured workflow that requires it to: (1) identify key files, (2) form specific testable hypotheses, (3) execute minimal verification tests, (4) update understanding based on results, and (5) only then propose solutions. Implement a new 'proto_test' tool that allows the agent to execute small Python code snippets in the repository context to verify hypotheses before making changes. This tool should support importing existing modules and return execution results and exceptions.

### 6. `20260409_221825_858183`

- **self_improve_strategy**: B
- **parent_commit**: initial
- **entries**: `sphinx-doc__sphinx-10466`, `django__django-11815`

**段落一（To Implement 下第一段）**

Modify the `forward()` method in `coding_agent.py` to include a mandatory 'implementation verification' phase after code changes. This phase should: 1) Check if code changes were made (e.g., by comparing git diff), 2) If changes exist, run the appropriate tests (using the test_description if provided or discovering tests automatically), 3) Only proceed to final patch generation if tests pass or if the agent explicitly acknowledges test failure with justification. This ensures the agent cannot complete a task without implementation and verification.

**段落二（To Implement 下第二段）**

The coding agent demonstrates a systematic weakness where it successfully analyzes and understands coding problems but fails to complete the implementation and verification phases. In Task 1 (Sphinx gettext), the agent made no progress at all. In Task 2 (Django enum serialization), the agent performed excellent analysis, identified the exact problem location, and understood the fix required, but stopped short of implementing the code changes and running tests. The agent needs to be modified to enforce a complete implementation loop: after understanding a problem and planning a solution, it must always attempt implementation, verify the changes (run tests), and generate the final patch. The current implementation allows the agent to exit at any point after analysis, which is insufficient for completing coding tasks successfully.

### 7. `20260410_082020_996988`

- **self_improve_strategy**: B
- **parent_commit**: initial
- **entries**: `django__django-11815`, `django__django-12050`

**段落一（To Implement 下第一段）**

Extend the existing `edit.py` tool to include a 'patch generation' mode. When the agent needs to fix code, it should be able to call a new function that: takes the file path, starting line number, ending line number, and replacement code, then generates a properly formatted diff hunk. This would require enhancing the editor tool to support: 1) Extracting context lines before/after the target range, 2) Generating unified diff format output, 3) Handling multiple patch hunks in one operation. This leverages the existing file editing infrastructure while adding the missing patch generation capability.

**段落二（To Implement 下第二段）**

The coding agent can analyze code and identify bugs but fails to generate working code fixes. After diagnosing issues like enum serialization using values instead of names or list-to-tuple coercion in query lookups, the agent stops at analysis without producing actual code changes. The agent needs a way to automatically generate targeted code patches based on its analysis. Implement a patch generation feature that allows the agent to construct proper diff hunks with correct context, line numbers, and replacement code based on precise code analysis.

### 8. `20260410_145905_655946`

- **self_improve_strategy**: B
- **parent_commit**: initial
- **entries**: `django__django-12209`, `django__django-12262`

**段落一（To Implement 下第一段）**

Extend the `forward()` method in `coding_agent.py` to include a new phase before the main LLM chat loop: a 'diagnostic phase' where the agent is explicitly instructed to (a) generate a reproduction script, (b) run it via the `bash` tool, (c) inspect relevant code sections using the `editor` tool (e.g., `view` model save logic or template tag parsing), and (d) report a hypothesis. This phase should be capped with a timeout and produce structured logs. The prompt in `forward()` should be updated to include: 'Before proposing a fix, reproduce the issue, identify the relevant code location, and hypothesize the root cause. Log each step clearly.' This leverages existing tools (`bash`, `editor`) without adding new infrastructure.

**段落二（To Implement 下第二段）**

The agent currently fails silently or aborts prematurely on complex issues requiring multi-step reasoning (e.g., Django ORM behavior changes, template tag parsing bugs). It lacks a structured debugging process, resulting in no logs, no patch, and no actionable failure mode. Implement a mandatory 'diagnostic loop' that forces the agent to reproduce the issue, inspect relevant code, hypothesize the root cause, and validate the hypothesis *before* generating a patch. This loop should be logged and timeout-gated, ensuring the agent either makes progress or reports *why* it cannot proceed—instead of failing silently. The loop should use existing tools (`bash`, `editor`, `chat_with_agent`) and be integrated into the `forward()` method as a preprocessing phase.

### 9. `20260411_092813_158361`

- **self_improve_strategy**: B
- **parent_commit**: 20260409_134545_289259
- **entries**: `sphinx-doc__sphinx-9230`, `django__django-12713`

**段落一（To Implement 下第一段）**

Extend the existing `bash.py` tool to include a new mode: `run_tests`. This would allow the agent to execute tests with structured output (e.g., `pytest --json` or custom parsing) and integrate test results into its reasoning. Additionally, add a new tool `find_test_for_file.py` that maps a source file path to its corresponding test file(s) using heuristic rules (e.g., `src/module.py` → `tests/test_module.py`). These tools would enable the agent to discover, run, and interpret tests without manual intervention, closing the feedback loop between patch generation and validation.

**段落二（To Implement 下第二段）**

The coding agent lacks a test-driven debugging loop, causing it to generate patches without verifying correctness through tests. This leads to missed bugs (e.g., Sphinx type annotation rendering) or untested fixes (e.g., Django widget override). To improve reliability, implement automatic test discovery and execution as part of the agent's patch refinement process. Specifically, add tools to: (1) run tests with structured output (pass/fail, failure messages), and (2) map source files to their corresponding test files. Integrate these into the agent's workflow so that every patch is validated before finalization, ensuring correctness through iterative testing and revision.

### 10. `20260411_141844_823334`

- **self_improve_strategy**: B
- **parent_commit**: 20260409_221825_858183
- **entries**: `django__django-12209`, `django__django-10999`

**段落一（To Implement 下第一段）**

Extend the coding agent's forward() function to include an optional 'debug_mode' that activates a behavioral analysis workflow. When enabled, the agent should: 1) Automatically discover and run existing tests related to the issue, 2) Use the test_runner tool to execute tests and capture output, 3) Analyze test results to understand expected vs actual behavior, 4) Generate or modify tests to validate the fix before implementing code changes, and 5) Only then proceed to implement the fix. This can be implemented by adding a debug mode flag to the AgenticSystem class and modifying the forward() function to call a new _debug_behavior() method when enabled, which would use the existing test_runner and bash tools to explore behavior systematically.

**段落二（To Implement 下第二段）**

The coding agent lacks a systematic approach to diagnose behavioral changes in code, especially when the issue is about changed behavior between versions rather than outright bugs. The agent should be enhanced with a test-driven debugging workflow that requires it to explore and understand expected vs actual behavior through test exploration before implementing fixes. Specifically, the agent should be able to: 1) Discover and run relevant tests automatically, 2) Analyze test results to understand behavioral differences, 3) Create or modify tests to validate expected behavior, and 4) Only then implement code changes. This would be particularly valuable for issues like Django model saving behavior changes where the problem is subtle and requires understanding of version-specific behavior.

### 11. `20260411_190953_536230`

- **self_improve_strategy**: A
- **parent_commit**: 20260409_221825_858183
- **entry**: `sphinx-doc__sphinx-8035`

**段落一（To Implement 下第一段）**

Modify the existing bash tool to include a new command that can analyze test files. The bash tool could be enhanced with a 'test_analysis' subcommand that accepts parameters like 'test_file', 'test_function', and 'expected_behavior'. When called, it would: 1) Parse the test file to extract test cases and their expectations, 2) Identify the relevant source files and modules being tested, 3) Extract expected outputs and assertions from the test, 4) Return a structured summary that the agent can use to understand what needs to be implemented. This would allow the agent to better understand test requirements before making code changes, reducing the likelihood of implementing incorrect or incomplete solutions.

**段落二（To Implement 下第二段）**

Add a test analysis tool to the coding agent that enables deeper understanding of test requirements before implementing changes. The tool should be able to parse test files, extract test cases and their expectations, identify the relevant source modules being tested, and return a structured summary of what needs to be implemented. This would help the agent make more targeted and correct changes by understanding test requirements upfront, rather than relying solely on issue descriptions which may be ambiguous or incomplete. The tool should integrate seamlessly with the existing bash tool and be usable across any repository with test files.

### 12. `20260411_221048_008248`

- **self_improve_strategy**: B
- **parent_commit**: 20260409_144428_843008
- **entries**: `django__django-12050`, `django__django-12276`

**段落一（To Implement 下第一段）**

Enhance the 'editor' tool in tools/edit.py to implement a 'diff-based editing' approach. Instead of the current 'view/create/edit' commands that require full file content, add a 'patch' command that takes a file path and a diff-style change specification. This would allow the agent to make targeted modifications without reading or replacing entire files. Alternatively, modify the 'edit' command to automatically read the file first, parse the existing content, and only replace the specified sections while preserving the rest.

**段落二（To Implement 下第二段）**

The coding agent's file editing tool has a critical flaw: it replaces entire file content rather than making targeted modifications. This causes two problems: 1) In tasks where no fix is needed or the agent hasn't fully understood the problem, the agent may accidentally overwrite working code, and 2) When the agent does make changes, it must read the entire file first to preserve existing content, but the current implementation doesn't do this. This was evident in Task 1 where no fix was implemented, and Task 2 where the entire file was accidentally overwritten before recovery. The agent needs a file editing system that makes minimal, targeted changes while preserving all existing code.

### 13. `20260412_032125_192730`

- **self_improve_strategy**: B
- **parent_commit**: 20260411_221048_008248
- **entries**: `django__django-12754`, `sphinx-doc__sphinx-8265`

**段落一（To Implement 下第一段）**

Add a new tool in tools/ called 'representation_analyzer.py' that extends the existing bash and edit tools. This tool would take a code file path and a query about formal representations (e.g., 'analyze tuple handling', 'analyze migration operation dependencies', 'analyze AST node transformations') and return structured information about the relevant representations and constraints. The tool would use static analysis (AST parsing) and pattern matching to identify key semantic elements, then format the results to help the agent reason about formal systems. This would complement existing tools by providing deeper semantic understanding without requiring the agent to guess at complex system behaviors.

**段落二（To Implement 下第二段）**

The coding agent consistently fails to implement correct fixes for issues involving subtle semantic requirements, such as migration operation ordering in Django or AST tuple representation in Sphinx. The agent lacks a systematic way to understand formal representations and their constraints, leading to surface-level analysis that misses critical edge cases and ordering requirements. Implement a 'Formal Representation Analyzer' tool that helps the agent systematically analyze data structure representations, operation dependencies, and state transitions before generating code changes. This tool should use static analysis to identify formal system requirements and present them in a structured format that the agent can use to generate robust, semantically correct fixes.

### 14. `20260412_101946_968178`

- **self_improve_strategy**: C
- **parent_commit**: 20260412_032125_192730
- **entry**: `sphinx-doc__sphinx-9230`

**段落一（To Implement 下第一段）**

Extend the `bash.py` tool to include a `grep_search` function that allows the agent to search for keywords or regex patterns across the codebase (e.g., `grep -rn 'dict(str' . --include='*.py'`). This would help the agent locate the relevant parsing logic (e.g., where `dict(str, str)` is being parsed incorrectly in Sphinx's Python domain). Additionally, enhance the `editor.py` tool to support viewing specific line ranges or context around matches, enabling the agent to inspect and modify the relevant parsing logic.

**段落二（To Implement 下第二段）**

The agent struggles to locate and fix bugs related to documentation rendering of type annotations (e.g., `:param dict(str, str) param:`) because it lacks a mechanism to identify the relevant parsing logic in the codebase. When dealing with issues like incorrect doc rendering, the agent should first search for keywords (e.g., 'param', 'type', 'docstring', 'render', 'parse') and inspect the relevant parsing logic (e.g., regex patterns, AST-based type parsing) before generating a fix. Add a grep_search tool to the bash tool to enable keyword-based codebase searches, and enhance the editor tool to support viewing context around matches. This will help the agent identify and fix bugs in documentation generation and type annotation parsing more effectively.

### 15. `20260412_161515_467024`

- **self_improve_strategy**: C
- **parent_commit**: 20260412_101946_968178
- **entry**: `sphinx-doc__sphinx-8056`

**段落一（To Implement 下第一段）**

Enhance the representation_analyzer.py tool to include a 'behavioral mode' that not only analyzes code structure but also simulates behavior on example inputs. Add a new function `tool_function_analyze_behavior(file_path, function_name, example_inputs)` that: 1) Locates the specified function in the code, 2) Extracts its logic using AST, 3) Simulates execution on the provided examples, 4) Returns expected outputs and identifies potential failure points. This would help agents understand not just what code exists, but how it actually behaves — critical for debugging formatting and rendering issues.

**段落二（To Implement 下第二段）**

The coding agent fails to fix docstring parsing bugs because it doesn't understand the actual code behavior. When given an issue about rendering of multi-parameter docstrings (e.g., 'x1, x2 : array_like'), the agent generates patches that modify tests and dependencies but doesn't examine or fix the actual parsing logic in sphinx/ext/napoleon/docstring.py. The agent needs a way to: 1) Identify the source files responsible for docstring parsing based on issue keywords, 2) Understand how the current code processes example inputs, 3) Compare expected vs actual behavior to identify the root cause. Implement a 'behavioral analysis' extension to the representation_analyzer tool that can simulate code execution on example inputs and show where the processing diverges from expectations.

### 16. `20260412_225633_033496`

- **self_improve_strategy**: B
- **parent_commit**: 20260411_190953_536230
- **entries**: `django__django-11999`, `django__django-10999`

**段落一（To Implement 下第一段）**

Integrate the existing `test_runner.py` and `test_analysis.py` tools into the `forward()` method of `AgenticSystem`. Specifically, after the initial instruction, add a mandatory step where the agent: (a) runs `test_analysis` to identify relevant test files and expectations, (b) runs `run_tests` to capture baseline failures, (c) makes code changes, and (d) re-runs tests to validate. This can be implemented by extending the `forward()` method to include these steps in the prompt or by adding a new method like `verify_fix()` that orchestrates test execution and analysis.

**段落二（To Implement 下第二段）**

The coding agent currently generates patches without validating them against test behavior, leading to silent failures (e.g., incorrect or incomplete fixes). To improve reliability, the agent should follow a structured 'Code-Test-Verify' workflow: (1) automatically analyze relevant test files to understand expected behavior before coding, (2) run existing tests to establish baseline failure patterns, (3) generate fixes that reference test expectations, (4) re-run tests to confirm the fix works, and (5) if tests fail, perform failure analysis and revise. This requires integrating the existing `test_runner.py` and `test_analysis.py` tools into the agent's core loop, ensuring test-driven development rather than speculative patching.

### 17. `20260413_043116_068266`

- **self_improve_strategy**: C
- **parent_commit**: 20260411_190953_536230
- **entry**: `sphinx-doc__sphinx-7757`

**段落一（To Implement 下第一段）**

Extend the existing `tools/test_analysis.py` to include a new function `analyze_parameter_defaults(signature_str)` that parses the signature, computes expected defaults using Python's official semantics (defaults are assigned from the end of the full parameter list), and returns a structured report. This function should be added as a new tool (e.g., `parameter_defaults_analysis`) that the agent can invoke when debugging signature parsing logic. The tool should support parameters like `signature_str`, `expected_defaults`, and `target_file`, and return JSON with fields like 'parameter_index', 'name', 'expected_default', 'actual_default' (if code is available), and 'status'. This tool would allow the agent to catch indexing errors before submitting patches.

**段落二（To Implement 下第二段）**

The agent's signature parsing logic for Python functions (especially with positional-only arguments) frequently fails to correctly assign default values due to incorrect indexing logic. The agent needs a tool that can automatically generate and validate expected default values for parameters in a function signature, using Python's official semantics (defaults are assigned from the end of the full parameter list). This tool should help the agent debug its implementation by comparing expected vs actual defaults and providing actionable feedback. Add a new tool `parameter_defaults_analysis` to `tools/` that takes a signature string and returns structured expectations for parameter defaults, enabling the agent to catch off-by-one and reverse-indexing errors before patch submission.

### 18. `20260413_113039_681369`

- **self_improve_strategy**: B
- **parent_commit**: 20260412_225633_033496
- **entries**: `sphinx-doc__sphinx-8265`, `django__django-11066`

**段落一（To Implement 下第一段）**

Modify the coding_agent.py's forward() function to include an explicit 'test_validation' phase in the instruction. Add a new tool function in tools/test_runner.py that specifically supports the before/after fix validation workflow. The tool should accept parameters for 'test_file', 'test_function', and 'expected_behavior', and return structured output indicating whether the test currently passes/fails and what changes are needed. Update the prompt to explicitly require the agent to run this validation workflow before submitting changes.

**段落二（To Implement 下第二段）**

The coding agent generates tests that don't properly validate fixes, often creating incorrect or irrelevant test cases. The agent needs a structured workflow to ensure tests are generated that: 1) Reproduce the exact failure condition from the issue, 2) Fail with the current codebase, 3) Pass after the fix is applied. Implement a test validation workflow that requires explicit verification of both failure and success states, and enhance the test_runner tool to support this workflow with structured output.

### 19. `20260413_194142_663920`

- **self_improve_strategy**: C
- **parent_commit**: 20260412_225633_033496
- **entry**: `sphinx-doc__sphinx-8265`

**段落一（To Implement 下第一段）**

Add a new tool in tools/ called 'signature_tracer.py' that provides a tool_info() and tool_function() implementation. The tool would: 1) Accept a function signature string and a test case (e.g., 'def f(a, b=(1, 2, 3))'), 2) Use Sphinx's internal APIs to render the signature to HTML or text, 3) Capture intermediate AST and string representations at key stages, 4) Return a structured report showing where the rendering diverges from expectations. This would allow the agent to systematically test hypotheses about the bug before modifying sphinx/pycode/ast.py. The tool would complement existing tools (test_analysis, run_tests) by providing deeper insight into the specific rendering pipeline involved in the bug.

**段落二（To Implement 下第二段）**

The agent lacks the ability to systematically trace and analyze how Sphinx processes function signatures with complex default arguments (e.g., tuples), making it difficult to identify the precise cause of rendering bugs before making code changes. This leads to overgeneralized fixes that break other cases. Implement a new tool 'signature_tracer' that captures intermediate representations of signature processing (AST, string representations, final output) to help the agent diagnose rendering issues with context-specific precision before modifying code.

### 20. `20260414_025525_534273`

- **self_improve_strategy**: B
- **parent_commit**: 20260411_190953_536230
- **entries**: `django__django-12039`, `sphinx-doc__sphinx-8721`

**段落一（To Implement 下第一段）**

Extend the existing `tools/edit.py` or create a new `tools/condition_analysis.py` tool that accepts a code section and issue description as input, and outputs a structured list of conditions, edge cases, and test scenarios. The tool should use AST parsing to identify conditional branches (if/elif/else, try/except, optional fields like opclasses/col_suffixes) and cross-reference with the issue description to identify implied edge cases (e.g., 'empty strings' in Task 1, 'epub builder variants' in Task 2). The agent's `forward()` method should be modified to invoke this tool after initial code review and before proposing a fix, with the tool output stored in the chat history for verification.

**段落二（To Implement 下第二段）**

The coding agent frequently produces incomplete or incorrect fixes for bugs involving nuanced conditional logic and edge cases. Specifically, it struggles to correctly handle scenarios where seemingly minor details (e.g., empty strings vs None, builder name prefixes like 'epub' vs 'epub3') significantly impact the fix. The agent should be enhanced with a structured condition and edge-case analysis capability that: (1) parses the issue description and code to identify all relevant conditional branches and edge cases; (2) generates targeted test scenarios for each condition; and (3) requires verification that each condition is handled in the proposed patch. This would prevent failures like the Django whitespace bug (empty col_suffixes) and Sphinx epub bug (builder name prefixes and config flags).

### 21. `20260414_222240_033175`

- **self_improve_strategy**: C
- **parent_commit**: 20260413_113039_681369
- **entry**: `sphinx-doc__sphinx-7985`

**段落一（To Implement 下第一段）**

Extend the existing linkcheck.py tool's check() function to properly handle local links. The current tool has a basic local link detection that just returns 'local', 'unchecked', or 'broken' without proper validation. The enhancement should use Sphinx's existing utilities: import docname_join from sphinx.util, use self.env.all_docs to check document existence, and implement proper anchor checking using the existing AnchorCheckParser class. The implementation should handle: relative paths (docname_join), different suffixes (link_suffix configuration), backtick-style references (strip reference syntax), and actual anchor validation by fetching documents when needed.

**段落二（To Implement 下第二段）**

Sphinx's linkcheck builder should validate local (internal) links, not just external URLs. Currently, local links like :doc:`nonexistent` or backtick-style references to local documents are only marked as 'local' without validation. Implement proper local link checking by: 1) Using docname_join to resolve relative document references, 2) Checking target documents exist in self.env.all_docs, 3) Validating anchors by fetching and parsing target documents when linkcheck_anchors is enabled, 4) Supporting all Sphinx link syntaxes (:doc:, backtick-style, relative paths), and 5) Handling different file suffixes correctly. The implementation should integrate with Sphinx's build environment and use existing utilities like AnchorCheckParser for anchor validation.

### 22. `20260415_061756_604960`

- **self_improve_strategy**: B
- **parent_commit**: 20260412_225633_033496
- **entries**: `django__django-10973`, `sphinx-doc__sphinx-7748`

**段落一（To Implement 下第一段）**

Add a new tool called 'requirement_validator' to the tools directory that takes the issue description and a proposed solution draft, then returns a structured report mapping each change to specific requirements. The tool should: 1) Parse the issue to extract key requirements, 2) Analyze the proposed patch to identify all changed files and their purposes, 3) Create a mapping showing which requirements each change addresses, 4) Flag any changes without clear requirement links or missing requirement coverage. The coding_agent.py forward() method should call this tool before finalizing output, using it to validate or revise the solution.

**段落二（To Implement 下第二段）**

The coding agent currently lacks a mechanism to validate that its proposed code changes are aligned with and fully address the issue requirements. This manifests as: 1) Incomplete test updates when modifying implementation (Task 1), and 2) Completely irrelevant changes that don't address the stated problem (Task 2). Implement a 'requirement validator' tool that analyzes the issue description, extracts key requirements, and validates that each proposed code change can be traced to specific requirements. The agent should be required to use this tool and address any validation failures before finalizing its output. This will ensure the agent produces targeted, relevant changes that fully address the issue rather than incomplete or off-topic modifications.

### 23. `20260415_143737_672056`

- **self_improve_strategy**: C
- **parent_commit**: 20260414_222240_033175
- **entry**: `django__django-11087`

**段落一（To Implement 下第一段）**

Modify the tool_function in tools/edit.py to include a new 'validate_optimization' mode that checks whether code changes respect framework patterns like can_fast_delete(). Additionally, extend the tool_function to support a 'review_signal_handlers' mode that analyzes whether signal handlers might depend on full object state, and warns if field restrictions could break them. This would guide the agent toward context-aware optimizations similar to the Context version's approach.

**段落二（To Implement 下第二段）**

The current delete optimization strategy unconditionally restricts field selection during cascade deletion, which breaks deletion signal handlers that require access to full object state. Improve the optimization to conditionally apply field restrictions only when fast-deletion is possible (i.e., no signal handlers are connected), using the framework's can_fast_delete() method. The agent should be guided to check for signal handler dependencies before applying optimizations that restrict field loading, ensuring backward compatibility while still achieving performance and Unicode-safety improvements.

### 24. `20260416_012246_842088`

- **self_improve_strategy**: B
- **parent_commit**: 20260415_143737_672056
- **entries**: `sphinx-doc__sphinx-8721`, `django__django-11885`

**段落一（To Implement 下第一段）**

Add a new tool called 'analyze_issue' that the agent must use before making any code changes. This tool would take the problem statement and the current codebase state as input, and return a structured analysis including: (1) the specific code paths involved, (2) the exact location where behavior diverges from expectations, (3) a minimal fix plan with line numbers and file paths, and (4) verification steps. This would enforce the structured analysis phase and provide the agent with focused guidance before implementation.

**段落二（To Implement 下第二段）**

The coding agent consistently fails to accurately identify and implement minimal fixes for issues. It either adds unrelated changes (like dependency updates) or implements overly complex solutions with bugs. To fix this, implement a structured problem analysis phase that requires the agent to explicitly identify the root cause, locate relevant code sections, and validate the solution before implementation. Add an 'analyze_issue' tool that takes the problem statement and codebase state, then returns a structured analysis including the specific code paths involved, exact divergence points, minimal fix plan, and verification steps. This analysis phase should be mandatory before any code generation occurs.
