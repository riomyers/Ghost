#!/usr/bin/env python3
"""Ambient sound classifier — pure signal processing, no ML models.

Captures 1-second audio clips via arecord (ALSA), computes energy level
and zero-crossing rate, classifies into: silence, speech, noise, alarm.

If "alarm" detected for 3+ consecutive checks, pushes an alert event
to the proactive speech queue.

Runs in a background thread. Thread-safe.

Target hardware: 2012 MacBook, Intel CPU, no GPU.
"""

import os
import sys
import struct
import subprocess
import tempfile
import threading
import time
import logging
import math
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Audio capture settings
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit = 2 bytes
DURATION = 1  # seconds
CHECK_INTERVAL = 5  # seconds between checks

# Classification thresholds (tuned for 16-bit PCM, normalized 0-1)
# Energy: RMS of samples normalized to [0, 1]
ENERGY_SILENCE = 0.01    # Below this = silence
ENERGY_LOW = 0.03        # Below this = quiet (speech threshold)
ENERGY_HIGH = 0.15       # Above this = loud

# Zero-crossing rate: fraction of samples where sign changes
ZCR_SPEECH = 0.05        # Speech typically has higher ZCR
ZCR_LOW = 0.02           # Below this with high energy = noise/alarm

# Alarm detection
ALARM_CONSECUTIVE_THRESHOLD = 3  # 3 consecutive alarm classifications
ALARM_ENERGY_MIN = 0.10          # Minimum energy to consider alarm
ALARM_SUSTAIN_VARIANCE = 0.3     # Max variance ratio for sustained sound


