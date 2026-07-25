# squeue -u $USER -o "%.18i %.30j %.20R" to check gpu node

import os
import requests

BASE_URL = f"http://172.33.1.5:8000/v1"

headers = {
    "Authorization": "Bearer dummy",  # vLLM OpenAI-compatible server accepts dummy key
    "Content-Type": "application/json",
}

# Health/models check
r = requests.get(f"{BASE_URL}/models", headers=headers, timeout=20)
r.raise_for_status()
models = r.json()["data"]
model_id = models[0]["id"]
print("Model:", model_id)

# Chat completion
payload = {
    "model": model_id,
    "messages": [{"role": "user", "content": "Reply with: OK"}],
    "temperature": 0.0,
    "max_tokens": 1600,
}
r2 = requests.post(f"{BASE_URL}/chat/completions", headers=headers, json=payload, timeout=60)
r2.raise_for_status()
print(r2.json()["choices"][0]["message"]["content"])