#!/usr/bin/env python3
"""Ghost Service Watchdog — monitors and auto-restarts critical services.

Tracks: ear daemon, Ollama, dashboard, presence detector.
Exponential backoff: 5s, 15s, 60s, 300s. Escalates after 3 failures in 10 min.
"""

import json
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

LOG_FILE = Path.home() / ".config" / "ghost" / "watchdog.log"
STATE_FILE = Path.home() / ".config" / "ghost" / "watchdog-state.json"
CHECK_INTERVAL = 30  # seconds between checks

# Services to monitor: name → check command, restart command
SERVICES = {
    "pickle-agent": {
        "check": ["systemctl", "is-active", "--quiet", "pickle-agent"],
        "restart": ["sudo", "systemctl", "restart", "pickle-agent"],
        "critical": True,
    },
    "ollama": {
        "check_url": "http://localhost:11434/api/tags",
        "restart": ["systemctl", "restart", "ollama"],
        "critical": True,
    },
    "ghost-dashboard": {
        "check": ["systemctl", "is-active", "--quiet", "ghost-dashboard"],
        "restart": ["sudo", "systemctl", "restart", "ghost-dashboard"],
        "critical": False,
    },
}

BACKOFF_STAGES = [5, 15, 60, 300]  # seconds
ESCALATION_THRESHOLD = 3  # failures in window
ESCALATION_WINDOW = 600  # 10 minutes


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def _check_url(url, timeout=5):
    """Check if a URL responds with 200."""
    import urllib.request
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False


def _check_process(check_cmd):
    """Check if a service is running via command."""
    try:
        result = subprocess.run(check_cmd, capture_output=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False


class ServiceState:
    def __init__(self, name):
        self.name = name
        self.healthy = True
        self.failure_count = 0
        self.recent_failures = []  # timestamps
        self.last_restart = 0.0
        self.backoff_index = 0
        self.escalated = False

    def record_failure(self):
        now = time.time()
        self.failure_count += 1
        self.healthy = False
        self.recent_failures.append(now)
        # Prune old failures outside window
        cutoff = now - ESCALATION_WINDOW
        self.recent_failures = [t for t in self.recent_failures if t > cutoff]

    def record_success(self):
        if not self.healthy:
            log(f"{self.name}: recovered")
        self.healthy = True
        self.failure_count = 0
        self.backoff_index = 0
        self.escalated = False

    def should_restart(self):
        if self.healthy:
            return False
        now = time.time()
        backoff = BACKOFF_STAGES[min(self.backoff_index, len(BACKOFF_STAGES) - 1)]
        return (now - self.last_restart) >= backoff

    def should_escalate(self):
        now = time.time()
        cutoff = now - ESCALATION_WINDOW
        recent = [t for t in self.recent_failures if t > cutoff]
        return len(recent) >= ESCALATION_THRESHOLD and not self.escalated

    def to_dict(self):
        return {
            "name": self.name,
            "healthy": self.healthy,
            "failure_count": self.failure_count,
            "backoff_index": self.backoff_index,
            "escalated": self.escalated,
            "last_restart": self.last_restart,
        }


class Watchdog:
    def __init__(self):
        self._states = {name: ServiceState(name) for name in SERVICES}
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

    def check_service(self, name):
        """Check a single service, restart if needed."""
        cfg = SERVICES[name]
        state = self._states[name]

        # Check health
        if "check_url" in cfg:
            alive = _check_url(cfg["check_url"])
        elif "check" in cfg:
            alive = _check_process(cfg["check"])
        else:
            return

        with self._lock:
            if alive:
                state.record_success()
                return

            state.record_failure()
            log(f"{name}: DOWN (failure #{state.failure_count})", "WARN")

            # Check escalation
            if state.should_escalate():
                state.escalated = True
                msg = f"{name} keeps dying — {ESCALATION_THRESHOLD} failures in {ESCALATION_WINDOW // 60} min"
                log(msg, "ERROR")
                # Push to proactive speech if available
                try:
                    from proactive import push_event
                    push_event("alert", msg, priority=2, source="watchdog")
                except ImportError:
                    pass

            # Restart with backoff
            if state.should_restart():
                restart_cmd = cfg.get("restart")
                if restart_cmd:
                    log(f"{name}: restarting (backoff stage {state.backoff_index})")
                    try:
                        subprocess.run(restart_cmd, capture_output=True, timeout=30)
                        state.last_restart = time.time()
                        state.backoff_index = min(
                            state.backoff_index + 1, len(BACKOFF_STAGES) - 1
                        )
                    except Exception as e:
                        log(f"{name}: restart failed: {e}", "ERROR")

    def check_all(self):
        """Run one check cycle across all services."""
        for name in SERVICES:
            try:
                self.check_service(name)
            except Exception as e:
                log(f"Watchdog error checking {name}: {e}", "ERROR")
        self._save_state()

    def get_status(self):
        """Get status of all services."""
        with self._lock:
            return {name: state.to_dict() for name, state in self._states.items()}

    def _save_state(self):
        """Persist state to disk."""
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "timestamp": datetime.now().isoformat(),
                "services": self.get_status(),
            }
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(STATE_FILE)
        except Exception:
            pass

    def run_forever(self):
        """Main loop — check all services every CHECK_INTERVAL seconds."""
        self._running = True
        log("Watchdog started")
        while self._running:
            self.check_all()
            time.sleep(CHECK_INTERVAL)
        log("Watchdog stopped")

    def start(self):
        """Start watchdog in background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.run_forever, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False


# Module-level singleton
_watchdog = Watchdog()
get_status = _watchdog.get_status
start = _watchdog.start
stop = _watchdog.stop


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        status = get_status()
        for name, info in status.items():
            icon = "✓" if info["healthy"] else "✗"
            print(f"  {icon} {name}: {'healthy' if info['healthy'] else f'FAILED ({info[\"failure_count\"]}x)'}")
    else:
        _watchdog.run_forever()
