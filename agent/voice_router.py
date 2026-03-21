#!/usr/bin/env python3
"""Ghost Voice Command Router — routes voice input to the right handler.

Priority: local intents → smart home → brain (nexus/ollama)
Called by ghost-think and ear daemon.
"""

import re
import time
from datetime import datetime


def route_command(text):
    """Route a voice command to the appropriate handler.

    Returns (response_text, source) tuple.
    source is one of: "local", "smart_home", "nexus", "ollama", "offline"
    """
    if not text or not text.strip():
        return None, "none"

    # 1. Try local intents (no model needed)
    response = _try_local_intents(text)
    if response:
        return response, "local"

    # 2. Try smart home commands
    try:
        from smart_home import handle_voice_command
        response = handle_voice_command(text)
        if response:
            return response, "smart_home"
    except ImportError:
        pass

    # 3. Fall through to brain (caller handles this)
    return None, "passthrough"


def _try_local_intents(text):
    """Handle simple commands without any AI model."""
    t = text.lower().strip()

    # Time
    if re.search(r"\b(what time|current time|time is it)\b", t):
        now = datetime.now()
        return f"It's {now.strftime('%-I:%M %p')}."

    # Date
    if re.search(r"\b(what('s| is) the date|today('s| is) date|what day)\b", t):
        now = datetime.now()
        return f"It's {now.strftime('%A, %B %-d, %Y')}."

    # Timer
    timer_match = re.search(r"set (?:a )?timer (?:for )?(\d+)\s*(min|sec|hour)", t)
    if timer_match:
        amount = int(timer_match.group(1))
        unit = timer_match.group(2)
        # Start timer in background thread
        import threading
        seconds = amount * (60 if "min" in unit else 3600 if "hour" in unit else 1)
        threading.Thread(target=_timer_callback, args=(seconds, amount, unit), daemon=True).start()
        return f"Timer set for {amount} {unit}s."

    # System status
    if re.search(r"\b(system status|how are you|you okay|you alright|status report)\b", t):
        return _system_status()

    # Uptime
    if re.search(r"\b(uptime|how long.+running|how long.+been up)\b", t):
        return _get_uptime()

    # Who am I
    if re.search(r"\b(who are you|what are you|identify)\b", t):
        return "I'm Ghost — Pickle Rick running on a 2012 MacBook. The smartest Linux box in any dimension."

    return None


def _timer_callback(seconds, amount, unit):
    """Background timer that speaks when done."""
    time.sleep(seconds)
    try:
        from proactive import push_event
        push_event("alert", f"Timer done — {amount} {unit}s up.", priority=2, source="timer")
    except ImportError:
        pass


def _system_status():
    """Quick system health check."""
    import subprocess
    parts = []

    # CPU load
    try:
        with open("/proc/loadavg") as f:
            load = f.read().split()[0]
        parts.append(f"Load {load}")
    except Exception:
        pass

    # Memory
    try:
        with open("/proc/meminfo") as f:
            lines = f.readlines()
        mem = {}
        for line in lines[:5]:
            k, v = line.split(":")
            mem[k.strip()] = int(v.strip().split()[0])
        total = mem.get("MemTotal", 1)
        avail = mem.get("MemAvailable", 0)
        pct = round((1 - avail / total) * 100)
        parts.append(f"Memory {pct}%")
    except Exception:
        pass

    # Temp
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            temp = int(f.read().strip()) / 1000
        parts.append(f"{temp:.0f}°C")
    except Exception:
        pass

    if parts:
        return f"Systems nominal. {', '.join(parts)}."
    return "Systems running. Can't read detailed stats."


def _get_uptime():
    """Get system uptime."""
    try:
        with open("/proc/uptime") as f:
            seconds = float(f.read().split()[0])
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        if days > 0:
            return f"Been running {days} days and {hours} hours."
        return f"Been running {hours} hours."
    except Exception:
        return "Can't read uptime."
