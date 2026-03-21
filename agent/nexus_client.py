import json
import urllib.request

NEXUS_URL = "https://nexus.subatomic.pro"
NEXUS_KEY = "87dd2636cf397a90ddc68820750e949d6aa4f837e8e8770c98a269caf647de84"
DEFAULT_MODEL = "haiku"

def chat(prompt, model=None, system_prompt=None, json_schema=None, timeout=60):
    body = {"prompt": prompt, "model": model or DEFAULT_MODEL}
    if system_prompt:
        body["systemPrompt"] = system_prompt
    if json_schema:
        body["jsonSchema"] = json_schema
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{NEXUS_URL}/v1/chat",
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Ghost/1.0",
            "Authorization": f"Bearer {NEXUS_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read().decode())
    return result.get("result", ""), result.get("model", "?"), result.get("provider", "?")
