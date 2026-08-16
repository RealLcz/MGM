# This file is adapted from https://github.com/jennyzzt/dgm.

import ast
import copy
import json
import re
from time import time

import anthropic
import backoff
import openai

from llm import (
    create_client,
    deepseek_api_extra_body,
    deepseek_api_timeout,
    deepseek_max_output_tokens,
    is_deepseek_api_model,
)
from tools import load_all_tools

CLAUDE_MODEL = "anthropic/claude-sonnet-4"
OPENAI_MODEL = "gpt-5"
MAX_XML_TOOL_FORMAT_RETRIES = 2
_XML_TOOL_FORMAT_NUDGE = (
    "Your last reply included a tool call in plain text. "
    "Use the API tool_calls field with JSON arguments only "
    "(do not write <tool_call> XML in the message body)."
)


def _content_looks_like_tool_attempt(content: str) -> bool:
    if not content:
        return False
    markers = ("<tool_call>", "<function=", "<function>", "<parameter=")
    return any(marker in content for marker in markers)


def _message_to_api_dict(message):
    """Serialize chat messages for OpenAI-compatible APIs (incl. DeepSeek)."""
    if isinstance(message, dict):
        data = dict(message)
    else:
        data = message.model_dump(exclude_none=True)

    for key in (
        "reasoning_content",
        "refusal",
        "annotations",
        "audio",
        "function_call",
    ):
        data.pop(key, None)

    if data.get("role") == "assistant" and data.get("tool_calls"):
        data["content"] = data.get("content") or ""
        normalized_calls = []
        for call in data["tool_calls"]:
            if isinstance(call, dict):
                fn = call.get("function", {})
                normalized_calls.append(
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": fn["name"],
                            "arguments": fn["arguments"],
                        },
                    }
                )
            else:
                normalized_calls.append(
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                )
        data["tool_calls"] = normalized_calls
    return data


def _normalize_messages_for_api(messages):
    return [_message_to_api_dict(message) for message in messages]


def _ensure_vllm_user_turn(messages):
    """Qwen3.x vLLM chat templates require at least one user turn in messages."""
    normalized = _normalize_messages_for_api(messages)
    non_system = [m for m in normalized if m.get("role") != "system"]
    if not non_system:
        return normalized + [{"role": "user", "content": "Please proceed."}]
    if not any(m.get("role") == "user" for m in non_system):
        normalized = [{"role": "user", "content": "Please proceed."}] + normalized
    elif non_system[-1].get("role") == "tool":
        normalized = normalized + [
            {
                "role": "user",
                "content": "Continue based on the tool results above.",
            }
        ]
    return normalized


def _openai_function_tool(tool_info):
    required = [
        val_name for val_name in tool_info["input_schema"]["properties"].keys()
    ]
    return {
        "type": "function",
        "function": {
            "name": tool_info["name"],
            "description": tool_info["description"],
            "parameters": {
                "type": "object",
                "properties": tool_info["input_schema"]["properties"],
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def parse_qwen_xml_tool_call(content: str):
    """Fallback when vLLM/Qwen puts tool calls in content instead of tool_calls."""
    if not content or not _content_looks_like_tool_attempt(content):
        return None

    fn_match = re.search(r"<function=([^>\n]+)>", content)
    if not fn_match:
        fn_match = re.search(r"<function>\s*([^<\n]+)", content)
    if not fn_match:
        return None

    tool_name = fn_match.group(1).strip()
    tool_input = {}
    for key, val in re.findall(
        r"<parameter=([^>\n]+)>\s*(.*?)\s*</parameter>", content, re.DOTALL
    ):
        key = key.strip()
        if key:
            tool_input[key] = val.strip()

    if not tool_input:
        return None

    return {
        "tool_id": f"xml-fallback-{int(time() * 1000)}",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "from_xml_fallback": True,
    }


def _assistant_message_with_tool_call(message, tool_use):
    content = getattr(message, "content", None) or ""
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": tool_use["tool_id"],
                "type": "function",
                "function": {
                    "name": tool_use["tool_name"],
                    "arguments": json.dumps(tool_use["tool_input"]),
                },
            }
        ],
    }


