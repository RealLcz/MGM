# This file is adapted from https://github.com/jennyzzt/dgm.

# Code adapted from https://github.com/SakanaAI/AI-Scientist/blob/main/ai_scientist/llm.py.
import json
import os
import re

import anthropic
import backoff
import openai

MAX_OUTPUT_TOKENS = 10240
# DeepSeek V4 supports large outputs, but tool-call agents should not ask for
# huge completions by default; very large caps can leave API calls hanging.
DEEPSEEK_MAX_OUTPUT_TOKENS = int(
    os.environ.get("DEEPSEEK_MAX_OUTPUT_TOKENS", "16384")
)
DEEPSEEK_API_MAX_OUTPUT_TOKENS = 384000
DEEPSEEK_API_TIMEOUT = float(os.environ.get("DEEPSEEK_API_TIMEOUT", "240"))
DEFAULT_LLM_MODEL = os.environ.get("HGM_LLM_MODEL_ID", "Qwen/Qwen3.6-35B-A3B")
AVAILABLE_LLMS = [
    "gpt-5",
    "o4-mini",
    "o3",
    "Qwen/Qwen3.6-35B-A3B",
    "Qwen/Qwen3-Coder-Next",
    "google/gemma-4-26B-A4B-it",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek/deepseek-chat-v3.1",
    "anthropic/claude-sonnet-4",
]

VLLM_MODEL_PREFIXES = ("Qwen/", "google/")
DEEPSEEK_API_BASE_URL = os.getenv(
    "DEEPSEEK_API_BASE_URL", "https://api.deepseek.com/v1"
).rstrip("/")
DEEPSEEK_API_MODEL_IDS = frozenset(
    {
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "deepseek-chat",
        "deepseek-reasoner",
    }
)


def resolve_llm_model(model: str | None) -> str:
    """Return model id; empty/missing values use HGM_LLM_MODEL_ID or Qwen3.6 default."""
    if model and str(model).strip():
        return str(model).strip()
    return DEFAULT_LLM_MODEL


def is_deepseek_api_model(model: str | None) -> bool:
    if not model:
        return False
    normalized = str(model).strip().lower()
    return (
        normalized in DEEPSEEK_API_MODEL_IDS
        or normalized.startswith("deepseek-v4-")
    )


def deepseek_api_key() -> str | None:
    return os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")


def deepseek_max_output_tokens() -> int:
    raw = os.getenv("DEEPSEEK_MAX_OUTPUT_TOKENS")
    if raw is None or str(raw).strip() == "":
        return DEEPSEEK_MAX_OUTPUT_TOKENS
    try:
        return min(int(raw), DEEPSEEK_API_MAX_OUTPUT_TOKENS)
    except ValueError:
        return DEEPSEEK_MAX_OUTPUT_TOKENS


def deepseek_api_timeout() -> float:
    raw = os.getenv("DEEPSEEK_API_TIMEOUT")
    if raw is None or str(raw).strip() == "":
        return DEEPSEEK_API_TIMEOUT
    try:
        return max(30.0, float(raw))
    except ValueError:
        return DEEPSEEK_API_TIMEOUT


def deepseek_api_extra_body() -> dict:
    """Build DeepSeek API extra_body for thinking / reasoning controls."""
    mode = os.getenv("DEEPSEEK_THINKING_MODE", "disabled").strip().lower()
    if mode in ("enabled", "enable", "on", "true", "1", "high", "max", "thinking"):
        extra = {"thinking": {"type": "enabled"}}
        effort = os.getenv("DEEPSEEK_REASONING_EFFORT", "max").strip()
        if effort:
            extra["reasoning_effort"] = effort
        return extra
    return {"thinking": {"type": "disabled"}}


def uses_vllm_model(model: str | None) -> bool:
    model = resolve_llm_model(model)
    if is_deepseek_api_model(model):
        return False
    return model.startswith(VLLM_MODEL_PREFIXES) or "vllm" in model.lower()


def llm_container_env() -> dict[str, str | None]:
    """Environment variables passed into agent task containers."""
    return {
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY"),
        "AWS_REGION": os.getenv("AWS_REGION"),
        "AWS_REGION_NAME": os.getenv("AWS_REGION_NAME"),
        "AWS_ACCESS_KEY_ID": os.getenv("AWS_ACCESS_KEY_ID"),
        "AWS_SECRET_ACCESS_KEY": os.getenv("AWS_SECRET_ACCESS_KEY"),
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
        "OpenRouter_API_KEY": os.getenv("OpenRouter_API_KEY"),
        "DEEPSEEK_API_KEY": os.getenv("DEEPSEEK_API_KEY"),
        "DEEPSEEK_API_BASE_URL": os.getenv("DEEPSEEK_API_BASE_URL"),
        "DEEPSEEK_MAX_OUTPUT_TOKENS": os.getenv("DEEPSEEK_MAX_OUTPUT_TOKENS"),
        "DEEPSEEK_API_TIMEOUT": os.getenv("DEEPSEEK_API_TIMEOUT"),
        "DEEPSEEK_THINKING_MODE": os.getenv("DEEPSEEK_THINKING_MODE"),
        "DEEPSEEK_REASONING_EFFORT": os.getenv("DEEPSEEK_REASONING_EFFORT"),
        "VLLM_HOST": os.getenv("VLLM_CONTAINER_HOST", "127.0.0.1"),
        "VLLM_PORT": os.getenv("REMOTE_VLLM_PORT", os.getenv("VLLM_PORT", "8000")),
    }


