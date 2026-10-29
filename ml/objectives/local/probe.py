"""Prepare a development sample or measure a CPU llama-server without importing torch.

Run from any directory. Predictions and resource logs belong in a private output directory.
The reserved test corpus is never read by this tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from riftlab_objectives.metrics import summarise
from riftlab_objectives.prompt import LABELS, messages, parse


def sample() -> list[dict]:
    path = Path(__file__).resolve().parents[1] / "corpus/val-v3.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    selected = []
    for label in LABELS:
        group = sorted(
            (row for row in rows if row["label"] == label),
            key=lambda row: hashlib.sha256(row["id"].encode()).hexdigest(),
        )[:8]
        selected.extend(group)
    for row in selected:
        row["prompt"] = (
            "".join(
                f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n"
                for m in messages(row["text"])
            )
            + "<|im_start|>assistant\n"
        )
    return selected


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def request(url: str, body: dict | None = None, timeout: float = 15) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
        req, timeout=timeout
    ) as response:
        return json.load(response)


def completion(url: str, row: dict, phase: str) -> dict:
    started = time.monotonic()
    body = request(
        url + "/completion",
        {
            "prompt": row["prompt"],
            "n_predict": 12,
            "temperature": 0,
            "seed": 0,
            "repeat_penalty": 1,
            "cache_prompt": False,
            "stream": False,
        },
    )
    raw = body["content"]
    pred, valid = parse(raw)
    return {key: value for key, value in row.items() if key != "prompt"} | {
        "raw": raw,
        "pred": pred,
        "jsonValid": valid,
        "latencyS": round(time.monotonic() - started, 4),
        "phase": phase,
        "timings": body.get("timings"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepare", type=Path, help="write the frozen development requests only"
    )
    parser.add_argument(
        "--windows-results", type=Path, help="summarise a Windows probe"
    )
    parser.add_argument("--server", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    rows = sample()
    if args.prepare:
        write(args.prepare, rows)
        return
    if args.windows_results:
        path = args.windows_results
        records = json.loads((path / "responses.json").read_text(encoding="utf-8-sig"))
        by_id = {row["id"]: row for row in rows}
        predictions = []
        for record in records:
            pred, valid = parse(record["raw"])
            predictions.append(
                by_id[record["id"]] | record | {"pred": pred, "jsonValid": valid}
            )
        summary = summarise(
            [row for row in predictions if row["phase"] == "sequential"]
        )
        write(path / "summary.json", summary)
        write(path / "predictions.json", predictions)
        print(json.dumps(summary, indent=2))
        return
    if not all((args.server, args.model, args.adapter, args.output)):
        parser.error("CPU probe requires --server, --model, --adapter, --output")
    args.output.mkdir(parents=True, exist_ok=False)
    stopped = threading.Event()
    state = {"peakRssMiB": 0, "minLinuxAvailableMiB": 1e9, "failure": None}
    predictions = []
    command = [
        str(args.server.resolve()),
        "-m",
        str(args.model.resolve()),
        "--lora",
        str(args.adapter.resolve()),
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
        str(args.port),
        "--no-webui",
    ]
    started = time.monotonic()
    with (args.output / "server.log").open("w") as log:
        process = subprocess.Popen(
            command, stdout=log, stderr=log, start_new_session=True
        )

        def monitor() -> None:
            while not stopped.wait(0.1) and process.poll() is None:
                try:
                    status = (
                        Path(f"/proc/{process.pid}/status").read_text().splitlines()
                    )
                    rss = (
                        int(
                            next(s.split()[1] for s in status if s.startswith("VmRSS:"))
                        )
                        / 1024
                    )
                    memory = Path("/proc/meminfo").read_text().splitlines()
                    available = (
                        int(
                            next(
                                s.split()[1]
                                for s in memory
                                if s.startswith("MemAvailable:")
                            )
                        )
                        / 1024
                    )
                except (FileNotFoundError, StopIteration):
                    continue
                state["peakRssMiB"] = max(state["peakRssMiB"], rss)
                state["minLinuxAvailableMiB"] = min(
                    state["minLinuxAvailableMiB"], available
                )
                if rss > 2048 or available < 1536 or time.monotonic() - started > 600:
                    state["failure"] = "resource/time budget exceeded"
                    os.killpg(process.pid, signal.SIGKILL)
                    return

        watcher = threading.Thread(target=monitor, daemon=True)
        watcher.start()
        try:
            url = f"http://127.0.0.1:{args.port}"
            while True:
                if process.poll() is not None:
                    raise RuntimeError("server exited before becoming ready")
                try:
                    if request(url + "/health", timeout=1).get("status") == "ok":
                        break
                except (OSError, urllib.error.URLError):
                    pass
                if time.monotonic() - started > 90:
                    raise TimeoutError("model load timeout")
                time.sleep(0.2)
            state["loadS"] = round(time.monotonic() - started, 4)
            for row in rows:
                predictions.append(completion(url, row, "sequential"))
            with ThreadPoolExecutor(max_workers=2) as pool:
                predictions.extend(
                    pool.map(lambda row: completion(url, row, "concurrent"), rows[:2])
                )
            write(args.output / "summary.json", summarise(predictions[: len(rows)]))
        except (OSError, ValueError, RuntimeError) as exc:
            state["failure"] = state["failure"] or str(exc)
        finally:
            stopped.set()
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            watcher.join(timeout=2)
            state["elapsedS"] = round(time.monotonic() - started, 4)
            write(args.output / "responses.json", predictions)
            write(args.output / "resources.json", state)
    print(json.dumps(state, indent=2))
    if state["failure"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
