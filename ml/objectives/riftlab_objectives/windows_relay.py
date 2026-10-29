"""A bounded JSON-lines transport to a Windows-loopback model from WSL."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Thread


class WindowsRelay:
    def __init__(self, url: str, script: Path, key_file: Path | None = None):
        def windows_path(path: Path) -> str:
            return subprocess.check_output(
                ["wslpath", "-w", str(path.resolve())], text=True
            ).strip()

        command = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            windows_path(script),
            "-CompletionUrl",
            url,
        ]
        if key_file:
            command += ["-KeyFile", windows_path(key_file)]
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.responses: Queue[str | None] = Queue(maxsize=1)

        def read() -> None:
            try:
                while line := self.process.stdout.readline(65538):
                    if len(line) > 65536:
                        self.process.terminate()
                        break
                    self.responses.put(line, timeout=15)
            except Full:
                self.process.terminate()
            finally:
                # Wake the in-flight caller when the native relay exits.
                try:
                    self.responses.put_nowait(None)
                except Full:
                    pass

        self.reader = Thread(target=read, daemon=True)
        self.reader.start()

    def __call__(self, payload: dict) -> dict:
        if self.process.poll() is not None:
            raise OSError("Windows relay is unavailable")
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        try:
            line = self.responses.get(timeout=14)
        except Empty as exc:
            self.close()
            raise TimeoutError("Windows relay timed out") from exc
        if line is None or len(line) > 65536:
            raise OSError("Windows relay returned no bounded response")
        return json.loads(line)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=3)
        self.reader.join(timeout=1)
        self.process.stdout.close()