def create_client(model: str):
    model = resolve_llm_model(model)
    if is_deepseek_api_model(model):
        api_key = deepseek_api_key()
        if not api_key:
            raise ValueError(
                "DeepSeek API requires DEEPSEEK_API_KEY or OPENAI_API_KEY."
            )
        print(
            f"Using DeepSeek API with model {model} at {DEEPSEEK_API_BASE_URL}."
        )
        return (
            openai.OpenAI(
                base_url=DEEPSEEK_API_BASE_URL,
                api_key=api_key,
                timeout=deepseek_api_timeout(),
                max_retries=1,
            ),
            model,
        )
    if "gpt" in model or model.startswith("o"):
        print(f"Using OpenAI API with model {model}.")
        return openai.OpenAI(), model
    elif model.startswith(VLLM_MODEL_PREFIXES):
        vllm_host = os.getenv("VLLM_HOST", "127.0.0.1")
        vllm_port = os.getenv("VLLM_PORT", "8000")
        print(
            f"Using vllm API with served model {model} at http://{vllm_host}:{vllm_port}/v1."
        )
        return (
            openai.OpenAI(
                base_url=f"http://{vllm_host}:{vllm_port}/v1",
                api_key="dummy",
            ),
            model,
        )
    elif "vllm" in model.lower():
        print(f"Using vllm API with model {model}.")
        return (
            openai.OpenAI(base_url=f"http://{model[11:]}:8000/v1", api_key="dummy"),
            model,
        )
    else:
        print(f"Using OpenRouter API with model {model}.")
        return (
            openai.OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=os.getenv("OpenRouter_API_KEY"),
            ),
            model,
        )


@backoff.on_exception(
    backoff.expo,
    (
        openai.RateLimitError,
        openai.APITimeoutError,
        anthropic.RateLimitError,
        anthropic.APIStatusError,
    ),
    max_time=120,
)
def get_json_response_from_llm(
    msg,
    client,
    model,
    system_message,
):
    new_msg_history = [{"role": "user", "content": msg}]
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_message},
            *new_msg_history,
        ],
        n=1,
        stop=None,
        seed=0,
        response_format={
            "type": "json_object",
        },
    )
    content = response.choices[0].message.content
    import json

    content_json = json.loads(content)
    new_msg_history = new_msg_history + [{"role": "assistant", "content": content}]

    return content_json, new_msg_history


def get_response_from_llm(
    msg,
    client,
    model,
    system_message,
    print_debug=False,
    msg_history=None,
    temperature=0.7,
):
    if msg_history is None:
        msg_history = []

    if model.startswith("o"):
        new_msg_history = msg_history + [
            {"role": "user", "content": system_message + msg}
        ]
        response = client.chat.completions.create(
            model=model,
            messages=new_msg_history,
            temperature=1,
            n=1,
            seed=0,
        )
        content = response.choices[0].message.content
        new_msg_history = new_msg_history + [{"role": "assistant", "content": content}]
    elif "gpt" in model.lower():
        new_msg_history = msg_history + [{"role": "user", "content": msg}]
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                *new_msg_history,
            ],
            n=1,
            stop=None,
            seed=0,
        )
        content = response.choices[0].message.content
        new_msg_history = new_msg_history + [{"role": "assistant", "content": content}]
    else:
        new_msg_history = msg_history + [{"role": "user", "content": msg}]
        api_model = (
            client.models.list().data[0].id
            if uses_vllm_model(model)
            else model
        )
        response = client.chat.completions.create(
            model=api_model,
            messages=[
                {"role": "system", "content": system_message},
                *new_msg_history,
            ],
            temperature=temperature,
            max_tokens=MAX_OUTPUT_TOKENS,
            n=1,
            stop=None,
        )
        content = response.choices[0].message.content
        new_msg_history = new_msg_history + [{"role": "assistant", "content": content}]
    if print_debug:
        print()
        print("*" * 20 + " LLM START " + "*" * 20)
        print(f'User: {new_msg_history[-2]["content"]}')
        print(f'Assistant: {new_msg_history[-1]["content"]}')
        print("*" * 21 + " LLM END " + "*" * 21)
        print()
    return content, new_msg_history


def _json_has_required_keys(value, required_keys):
    return (
        not required_keys
        or isinstance(value, dict)
        and set(required_keys).issubset(value.keys())
    )


def _balanced_json_candidates(text):
    candidates = []
    start = None
    depth = 0
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start : index + 1])
                start = None

    return candidates


def _try_load_json(candidate):
    candidate = candidate.strip()
    if not candidate:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        candidate_clean = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", candidate)
        try:
            return json.loads(candidate_clean)
        except json.JSONDecodeError:
            return None


def extract_json_between_markers(llm_output, required_keys=None):
    if not llm_output:
        return None

    required_keys = set(required_keys or [])
    candidates = re.findall(
        r"```json[^\n]*\n(.*?)```", llm_output, flags=re.IGNORECASE | re.DOTALL
    )
    candidates.extend(_balanced_json_candidates(llm_output))

    for candidate in reversed(candidates):
        parsed = _try_load_json(candidate)
        if parsed is not None and _json_has_required_keys(parsed, required_keys):
            return parsed

    return None
