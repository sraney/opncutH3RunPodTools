"""Runs ON the pod (standard library only): runs shell commands that arrive as files.

For a pod with no public SSH port. mvpipe reaches such a pod's files through JupyterLab's HTTPS file API
(see jupyter.py), but Jupyter can't run a command without a websocket. So this small process, started by
the pod's boot script, watches a folder Jupyter can write to:

    <dir>/cmd/<id>.json    {"cmd": "<bash>", "timeout": <s>}  (Jupyter writes files atomically)
    <dir>/out/<id>.json    {"rc": <exit code>, "stdout": "...", "stderr": "..."}  written when the command ends
    <dir>/alive.json       {"pid", "t": <unix time>, "version": <hash of this file>} every couple of seconds,
                           so mvpipe can tell it runs, and restart it when this file has changed

Each command runs in its own thread, so a slow one never holds up a status check.
"""
import argparse
import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path

HEARTBEAT_S = 2
POLL_S = 0.2
SETTLE_S = 0.5  # a command file this new may still be in Jupyter's hands


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def run(req_path: Path, out_dir: Path) -> None:
    try:
        req = json.loads(req_path.read_text(encoding="utf-8"))
        try:
            r = subprocess.run(["bash", "-c", req["cmd"]], stdin=subprocess.DEVNULL, capture_output=True,
                               timeout=req.get("timeout") or None)
            res = {"rc": r.returncode, "stdout": r.stdout.decode("utf-8", "replace"),
                   "stderr": r.stderr.decode("utf-8", "replace")}
        except subprocess.TimeoutExpired:
            res = {"rc": None, "stdout": "", "stderr": "timed out", "timeout": True}
    except Exception as e:  # a request we can't read still gets an answer
        res = {"rc": 255, "stdout": "", "stderr": f"pod agent: {e}"}
    write_json(out_dir / f"{req_path.stem}.json", res)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    d = Path(ap.parse_args().dir)
    cmd_dir, out_dir = d / "cmd", d / "out"
    for p in (cmd_dir, out_dir):
        p.mkdir(parents=True, exist_ok=True)
    version = hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:12]
    print(f"[agent] pid {os.getpid()} watching {cmd_dir}", flush=True)
    beat = 0.0
    while True:
        now = time.time()
        if now - beat >= HEARTBEAT_S:
            write_json(d / "alive.json", {"pid": os.getpid(), "t": now, "version": version})
            beat = now
        for p in sorted(cmd_dir.glob("*.json")):
            if p.name.startswith("."):
                continue
            try:
                # Jupyter reads a file back right after writing it, to answer the upload; taking it away
                # before then makes the upload fail (HTTP 500) although the command arrived
                if now - p.stat().st_mtime < SETTLE_S:
                    continue
            except FileNotFoundError:
                continue
            taken = p.with_suffix(".taken")
            try:
                p.rename(taken)
            except FileNotFoundError:
                continue
            threading.Thread(target=lambda t=taken: (run(t, out_dir), t.unlink(missing_ok=True)),
                             daemon=True).start()
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
