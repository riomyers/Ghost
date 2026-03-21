#!/usr/bin/env python3
"""Presence-aware behavior — integrates webcam + sound for context-driven actions.

Combines presence.py (webcam occupancy) and sound_classifier.py (ambient audio)
to drive Ghost's behavior: when to speak, volume level, greeting events.

Runs in a background thread that monitors state transitions.
Thread-safe.
"""

import os
import sys
import threading
import time
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Timing
POLL_INTERVAL = 5           # seconds between state checks
EMPTY_THRESHOLD = 300        # 5 minutes empty before greeting on return
GREETING_COOLDOWN = 1800     # 30 minutes between greetings

# Volume mapping
VOLUME_NORMAL = 'normal'
VOLUME_QUIET = 'quiet'
VOLUME_SILENT = 'silent'


class PresenceBehavior:
    """Monitors presence + sound and drives behavioral decisions."""

    def __init__(self):
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

        # State
        self._occupied = True  # Start assuming occupied
        self._last_occupied_at = time.time()
        self._last_empty_at = None
        self._last_greeting_at = 0.0
        self._volume = VOLUME_NORMAL
        self._ambient = 'silence'

        # Modules — imported lazily
        self._presence = None
        self._sound = None

    def _init_modules(self):
        """Import and start presence + sound modules."""
        agent_dir = os.path.dirname(os.path.abspath(__file__))
        if agent_dir not in sys.path:
            sys.path.insert(0, agent_dir)

        try:
            import presence
            self._presence = presence
            presence.start()
            logger.info('Presence module loaded')
        except Exception as e:
            logger.error(f'Failed to load presence module: {e}')

        try:
            import sound_classifier
            self._sound = sound_classifier
            sound_classifier.start()
            logger.info('Sound classifier module loaded')
        except Exception as e:
            logger.error(f'Failed to load sound classifier module: {e}')

    def _push_greeting(self):
        """Push a greeting event to the proactive queue."""
        try:
            import proactive
            proactive.push_event(
                'greeting',
                'Welcome back. I noticed you were away. Anything you need?',
                priority=7,
                source='presence_behavior',
            )
            logger.info('Pushed greeting event')
        except Exception as e:
            logger.error(f'Failed to push greeting: {e}')

    def _push_departure(self):
        """Log room becoming empty."""
        logger.info('Room is now empty')

    def _determine_volume(self, occupied: bool, ambient: str) -> str:
        """Determine volume level based on presence and ambient sound.

        - Not occupied -> silent (no point speaking to empty room)
        - Occupied + silence/speech -> normal
        - Occupied + noise -> quiet (competing with background noise)
        - Occupied + alarm -> silent (don't add to chaos)
        """
        if not occupied:
            return VOLUME_SILENT

        if ambient == 'alarm':
            return VOLUME_SILENT

        if ambient == 'noise':
            return VOLUME_QUIET

        return VOLUME_NORMAL

    def _monitor_loop(self):
        """Main monitoring loop — checks state transitions."""
        self._init_modules()

        while self._running:
            now = time.time()

            # Read current sensor states
            occupied = True  # Default
            ambient = 'silence'

            if self._presence is not None:
                try:
                    occupied = self._presence.is_occupied()
                except Exception as e:
                    logger.error(f'Presence read failed: {e}')

            if self._sound is not None:
                try:
                    ambient_data = self._sound.get_ambient()
                    ambient = ambient_data.get('classification', 'silence')
                except Exception as e:
                    logger.error(f'Sound read failed: {e}')

            volume = self._determine_volume(occupied, ambient)

            with self._lock:
                was_occupied = self._occupied

                # State transition: empty -> occupied
                if occupied and not was_occupied:
                    empty_duration = now - (self._last_empty_at or now)
                    since_greeting = now - self._last_greeting_at

                    if empty_duration >= EMPTY_THRESHOLD and since_greeting >= GREETING_COOLDOWN:
                        self._push_greeting()
                        self._last_greeting_at = now

                    self._last_occupied_at = now

                # State transition: occupied -> empty
                if not occupied and was_occupied:
                    self._last_empty_at = now
                    self._push_departure()

                self._occupied = occupied
                self._ambient = ambient
                self._volume = volume

            time.sleep(POLL_INTERVAL)

    def start(self):
        """Start the behavior monitoring thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name='presence-behavior')
        self._thread.start()
        logger.info('Presence behavior monitor started')

    def stop(self):
        """Stop the behavior monitoring thread and child modules."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

        if self._presence is not None:
            try:
                self._presence.stop()
            except Exception:
                pass

        if self._sound is not None:
            try:
                self._sound.stop()
            except Exception:
                pass

        logger.info('Presence behavior monitor stopped')

    def should_speak(self) -> bool:
        """Return True if Ghost should speak (room is occupied). Thread-safe."""
        with self._lock:
            return self._occupied

    def get_volume_level(self) -> str:
        """Return recommended volume: 'normal', 'quiet', or 'silent'. Thread-safe."""
        with self._lock:
            return self._volume

    def get_context(self) -> dict:
        """Return full presence/behavior context. Thread-safe."""
        presence_status = {}
        sound_status = {}

        if self._presence is not None:
            try:
                presence_status = self._presence.get_status()
            except Exception:
                pass

        if self._sound is not None:
            try:
                sound_status = self._sound.get_ambient()
            except Exception:
                pass

        with self._lock:
            return {
                'occupied': self._occupied,
                'volume': self._volume,
                'ambient': self._ambient,
                'last_occupied_at': datetime.fromtimestamp(self._last_occupied_at, tz=timezone.utc).isoformat() if self._last_occupied_at else None,
                'last_empty_at': datetime.fromtimestamp(self._last_empty_at, tz=timezone.utc).isoformat() if self._last_empty_at else None,
                'last_greeting_at': datetime.fromtimestamp(self._last_greeting_at, tz=timezone.utc).isoformat() if self._last_greeting_at else None,
                'presence': presence_status,
                'sound': sound_status,
            }


# Module-level singleton
_behavior = PresenceBehavior()


def start():
    """Start presence-aware behavior monitoring (starts presence + sound too)."""
    _behavior.start()


def stop():
    """Stop all presence-aware monitoring."""
    _behavior.stop()


def should_speak() -> bool:
    """Should Ghost speak right now? True if room is occupied."""
    return _behavior.should_speak()


def get_volume_level() -> str:
    """Recommended volume: 'normal', 'quiet', or 'silent'."""
    return _behavior.get_volume_level()


def get_context() -> dict:
    """Full context dict with presence, sound, and behavior state."""
    return _behavior.get_context()


if __name__ == '__main__':
    import json
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    print('Starting presence behavior monitor (Ctrl+C to stop)...')
    start()
    try:
        while True:
            time.sleep(10)
            ctx = get_context()
            speak = should_speak()
            vol = get_volume_level()
            print(f'speak={speak} volume={vol}')
            print(json.dumps(ctx, indent=2, default=str))
    except KeyboardInterrupt:
        stop()
        print('Stopped.')
