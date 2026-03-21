#!/usr/bin/env python3
"""Ghost TUI Dashboard — always-on system monitor for tmux.

Textual-based dashboard showing system health, services, network,
observations feed, and proactive events. Auto-refreshes every 5s.

Run: python3 tui_dashboard.py
Requires: pip install textual
"""

import os
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import Footer, Header, Input, Static

# ---------------------------------------------------------------------------
# Optional module imports — graceful degradation
# ---------------------------------------------------------------------------
_agent_dir = Path(__file__).resolve().parent
if str(_agent_dir) not in sys.path:
    sys.path.insert(0, str(_agent_dir))

try:
    from network_monitor import NetworkMonitor
    _net_monitor = NetworkMonitor(check_interval=30)
    _net_monitor.start()
    _HAS_NETWORK = True
except Exception:
    _HAS_NETWORK = False
    _net_monitor = None

try:
    from watchdog import get_status as watchdog_get_status
    _HAS_WATCHDOG = True
except Exception:
    _HAS_WATCHDOG = False

try:
    from proactive import get_pending as proactive_get_pending
    _HAS_PROACTIVE = True
except Exception:
    _HAS_PROACTIVE = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
WATCHDOG_LOG = Path.home() / ".config" / "ghost" / "watchdog.log"
AGENT_LOG_DIR = Path("/home/atom/pickle-agent/logs")
EVENTS_DB = Path.home() / ".config" / "ghost" / "events.db"

# ---------------------------------------------------------------------------
# System readers (Linux /proc, /sys)
# ---------------------------------------------------------------------------

def read_cpu_percent() -> float:
    """Read CPU usage from /proc/stat (two-sample delta)."""
    try:
        def _read():
            with open("/proc/stat") as f:
                parts = f.readline().split()
            # user nice system idle iowait irq softirq steal
            vals = list(map(int, parts[1:8]))
            idle = vals[3]
            total = sum(vals)
            return idle, total

        idle1, total1 = _read()
        time.sleep(0.1)
        idle2, total2 = _read()

        d_idle = idle2 - idle1
        d_total = total2 - total1
        if d_total == 0:
            return 0.0
        return round((1.0 - d_idle / d_total) * 100, 1)
    except Exception:
        return -1.0


def read_memory_percent() -> tuple[float, str]:
    """Read memory usage from /proc/meminfo. Returns (percent, 'used/total')."""
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                key = parts[0].rstrip(":")
                if key in ("MemTotal", "MemAvailable"):
                    mem[key] = int(parts[1])  # kB
        total = mem.get("MemTotal", 1)
        avail = mem.get("MemAvailable", 0)
        used = total - avail
        pct = round(used / max(total, 1) * 100, 1)
        label = f"{used // 1024}M/{total // 1024}M"
        return pct, label
    except Exception:
        return -1.0, "N/A"


def read_disk_percent() -> tuple[float, str]:
    """Read root disk usage. Returns (percent, 'used/total')."""
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        pct = round(used / max(total, 1) * 100, 1)
        label = f"{used // (1024**3)}G/{total // (1024**3)}G"
        return pct, label
    except Exception:
        return -1.0, "N/A"


def read_temperature() -> float:
    """Read CPU temp from /sys/class/thermal (Linux)."""
    try:
        raw = Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()
        return round(int(raw) / 1000, 1)
    except Exception:
        return -1.0


def read_uptime() -> str:
    """Read system uptime from /proc/uptime."""
    try:
        raw = Path("/proc/uptime").read_text().strip()
        secs = int(float(raw.split()[0]))
        days, rem = divmod(secs, 86400)
        hours, rem = divmod(rem, 3600)
        mins, _ = divmod(rem, 60)
        if days > 0:
            return f"{days}d {hours}h {mins}m"
        return f"{hours}h {mins}m"
    except Exception:
        return "N/A"


def read_hostname() -> str:
    """Get hostname."""
    try:
        return socket.gethostname()
    except Exception:
        return "ghost"