def process_tool_call(tools_dict, tool_name, tool_input):
    try:
        if tool_name in tools_dict:
            return tools_dict[tool_name]["function"](**tool_input)
        else:
            return f"Error: Tool '{tool_name}' not found"
    except Exception as e:
        return f"Error executing tool '{tool_name}': {str(e)}"


_BACKOFF_EXCEPTIONS = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.APIStatusError,
)

# HTTP status codes that indicate a non-retryable failure. Retrying these only
# wastes time and budget — e.g. 402 Insufficient Balance, 401 auth, 400 bad
# request. Encountering one should abort the agent loop immediately so the
# partial edits made so far can still be captured as model_patch.
_NON_RETRYABLE_STATUS_CODES = frozenset({400, 401, 402, 403, 404, 422})


def _is_non_retryable(exc: Exception) -> bool:
    """True for errors that will never succeed on retry (auth, balance, etc.)."""
    # OpenAI/Anthropic status errors carry a status_code attribute.
    status = getattr(exc, "status_code", None)
    if status is not None:
        try:
            if int(status) in _NON_RETRYABLE_STATUS_CODES:
                return True
        except (TypeError, ValueError):
            pass
    # Fall back to message sniffing for SDKs that don't expose status_code.
    msg = str(exc)
    if "Insufficient Balance" in msg:
        return True
    if "Authentication Fails" in msg or "authentication_error" in msg:
        return True
    return False


def _remaining_seconds(deadline: float | None) -> float | None:
    """Seconds left until `deadline` (wall clock), or None if no deadline."""
    if deadline is None:
        return None
    left = deadline - time()
    return left if left > 0 else 0.0


def _backoff_max_time(default_seconds: float, deadline: float | None) -> float:
    """Clamp backoff max_time so retries never run past the agent deadline.

    Without this, a single get_response_withtools call could block inside
    backoff for up to `default_seconds` (600s) even when the outer `timeout`
    wrapper is about to SIGTERM the process — that is exactly what produced
    the `exit code -15` errors on the JavaScript polyglot tasks.
    """
    if deadline is None:
        return default_seconds
    left = _remaining_seconds(deadline)
    if left is None:
        return default_seconds
    # Keep at least a little room for one final attempt; never negative.
    return max(5.0, min(default_seconds, left))


def _deepseek_create_with_fallback(client, request_kwargs, extra_body, logging=None):
    """Call DeepSeek chat completions, falling back if thinking knobs rejected.

    Only TypeError raised by `extra_body` is swallowed; everything else
    propagates so the outer backoff can handle transient failures.
    """
    try:
        return client.chat.completions.create(
            **request_kwargs, extra_body=extra_body
        )
    except TypeError as exc:
        # Some SDK versions reject `extra_body`; retry without it.
        if logging:
            logging(
                f"DeepSeek create() rejected extra_body ({exc}); retrying without it."
            )
        return client.chat.completions.create(**request_kwargs)
    except Exception as exc:
        if extra_body.get("thinking", {}).get("type") == "enabled":
            if logging:
                logging(
                    f"DeepSeek thinking request failed ({exc}); retrying without thinking."
                )
            return client.chat.completions.create(
                **request_kwargs,
                extra_body={"thinking": {"type": "disabled"}},
            )
        raise


