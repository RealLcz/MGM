# This file is adapted from https://github.com/jennyzzt/dgm.

# Code adapted from https://github.com/SakanaAI/AI-Scientist/blob/main/ai_scientist/llm.py.
import json
import os
import re

import anthropic
import backoff
import openai

MAX_OUTPUT_TOKENS = 4096
AVAILABLE_LLMS = [
    "gpt-5",
    "o4-mini",
    "o3",
    "Qwen/Qwen3-Coder-Next",
    "google/gemma-4-26B-A4B-it",
    "deepseek/deepseek-chat-v3.1",
    "anthropic/claude-sonnet-4",
]

VLLM_MODEL_PREFIXES = ("Qwen/", "google/")


def create_client(model: str):
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
        response = client.chat.completions.create(
            model=client.models.list().data[0].id,
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
