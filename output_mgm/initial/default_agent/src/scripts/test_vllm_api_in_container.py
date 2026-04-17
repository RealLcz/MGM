import argparse
import json
import os
import sys

import requests


def fail(msg: str, code: int = 1):
    print(f"[FAIL] {msg}")
    sys.exit(code)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=None, help="例如 http://172.33.1.1:8000/v1")
    parser.add_argument("--model", default=None, help="可选；不填则自动从 /models 取第一个")
    parser.add_argument("--timeout", type=int, default=15)
    args = parser.parse_args()

    host = os.getenv("VLLM_HOST", "127.0.0.1")
    port = os.getenv("VLLM_PORT", "8000")
    base_url = args.base_url or f"http://{host}:{port}/v1"
    headers = {"Authorization": "Bearer dummy", "Content-Type": "application/json"}

    print(f"[INFO] Testing base_url={base_url}")

    try:
        r = requests.get(f"{base_url}/models", headers=headers, timeout=args.timeout)
    except Exception as e:
        fail(f"/models 请求失败: {e}")

    if r.status_code != 200:
        fail(f"/models 非200: {r.status_code}, body={r.text[:500]}")

    data = r.json()
    models = data.get("data", [])
    if not models:
        fail("/models 返回空 data")

    model = args.model or models[0]["id"]
    print(f"[OK] /models 可用, using model={model}")

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "只回复 OK"}],
        "max_tokens": 16,
        "temperature": 0.0,
    }

    try:
        r2 = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            data=json.dumps(payload),
            timeout=args.timeout,
        )
    except Exception as e:
        fail(f"/chat/completions 请求失败: {e}")

    if r2.status_code != 200:
        fail(f"/chat/completions 非200: {r2.status_code}, body={r2.text[:1000]}")

    j = r2.json()
    content = j.get("choices", [{}])[0].get("message", {}).get("content", "")
    print(f"[OK] 推理成功, reply={content!r}")
    print("[PASS] 容器内已成功调用 vLLM API")


if __name__ == "__main__":
    main()