def read_log_tail(n: int = 10) -> list[str]:
    """Read last n lines from watchdog log or agent log."""
    # Try watchdog log first
    log_path = WATCHDOG_LOG
    if not log_path.exists():
        # Fall back to latest agent log
        if AGENT_LOG_DIR.exists():
            logs = sorted(AGENT_LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            if logs:
                log_path = logs[0]

    if not log_path.exists():
        return ["[dim]No log files found[/dim]"]

    try:
        with open(log_path, "rb") as f:
            # Seek to end, read last chunk
            f.seek(0, 2)
            size = f.tell()
            read_size = min(size, 8192)
            f.seek(max(0, size - read_size))
            data = f.read().decode("utf-8", errors="replace")
        lines = data.strip().split("\n")
        return lines[-n:]
    except Exception as e:
        return [f"[red]Error reading log: {e}[/red]"]


def get_service_statuses() -> dict[str, dict]:
    """Get service statuses from watchdog or systemctl directly."""
    services = {
        "pickle-agent": {"healthy": None, "critical": True},
        "ollama": {"healthy": None, "critical": True},
        "ghost-dashboard": {"healthy": None, "critical": False},
    }

    # Try watchdog module first
    if _HAS_WATCHDOG:
        try:
            wd_status = watchdog_get_status()
            for name, info in wd_status.items():
                if name in services:
                    services[name]["healthy"] = info.get("healthy")
                    services[name]["failure_count"] = info.get("failure_count", 0)
            return services
        except Exception:
            pass

    # Fall back to systemctl
    for name in services:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", "--quiet", name],
                capture_output=True, timeout=5,
            )
            services[name]["healthy"] = r.returncode == 0
            services[name]["failure_count"] = 0
        except Exception:
            services[name]["healthy"] = None
            services[name]["failure_count"] = 0

    return services


