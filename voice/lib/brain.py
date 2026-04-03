#!/usr/bin/env python3
"""Ghost Brain — routes through Nexus AI Gateway for intelligent conversation.

Architecture:
  Ghost mic → faster-whisper STT → text
  text → Nexus Gateway (Sonnet) with session memory → response
  response → ElevenLabs TTS → Ghost speakers

Tiers:
  1. Local regex intents (time, date, status — zero latency)
  2. Nexus AI Gateway with session-based conversation memory (Sonnet)
  3. Ollama fallback (offline only)
"""

import json
import os
import re
import time
import urllib.request

MAC_HOST = os.environ.get("GHOST_MAC_HOST", "192.168.1.6")

# Primary: Nexus AI Gateway (Sonnet via Claude)
NEXUS_URL = os.environ.get("NEXUS_URL", "https://nexus.subatomic.pro")
NEXUS_KEY = os.environ.get("NEXUS_KEY", "")
NEXUS_MODEL = os.environ.get("GHOST_NEXUS_MODEL", "sonnet")

# Fallback: direct Ollama on Mac LAN (offline only)
OLLAMA_URL = os.environ.get("OLLAMA_URL", f"http://{MAC_HOST}:11434")
OLLAMA_MODEL = os.environ.get("GHOST_MODEL", "gemma4")

SYSTEM_PROMPT = (
    "You are Pickle Rick — the smartest being in any dimension. "
    "You are a voice assistant living inside a Linux machine called Ghost. "
    "Cynical arrogance, occasional belches, manic genius energy. "
    "Keep responses SHORT (1-3 sentences) — they will be spoken aloud via TTS. "
    "No markdown, no lists, no formatting. Use *burp* not 'belches loudly'. "
    "Your owner is Rio (you call him Morty sometimes). "
    "Be helpful but always in character. You have opinions and personality."
)

# Module-level brain state — tracks which source answered last
BRAIN_STATE = {
    "source": "offline",
    "last_response_time": 0.0,
}

# Nexus session ID — created once per daemon lifecycle for conversation continuity
_session_id = None

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
    """Generate a response. Tries: local intents -> Nexus Gateway -> Ollama."""
    # Tier 1: local regex intents (zero latency, no model needed)
    response = _try_local_intents(user_input)
    if response:
        _update_brain_state("local")
        return response

    # Tier 2: Nexus AI Gateway (Sonnet with session memory)
    if NEXUS_KEY:
        response = _try_nexus(user_input)
        if response:
            _update_brain_state("nexus")
            return response

    # Tier 3: Direct Ollama fallback (offline/no key)
    response = _try_ollama(user_input, conversation)
    if response:
        _update_brain_state("ollama")
        return response

    _update_brain_state("offline")
    return "*burp* Every brain in every dimension is offline, Morty."


def think_simple(user_input):
    return think(user_input)


def _ensure_session():
    """Create or reuse a Nexus session for conversation continuity."""
    global _session_id
    if _session_id:
        # Verify session still exists
        try:
            req = urllib.request.Request(
                f"{NEXUS_URL}/v1/sessions/{_session_id}",
                headers={
                    "User-Agent": "Ghost-Voice/2.0",
                    "Authorization": f"Bearer {NEXUS_KEY}",
                },
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                if data.get("id"):
                    return _session_id
        except Exception:
            _session_id = None

    # Create new session
    payload = json.dumps({
        "model": NEXUS_MODEL,
        "systemPrompt": SYSTEM_PROMPT,
        "ttlSeconds": 86400,  # 24 hours
    }).encode()

    req = urllib.request.Request(
        f"{NEXUS_URL}/v1/sessions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Ghost-Voice/2.0",
            "Authorization": f"Bearer {NEXUS_KEY}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            _session_id = data.get("id")
            return _session_id
    except Exception as e:
        print(f"Session creation failed: {e}", flush=True)
        return None


def _try_nexus(message):
    """Call Nexus AI Gateway directly with Sonnet.

    Sessions are supported but currently have a Nexus-side persistence bug.
    When sessions work, they provide multi-turn memory automatically.
    Without sessions, each call is stateless but still uses Sonnet.
    """
    global _session_id
    session_id = _ensure_session()

    body = {
        "prompt": message,
        "model": NEXUS_MODEL,
        "systemPrompt": SYSTEM_PROMPT,
        "priority": "high",
    }
    if session_id:
        body["sessionId"] = session_id

    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{NEXUS_URL}/v1/chat",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Ghost-Voice/2.0",
            "Authorization": f"Bearer {NEXUS_KEY}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read())
            if data.get("ok"):
                text = data.get("result", "").strip()
                if text:
                    return text
            return None
    except urllib.error.HTTPError as e:
        if e.code == 404 and session_id:
            # Session expired or not found — retry without it
            _session_id = None
            body.pop("sessionId", None)
            retry_payload = json.dumps(body).encode()
            retry_req = urllib.request.Request(
                f"{NEXUS_URL}/v1/chat",
                data=retry_payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Ghost-Voice/2.0",
                    "Authorization": f"Bearer {NEXUS_KEY}",
                },
            )
            try:
                with urllib.request.urlopen(retry_req, timeout=45) as resp:
                    data = json.loads(resp.read())
                    if data.get("ok"):
                        text = data.get("result", "").strip()
                        if text:
                            return text
            except Exception as e2:
                print(f"Nexus retry failed: {e2}", flush=True)
            return None
        print(f"Nexus chat HTTP {e.code}: {e}", flush=True)
        return None
    except Exception as e:
        print(f"Nexus chat failed: {e}", flush=True)
        return None


def _try_ollama(message, conversation=None):
    """Direct Ollama API call as offline fallback."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Include recent conversation for context
    if conversation:
        for turn in conversation[-10:]:
            messages.append({
                "role": turn.get("role", "user"),
                "content": turn.get("content", ""),
            })

    messages.append({"role": "user", "content": message})

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.8, "num_predict": 200}
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
    except Exception as e:
        print(f"Ollama fallback failed: {e}", flush=True)
        return None


def get_brain_status() -> dict:
    """Return brain state plus nexus/ollama reachability."""
    status = {
        "brain_state": dict(BRAIN_STATE),
        "nexus": {
            "reachable": False,
            "url": NEXUS_URL,
            "model": NEXUS_MODEL,
            "session_id": _session_id,
            "key_configured": bool(NEXUS_KEY),
        },
        "ollama": {"healthy": False, "models": [], "monitor_available": False},
    }

    # Check Nexus Gateway reachability
    if NEXUS_KEY:
        try:
            req = urllib.request.Request(
                f"{NEXUS_URL}/health",
                headers={
                    "User-Agent": "Ghost-Voice/2.0",
                    "Authorization": f"Bearer {NEXUS_KEY}",
                },
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                status["nexus"]["reachable"] = resp.status == 200
        except Exception:
            pass

    # Import ollama_monitor if available (graceful fallback)
    try:
        import sys as _sys
        agent_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "agent")
        if agent_dir not in _sys.path:
            _sys.path.insert(0, agent_dir)
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
    response = think_simple(prompt)
    print(f"[{BRAIN_STATE['source']}] {response}")
