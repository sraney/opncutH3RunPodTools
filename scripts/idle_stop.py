#!/usr/bin/env python3
"""Stop (or terminate) this pod after it has had nothing to do for a while. Standard library only.

Started by start.sh when IDLE_STOP_MINUTES is set. The pod counts as busy while any ComfyUI worker
has a running or queued job, or while someone is logged in over SSH. A worker that does not answer
counts as idle, so a pod whose ComfyUI cannot start is also stopped.

  IDLE_STOP_MINUTES   minutes of idleness before acting (minimum 5)
  IDLE_ACTION         "stop" (default) or "terminate". RunPod cannot stop a pod that has a network
                      volume attached; such a pod can only be terminated. Terminating deletes the
                      container disk and keeps the volume.
  RUNPOD_POD_ID, RUNPOD_API_KEY   set by RunPod inside every pod
"""
import json, os, re, sys, time, urllib.request

API = "https://api.runpod.io/graphql"
POLL_S = 30
SSH_SESSION = re.compile(rb"^sshd(-session)?: \S+@")


def log(msg):
    print("[idle-stop %s] %s" % (time.strftime("%H:%M:%S", time.gmtime()), msg), flush=True)


def queue_busy(port):
    try:
        q = json.load(urllib.request.urlopen("http://127.0.0.1:%d/queue" % port, timeout=10))
        return bool(q.get("queue_running") or q.get("queue_pending"))
    except Exception:
        return False


def ssh_session_open():
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as f:
                if SSH_SESSION.match(f.read()):
                    return True
        except OSError:
            continue
    return False


def act(action, pod_id, key):
    if action == "terminate":
        query = 'mutation { podTerminate(input: {podId: "%s"}) }' % pod_id
    else:
        query = 'mutation { podStop(input: {podId: "%s"}) { id desiredStatus } }' % pod_id
    req = urllib.request.Request(API, data=json.dumps({"query": query}).encode(), headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + key})
    reply = json.load(urllib.request.urlopen(req, timeout=30))
    if reply.get("errors"):
        raise RuntimeError(reply["errors"])


def main():
    minutes = max(5.0, float(os.environ.get("IDLE_STOP_MINUTES", "0")))
    action = os.environ.get("IDLE_ACTION", "stop")
    pod_id, key = os.environ.get("RUNPOD_POD_ID"), os.environ.get("RUNPOD_API_KEY")
    base, workers = int(os.environ.get("COMFYUI_BASE_PORT", "8188")), int(os.environ.get("MVPIPE_WORKERS", "1"))
    if action not in ("stop", "terminate"):
        log("IDLE_ACTION must be stop or terminate, not %r: not watching" % action)
        return 1
    if not pod_id or not key:
        log("RUNPOD_POD_ID or RUNPOD_API_KEY is not set: not watching")
        return 1
    log("will %s pod %s after %g idle minutes" % (action, pod_id, minutes))
    idle_since = time.time()
    while True:
        time.sleep(POLL_S)
        if any(queue_busy(base + i) for i in range(workers)) or ssh_session_open():
            idle_since = time.time()
            continue
        if time.time() - idle_since < minutes * 60:
            continue
        log("idle for %g minutes: asking RunPod to %s this pod" % (minutes, action))
        try:
            act(action, pod_id, key)
        except Exception as err:
            log("%s failed: %s (trying again after another idle period)" % (action, err))
        idle_since = time.time()


if __name__ == "__main__":
    sys.exit(main())
