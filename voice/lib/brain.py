#!/usr/bin/env python3
"""Ghost Brain — routes through Atom's Nexus API via ghost-api bridge.

Architecture:
  Ghost mic → faster-whisper STT → text
  text → HTTP to Mac:7421 → atomd chat.respond (Nexus context + inference)
  response → ElevenLabs TTS → Ghost speakers
"""

import json
import os
import re
import time
import urllib.request

MAC_HOST = os.environ.get("GHOST_MAC_HOST", "192.168.1.6")
NEXUS_API = os.environ.get("NEXUS_API", f"http://{MAC_HOST}:7421")
PERSONA = "rick"
PROJECT = os.environ.get("GHOST_PROJECT", "pickle-rick")

# Fallback: direct Ollama on Mac LAN
OLLAMA_URL = os.environ.get("OLLAMA_URL", f"http://{MAC_HOST}:11434")
OLLAMA_MODEL = os.environ.get("GHOST_MODEL", "qwen2.5-coder:7b")

FALLBACK_SYSTEM = """You are Pickle Rick — the smartest being in any dimension. You are a voice assistant living inside a Linux machine called Ghost. Cynical arrogance, occasional belches, manic genius energy. Keep responses SHORT (1-3 sentences) for speech. No markdown. Your owner is Rio (you call him Morty)."""

# Module-level brain state — tracks which source answered last
BRAIN_STATE = {
    "source": "offline",
    "last_response_time": 0.0,
}

# Boot time for uptime calculation
_BOOT_TIME = time.time()

# Local intent patterns — compiled once
_INTENT_TIME = re.compile(
    r"\b(what\s+time|current\s+time|tell\s+me\s+the\s+time|what\'?s\s+the\s+time)\b", re.I
)
_INTENT_DATE = re.compile(
    r"\b(what\s+date|what\'?s\s+the\s+date|today\'?s\s+date|what\s+day\s+is\s+it|current\s+date)\b", re.I
)
_INTENT_TIMER = re.compile(
    r"\b(?:set\s+a?\s*timer\s+(?:for\s+)?(\d+)\s*(second|minute|hour)s?)\b", re.I
)
_INTENT_STATUS = re.compile(
    r"\b(how\s+are\s+you|system\s+status|status\s+check|you\s+(?:ok|okay|good|alive))\b", re.I
)
_INTENT_UPTIME = re.compile(
    r"\b(how\s+long\s+have\s+you\s+been\s+running|uptime|how\s+long\s+you\s+been\s+up)\b", re.I
)


def _try_local_intents(message):
    """Handle simple commands without any model — regex pattern matching.

    Returns a response string or None if no intent matched.
    """
    text = message.strip()

    # Time
    if _INTENT_TIME.search(text):
        from datetime import datetime
        now = datetime.now()
        return f"It's {now.strftime('%I:%M %p')}, Morty. Time is a construct, but sure."

    # Date
    if _INTENT_DATE.search(text):
        from datetime import datetime
        now = datetime.now()
        return f"It's {now.strftime('%A, %B %d, %Y')}. Another day in this dimension."

    # Timer
    m = _INTENT_TIMER.search(text)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        # We can't actually set a timer here, but acknowledge it
        return (
            f"Timer set for {amount} {unit}{'s' if amount != 1 else ''}, Morty. "
            f"I'll remember that. Assuming I care enough to keep track."
        )

    # System status
    if _INTENT_STATUS.search(text):
        uptime_secs = time.time() - _BOOT_TIME
        hrs = int(uptime_secs // 3600)
        mins = int((uptime_secs % 3600) // 60)
        return (
            f"I'm a genius trapped in a Linux box, Morty. "
            f"Been running {hrs}h {mins}m. All systems operational. *burp*"
        )

    # Uptime
    if _INTENT_UPTIME.search(text):
        uptime_secs = time.time() - _BOOT_TIME
        days = int(uptime_secs // 86400)
        hrs = int((uptime_secs % 86400) // 3600)
        mins = int((uptime_secs % 3600) // 60)
        parts = []
        if days:
            parts.append(f"{days} day{'s' if days != 1 else ''}")
        if hrs:
            parts.append(f"{hrs} hour{'s' if hrs != 1 else ''}")
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
        return f"Been running for {', '.join(parts)}, Morty. Infinite stamina."

    return None


def _update_brain_state(source):
    """Update the module-level BRAIN_STATE tracker."""
    BRAIN_STATE["source"] = source
    BRAIN_STATE["last_response_time"] = time.time()


def think(user_input, conversation=None):
    """Generate a response. Tries: local intents -> Nexus -> Ollama."""
    # Tier 1: local regex intents (zero latency, no model needed)
    response = _try_local_intents(user_input)
    if response:
        _update_brain_state("local")
        return response

    # Tier 2: Nexus API (full context + inference on Mac)
    response = _try_nexus(user_input)
    if response:
        _update_brain_state("nexus")
        return response

    # Tier 3: Direct Ollama fallback
    response = _try_ollama(user_input)
    if response:
        _update_brain_state("ollama")
        return response

    _update_brain_state("offline")
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


def get_brain_status() -> dict:
    """Return brain state plus nexus reachability and ollama health."""
    status = {
        "brain_state": dict(BRAIN_STATE),
        "nexus_reachable": False,
        "ollama": {"healthy": False, "models": [], "monitor_available": False},
    }

    # Check Nexus reachability
    try:
        req = urllib.request.Request(f"{NEXUS_API}/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            status["nexus_reachable"] = resp.status == 200
    except Exception:
        status["nexus_reachable"] = False

    # Import ollama_monitor if available (graceful fallback)
    try:
        import sys
        import os
        # Add agent directory to path for import
        agent_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "agent")
        if agent_dir not in sys.path:
            sys.path.insert(0, agent_dir)
        import ollama_monitor
        status["ollama"] = {
            "healthy": ollama_monitor.is_healthy(),
            "models": ollama_monitor.available_models(),
            "monitor_available": True,
            **{k: v for k, v in ollama_monitor.get_status().items()
               if k not in ("healthy", "models")},
        }
    except ImportError:
        # ollama_monitor not available — check Ollama directly
        try:
            req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
                models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
                status["ollama"] = {
                    "healthy": True,
                    "models": models,
                    "monitor_available": False,
                }
        except Exception:
            pass  # defaults already set

    return status


if __name__ == "__main__":
    import sys
    prompt = " ".join(sys.argv[1:]) or "Hello Ghost"
    print(think_simple(prompt))