class SoundClassifier:
    """Background thread that classifies ambient sound."""

    def __init__(self):
        self._lock = threading.Lock()
        self._classification = 'silence'
        self._energy_level = 0.0
        self._last_check = None
        self._running = False
        self._thread = None
        self._mic_available = True
        self._consecutive_alarm = 0

        # Rolling energy history for alarm sustain detection
        self._energy_history = []
        self._max_history = 5

    def _capture_audio(self) -> bytes:
        """Capture 1 second of audio via arecord. Returns raw PCM bytes."""
        tmp = None
        try:
            tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
            tmp.close()

            result = subprocess.run(
                [
                    'arecord',
                    '-q',               # quiet
                    '-f', 'S16_LE',     # 16-bit signed little-endian
                    '-r', str(SAMPLE_RATE),
                    '-c', str(CHANNELS),
                    '-d', str(DURATION),
                    '-t', 'wav',
                    tmp.name,
                ],
                capture_output=True,
                timeout=DURATION + 5,
            )

            if result.returncode != 0:
                stderr = result.stderr.decode(errors='replace').strip()
                if stderr:
                    logger.warning(f'arecord error: {stderr}')
                self._mic_available = False
                return b''

            self._mic_available = True

            # Read WAV file, skip 44-byte header
            with open(tmp.name, 'rb') as f:
                data = f.read()

            if len(data) <= 44:
                return b''

            return data[44:]

        except FileNotFoundError:
            logger.error('arecord not found — install alsa-utils')
            self._mic_available = False
            return b''
        except subprocess.TimeoutExpired:
            logger.warning('arecord timed out')
            return b''
        except Exception as e:
            logger.error(f'Audio capture failed: {e}')
            return b''
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass

    def _compute_features(self, pcm: bytes) -> tuple:
        """Compute energy (RMS) and zero-crossing rate from raw PCM.

        Returns (energy: float, zcr: float) both normalized to [0, 1].
        """
        if not pcm:
            return 0.0, 0.0

        n_samples = len(pcm) // SAMPLE_WIDTH
        if n_samples == 0:
            return 0.0, 0.0

        # Unpack 16-bit signed little-endian samples
        fmt = f'<{n_samples}h'
        try:
            samples = struct.unpack(fmt, pcm[:n_samples * SAMPLE_WIDTH])
        except struct.error:
            return 0.0, 0.0

        # RMS energy, normalized to [0, 1] range (max int16 = 32768)
        sum_sq = sum(s * s for s in samples)
        rms = math.sqrt(sum_sq / n_samples) / 32768.0

        # Zero-crossing rate
        crossings = 0
        for i in range(1, n_samples):
            if (samples[i] >= 0) != (samples[i - 1] >= 0):
                crossings += 1
        zcr = crossings / n_samples

        return rms, zcr

    def _classify(self, energy: float, zcr: float) -> str:
        """Classify based on energy and zero-crossing rate.

        Returns one of: silence, speech, noise, alarm
        """
        if energy < ENERGY_SILENCE:
            return 'silence'

        # Check for alarm: high sustained energy, low ZCR (tonal)
        if energy > ALARM_ENERGY_MIN and zcr < ZCR_LOW:
            # Check if energy is sustained (low variance in recent history)
            if len(self._energy_history) >= 2:
                avg = sum(self._energy_history) / len(self._energy_history)
                if avg > 0:
                    variance_ratio = sum(abs(e - avg) for e in self._energy_history) / (len(self._energy_history) * avg)
                    if variance_ratio < ALARM_SUSTAIN_VARIANCE:
                        return 'alarm'

        if energy < ENERGY_LOW:
            # Low energy — could be quiet speech or ambient
            if zcr > ZCR_SPEECH:
                return 'speech'
            return 'silence'

        if energy < ENERGY_HIGH:
            # Medium energy
            if zcr > ZCR_SPEECH:
                return 'speech'
            return 'noise'

        # High energy
        if zcr > ZCR_SPEECH:
            return 'speech'  # Loud speech
        return 'noise'  # Loud noise

    def _push_alarm_event(self):
        """Push an alert event to the proactive speech queue."""
        try:
            # Import here to avoid circular imports and keep module independent
            agent_dir = os.path.dirname(os.path.abspath(__file__))
            if agent_dir not in sys.path:
                sys.path.insert(0, agent_dir)
            import proactive
            proactive.push_event(
                'alarm',
                'I am detecting a sustained alarm sound in the room. You might want to check it out.',
                priority=2,
                source='sound_classifier',
            )
            logger.warning('Alarm detected — pushed alert event')
        except Exception as e:
            logger.error(f'Failed to push alarm event: {e}')

    def _classification_loop(self):
        """Main classification loop — runs in background thread."""
        while self._running:
            pcm = self._capture_audio()
            energy, zcr = self._compute_features(pcm)

            # Update energy history
            self._energy_history.append(energy)
            if len(self._energy_history) > self._max_history:
                self._energy_history.pop(0)

            classification = self._classify(energy, zcr)
            now = datetime.now(timezone.utc).isoformat()

            with self._lock:
                self._classification = classification
                self._energy_level = round(energy, 4)
                self._last_check = now

            # Alarm tracking
            if classification == 'alarm':
                self._consecutive_alarm += 1
                if self._consecutive_alarm >= ALARM_CONSECUTIVE_THRESHOLD:
                    self._push_alarm_event()
                    # Reset counter so we don't spam — will trigger again after
                    # another 3 consecutive alarms
                    self._consecutive_alarm = 0
            else:
                self._consecutive_alarm = 0

            time.sleep(CHECK_INTERVAL)

    def start(self):
        """Start the background classification thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._classification_loop, daemon=True, name='sound-classifier')
        self._thread.start()
        logger.info('Sound classifier started')

    def stop(self):
        """Stop the background classification thread."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        logger.info('Sound classifier stopped')

    def get_ambient(self) -> dict:
        """Return current ambient classification. Thread-safe.

        Keys: classification, energy_level, last_check
        """
        with self._lock:
            return {
                'classification': self._classification,
                'energy_level': self._energy_level,
                'last_check': self._last_check,
                'mic_available': self._mic_available,
            }


# Module-level singleton
_classifier = SoundClassifier()


def start():
    """Start the global sound classifier."""
    _classifier.start()


def stop():
    """Stop the global sound classifier."""
    _classifier.stop()


def get_ambient() -> dict:
    """Get current ambient sound classification."""
    return _classifier.get_ambient()


if __name__ == '__main__':
    import json
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    print('Starting sound classifier (Ctrl+C to stop)...')
    start()
    try:
        while True:
            time.sleep(5)
            ambient = get_ambient()
            print(json.dumps(ambient, indent=2))
    except KeyboardInterrupt:
        stop()
        print('Stopped.')
