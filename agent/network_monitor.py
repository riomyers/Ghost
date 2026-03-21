"""
T20: Network health monitor for Ghost agent.

Checks connectivity to Mac atomd bridge, DNS, and internet every 30 seconds.
Classifies state as "full", "lan-only", or "offline".
Thread-safe — other modules read status via get_status().
Logs state transitions only.
"""

import logging
import os
import socket
import threading
import time
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

MAC_HOST = os.environ.get("GHOST_MAC_HOST", "192.168.1.6")
MAC_PORT = 7421
CHECK_INTERVAL = 30


def _check_mac_reachable() -> bool:
    """Ping the Mac atomd bridge at MAC_HOST:7421/ping."""
    url = f"http://{MAC_HOST}:{MAC_PORT}/ping"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _check_dns() -> bool:
    """Resolve google.com via DNS."""
    try:
        socket.getaddrinfo("google.com", 80, proto=socket.IPPROTO_TCP)
        return True
    except (socket.gaierror, OSError):
        return False


def _check_internet() -> bool:
    """HEAD request to google.com."""
    try:
        req = urllib.request.Request("https://www.google.com", method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return 200 <= resp.status < 400
    except Exception:
        return False


def _classify(mac: bool, dns: bool, internet: bool) -> str:
    """Classify network state from check results."""
    if mac and dns and internet:
        return "full"
    if mac:
        return "lan-only"
    return "offline"


class NetworkMonitor:
    """Background network health monitor.

    Usage:
        monitor = NetworkMonitor()
        monitor.start()
        status = monitor.get_status()
        # ...
        monitor.stop()
    """

    def __init__(self, check_interval: int = CHECK_INTERVAL):
        self._interval = check_interval
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._status: dict = {
            "state": "offline",
            "mac_reachable": False,
            "dns_ok": False,
            "internet_ok": False,
            "last_check": None,
            "last_change": None,
        }

    def get_status(self) -> dict:
        """Return a snapshot of the current network status. Thread-safe."""
        with self._lock:
            return dict(self._status)

    def start(self) -> None:
        """Start the background monitor thread (daemon)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="network-monitor")
        self._thread.start()
        logger.info("Network monitor started (interval=%ds, mac=%s:%d)", self._interval, MAC_HOST, MAC_PORT)

    def stop(self) -> None:
        """Signal the monitor thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 5)
            self._thread = None
        logger.info("Network monitor stopped")

    def check_now(self) -> dict:
        """Run a check immediately (callable from any thread). Returns status dict."""
        mac = _check_mac_reachable()
        dns = _check_dns()
        internet = _check_internet()
        state = _classify(mac, dns, internet)
        now = time.time()

        with self._lock:
            prev_state = self._status["state"]
            self._status["mac_reachable"] = mac
            self._status["dns_ok"] = dns
            self._status["internet_ok"] = internet
            self._status["last_check"] = now
            self._status["state"] = state

            if state != prev_state:
                self._status["last_change"] = now
                logger.info(
                    "Network state: %s -> %s (mac=%s dns=%s internet=%s)",
                    prev_state, state, mac, dns, internet,
                )

            return dict(self._status)

    def _run(self) -> None:
        """Background loop: check connectivity every interval."""
        # Run first check immediately
        self.check_now()

        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._interval)
            if self._stop_event.is_set():
                break
            self.check_now()


# Module-level singleton for easy import
_monitor: NetworkMonitor | None = None
_monitor_lock = threading.Lock()


def get_monitor() -> NetworkMonitor:
    """Get or create the singleton NetworkMonitor instance."""
    global _monitor
    with _monitor_lock:
        if _monitor is None:
            _monitor = NetworkMonitor()
        return _monitor


def get_status() -> dict:
    """Convenience: get status from the singleton monitor."""
    return get_monitor().get_status()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    mon = NetworkMonitor(check_interval=10)
    mon.start()
    try:
        while True:
            time.sleep(5)
            s = mon.get_status()
            print(f"[{s['state']}] mac={s['mac_reachable']} dns={s['dns_ok']} inet={s['internet_ok']}")
    except KeyboardInterrupt:
        mon.stop()
