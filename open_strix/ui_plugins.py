from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

UIState = Literal["starting", "running", "dead"]
MAX_LOG_BYTES = 10 * 1024 * 1024
MAX_RESTARTS_PER_WINDOW = 3
RESTART_WINDOW_SECONDS = 60.0
FAST_EXIT_SECONDS = 5.0
STOP_TIMEOUT_SECONDS = 5.0
RESTART_BASE_DELAY_SECONDS = 0.05
RESTART_MAX_DELAY_SECONDS = 0.5
_UI_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


@dataclass
class UIPlugin:
    name: str
    command: str
    env: dict[str, str]
    skill_dir: Path
    port: int
    process: asyncio.subprocess.Process | None = None
    attempts: int = 0
    state: UIState = "starting"
    started_at: float = 0.0
    restart_window_started: float | None = None
    monitor_task: asyncio.Task[Any] | None = field(default=None, repr=False)
    _stopping: bool = field(default=False, repr=False)


def _allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class UIPluginManager:
    def __init__(self, strix: Any) -> None:
        self.strix = strix
        self.plugins: list[UIPlugin] = []
        self._lock = asyncio.Lock()

    def discover(self) -> list[UIPlugin]:
        """Scan skill directories for ui.json files."""
        plugins: list[UIPlugin] = []
        skills_dir = self.strix.layout.skills_dir
        if not skills_dir.exists():
            self.plugins = []
            return plugins

        for ui_file in sorted(skills_dir.rglob("ui.json")):
            skill_dir = ui_file.parent
            try:
                raw = json.loads(ui_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                self.strix.log_event(
                    "ui_invalid_json",
                    path=str(ui_file),
                    error=str(exc),
                )
                continue

            if isinstance(raw, dict):
                if "uis" not in raw:
                    self.strix.log_event(
                        "ui_invalid_format",
                        path=str(ui_file),
                        error="expected a JSON object with 'uis' key",
                    )
                    continue
                entries = raw["uis"]
                if not isinstance(entries, list):
                    self.strix.log_event(
                        "ui_invalid_format",
                        path=str(ui_file),
                        error="'uis' key must be an array",
                    )
                    continue
            else:
                self.strix.log_event(
                    "ui_invalid_format",
                    path=str(ui_file),
                    error="expected a JSON object with 'uis' key",
                )
                continue

            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", "")).strip()
                command = str(entry.get("command", "")).strip()
                if not name or not command or not _UI_NAME_RE.fullmatch(name):
                    self.strix.log_event(
                        "ui_missing_fields",
                        path=str(ui_file),
                        entry=entry,
                    )
                    continue
                env = entry.get("env", {})
                if not isinstance(env, dict):
                    env = {}
                plugins.append(
                    UIPlugin(
                        name=name,
                        command=command,
                        env={str(k): str(v) for k, v in env.items()},
                        skill_dir=skill_dir,
                        port=_allocate_port(),
                    ),
                )

        self.plugins = plugins
        return plugins

    async def start_all(self) -> None:
        async with self._lock:
            for plugin in self.plugins:
                await self._start_plugin(plugin)

    async def stop_all(self) -> None:
        async with self._lock:
            await asyncio.gather(
                *(self._stop_plugin(plugin) for plugin in self.plugins),
                return_exceptions=True,
            )

    async def reload(self) -> list[UIPlugin]:
        await self.stop_all()
        plugins = self.discover()
        await self.start_all()
        return plugins

    def status(self) -> list[dict[str, Any]]:
        return [
            {
                "name": plugin.name,
                "status": plugin.state,
                "available": plugin.state == "running",
            }
            for plugin in self.plugins
        ]

    def find(self, name: str) -> UIPlugin | None:
        for plugin in self.plugins:
            if plugin.name == name:
                return plugin
        return None

    async def _start_plugin(self, plugin: UIPlugin) -> None:
        if plugin.state == "dead":
            return
        if plugin.process is not None and plugin.process.returncode is None:
            return

        plugin._stopping = False
        plugin.state = "starting"
        plugin.started_at = time.monotonic()
        log_path = self._log_path(plugin)
        self._rotate_log(log_path)
        env = {
            **os.environ,
            **plugin.env,
            "OPEN_STRIX_PORT": str(plugin.port),
            "STATE_DIR": str(plugin.skill_dir),
            "UI_NAME": plugin.name,
        }

        proc = await asyncio.create_subprocess_shell(
            plugin.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(plugin.skill_dir),
            env=env,
        )
        plugin.process = proc
        plugin.state = "running"
        self.strix.log_event(
            "ui_started",
            name=plugin.name,
            command=plugin.command,
            port=plugin.port,
            pid=proc.pid,
        )

        stdout_task = asyncio.create_task(self._pipe_output(plugin, proc.stdout, "stdout"))
        stderr_task = asyncio.create_task(self._pipe_output(plugin, proc.stderr, "stderr"))
        plugin.monitor_task = asyncio.create_task(
            self._monitor_plugin(plugin, stdout_task, stderr_task),
        )

    async def _monitor_plugin(
        self,
        plugin: UIPlugin,
        stdout_task: asyncio.Task[Any],
        stderr_task: asyncio.Task[Any],
    ) -> None:
        proc = plugin.process
        if proc is None:
            return
        rc = await proc.wait()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        if plugin._stopping:
            return

        uptime = time.monotonic() - plugin.started_at
        should_count_attempt = uptime < FAST_EXIT_SECONDS or rc != 0
        if should_count_attempt and not self._record_attempt(plugin):
            plugin.state = "dead"
            self.strix.log_event(
                "ui_dead",
                name=plugin.name,
                returncode=rc,
                attempts=plugin.attempts,
            )
            return

        if uptime > RESTART_WINDOW_SECONDS:
            plugin.attempts = 0
            plugin.restart_window_started = None

        plugin.process = None
        delay = min(
            RESTART_BASE_DELAY_SECONDS * (2.0 ** max(plugin.attempts - 1, 0)),
            RESTART_MAX_DELAY_SECONDS,
        )
        self.strix.log_event(
            "ui_exited",
            name=plugin.name,
            returncode=rc,
            uptime_seconds=round(uptime, 3),
            restart_delay_seconds=delay,
        )
        await asyncio.sleep(delay)
        await self._start_plugin(plugin)

    def _record_attempt(self, plugin: UIPlugin) -> bool:
        now = time.monotonic()
        if (
            plugin.restart_window_started is None
            or now - plugin.restart_window_started > RESTART_WINDOW_SECONDS
        ):
            plugin.restart_window_started = now
            plugin.attempts = 0

        if plugin.attempts >= MAX_RESTARTS_PER_WINDOW:
            return False
        plugin.attempts += 1
        return True

    async def _stop_plugin(self, plugin: UIPlugin) -> None:
        plugin._stopping = True
        proc = plugin.process
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=STOP_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        if plugin.monitor_task is not None and not plugin.monitor_task.done():
            plugin.monitor_task.cancel()
            try:
                await plugin.monitor_task
            except asyncio.CancelledError:
                pass
        plugin.process = None
        plugin.monitor_task = None
        if plugin.state != "dead":
            plugin.state = "starting"

    async def _pipe_output(
        self,
        plugin: UIPlugin,
        stream: asyncio.StreamReader | None,
        label: str,
    ) -> None:
        if stream is None:
            return
        log_path = self._log_path(plugin)
        with log_path.open("ab") as log_file:
            while True:
                chunk = await stream.read(64 * 1024)
                if not chunk:
                    break
                log_file.write(f"[{label}] ".encode("utf-8") + chunk)
                log_file.flush()

    def _log_path(self, plugin: UIPlugin) -> Path:
        log_dir = self.strix.layout.state_dir / "ui-plugins"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / f"{plugin.name}.log"

    def _rotate_log(self, path: Path) -> None:
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            path.write_bytes(b"")