def get_pending_events(limit: int = 5) -> list[dict]:
    """Get pending proactive events from events.db."""
    if _HAS_PROACTIVE:
        try:
            events = proactive_get_pending()
            return events[:limit]
        except Exception:
            pass

    # Direct DB fallback
    if not EVENTS_DB.exists():
        return []
    try:
        db = sqlite3.connect(str(EVENTS_DB), timeout=5)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """SELECT id, type, message, priority, source, created_at
               FROM events
               WHERE spoken_at IS NULL AND expired = 0
               ORDER BY priority ASC, id ASC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def detect_brain_status() -> str:
    """Detect brain routing: nexus, local (ollama), or offline."""
    # Check if nexus/atomd bridge is reachable
    try:
        import urllib.request
        req = urllib.request.Request(
            f"http://{os.environ.get('GHOST_MAC_HOST', '192.168.1.6')}:7421/ping",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                return "nexus"
    except Exception:
        pass

    # Check if Ollama is responding locally
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3):
            return "local"
    except Exception:
        pass

    return "offline"


def detect_presence() -> str:
    """Detect room presence — stub for BLE/motion sensor integration."""
    # Check for presence state file
    state_file = Path.home() / ".config" / "ghost" / "presence-state.json"
    if state_file.exists():
        try:
            import json
            data = json.loads(state_file.read_text())
            return data.get("state", "unknown")
        except Exception:
            pass
    return "unknown"


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def pct_color(value: float, warn: float = 70.0, crit: float = 90.0) -> str:
    """Return rich color tag based on percentage thresholds."""
    if value < 0:
        return "dim"
    if value >= crit:
        return "red bold"
    if value >= warn:
        return "yellow"
    return "green"


def temp_color(temp: float) -> str:
    """Return color for temperature."""
    if temp < 0:
        return "dim"
    if temp >= 85:
        return "red bold"
    if temp >= 70:
        return "yellow"
    return "green"


def bool_indicator(val: bool | None) -> str:
    """Green circle for True, red for False, dim for unknown."""
    if val is True:
        return "[green]●[/green]"
    if val is False:
        return "[red]●[/red]"
    return "[dim]●[/dim]"


def state_color(state: str) -> str:
    """Color for network/brain state labels."""
    mapping = {
        "full": "green",
        "lan-only": "yellow",
        "offline": "red",
        "nexus": "green",
        "local": "yellow",
        "occupied": "green",
        "empty": "dim",
        "unknown": "dim",
    }
    return mapping.get(state, "dim")


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class StatusBar(Static):
    """Top status bar — hostname, uptime, brain, presence, network, time."""

    def on_mount(self) -> None:
        self.update_content()

    def update_content(self) -> None:
        hostname = read_hostname()
        uptime = read_uptime()
        brain = detect_brain_status()
        presence = detect_presence()
        now = datetime.now().strftime("%H:%M:%S")

        # Network state
        net_state = "offline"
        if _HAS_NETWORK and _net_monitor:
            try:
                ns = _net_monitor.get_status()
                net_state = ns.get("state", "offline")
            except Exception:
                pass

        brain_c = state_color(brain)
        pres_c = state_color(presence)
        net_c = state_color(net_state)

        self.update(
            f"  [bold cyan]{hostname}[/bold cyan]"
            f"  [dim]up[/dim] [white]{uptime}[/white]"
            f"  [dim]brain[/dim] [{brain_c}]{brain}[/{brain_c}]"
            f"  [dim]presence[/dim] [{pres_c}]{presence}[/{pres_c}]"
            f"  [dim]net[/dim] [{net_c}]{net_state}[/{net_c}]"
            f"  [dim]{now}[/dim]"
        )


class SystemHealth(Static):
    """System health panel — CPU, Memory, Disk, Temperature."""

    def on_mount(self) -> None:
        self.update_content()

    def update_content(self) -> None:
        cpu = read_cpu_percent()
        mem_pct, mem_label = read_memory_percent()
        disk_pct, disk_label = read_disk_percent()
        temp = read_temperature()

        cpu_c = pct_color(cpu, 60, 85)
        mem_c = pct_color(mem_pct, 70, 90)
        disk_c = pct_color(disk_pct, 70, 90)
        tmp_c = temp_color(temp)

        cpu_str = f"[{cpu_c}]{cpu:5.1f}%[/{cpu_c}]" if cpu >= 0 else "[dim] N/A [/dim]"
        mem_str = f"[{mem_c}]{mem_pct:5.1f}%[/{mem_c}] [dim]{mem_label}[/dim]" if mem_pct >= 0 else "[dim] N/A [/dim]"
        disk_str = f"[{disk_c}]{disk_pct:5.1f}%[/{disk_c}] [dim]{disk_label}[/dim]" if disk_pct >= 0 else "[dim] N/A [/dim]"
        temp_str = f"[{tmp_c}]{temp:5.1f}C[/{tmp_c}]" if temp >= 0 else "[dim] N/A [/dim]"

        lines = [
            "[bold underline]System Health[/bold underline]",
            "",
            f"  CPU       {cpu_str}",
            f"  Memory    {mem_str}",
            f"  Disk      {disk_str}",
            f"  Temp      {temp_str}",
        ]

        # Load average (Linux)
        try:
            load = Path("/proc/loadavg").read_text().split()[:3]
            load_str = " ".join(load)
            load_1m = float(load[0])
            lc = "green" if load_1m < 2 else ("yellow" if load_1m < 4 else "red")
            lines.append(f"  Load      [{lc}]{load_str}[/{lc}]")
        except Exception:
            pass

        self.update("\n".join(lines))


class ObservationsFeed(Static):
    """Auto-scrolling log feed from watchdog or agent logs."""

    def on_mount(self) -> None:
        self.update_content()

    def update_content(self) -> None:
        lines = read_log_tail(12)
        formatted = ["[bold underline]Observations Feed[/bold underline]", ""]

        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Colorize log levels
            width = _get_usable_width(margin=16)
            if "[ERROR]" in line or "ERROR" in line:
                formatted.append(f"  [red]{_truncate(line, width)}[/red]")
            elif "[WARN]" in line or "WARN" in line:
                formatted.append(f"  [yellow]{_truncate(line, width)}[/yellow]")
            elif "[INFO]" in line:
                formatted.append(f"  [dim]{_truncate(line, width)}[/dim]")
            else:
                formatted.append(f"  {_truncate(line, width)}")

        if len(formatted) <= 2:
            formatted.append("  [dim]No observations yet[/dim]")

        self.update("\n".join(formatted))


class ServicesPanel(Static):
    """Service status panel — green/red indicators."""

    def on_mount(self) -> None:
        self.update_content()

    def update_content(self) -> None:
        services = get_service_statuses()
        lines = ["[bold underline]Services[/bold underline]", ""]

        for name, info in services.items():
            indicator = bool_indicator(info.get("healthy"))
            fc = info.get("failure_count", 0)
            suffix = ""
            if fc and fc > 0:
                suffix = f" [red]({fc}x fail)[/red]"
            crit = " [dim]*[/dim]" if info.get("critical") else ""
            lines.append(f"  {indicator} {name}{crit}{suffix}")

        lines.append("")
        lines.append("  [dim]* = critical service[/dim]")
        self.update("\n".join(lines))


class EventsPanel(Static):
    """Pending proactive events panel."""

    def on_mount(self) -> None:
        self.update_content()

    def update_content(self) -> None:
        events = get_pending_events(5)
        lines = ["[bold underline]Pending Events[/bold underline]", ""]

        if not events:
            lines.append("  [dim]No pending events[/dim]")
        else:
            for ev in events:
                pri = ev.get("priority", 5)
                etype = ev.get("type", "?")
                msg = ev.get("message", "")
                src = ev.get("source", "")

                # Color by priority
                if pri <= 2:
                    pc = "red bold"
                elif pri <= 4:
                    pc = "yellow"
                else:
                    pc = "cyan"

                evt_width = _get_usable_width(margin=30)
                lines.append(
                    f"  [{pc}]P{pri}[/{pc}] "
                    f"[dim]{etype}[/dim] "
                    f"{_truncate(msg, evt_width)}"
                )
                if src:
                    lines[-1] += f" [dim]({src})[/dim]"

        self.update("\n".join(lines))


class NetworkPanel(Static):
    """Network status panel."""

    def on_mount(self) -> None:
        self.update_content()

    def update_content(self) -> None:
        lines = ["[bold underline]Network[/bold underline]", ""]

        if _HAS_NETWORK and _net_monitor:
            try:
                ns = _net_monitor.get_status()
                state = ns.get("state", "offline")
                sc = state_color(state)
                mac = ns.get("mac_reachable", False)
                dns = ns.get("dns_ok", False)
                inet = ns.get("internet_ok", False)

                lines.append(f"  State     [{sc}]{state}[/{sc}]")
                lines.append(f"  Mac       {bool_indicator(mac)} {'reachable' if mac else 'unreachable'}")
                lines.append(f"  DNS       {bool_indicator(dns)}")
                lines.append(f"  Internet  {bool_indicator(inet)}")

                last_check = ns.get("last_check")
                if last_check:
                    ago = int(time.time() - last_check)
                    lines.append(f"  Checked   [dim]{ago}s ago[/dim]")

                last_change = ns.get("last_change")
                if last_change:
                    change_ago = int(time.time() - last_change)
                    if change_ago < 300:
                        lines.append(f"  Changed   [yellow]{change_ago}s ago[/yellow]")
            except Exception as e:
                lines.append(f"  [red]Error: {e}[/red]")
        else:
            lines.append("  [dim]Network monitor not available[/dim]")
            # Minimal fallback checks
            try:
                socket.getaddrinfo("google.com", 80, proto=socket.IPPROTO_TCP)
                lines.append(f"  DNS       {bool_indicator(True)}")
            except Exception:
                lines.append(f"  DNS       {bool_indicator(False)}")

        self.update("\n".join(lines))


class CommandBar(Input):
    """Bottom command input. Prefix with : for commands."""
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_usable_width(margin: int = 12) -> int:
    """Get usable terminal width minus borders/padding/margins."""
    import shutil
    cols = shutil.get_terminal_size((120, 40)).columns
    return max(cols - margin, 40)


def _truncate(text: str, maxlen: int = 0) -> str:
    """Truncate text with ellipsis if too long.

    If maxlen is 0, auto-detect from terminal width.
    """
    if maxlen <= 0:
        maxlen = _get_usable_width()
    if len(text) > maxlen:
        return text[:maxlen - 1] + "\u2026"
    return text


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

DASHBOARD_CSS = """
Screen {
    background: $surface;
}