def get_response_withtools(
    client,
    model,
    messages,
    tools,
    tool_choice,
    logging=None,
    max_retry=3,
    deadline=None,
):
    """Make one LLM tool-calling request.

    `deadline` is an absolute wall-clock time (from `time()`) by which the
    caller wants control back. When set, the backoff `max_time` is clamped so
    retries cannot blow past it; this prevents the agent from blocking inside a
    retry loop when the outer `timeout` wrapper is about to SIGTERM the process.
    """
    backoff_max_time = _backoff_max_time(600.0, deadline)

    @backoff.on_exception(
        backoff.expo,
        _BACKOFF_EXCEPTIONS,
        max_time=backoff_max_time,
        max_value=60,
    )
    def _do_request():
        if model.startswith("o") or "gpt" in model.lower():
            response = client.responses.create(
                model=model,
                input=[
                    {
                        "role": "system",
                        "content": "You are the best coder in the world!",
                    }
                ]
                + messages,
                tool_choice=tool_choice,
                tools=tools,
                parallel_tool_calls=False,
            )
        else:
            api_messages = _ensure_vllm_user_turn(
                [
                    {
                        "role": "system",
                        "content": "You are the best coder in the world!",
                    }
                ]
                + messages
            )
            request_kwargs = {
                "model": client.models.list().data[0].id
                if "vllm" in model.lower()
                else model,
                "messages": api_messages,
                "tool_choice": tool_choice,
                "tools": tools,
                "parallel_tool_calls": False,
            }
            if is_deepseek_api_model(model):
                request_kwargs["model"] = model
                request_kwargs["max_tokens"] = deepseek_max_output_tokens()
                request_kwargs["timeout"] = deepseek_api_timeout()
                extra_body = deepseek_api_extra_body()
                response = _deepseek_create_with_fallback(
                    client, request_kwargs, extra_body, logging=logging
                )
            else:
                response = client.chat.completions.create(**request_kwargs)
        return response

    try:
        return _do_request()
    except Exception as e:
        if logging:
            logging(f"Error in get_response_withtools: {str(e)}")
        # Non-retryable errors (402 balance, 401 auth, 400 bad request, etc.)
        # must not be retried — retrying wastes time and will never succeed.
        if _is_non_retryable(e):
            raise
        # Context-window errors are not retryable; surface them immediately.
        if "Input is too long for requested model" in str(e):
            raise
        # Only retry recursively when we still have budget; otherwise let it
        # raise so the caller can break its loop cleanly instead of being
        # SIGTERM'd by the outer `timeout` wrapper.
        left = _remaining_seconds(deadline)
        if max_retry > 0 and (left is None or left > 10):
            return get_response_withtools(
                client,
                model,
                messages,
                tools,
                tool_choice,
                logging,
                max_retry - 1,
                deadline=deadline,
            )
        raise


def check_for_tool_use(response, model=""):
    """
    Checks if the response contains a tool call.
    """

    if model.startswith("o") or "gpt" in model.lower():
        # OpenAI, check for tool_calls in response
        for tool_call in response.output:
            if tool_call.type == "function_call":
                break

        if tool_call:
            return {
                "tool_id": tool_call.call_id,
                "tool_name": tool_call.name,
                "tool_input": json.loads(tool_call.arguments),
            }

    else:
        message = response.choices[0].message
        if message.tool_calls:
            call = message.tool_calls[0]
            return {
                "tool_id": call.id,
                "tool_name": call.function.name,
                "tool_input": json.loads(call.function.arguments),
            }

        xml_fallback = None
        if not is_deepseek_api_model(model):
            xml_fallback = parse_qwen_xml_tool_call(message.content or "")
        if xml_fallback:
            return xml_fallback

        return False

    # No tool use found
    return None


