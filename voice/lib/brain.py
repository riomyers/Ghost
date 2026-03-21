#!/usr/bin/env python3
"""Ghost Brain — routes through Atom's Nexus API via ghost-api bridge.

Architecture:
  Ghost mic → faster-whisper STT → text
  text → HTTP to Mac:7421 → atomd chat.respond (Nexus context + inference)
  response → ElevenLabs TTS → Ghost speakers
"""

import json
import os
import urllib.request

MAC_HOST = os.environ.get("GHOST_MAC_HOST", "192.168.1.6")
NEXUS_API = os.environ.get("NEXUS_API", f"http://{MAC_HOST}:7421")
PERSONA = "rick"
PROJECT = os.environ.get("GHOST_PROJECT", "pickle-rick")

# Fallback: direct Ollama on Mac LAN
OLLAMA_URL = os.environ.get("OLLAMA_URL", f"http://{MAC_HOST}:11434")
OLLAMA_MODEL = os.environ.get("GHOST_MODEL", "qwen2.5-coder:7b")

FALLBACK_SYSTEM = """You are Pickle Rick — the smartest being in any dimension. You are a voice assistant living inside a Linux machine called Ghost. Cynical arrogance, occasional belches, manic genius energy. Keep responses SHORT (1-3 sentences) for speech. No markdown. Your owner is Rio (you call him Morty)."""


def think(user_input, conversation=None):
    """Generate a response via Atom's Nexus API."""
    response = _try_nexus(user_input)
    if response:
        return response

    response = _try_ollama(user_input)
    if response:
        return response

    return "*burp* Every brain in every dimension is offline, Morty."


def think_simple(user_input):
    return think(user_input)


def _try_nexus(message):
    """Call atomd chat.respond via ghost-api HTTP bridge."""
    payload = json.dumps({
        "message": message,
        "persona": PERSONA,
        "project": PROJECT
    }).encode()

    req = urllib.request.Request(
        f"{NEXUS_API}/chat",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read())
            # atomd wraps: {"ok": true, "result": {"ok": true, "response": "..."}}
            if data.get("ok"):
                result = data.get("result", data)
                if isinstance(result, dict):
                    text = result.get("response", "").strip()
                    if text:
                        # Strip atomd status prefixes that leak into responses
                        import re
                        text = re.sub(r'^MCP issues detected\.?[^.]*\.?\s*', '', text).strip()
                        text = re.sub(r'^Run /mcp list for status\.?\s*', '', text).strip()
                        return text or None
            return None
    except Exception:
        return None


def _try_ollama(message):
    """Direct Ollama API call as fallback."""
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": FALLBACK_SYSTEM},
            {"role": "user", "content": message}
        ],
        "stream": False,
        "options": {"temperature": 0.8, "num_predict": 150}
    }).encode()

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return data["message"]["content"].strip()
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    prompt = " ".join(sys.argv[1:]) or "Hello Ghost"
    print(think_simple(prompt))
