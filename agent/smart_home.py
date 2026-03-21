#!/usr/bin/env python3
"""Ghost Smart Home Bridge — Home Assistant REST API client.

Discovers entities, toggles devices, reads sensors via HA's REST API.
Config via env vars: GHOST_HA_URL, GHOST_HA_TOKEN
"""

import json
import os
import re
import urllib.request
from datetime import datetime

HA_URL = os.environ.get("GHOST_HA_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.environ.get("GHOST_HA_TOKEN", "")

# Voice command patterns → HA service calls
VOICE_PATTERNS = [
    # Lights
    (r"turn (?:on|off) (?:the )?(.+?)(?:\s+light)?$", "_toggle_entity"),
    (r"(?:switch|flip) (?:the )?(.+?)(?:\s+light)?$", "_toggle_entity"),
    (r"lights? (?:on|off)$", "_all_lights"),
    # Switches
    (r"turn (?:on|off) (?:the )?(.+)$", "_toggle_entity"),
    # Temperature
    (r"(?:what(?:'s| is) the )?temperature", "_get_temperature"),
    (r"how (?:hot|cold|warm) is it", "_get_temperature"),
    # General state
    (r"(?:what(?:'s| is) the )?status of (.+)$", "_get_entity_state"),
]


def _ha_request(path, method="GET", data=None):
    """Make authenticated request to Home Assistant API."""
    if not HA_TOKEN:
        return None

    url = f"{HA_URL}/api/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }

    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def is_available():
    """Check if Home Assistant is reachable and configured."""
    if not HA_TOKEN:
        return False
    result = _ha_request("/")
    return result is not None and "message" in (result or {})


def get_entities(domain=None):
    """Get all entities, optionally filtered by domain (light, switch, sensor)."""
    states = _ha_request("states")
    if not states:
        return []
    if domain:
        return [s for s in states if s.get("entity_id", "").startswith(f"{domain}.")]
    return states


def get_entity_state(entity_id):
    """Get state of a specific entity."""
    result = _ha_request(f"states/{entity_id}")
    if not result:
        return None
    return {
        "entity_id": result.get("entity_id"),
        "state": result.get("state"),
        "name": result.get("attributes", {}).get("friendly_name", entity_id),
        "last_changed": result.get("last_changed"),
    }


def call_service(domain, service, entity_id=None, data=None):
    """Call a Home Assistant service."""
    payload = data or {}
    if entity_id:
        payload["entity_id"] = entity_id
    return _ha_request(f"services/{domain}/{service}", method="POST", data=payload)


def toggle(entity_id):
    """Toggle an entity (light, switch, etc.)."""
    domain = entity_id.split(".")[0] if "." in entity_id else "homeassistant"
    return call_service(domain, "toggle", entity_id)


def turn_on(entity_id):
    domain = entity_id.split(".")[0] if "." in entity_id else "homeassistant"
    return call_service(domain, "turn_on", entity_id)


def turn_off(entity_id):
    domain = entity_id.split(".")[0] if "." in entity_id else "homeassistant"
    return call_service(domain, "turn_off", entity_id)


def _find_entity(name):
    """Fuzzy-match an entity by friendly name."""
    name_lower = name.lower().strip()
    states = _ha_request("states") or []
    best = None
    best_score = 0
    for s in states:
        friendly = s.get("attributes", {}).get("friendly_name", "").lower()
        eid = s.get("entity_id", "").lower()
        # Exact match
        if friendly == name_lower or eid == name_lower:
            return s["entity_id"]
        # Partial match
        if name_lower in friendly or name_lower in eid:
            score = len(name_lower) / max(len(friendly), 1)
            if score > best_score:
                best_score = score
                best = s["entity_id"]
    return best


def _toggle_entity(match, original_text):
    """Handle 'turn on/off X' voice commands."""
    name = match.group(1).strip()
    entity_id = _find_entity(name)
    if not entity_id:
        return f"Can't find anything called '{name}' in Home Assistant."

    action = "on" if "on" in original_text.lower() else "off"
    if action == "on":
        result = turn_on(entity_id)
    else:
        result = turn_off(entity_id)

    if result is not None:
        return f"Done. {name} is {action}."
    return f"Failed to turn {action} {name}."


def _all_lights(match, original_text):
    """Handle 'lights on/off' commands."""
    action = "on" if "on" in original_text.lower() else "off"
    if action == "on":
        call_service("light", "turn_on", data={"entity_id": "all"})
    else:
        call_service("light", "turn_off", data={"entity_id": "all"})
    return f"All lights {action}."


def _get_temperature(match, original_text):
    """Get temperature from sensors."""
    sensors = get_entities("sensor")
    temp_sensors = [
        s for s in sensors
        if "temperature" in s.get("attributes", {}).get("device_class", "")
        or "temp" in s.get("entity_id", "").lower()
    ]
    if not temp_sensors:
        return "No temperature sensors found."
    s = temp_sensors[0]
    name = s.get("attributes", {}).get("friendly_name", "sensor")
    return f"It's {s['state']}° according to {name}."


def _get_entity_state(match, original_text):
    """Get status of a named entity."""
    name = match.group(1).strip()
    entity_id = _find_entity(name)
    if not entity_id:
        return f"Can't find '{name}' in Home Assistant."
    state = get_entity_state(entity_id)
    if state:
        return f"{state['name']} is {state['state']}."
    return f"Couldn't read {name}."


def handle_voice_command(text):
    """Try to match a voice command to a smart home action.

    Returns response string if matched, None if not a smart home command.
    """
    if not HA_TOKEN:
        return None

    text_clean = text.lower().strip()
    for pattern, handler_name in VOICE_PATTERNS:
        match = re.search(pattern, text_clean, re.IGNORECASE)
        if match:
            handler = globals().get(handler_name)
            if handler:
                try:
                    return handler(match, text)
                except Exception as e:
                    return f"Smart home error: {e}"
    return None


def get_status():
    """Get smart home status for dashboard."""
    return {
        "available": is_available(),
        "ha_url": HA_URL,
        "configured": bool(HA_TOKEN),
    }