def convert_tool_info(tool_info, model=None):
    """
    Converts tool_info from Claude format to the given model's format.
    """
    if is_deepseek_api_model(model):
        return _openai_function_tool(tool_info)
    if "vllm" in model.lower():
        required = [
            val_name for val_name in tool_info["input_schema"]["properties"].keys()
        ]
        return {
            "type": "function",
            "function": {
                "name": tool_info["name"],
                "description": tool_info["description"],
                "parameters": {
                    "type": "object",
                    "properties": tool_info["input_schema"]["properties"],
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }
    elif model.startswith("o") or "gpt" in model.lower():

        def add_additional_properties(d):
            if isinstance(d, dict):
                if "properties" in d:
                    d["additionalProperties"] = False
                for k, v in d.items():
                    add_additional_properties(v)

        add_additional_properties(tool_info["input_schema"])
        for p in tool_info["input_schema"]["properties"].keys():
            if not p in tool_info["input_schema"]["required"]:
                tool_info["input_schema"]["required"].append(p)
                t = copy.deepcopy(tool_info["input_schema"]["properties"][p]["type"])
                if isinstance(t, str):
                    tool_info["input_schema"]["properties"][p]["type"] = [t, "null"]
                elif isinstance(t, list):
                    tool_info["input_schema"]["properties"][p]["type"] = t + ["null"]

        return {
            "type": "function",
            "name": tool_info["name"],
            "description": tool_info["description"],
            "parameters": tool_info["input_schema"],
            "strict": True,
        }
    else:
        return _openai_function_tool(tool_info)


def chat_with_agent_deepseek(
    msg,
    model="deepseek-v4-flash",
    msg_history=None,
    logging=print,
    max_llm_calls=1000,
    timeout=3600,
):
    start_time = time()
    # Absolute wall-clock deadline. Passed down to get_response_withtools so
    # its backoff retries are clamped to never run past the outer `timeout`
    # wrapper — otherwise the agent gets SIGTERM'd (exit code -15) while still
    # blocked inside a retry loop, which is what produced the 38 JavaScript
    # `incomplete` errors on the polyglot full run.
    deadline = start_time + timeout
    if msg_history is None:
        msg_history = []
    new_msg_history = [{"role": "user", "content": msg}]
    separator = "=" * 10
    logging(f"\n{separator} User Instruction {separator}\n{msg}")
    try:
        client, client_model = create_client(model)
        all_tools = load_all_tools(logging=logging)
        tools_dict = {tool["info"]["name"]: tool for tool in all_tools}
        tools = [
            convert_tool_info(tool["info"], model=client_model) for tool in all_tools
        ]

        empty_response_streak = 0
        for i in range(max_llm_calls):
            if timeout * 0.9 < time() - start_time:
                logging("Timeout reached, stopping further LLM calls.")
                return new_msg_history, i

            response = get_response_withtools(
                client=client,
                model=client_model,
                messages=msg_history + new_msg_history,
                tool_choice="auto",
                tools=tools,
                logging=logging,
                deadline=deadline,
            )

            if response is None or not getattr(response, "choices", None):
                empty_response_streak += 1
                logging(
                    f"Empty or malformed response from LLM (streak={empty_response_streak})"
                )
                # Guard against infinite loop on persistent empty responses.
                if empty_response_streak >= 5:
                    logging("Too many empty responses; stopping agent loop.")
                    return new_msg_history, i
                continue
            empty_response_streak = 0

            message = response.choices[0].message
            logging(f"Tool Response: {response}")
            tool_calls = message.tool_calls or []
            if not tool_calls:
                new_msg_history.append(_message_to_api_dict(message))
                return new_msg_history, i + 1

            new_msg_history.append(_message_to_api_dict(message))
            for call in tool_calls:
                tool_name = call.function.name
                tool_input = json.loads(call.function.arguments)
                tool_result = process_tool_call(tools_dict, tool_name, tool_input)

                logging(f"Tool Used: {tool_name}")
                logging(f"Tool Input: {tool_input}")
                logging(f"Tool Result: {tool_result}")

                new_msg_history.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": tool_name,
                        "content": f"{tool_result}",
                    }
                )

    except Exception as e:
        logging(f"Error in chat_with_agent_deepseek: {str(e)}")
        # Non-retryable errors (402 balance, 401 auth, ...) mean no further LLM
        # call can succeed. Re-raise so coding_agent_polyglot.py exits the
        # process and refresh_model_patch_cmd still captures whatever edits the
        # agent managed to make before the error. Swallowing these silently
        # produced empty model_patch files (36 empty_patch results) because the
        # agent loop just returned an empty history.
        if _is_non_retryable(e):
            raise

    return new_msg_history, max_llm_calls