#status-bar {
    dock: top;
    height: 1;
    background: #1a1a2e;
    color: #e0e0e0;
    padding: 0 1;
}

#main-area {
    height: 1fr;
}

#left-col {
    width: 60%;
    padding: 1;
}

#right-col {
    width: 40%;
    padding: 1;
    border-left: solid #333333;
}

#system-health {
    height: auto;
    min-height: 10;
    padding: 1;
    border: solid #333333;
    margin-bottom: 1;
}

#obs-feed {
    height: 1fr;
    padding: 1;
    border: solid #333333;
    overflow-y: auto;
}

#services-panel {
    height: auto;
    min-height: 9;
    padding: 1;
    border: solid #333333;
    margin-bottom: 1;
}

#events-panel {
    height: auto;
    min-height: 8;
    padding: 1;
    border: solid #333333;
    margin-bottom: 1;
}

#network-panel {
    height: 1fr;
    padding: 1;
    border: solid #333333;
}

#command-bar {
    dock: bottom;
    height: 1;
    background: #1a1a2e;
    border: none;
}

#command-bar:focus {
    border: none;
}

Static {
    color: #e0e0e0;
}
"""


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class GhostDashboard(App):
    """Ghost TUI Dashboard — always-on system monitor."""

    CSS = DASHBOARD_CSS
    TITLE = "Ghost Dashboard"
    SUB_TITLE = "Pickle Rick Agent Monitor"

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("tab", "focus_next", "Next Panel", show=True),
        Binding("shift+tab", "focus_previous", "Prev Panel", show=False),
        Binding("r", "force_refresh", "Refresh", show=True),
        Binding("colon", "focus_command", ": Command", show=True),
    ]

    _refresh_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield StatusBar(id="status-bar")
        with Horizontal(id="main-area"):
            with Vertical(id="left-col"):
                yield SystemHealth(id="system-health")
                yield ObservationsFeed(id="obs-feed")
            with Vertical(id="right-col"):
                yield ServicesPanel(id="services-panel")
                yield EventsPanel(id="events-panel")
                yield NetworkPanel(id="network-panel")
        yield CommandBar(
            placeholder=":command (quit, restart <svc>, status)",
            id="command-bar",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Start the auto-refresh timer."""
        self._refresh_timer = self.set_interval(5.0, self._refresh_all)

    def _refresh_all(self) -> None:
        """Refresh all panels."""
        try:
            self.query_one("#status-bar", StatusBar).update_content()
        except Exception:
            pass
        try:
            self.query_one("#system-health", SystemHealth).update_content()
        except Exception:
            pass
        try:
            self.query_one("#obs-feed", ObservationsFeed).update_content()
        except Exception:
            pass
        try:
            self.query_one("#services-panel", ServicesPanel).update_content()
        except Exception:
            pass
        try:
            self.query_one("#events-panel", EventsPanel).update_content()
        except Exception:
            pass
        try:
            self.query_one("#network-panel", NetworkPanel).update_content()
        except Exception:
            pass

    def action_force_refresh(self) -> None:
        """Manual refresh via 'r' key."""
        self._refresh_all()
        self.notify("Refreshed", severity="information", timeout=1)

    def action_focus_command(self) -> None:
        """Focus the command bar."""
        try:
            cmd = self.query_one("#command-bar", CommandBar)
            cmd.focus()
            cmd.value = ":"
            cmd.cursor_position = 1
        except Exception:
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle command input."""
        raw = event.value.strip()
        if raw.startswith(":"):
            raw = raw[1:].strip()

        cmd_bar = self.query_one("#command-bar", CommandBar)
        cmd_bar.value = ""

        if not raw:
            return

        parts = raw.split()
        command = parts[0].lower()

        if command in ("quit", "q", "exit"):
            self.exit()
        elif command == "status":
            self._show_status()
        elif command == "restart" and len(parts) > 1:
            self._restart_service(parts[1])
        elif command == "refresh":
            self._refresh_all()
            self.notify("Refreshed", severity="information", timeout=1)
        elif command == "help":
            self.notify(
                "Commands: :quit, :status, :restart <svc>, :refresh, :help",
                severity="information",
                timeout=5,
            )
        else:
            self.notify(f"Unknown command: {command}", severity="warning", timeout=3)

    def _show_status(self) -> None:
        """Show a quick status notification."""
        brain = detect_brain_status()
        uptime = read_uptime()
        self.notify(
            f"Brain: {brain} | Uptime: {uptime}",
            severity="information",
            timeout=5,
        )

    @work(thread=True)
    def _restart_service(self, service: str) -> None:
        """Restart a systemd service in background thread."""
        valid = {"pickle-agent", "ollama", "ghost-dashboard"}
        if service not in valid:
            self.call_from_thread(
                self.notify,
                f"Unknown service: {service}. Valid: {', '.join(sorted(valid))}",
                severity="warning",
                timeout=5,
            )
            return

        self.call_from_thread(
            self.notify,
            f"Restarting {service}...",
            severity="information",
            timeout=2,
        )

        try:
            result = subprocess.run(
                ["sudo", "systemctl", "restart", service],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                self.call_from_thread(
                    self.notify,
                    f"{service} restarted successfully",
                    severity="information",
                    timeout=3,
                )
            else:
                err = result.stderr.strip()[:100]
                self.call_from_thread(
                    self.notify,
                    f"Restart failed: {err}",
                    severity="error",
                    timeout=5,
                )
        except subprocess.TimeoutExpired:
            self.call_from_thread(
                self.notify, "Restart timed out", severity="error", timeout=5
            )
        except Exception as e:
            self.call_from_thread(
                self.notify, f"Restart error: {e}", severity="error", timeout=5
            )

        # Refresh after restart attempt
        time.sleep(2)
        self.call_from_thread(self._refresh_all)

    def on_unmount(self) -> None:
        """Cleanup on exit."""
        if _net_monitor:
            try:
                _net_monitor.stop()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = GhostDashboard()
    app.run()


if __name__ == "__main__":
    main()
