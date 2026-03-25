"""Ollama local model client — drop-in replacement for nexus_client.chat().

Same interface: chat(prompt, model, system_prompt, json_schema, timeout, priority)
Returns: (result_text, model_name, "ollama")

Used for all background Ghost processing (kernel, AOR, sensors, digest).
User-facing interactions (voice, dashboard) stay on Nexus for quality.
"""

import json
import os
import urllib.request

MAC_HOST = os.environ.get("GHOST_MAC_HOST", "192.168.1.6")
OLLAMA_URL = os.environ.get("OLLAMA_URL", f"http://{MAC_HOST}:11434")
DEFAULT_MODEL = os.environ.get("GHOST_OLLAMA_MODEL", "gemma3:12b")


def chat(prompt, model=None, system_prompt=None, json_schema=None,
         timeout=60, priority="normal"):
    """Call local Ollama. Returns (result, model, "ollama").

    Args:
        prompt: User message text.
        model: Ollama model name (default: GHOST_OLLAMA_MODEL env or gemma3:12b).
        system_prompt: System message prepended to conversation.
        json_schema: If provided, instructs model to return JSON matching schema.
        timeout: Request timeout in seconds.
        priority: Ignored (local model has no queue), kept for interface compat.
    """
    use_model = model or DEFAULT_MODEL

    messages = []
    sys_text = system_prompt or ""

    if json_schema:
        schema_instruction = (
            "\n\nYou MUST respond with ONLY a valid JSON object. "
            "No markdown, no explanation, no commentary — just the JSON. "
            f"Schema: {json.dumps(json_schema)}"
        )
        sys_text += schema_instruction

    if sys_text:
        messages.append({"role": "system", "content": sys_text})
    messages.append({"role": "user", "content": prompt})

    body = {
        "model": use_model,
        "messages": messages,
        "stream": False,
        "keep_alive": "15m",
        "options": {"temperature": 0.3, "num_predict": 500},
    }
    if json_schema:
        body["format"] = "json"

    payload = json.dumps(body).encode()

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            text = data.get("message", {}).get("content", "").strip()
            return text, use_model, "ollama"
    except Exception as e:
        raise RuntimeError(f"Ollama call failed ({OLLAMA_URL}): {e}") from e


def is_available():
    """Quick health check — can we reach Ollama?"""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def warmup():
    """Load model into memory so first real call doesn't timeout.

    Cold starts on CPU can take 30-60s. Call this at startup.
    """
    try:
        chat("respond with only: ok", timeout=120)
        return True
    except Exception:
        return False
