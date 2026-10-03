# OpenCut H3 RunPod tools: the mvpipe pod template

A Docker image that turns a rented RunPod GPU into an mvpipe generation host: ComfyUI at a pinned
release with the MiniMax H3 nodes (including *Add Guide*, which v0.30.0 lacks), the latent upscaler,
KJNodes, VideoHelperSuite, ffmpeg 7+, one ComfyUI worker per GPU, mvpipe's pod agent, an optional
idle watchdog, and a boot script that reports what is missing instead of failing silently.

This repo continues [mvpipe-pod](https://github.com/sraney/mvpipe-pod). What changed is listed under
[Changes from mvpipe-pod](#changes-from-mvpipe-pod).

What lives where:

| Place | Holds | Survives a pod restart? |
| --- | --- | --- |
| The image (`/opt`) | Python, torch, ComfyUI, custom nodes, ffmpeg, scripts | Rebuilt from the image every start |
| Network volume (`/workspace`) | `models/`, `input/`, `output/`, `user/`, `logs/`, `mvpipe/` | Yes, and it bills while it exists |

Pinned versions (all in the `Dockerfile` and `config/custom_nodes.txt`): ComfyUI v0.37.0, torch 2.11.0
for CUDA 12.8 (works on Blackwell and on any host driver 570+), Python 3.12 (Ubuntu 24.04).

Every image knows which commit it was built from. The boot log's first line prints it, it is in
`/opt/mvpipe/image.json`, and shells see it as `MVPIPE_IMAGE_VERSION`. A take re-rendered from its seed
only comes out the same on the same ComfyUI and node versions, so record this value with each render
and point the RunPod template at a commit tag (not `latest`) when you need old takes to reproduce.
The full package list of the image is in `/opt/mvpipe/pip-freeze.txt`.

## 1. Accounts you need

- **RunPod** with credit added.
- **A place to host the image**, either Docker Hub or GitHub (free). RunPod pulls the image from there.
- Optional: a **Hugging Face token** (only if a model download asks for a login).

## 2. Build and publish the image (once, and again whenever you change a pin)

### Option A: let GitHub build it (no Docker on your PC)

The workflow in `.github/workflows/build-pod-image.yml` builds the image whenever a file that goes
into it changes on `main`. You can also start it by hand: Actions tab, *build-pod-image*, **Run workflow**.

Each build publishes two tags:

- `ghcr.io/sraney/opncuth3runpodtools:latest`, which moves with `main`;
- `ghcr.io/sraney/opncuth3runpodtools:<commit sha>`, which never moves.

After the first build, open the package's settings on GitHub and set it to **Public** so RunPod can
pull it without a login.

### Option B: on your own PC (needs Docker; about 10 GB of disk, 20+ minutes)

```bash
docker build --build-arg IMAGE_VERSION=$(git rev-parse HEAD) -t YOURNAME/mvpipe-pod:latest .
docker login
docker push YOURNAME/mvpipe-pod:latest
```

On Windows, install Docker Desktop first and run these in PowerShell. Replace `YOURNAME` with your
Docker Hub user name. The push is large (several GB), so use a wired connection.

## 3. Create the network volume

RunPod console, *Storage*, *New Network Volume*.

- **Datacenter**: choose one that offers your GPU (RTX PRO 6000 or similar, 96 GB). The volume
  can only attach to pods in the same datacenter, so check GPU availability first.
- **Size**: 100 GB minimum (the H3 set is about 45 GB, plus outputs and LoRAs). 150 GB leaves room.

## 4. Create the pod template

RunPod console, *Templates*, *New Template*. Field names can differ slightly from this list.

| Field | Value |
| --- | --- |
| Name | `mvpipe-comfyui` |
| Container image | `ghcr.io/sraney/opncuth3runpodtools:latest`, or the same name with a commit sha to stay on one build |
| Container disk | 60 GB |
| Volume disk | 0 (you attach the network volume when deploying) |
| Volume mount path | `/workspace` |
| Expose HTTP ports | `8188, 8189, 8190, 8191, 8888` (8189+ are the extra GPUs' workers) |
| Expose TCP ports | `22` |
| Container start command | leave empty (the image starts `/start.sh`) |

Environment variables:

| Name | Value | Notes |
| --- | --- | --- |
| `PUBLIC_KEY` | your SSH public key | Optional. Without any key (here or in the volume file below) sshd is not started. Create one with `ssh-keygen -t ed25519`, then paste the contents of `~/.ssh/id_ed25519.pub`. For several keys, put one per line (the template's Raw editor accepts this). |
| `ENABLE_JUPYTER` | `1` | Optional. Lets you browse and upload files in a browser. |
| `JUPYTER_TOKEN` | a long random string | Used only when `ENABLE_JUPYTER=1`. If you leave it out, one is generated and saved to `/workspace/logs/jupyter_token.txt`. |
| `HF_TOKEN` | your Hugging Face token | Optional. Only for model downloads that need a login. |
| `COMFYUI_EXTRA_ARGS` | e.g. `--fast` | Optional extra ComfyUI flags. |
| `COMFYUI_LISTEN` | `127.0.0.1` | Optional. ComfyUI has no login, and by default anyone with the pod's `-8188` address can use it. With this value it is reachable only from the pod itself and through an SSH tunnel. Leave it out if mvpipe talks to the `proxy.runpod.net` address. |
| `IDLE_STOP_MINUTES` | e.g. `30` | Optional. Stops the pod after this many minutes with no job running or queued and nobody logged in over SSH (minimum 5). Off when left out. |
| `IDLE_ACTION` | `stop` or `terminate` | Optional, default `stop`. RunPod cannot stop a pod that has a network volume attached, only terminate it. Terminating deletes the container disk and keeps the volume, which holds everything this image stores. |
| `ENABLE_POD_AGENT` | `0` | Optional. Turns off mvpipe's pod agent, which is on by default (see below). |
| `MVPIPE_AGENT_DIR` | a folder on the volume | Optional. Where the pod agent looks for commands. Default: `/workspace/mvpipe/agent/<pod id>`. |

### The pod agent

mvpipe reaches a pod that has no public SSH port through Jupyter, which can move files but cannot run
a command. The pod agent closes that gap: it runs the commands mvpipe writes into
`/workspace/mvpipe/agent/<pod id>/cmd/` and writes each result into `out/`. The boot script starts it
and restarts it if it stops. If mvpipe has put its own copy at `/workspace/mvpipe/agent/pod_agent.py`,
that copy is used instead of the image's.

Anyone who can write to that folder can run commands as root on the pod, and the Jupyter token gives
that access. Treat `JUPYTER_TOKEN` like a root password.

### More than one SSH key (optional)

If a second machine also needs to log in, add its public key once to a file on the network volume:

```bash
mkdir -p /workspace/mvpipe && echo 'ssh-ed25519 AAAA... name@machine' >> /workspace/mvpipe/authorized_keys
```

Every pod that mounts the volume trusts the keys in that file at boot (one key per line, lines starting with `#` are ignored). Keys are only ever added, never removed; delete a line from the file and from the pod's `/root/.ssh/authorized_keys` to revoke one.

Remote commands also find the image's Python: `ssh <pod> 'python --version'` works the same as an interactive session.

## 5. First start

1. *Pods*, **Deploy**, pick the GPU, tick **Network Volume** and choose yours, pick `mvpipe-comfyui`.
2. Open the pod's **Logs**. You should see `[mvpipe-boot]` lines: sshd (or "not started"), a
   `[models]` report, then `worker 0 starting on port 8188`.
3. The first boot has no models yet, so the report lists them as MISSING. That is expected.

## 6. Put the models on the volume

Open a terminal on the pod (the console's *Connect* tab, or Jupyter's terminal) and run:

```bash
python3 /opt/mvpipe/scripts/verify_models.py --download
```

It downloads the confirmed files (about 44 GB) into `/workspace/models/...`, resumes if interrupted,
checks each download's SHA-256 when the manifest has one, and prints the report again. Tip: download on a cheap pod instead of the GPU pod to save money. Any
pod attached to the same volume works, since the script needs only Python.

Two files are marked *source not confirmed*, so they are skipped by default:

- the **latent upscaler**: the spec's file name was not found online; the published file is
  `minimax_h3_latent_upscaler_3d_conv_v1_fp16.safetensors` (probably the same model);
- the **lightx2v LoRA**: no file with the spec's name was found; the closest is the 1.38 GB
  `minimax_h3_fl2v_turbo_4step_v0.1.safetensors`.

If you have the originals on your current pod, copy them to `/workspace/models/latent_upscale_models/`
and `/workspace/models/loras/`. Otherwise add `--unconfirmed` to fetch the closest matches and test them.

Then restart the pod's workers (or stop and start the pod) so ComfyUI sees the new files.

### Pinning the model files

The boot check looks at file names and sizes only. To pin the exact files, run this once on a pod
whose models are known to work (it reads every file, so it takes several minutes):

```bash
python3 /opt/mvpipe/scripts/verify_models.py --hash
```

It prints the SHA-256 of every file that has none in the manifest. Add each one to its entry in
`config/models.json` as `"sha256": "<value>"` and rebuild. From then on `--hash` reports a file that
differs as `BADHASH`, and a download that differs is reported the same way.

## 7. Check it works

```bash
python3 /opt/mvpipe/scripts/check_nodes.py --wait 300
```

Every line should say `OK`. A `MISSING` on `MiniMaxH3AddGuide` means the ComfyUI version is too old.
The pod's address for mvpipe is `https://<POD_ID>-8188.proxy.runpod.net` (the pod ID is on the pod's
page); the second GPU is the same address with `-8189`.

## 8. Day to day

- **Stop the pod** when you are not generating. You pay for the GPU while it runs, and for the
  volume while it exists. Stopping the pod keeps the volume.
- The first job after a restart is slow (7-15 minutes of model loading). That is normal.
- Logs: `/workspace/logs/comfyui-gpu0.log` (and `-gpu1`, ...), `model_check.json`, `pod_agent.log`, `idle_stop.log`.
- To update a pin: edit `Dockerfile` or `config/custom_nodes.txt` and push to `main`. GitHub rebuilds the image; redeploy the pod to use it.
- To add a custom node: add one line (`<git url> <full commit sha>`) to `config/custom_nodes.txt`. The build keeps torch at the pinned version and fails if a node's requirements cannot live with it.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| Pod starts but `8188` link gives a 502 | ComfyUI is still starting; read `comfyui-gpu0.log`. Wait a few minutes. |
| Only one ComfyUI port works on a 2-GPU pod | Ports `8189+` are missing from the template's HTTP port list. |
| `CUDA error: no kernel image` | Image built for the wrong CUDA; keep `cu128` (or newer) torch for Blackwell. |
| Worker restarts every 5 s | Read the end of its log; usually a bad custom node or a missing model. |
| Out-of-memory loading the model | The GPU is below 96 GB; this is the open VRAM question in the spec. |

## Changes from mvpipe-pod

- **Pod agent**: it is part of the image and starts at boot. The old boot script looked for it in a folder where mvpipe never put it, so it did not start.
- **Idle watchdog**: new, optional (`IDLE_STOP_MINUTES`).
- **Image version**: each image records the commit it was built from.
- **torch stays pinned**: later installs are held to the torch build installed first, and the build fails if torch changed. Before, that check could not fail.
- **Secrets**: `RUNPOD_API_KEY` and other keys and tokens are no longer copied into `/etc/rp_environment`, and the Jupyter token no longer shows in the process list.
- **`COMFYUI_LISTEN`**: new, optional, to keep ComfyUI off the public HTTP ports.
- **Model checksums**: `verify_models.py --hash`, and an optional `sha256` per file in `config/models.json`.
- **Build**: GitHub builds on every change to `main` and tags each image with its commit.

## Not verified yet

- This image has not been built yet. The boot script, pod agent, idle watchdog and model check were tested in a small stand-in container without ComfyUI or a GPU.
- A real generation on a GPU.
- The idle watchdog's call to RunPod. It was not run against a real pod, so test it once with a short `IDLE_STOP_MINUTES` before relying on it.
- The pod agent's default folder, `/workspace/mvpipe/agent/<pod id>`. It matches what was on the volume of the earlier pod; set `MVPIPE_AGENT_DIR` if mvpipe expects another one.
- `config/models.json` has no checksums yet (see *Pinning the model files*).
- The exact RunPod console field names, which change over time.
- Whether the two "source not confirmed" files are the ones your workflow expects.