def chat_with_agent_openai(
    msg,
    model=OPENAI_MODEL,
    msg_history=None,
    logging=print,
    max_llm_calls=1000,  # Maximum number of LLM calls to make
    timeout=3600,
):
    start_time = time()
    deadline = start_time + timeout
    # Construct message
    if msg_history is None:
        msg_history = []
    new_msg_history = [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": msg,
                }
            ],
        }
    ]
    separator = "=" * 10
    logging(f"\n{separator} User Instruction {separator}\n{msg}")
    try:
        # Create client
        client, client_model = create_client(model)

        # Load all tools
        all_tools = load_all_tools(logging=logging)
        tools_dict = {tool["info"]["name"]: tool for tool in all_tools}
        tools = [
            convert_tool_info(tool["info"], model=client_model) for tool in all_tools
        ]

        for i in range(max_llm_calls):
            if timeout * 0.9 < time() - start_time:
                logging("Timeout reached, stopping further LLM calls.")
                return new_msg_history, i
            response = get_response_withtools(
                client=client,
                model=client_model,
                messages=msg_history + new_msg_history,
                tool_choice="auto",
                tools=tools,
                logging=logging,
                deadline=deadline,
            )
            logging(f"Tool Response: {response}")
            tool_use = check_for_tool_use(response, model=client_model)
            new_msg_history += response.output
            if not tool_use:
                return new_msg_history, i + 1
            # Process tool call
            tool_name = tool_use["tool_name"]
            tool_input = tool_use["tool_input"]
            tool_result = process_tool_call(tools_dict, tool_name, tool_input)

            logging(f"Tool Used: {tool_name}")
            logging(f"Tool Input: {tool_input}")
            logging(f"Tool Result: {tool_result}")

            new_msg_history.append(
                {
                    "type": "function_call_output",
                    "call_id": tool_use["tool_id"],
                    "output": tool_result,
                }
            )

    except Exception:
        pass

    return new_msg_history, max_llm_calls


def chat_with_agent_open_router(
    msg,
    model=CLAUDE_MODEL,
    msg_history=None,
    logging=print,
    max_llm_calls=1000,  # Maximum number of LLM calls to make
    timeout=3600,
):
    start_time = time()
    deadline = start_time + timeout
    # Construct message
    if msg_history is None:
        msg_history = []
    new_msg_history = [{"role": "user", "content": msg}]
    separator = "=" * 10
    logging(f"\n{separator} User Instruction {separator}\n{msg}")
    try:
        # Create client
        client, client_model = create_client(model)
        # Load all tools
        all_tools = load_all_tools(logging=logging)
        tools_dict = {tool["info"]["name"]: tool for tool in all_tools}
        tools = [
            convert_tool_info(tool["info"], model=client_model) for tool in all_tools
        ]
        xml_tool_format_retries = 0
        for i in range(max_llm_calls):
            if timeout * 0.9 < time() - start_time:
                logging("Timeout reached, stopping further LLM calls.")
                return new_msg_history, i
            # Process tool call
            response = get_response_withtools(
                client=client,
                model=client_model,
                messages=msg_history + new_msg_history,
                tool_choice="auto",
                tools=tools,
                logging=logging,
                deadline=deadline,
            )

            if response is None or not getattr(response, "choices", None):
                logging("Empty or malformed response from LLM, skipping iteration")
                continue

            message = response.choices[0].message
            logging(f"Tool Response: {response}")
            tool_use = check_for_tool_use(response, model=client_model)
            if not tool_use:
                content = message.content or ""
                if (
                    _content_looks_like_tool_attempt(content)
                    and xml_tool_format_retries < MAX_XML_TOOL_FORMAT_RETRIES
                    and i < max_llm_calls - 1
                ):
                    new_msg_history.append(_message_to_api_dict(message))
                    new_msg_history.append(
                        {"role": "user", "content": _XML_TOOL_FORMAT_NUDGE}
                    )
                    xml_tool_format_retries += 1
                    logging(
                        "Tool call found in plain text but not in tool_calls; "
                        f"retrying ({xml_tool_format_retries}/{MAX_XML_TOOL_FORMAT_RETRIES})."
                    )
                    continue
                new_msg_history.append(_message_to_api_dict(message))
                return new_msg_history, i + 1

            xml_tool_format_retries = 0
            if tool_use.get("from_xml_fallback"):
                logging(
                    f"Recovered XML tool call via fallback: {tool_use['tool_name']}"
                )
                new_msg_history.append(
                    _assistant_message_with_tool_call(message, tool_use)
                )
            else:
                new_msg_history.append(_message_to_api_dict(message))

            tool_name = tool_use["tool_name"]
            tool_input = tool_use["tool_input"]
            tool_result = process_tool_call(tools_dict, tool_name, tool_input)
            tool_use["content"] = tool_result

            logging(f"Tool Used: {tool_name}")
            logging(f"Tool Input: {tool_input}")
            logging(f"Tool Result: {tool_result}")

            # Get tool response
            new_msg_history.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_use["tool_id"],
                    "name": tool_use["tool_name"],
                    "content": f"{tool_result}",
                }
            )

    except Exception as e:
        logging(f"Error in chat_with_agent_open_router: {str(e)}")
        if _is_non_retryable(e):
            raise

    return new_msg_history, max_llm_calls


