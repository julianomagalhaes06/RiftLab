"""Start the quantized Windows/Vulkan model and its lightweight WSL sidecar.

All paths and hashes are read from a local manifest. No application configuration is changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def windows_path(path: Path) -> str:
    return subprocess.check_output(
        ["wslpath", "-w", str(path.resolve())], text=True
    ).strip()


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
            env = os.environ.copy()
            env["PYTHONPATH"] = (
                str(here.parent) + os.pathsep + env.get("PYTHONPATH", "")
            )
            sidecar_command = [
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
                "--windows-relay",
                str(here / "windows_http_relay.ps1"),
            ]
            if args.unix_socket:
                sidecar_command += ["--unix-socket", str(args.unix_socket.resolve())]
            sidecar = subprocess.Popen(
                sidecar_command,
                env=env,
                stdout=log,
                stderr=log,
            )
            for _ in range(50):
                if sidecar.poll() is not None:
                    raise RuntimeError("sidecar exited during startup")
                try:
                    opener = urllib.request.build_opener(
                        urllib.request.ProxyHandler({})
                    )
                    with opener.open(
                        f"http://127.0.0.1:{args.port}/health", timeout=1
                    ) as response:
                        if json.load(response).get("status") == "UP":
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                raise TimeoutError("sidecar startup timed out")
            print(
                f"Modelo local pronto: http://127.0.0.1:{args.port}/interpret",
                flush=True,
            )
            print(
                f"Versão: {manifest['parserVersion']}; encerramento em {args.duration}s ou Ctrl+C.",
                flush=True,
            )
            while native.poll() is None:
                if sidecar.poll() is not None:
                    raise RuntimeError("sidecar stopped unexpectedly")
                time.sleep(0.5)
            if native.returncode:
                raise RuntimeError(
                    f"model monitor stopped the service; see {args.output / 'resources.json'}"
                )
        finally:
            if sidecar and sidecar.poll() is None:
                sidecar.send_signal(signal.SIGINT)
                try:
                    sidecar.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    sidecar.kill()
                    sidecar.wait()
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


if __name__ == "__main__":

    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        main()
    except KeyboardInterrupt:
        pass
