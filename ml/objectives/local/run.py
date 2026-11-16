"""Start the quantized local model and its lightweight sidecar.

Windows/Vulkan (default): the model runs on Windows through PowerShell and the sidecar stays in
WSL. Linux (`"platform": "linux"` in the manifest): llama-server runs on the host CPU with the
parameters of the measured CPU probe, and the sidecar talks to it directly on loopback.

All paths and hashes are read from a local manifest. No application configuration is changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

# Same budget as the Windows monitor: model RSS up to 2 GiB and at least 2 GiB available.
MAX_MODEL_RSS_MIB = 2048
MIN_AVAILABLE_MIB = 2048


def windows_path(path: Path) -> str:
    return subprocess.check_output(
        ["wslpath", "-w", str(path.resolve())], text=True
    ).strip()


def linux_server_command(paths: dict[str, Path], port: int, key: Path) -> list[str]:
    """CPU only, loopback only, one slot: the configuration measured by probe.py."""
    return [
        str(paths["server"]),
        "-m",
        str(paths["model"]),
        "--lora",
        str(paths["adapter"]),
        "--device",
        "none",
        "-ngl",
        "0",
        "-c",
        "1024",
        "-b",
        "128",
        "-ub",
        "64",
        "-np",
        "1",
        "-t",
        "4",
        "-tb",
        "4",
        "--cache-ram",
        "0",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-webui",
        "--api-key-file",
        str(key.resolve()),
    ]


def available_mib() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024
    raise RuntimeError("MemAvailable not reported by /proc/meminfo")


def rss_mib(pid: int) -> float | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except FileNotFoundError:
        return None
    return None


def port_in_use(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0


class LinuxModel:
    """llama-server on the host CPU, in its own process group, under the memory/time budget."""

    def __init__(self, command: list[str], output: Path, duration: int, log) -> None:
        if port_in_use(int(command[command.index("--port") + 1])):
            raise RuntimeError("model port is already in use")
        if available_mib() < MIN_AVAILABLE_MIB + 128:
            raise RuntimeError("insufficient free memory before starting the model")
        self.output = output
        self.state: dict[str, object] = {
            "peakRssMiB": 0.0,
            "minAvailableMiB": available_mib(),
            "stopReason": None,
        }
        self.started = time.monotonic()
        # Prazo da sessão em tempo real: conta também o computador suspenso.
        self.deadline = time.clock_gettime(time.CLOCK_BOOTTIME) + duration
        self.process = subprocess.Popen(
            command, stdout=log, stderr=log, start_new_session=True
        )
        self.stopped = threading.Event()
        self.watcher = threading.Thread(target=self._monitor, daemon=True)
        self.watcher.start()

    def _monitor(self) -> None:
        while not self.stopped.wait(0.2) and self.process.poll() is None:
            rss = rss_mib(self.process.pid)
            available = available_mib()
            if rss is not None:
                self.state["peakRssMiB"] = max(float(self.state["peakRssMiB"]), rss)
            self.state["minAvailableMiB"] = min(
                float(self.state["minAvailableMiB"]), available
            )
            if (rss or 0) > MAX_MODEL_RSS_MIB or available < MIN_AVAILABLE_MIB:
                self.state["stopReason"] = "memory budget exceeded"
            elif time.clock_gettime(time.CLOCK_BOOTTIME) > self.deadline:
                self.state["stopReason"] = "duration"
            else:
                continue
            self.stop()
            return

    def wait_ready(self, port: int, timeout: float = 95) -> None:
        deadline = time.monotonic() + timeout
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while True:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"model startup failed; see {self.output / 'launcher.log'}"
                )
            try:
                with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as reply:
                    if json.load(reply).get("status") == "ok":
                        self.state["loadS"] = round(time.monotonic() - self.started, 3)
                        return
            except (OSError, ValueError):
                pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"model startup exceeded {timeout:.0f} seconds")
            time.sleep(0.2)

    def stop(self) -> None:
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()

    def close(self) -> None:
        self.stopped.set()
        self.stop()
        self.watcher.join(timeout=2)
        self.state["elapsedS"] = round(time.monotonic() - self.started, 3)
        (self.output / "resources.json").write_text(json.dumps(self.state, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--duration", type=int, default=1800, choices=range(1, 3601), metavar="SECONDS"
    )
    parser.add_argument("--model-port", type=int, default=18765)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--unix-socket", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    platform = manifest.get("platform", "windows")
    if platform not in ("windows", "linux"):
        parser.error(f"unsupported platform: {platform}")
    paths = {}
    for name in ("server", "model", "adapter"):
        path = (args.manifest.resolve().parent / manifest[name]["path"]).resolve()
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if hasattr(os, "posix_fadvise"):
                os.posix_fadvise(stream.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        if actual != manifest[name]["sha256"]:
            parser.error(f"hash mismatch: {name}")
        paths[name] = path
    args.output.mkdir(parents=True, exist_ok=False)
    key = args.output / "api-token"
    with open(
        key, "x", opener=lambda path, flags: os.open(path, flags, 0o600)
    ) as stream:
        stream.write(secrets.token_urlsafe(32))
    here = Path(__file__).resolve().parent
    if platform == "linux":
        run_linux(args, manifest, paths, key, here)
        return
    command = [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        windows_path(here / "benchmark_windows.ps1"),
    ]
    for name, path in paths.items():
        command += ["-" + name.capitalize(), windows_path(path)]
    command += [
        "-Output",
        windows_path(args.output),
        "-KeyFile",
        windows_path(key),
        "-ServeSeconds",
        str(args.duration),
        "-Port",
        str(args.model_port),
    ]
    native = None
    sidecar = None
    with (args.output / "launcher.log").open("w") as log:
        try:
            native = subprocess.Popen(command, stdout=log, stderr=log)
            deadline = time.monotonic() + 95
            while not (args.output / "ready.json").exists():
                if native.poll() is not None:
                    raise RuntimeError(
                        f"model startup failed; see {args.output / 'launcher.log'}"
                    )
                if time.monotonic() > deadline:
                    raise TimeoutError("model startup exceeded 95 seconds")
                time.sleep(0.2)
            sidecar = start_sidecar(args, manifest, key, here, log, windows_relay=True)
            while native.poll() is None:
                if sidecar.poll() is not None:
                    raise RuntimeError("sidecar stopped unexpectedly")
                time.sleep(0.5)
            if native.returncode:
                raise RuntimeError(
                    f"model monitor stopped the service; see {args.output / 'resources.json'}"
                )
        finally:
            stop_sidecar(sidecar)
            if native and native.poll() is None:
                pid_file = args.output / "server.pid"
                if pid_file.exists():
                    pid = int(pid_file.read_text(encoding="utf-8-sig").strip())
                    subprocess.run(
                        ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                        stdout=log,
                        stderr=log,
                        check=False,
                        timeout=10,
                    )
                try:
                    native.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    native.terminate()
                    native.wait(timeout=5)


def start_sidecar(args, manifest, key: Path, here: Path, log, *, windows_relay: bool):
    """Serve POST /interpret (HTTP on loopback and, optionally, a Unix socket) once healthy."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(here.parent) + os.pathsep + env.get("PYTHONPATH", "")
    command = [
        sys.executable,
        "-m",
        "riftlab_objectives.serve_llama",
        "--llama-url",
        f"http://127.0.0.1:{args.model_port}/completion",
        "--version",
        manifest["parserVersion"],
        "--port",
        str(args.port),
        "--api-key-file",
        str(key.resolve()),
    ]
    if windows_relay:
        command += ["--windows-relay", str(here / "windows_http_relay.ps1")]
    if args.unix_socket:
        command += ["--unix-socket", str(args.unix_socket.resolve())]
    sidecar = subprocess.Popen(command, env=env, stdout=log, stderr=log)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(50):
        if sidecar.poll() is not None:
            raise RuntimeError("sidecar exited during startup")
        try:
            with opener.open(
                f"http://127.0.0.1:{args.port}/health", timeout=1
            ) as response:
                if json.load(response).get("status") == "UP":
                    break
        except OSError:
            time.sleep(0.1)
    else:
        sidecar.kill()
        raise TimeoutError("sidecar startup timed out")
    print(f"Modelo local pronto: http://127.0.0.1:{args.port}/interpret", flush=True)
    print(
        f"Versão: {manifest['parserVersion']}; encerramento em {args.duration}s ou Ctrl+C.",
        flush=True,
    )
    return sidecar


def stop_sidecar(sidecar) -> None:
    if sidecar and sidecar.poll() is None:
        sidecar.send_signal(signal.SIGINT)
        try:
            sidecar.wait(timeout=6)
        except subprocess.TimeoutExpired:
            sidecar.kill()
            sidecar.wait()


def run_linux(args, manifest, paths: dict[str, Path], key: Path, here: Path) -> None:
    model = None
    sidecar = None
    with (args.output / "launcher.log").open("w") as log:
        try:
            model = LinuxModel(
                linux_server_command(paths, args.model_port, key),
                args.output,
                args.duration,
                log,
            )
            model.wait_ready(args.model_port)
            sidecar = start_sidecar(args, manifest, key, here, log, windows_relay=False)
            while model.process.poll() is None:
                if sidecar.poll() is not None:
                    raise RuntimeError("sidecar stopped unexpectedly")
                time.sleep(0.5)
            if model.state["stopReason"] != "duration":
                raise RuntimeError(
                    f"model stopped ({model.state['stopReason'] or 'exited'}); "
                    f"see {args.output / 'resources.json'}"
                )
        finally:
            stop_sidecar(sidecar)
            if model:
                model.close()


if __name__ == "__main__":

    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        main()
    except KeyboardInterrupt:
        pass