def convert_msg_history_openai(msg_history):
    """
    Convert OpenAI-style message history into a generic format.
    """
    new_msg_history = []

    for msg in msg_history:
        role = ""
        content = ""
        if isinstance(msg, dict):
            if "role" in msg.keys():
                role = msg["role"]
            else:
                role = "user"
            if "content" in msg.keys():
                content = msg["content"]
            else:
                content = "Tool Result: " + msg.get("output", "")

        else:
            role = "assistant"
            content = str(msg)

        new_msg_history.append({"role": role, "content": content})

    return new_msg_history


def convert_msg_history_open_router(msg_history):
    """
    Convert OpenRouter-style message history into a generic format.
    """
    new_msg_history = []

    for msg in msg_history:
        if not isinstance(msg, dict):
            msg = dict(msg)
        role = msg.get("role", "")
        if "content" in msg.keys():
            if role == "tool":
                content = "Tool Result: " + msg["content"]
            else:
                content = msg["content"]
        else:
            content = f"Function: {msg['tool_calls'][0].name}\nArguments: {msg['tool_calls'][0].function.arguments}"

        new_msg_history.append({"role": role, "content": content})

    return new_msg_history


def convert_msg_history(msg_history, model=None):
    """
    Convert message history from the model-specific format to a generic format.
    """
    if model.startswith("o") or "gpt" in model.lower():
        return convert_msg_history_openai(msg_history)
    if is_deepseek_api_model(model):
        return convert_msg_history_open_router(msg_history)
    else:
        return convert_msg_history_open_router(msg_history)


def chat_with_agent(
    msg,
    model=CLAUDE_MODEL,
    msg_history=None,
    logging=print,
    convert=False,  # Convert the message history to a generic format, so that msg_history can be used across models
    max_llm_calls=1000,  # Maximum number of LLM calls to make
    timeout=3600,
):
    if msg_history is None:
        msg_history = []

    if model.startswith("o") or "gpt" in model.lower():
        # OpenAI models
        new_msg_history, n_llm_calls = chat_with_agent_openai(
            msg,
            model=model,
            msg_history=msg_history,
            logging=logging,
            max_llm_calls=max_llm_calls,
            timeout=timeout,
        )
        new_msg_history = msg_history + new_msg_history

    elif is_deepseek_api_model(model):
        new_msg_history, n_llm_calls = chat_with_agent_deepseek(
            msg,
            model=model,
            msg_history=msg_history,
            logging=logging,
            max_llm_calls=max_llm_calls,
            timeout=timeout,
        )
        new_msg_history = msg_history + new_msg_history

    else:
        new_msg_history, n_llm_calls = chat_with_agent_open_router(
            msg,
            model=model,
            msg_history=msg_history,
            logging=logging,
            max_llm_calls=max_llm_calls,
            timeout=timeout,
        )
        new_msg_history = msg_history + new_msg_history

    return new_msg_history, n_llm_calls


if __name__ == "__main__":
    # Test the tool calling functionality
    msg = "First create the current directory. Then implement a function that returns the current directory and save it in the directory just created. Finally call the function and return the result. In the end, summarize what you did."
    model = "vllm-qwenS-10.109.17.7"
    history, _ = chat_with_agent(msg, model=model, max_llm_calls=2)
    from utils.eval_utils import msg_history_to_report

    print(msg_history_to_report("hgm", history, model=model))
    # history = convert_msg_history(history, model)
    # chat_with_agent(msg, model, history, max_llm_calls=2)
