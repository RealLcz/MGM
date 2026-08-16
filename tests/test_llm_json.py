import sys
from types import SimpleNamespace


class _DummyError(Exception):
    pass


sys.modules.setdefault(
    "anthropic",
    SimpleNamespace(RateLimitError=_DummyError, APIStatusError=_DummyError),
)
sys.modules.setdefault(
    "openai",
    SimpleNamespace(
        RateLimitError=_DummyError,
        APITimeoutError=_DummyError,
        OpenAI=object,
    ),
)
sys.modules.setdefault(
    "backoff",
    SimpleNamespace(
        expo=lambda *args, **kwargs: None,
        on_exception=lambda *args, **kwargs: lambda fn: fn,
    ),
)

from llm import extract_json_between_markers


def test_extract_json_prefers_last_valid_fenced_block():
    output = """
First an invalid example:
```json
{"task_1_analysis": "...", "potential_improvements": [...]}
```

Then the final answer:
```json
{"implementation_suggestion": "ship it", "problem_description": "real issue"}
```
"""

    parsed = extract_json_between_markers(
        output,
        required_keys={"implementation_suggestion", "problem_description"},
    )

    assert parsed == {
        "implementation_suggestion": "ship it",
        "problem_description": "real issue",
    }


def test_extract_json_required_keys_skip_incomplete_json():
    output = """
```json
{"problem_description": "missing implementation suggestion"}
```
```json
{"implementation_suggestion": "fix parser", "problem_description": "done"}
```
"""

    parsed = extract_json_between_markers(
        output,
        required_keys={"implementation_suggestion", "problem_description"},
    )

    assert parsed["implementation_suggestion"] == "fix parser"


def test_extract_json_fallback_scans_balanced_objects_from_end():
    output = """
The model may mention {"summary": "not enough"} before the final object.
Final object:
{"implementation_suggestion": "use balanced scan", "problem_description": {"title": "nested ok"}}
"""

    parsed = extract_json_between_markers(
        output,
        required_keys={"implementation_suggestion", "problem_description"},
    )

    assert parsed["problem_description"] == {"title": "nested ok"}
