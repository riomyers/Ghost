#!/usr/bin/env python3
"""Webcam presence detector — CPU-friendly HOG person detection.

Captures frames from the default webcam at adaptive intervals and uses
OpenCV's HOG+SVM people detector to determine room occupancy. Binary
occupied/empty only — never saves images, never streams.

Runs in a background thread. Thread-safe.

Target hardware: 2012 MacBook, Intel CPU, 16GB RAM, no GPU.
"""

import threading
import time
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Adaptive polling intervals (seconds)
UNCERTAIN_INTERVAL = 5     # When occupancy is uncertain (startup/transition)
CONFIRMED_INTERVAL = 30    # When occupancy is stable (confirmed occupied/empty)
THROTTLED_INTERVAL = 60    # When CPU is warm (>80C)
DISABLED_TEMP = 90000      # millidegrees — disable detection above this

# HOG detection tuning
HOG_WIN_STRIDE = (8, 8)
HOG_PADDING = (4, 4)
HOG_SCALE = 1.05
CONFIDENCE_THRESHOLD = 0.3  # Min confidence to count as a person
CAPTURE_WIDTH = 320         # Downscale for speed
CAPTURE_HEIGHT = 240

# State stability — require N consecutive agreeing frames before changing state
STABILITY_COUNT = 2


class PresenceDetector:
    """Background thread that detects room occupancy via webcam."""

    def __init__(self):
        self._lock = threading.Lock()
        self._occupied = True  # Default: assume occupied (safe default)
        self._confidence = 0.0
        self._last_check = None
        self._last_change = None
        self._check_count = 0
        self._running = False
        self._thread = None
        self._webcam_available = True
        self._disabled_thermal = False

        # State stability tracking
        self._consecutive_same = 0
        self._pending_state = True  # What the last N frames say

        # HOG detector — initialized lazily
        self._hog = None

    def _init_hog(self):
        """Initialize HOG descriptor with default people detector."""
        try:
            import cv2
            self._hog = cv2.HOGDescriptor()
            self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
            return True
        except ImportError:
            logger.error('OpenCV (cv2) not installed — presence detection disabled')
            self._webcam_available = False
            return False
        except Exception as e:
            logger.error(f'Failed to initialize HOG detector: {e}')
            self._webcam_available = False
            return False

    def _read_cpu_temp(self) -> int:
        """Read CPU temp in millidegrees. Returns 0 if unavailable."""
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                return int(f.read().strip())
        except (FileNotFoundError, ValueError, PermissionError):
            return 0

    def _get_interval(self) -> float:
        """Determine polling interval based on thermal state and occupancy certainty."""
        temp = self._read_cpu_temp()

        if temp > DISABLED_TEMP:
            self._disabled_thermal = True
            return THROTTLED_INTERVAL

        self._disabled_thermal = False

        if temp > 80000:
            return THROTTLED_INTERVAL

        # Uncertain = just started or recent state change
        if self._check_count < 3 or self._consecutive_same < STABILITY_COUNT:
            return UNCERTAIN_INTERVAL

        return CONFIRMED_INTERVAL

    def _detect_frame(self) -> tuple:
        """Capture one frame and run HOG detection.

        Returns (detected: bool, confidence: float).
        Never saves the frame.
        """
        import cv2

        cap = None
        try:
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                logger.warning('Webcam not available')
                self._webcam_available = False
                return True, 0.0  # Assume occupied

            # Set resolution low for speed
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAPTURE_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_HEIGHT)

            ret, frame = cap.read()
            if not ret or frame is None:
                logger.warning('Failed to capture frame')
                return True, 0.0  # Assume occupied

            # Convert to grayscale for HOG (faster)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # Run HOG people detection
            rects, weights = self._hog.detectMultiScale(
                gray,
                winStride=HOG_WIN_STRIDE,
                padding=HOG_PADDING,
                scale=HOG_SCALE,
            )

            # Frame is NOT saved — just analyzed and discarded
            del frame, gray

            if len(rects) == 0:
                return False, 0.0

            # Use highest weight as confidence
            max_weight = float(max(weights))
            detected = max_weight >= CONFIDENCE_THRESHOLD
            return detected, max_weight

        except Exception as e:
            logger.error(f'Detection error: {e}')
            return True, 0.0  # Assume occupied on error
        finally:
            if cap is not None:
                cap.release()

    def _detection_loop(self):
        """Main detection loop — runs in background thread."""
        if not self._init_hog():
            # HOG init failed — stay in "assume occupied" mode forever
            logger.warning('HOG init failed, assuming occupied permanently')
            return

        while self._running:
            interval = self._get_interval()

            if self._disabled_thermal:
                # Too hot — skip detection, assume occupied
                with self._lock:
                    self._occupied = True
                    self._confidence = 0.0
                    self._last_check = datetime.now(timezone.utc).isoformat()
                    self._check_count += 1
                time.sleep(interval)
                continue

            if not self._webcam_available:
                # No webcam — assume occupied
                with self._lock:
                    self._occupied = True
                    self._confidence = 0.0
                    self._last_check = datetime.now(timezone.utc).isoformat()
                    self._check_count += 1
                time.sleep(interval)
                continue

            detected, confidence = self._detect_frame()
            now = datetime.now(timezone.utc).isoformat()

            with self._lock:
                self._last_check = now
                self._check_count += 1
                self._confidence = confidence

                # Stability tracking
                if detected == self._pending_state:
                    self._consecutive_same += 1
                else:
                    self._pending_state = detected
                    self._consecutive_same = 1

                # Only change state after STABILITY_COUNT consecutive agreeing frames
                if self._consecutive_same >= STABILITY_COUNT and self._occupied != self._pending_state:
                    self._occupied = self._pending_state
                    self._last_change = now

            time.sleep(interval)

    def start(self):
        """Start the background detection thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._detection_loop, daemon=True, name='presence-detector')
        self._thread.start()
        logger.info('Presence detector started')

    def stop(self):
        """Stop the background detection thread."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        logger.info('Presence detector stopped')

    def is_occupied(self) -> bool:
        """Return whether someone is detected in the room. Thread-safe."""
        with self._lock:
            return self._occupied

    def get_status(self) -> dict:
        """Return full status dict. Thread-safe.

        Keys: occupied, confidence, last_check, last_change, check_count
        """
        with self._lock:
            return {
                'occupied': self._occupied,
                'confidence': round(self._confidence, 3),
                'last_check': self._last_check,
                'last_change': self._last_change,
                'check_count': self._check_count,
                'webcam_available': self._webcam_available,
                'thermal_disabled': self._disabled_thermal,
            }


# Module-level singleton
_detector = PresenceDetector()


def start():
    """Start the global presence detector."""
    _detector.start()


def stop():
    """Stop the global presence detector."""
    _detector.stop()


def is_occupied() -> bool:
    """Check if someone is in the room."""
    return _detector.is_occupied()


def get_status() -> dict:
    """Get full presence status."""
    return _detector.get_status()


if __name__ == '__main__':
    import json
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    print('Starting presence detector (Ctrl+C to stop)...')
    start()
    try:
        while True:
            time.sleep(5)
            status = get_status()
            print(json.dumps(status, indent=2))
    except KeyboardInterrupt:
        stop()
        print('Stopped.')
