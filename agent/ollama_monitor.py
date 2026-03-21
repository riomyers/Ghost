#!/usr/bin/env python3
"""Ollama health monitor — thread-safe singleton, checks every 60s.

Monitors local Ollama at localhost:11434. Checks model availability
(phi3:mini required). Logs state transitions only — no spam.
"""

import json
import logging
import threading
import time
import urllib.request

logger = logging.getLogger("ghost.ollama_monitor")

OLLAMA_URL = "http://localhost:11434"
CHECK_INTERVAL = 60  # seconds
REQUIRED_MODEL = "phi3:mini"


class _OllamaMonitor:
    """Thread-safe singleton that polls Ollama health."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._healthy = False
        self._models: list[str] = []
        self._last_check: float = 0.0
        self._last_error: str | None = None
        self._state_lock = threading.Lock()
        self._daemon: threading.Thread | None = None
        self._running = False

    def start(self):
        """Start the background health check daemon."""
        if self._running:
            return
        self._running = True
        self._daemon = threading.Thread(target=self._poll_loop, daemon=True)
        self._daemon.start()
        logger.info("Ollama monitor started (interval=%ds)", CHECK_INTERVAL)

    def stop(self):
        """Stop the background daemon."""
        self._running = False

    def _poll_loop(self):
        """Background loop — checks Ollama every CHECK_INTERVAL seconds."""
        # Do an immediate check on start
        self._check_once()
        while self._running:
            time.sleep(CHECK_INTERVAL)
            if not self._running:
                break
            self._check_once()

    def _check_once(self):
        """Single health check against Ollama API."""
        prev_healthy = self._healthy
        prev_models = self._models[:]

        try:
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/tags",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())

            models = []
            for m in data.get("models", []):
                name = m.get("name", "")
                if name:
                    models.append(name)

            with self._state_lock:
                self._models = sorted(models)
                self._healthy = True
                self._last_check = time.time()
                self._last_error = None

            # Log state transitions only
            if not prev_healthy:
                logger.info("Ollama is now HEALTHY (%d models available)", len(models))

            if prev_models != self._models:
                logger.info("Ollama models changed: %s", self._models)

            # Check for required model
            has_required = any(
                m == REQUIRED_MODEL or m.startswith(f"{REQUIRED_MODEL}:")
                for m in self._models
            )
            if not has_required:
                logger.warning(
                    "Required model '%s' not found in Ollama. "
                    "Available: %s. Will not auto-pull.",
                    REQUIRED_MODEL,
                    self._models,
                )

        except Exception as e:
            with self._state_lock:
                self._healthy = False
                self._last_check = time.time()
                self._last_error = str(e)

            if prev_healthy:
                logger.warning("Ollama is now UNHEALTHY: %s", e)

    def is_healthy(self) -> bool:
        """Whether Ollama is reachable and responding."""
        with self._state_lock:
            return self._healthy

    def available_models(self) -> list[str]:
        """List of model names currently loaded in Ollama."""
        with self._state_lock:
            return self._models[:]

    def get_status(self) -> dict:
        """Full status dict for dashboards/APIs."""
        with self._state_lock:
            has_required = any(
                m == REQUIRED_MODEL or m.startswith(f"{REQUIRED_MODEL}:")
                for m in self._models
            )
            return {
                "healthy": self._healthy,
                "models": self._models[:],
                "required_model": REQUIRED_MODEL,
                "required_model_available": has_required,
                "last_check": self._last_check,
                "last_error": self._last_error,
                "monitor_running": self._running,
            }


# Module-level singleton
_monitor = _OllamaMonitor()


def is_healthy() -> bool:
    """Check if Ollama is healthy."""
    return _monitor.is_healthy()


def available_models() -> list[str]:
    """Get list of available Ollama models."""
    return _monitor.available_models()


def get_status() -> dict:
    """Get full Ollama status."""
    return _monitor.get_status()


def start_monitor():
    """Start the background health check daemon."""
    _monitor.start()


def stop_monitor():
    """Stop the background daemon."""
    _monitor.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print("Starting Ollama monitor (Ctrl+C to stop)...")
    start_monitor()
    try:
        while True:
            time.sleep(5)
            status = get_status()
            print(f"Healthy: {status['healthy']} | Models: {status['models']} | "
                  f"Required ({REQUIRED_MODEL}): {status['required_model_available']}")
    except KeyboardInterrupt:
        stop_monitor()
        print("\nStopped.")
