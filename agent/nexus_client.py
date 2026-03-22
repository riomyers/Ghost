import json
import os
import urllib.request

NEXUS_URL = os.environ.get("NEXUS_URL", "https://nexus.subatomic.pro")
NEXUS_KEY = os.environ.get("NEXUS_KEY", "")
DEFAULT_MODEL = "haiku"


def chat(prompt, model=None, system_prompt=None, json_schema=None,
         timeout=60, priority="normal"):
    """Call Nexus AI Gateway.

    Args:
        priority: 'high' for user-facing requests, 'normal' for background,
                  'low' for reflections/sensors. Nexus uses this for queuing.
    """
    if not NEXUS_KEY:
        raise RuntimeError("NEXUS_KEY env var not set")

    body = {"prompt": prompt, "model": model or DEFAULT_MODEL}
    if system_prompt:
        body["systemPrompt"] = system_prompt
    if json_schema:
        body["jsonSchema"] = json_schema
    if priority != "normal":
        body["priority"] = priority
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
