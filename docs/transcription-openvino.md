# Intel GPU Transcription (OpenVINO)

[< Docs index](README.md) | [Project README](../README.md)

---

This guide offloads Whisper transcription to an Intel GPU using [OpenVINO Model Server](https://docs.openvino.ai/2025/model-server/ovms_what_is_openvino_model_server.html) (OVMS) running alongside the MinusPod CPU image. MinusPod talks to it as a remote OpenAI-compatible Whisper backend, so the heavy transcription work lands on the GPU instead of pinning every CPU core.

It applies to the **CPU image** on an Intel host with a capable integrated or discrete GPU (roughly 6th-gen / 2015 and newer; 11th-gen and newer perform best). It does not apply to the NVIDIA GPU image, which already uses local CUDA. There is no local OpenVINO device path inside MinusPod itself; OVMS runs as a separate container and MinusPod reaches it over HTTP.

This setup was contributed and tested on Intel hardware by @upmcplanetracker in [issue #364](https://github.com/ttlequals0/MinusPod/issues/364).

## How it fits together

- A sidecar container runs `openvino/model_server:latest-gpu` with the host Intel GPU passed through.
- OVMS serves Whisper at an OpenAI-compatible endpoint (`/v3/audio/transcriptions`).
- MinusPod's Transcription backend points at that endpoint. MinusPod appends `/audio/transcriptions` to the base URL you configure, so the base URL ends in `/v3`.
- A custom MediaPipe graph turns on word-level timestamps, which MinusPod needs for precise ad boundary detection.

## 1. Compose setup

Add a transcriber service next to your existing `minuspod` service in `docker-compose.cpu.yml` (the CPU compose file lives at [`docker-compose.cpu.yml`](../docker-compose.cpu.yml)):

```yaml
  minuspod-transcriber:
    image: openvino/model_server:latest-gpu
    container_name: minuspod-transcriber
    hostname: minuspod-transcriber
    # Share minuspod's network namespace so the two talk over loopback
    network_mode: "container:minuspod"
    devices:
      - /dev/dri:/dev/dri          # pass through the Intel GPU
    user: "0:0"                     # root: simplest path to the GPU and bind mount
    volumes:
      - ./openvino/models:/models:rw
    command:
      --config_path /models/config.json
      --rest_port 8001              # MinusPod already uses 8000 in this namespace
    environment:
      - TZ=America/New_York         # set your timezone
    restart: on-failure
    depends_on:
      - minuspod
```

**Container name.** `network_mode: "container:minuspod"` resolves by container name, and the shipped `docker-compose.cpu.yml` does not set one (Compose auto-generates something like `<project>-minuspod-1`). Add `container_name: minuspod` to the `minuspod` service so the reference resolves, or use one of the [other networking options](#other-networking-options) below.

**GPU access.** Running the sidecar as root (`user: "0:0"`) is the simplest way to give it both the GPU and the bind-mounted model directory, and it matches the setup tested in issue #364. To run non-root instead, drop `user` and add the host's `render` group so the container can reach `/dev/dri`:

```yaml
    group_add:
      - "993"   # GID from: getent group render | cut -d: -f3
```

If there is no `render` group, use the device's own group: `stat -c "%g" /dev/dri/render* | head -n1`. Going non-root also means the pulled model files must be readable by that user, so run the pull step below as the same user (`-u $(id -u):$(id -g)`) rather than as root.

**Startup order.** Because the transcriber joins MinusPod's network namespace, MinusPod has to come up first. `depends_on` handles ordering; `restart: on-failure` covers the case where the transcriber starts before MinusPod has bound its socket.

**Podman.** These image names assume Docker Hub. On Podman, prefix them with `docker.io/` (for example `docker.io/openvino/model_server:latest-gpu`). Rootless Podman also remaps UIDs and GIDs, so `user: "0:0"` is the reliable choice there.

### Other networking options

If a shared namespace does not fit your setup, drop `network_mode` and pick one:

1. **Shared bridge network.** Put both containers on the same Docker network and reach the transcriber by name: `http://minuspod-transcriber:8001/v3`.
2. **Published port.** Publish the transcriber's port (`ports: ["8001:8001"]`) and point MinusPod at the host's LAN address: `http://your-host-ip:8001/v3`. The transcriber can run on a separate machine from MinusPod this way.

## 2. Port conflict

Both MinusPod (gunicorn) and OVMS default to port 8000. In a shared network namespace they share one loopback interface, so OVMS would fail to bind with `Address already in use`. The `--rest_port 8001` flag above moves OVMS off 8000. If 8001 is also taken, pick another free port and use the same number in the MinusPod settings later.

## 3. Pull the model and enable word timestamps

OVMS can auto-download a model on startup, but that mode rewrites your config on every boot. To keep word timestamps locked on, pull the model once, then serve it through a fixed config that points at a custom graph.

### Step 1: pull the model once

This downloads the pre-quantized `whisper-large-v3-turbo` weights (~1.6 GB) from Hugging Face into `./openvino/models`, builds the repository layout, and exits without starting the server. With the GPU doing the work you are not limited to the tiny or small models.

```bash
docker run --rm -it \
  --user 0:0 \
  --device /dev/dri \
  -v ./openvino/models:/models:rw \
  openvino/model_server:latest-gpu \
  --pull \
  --source_model OpenVINO/whisper-large-v3-turbo-int8-ov \
  --model_repository_path /models \
  --task speech2text \
  --target_device GPU \
  --overwrite_models
```

### Step 2: write the server config

Create `./openvino/models/config.json`. The `name` is the friendly alias MinusPod will send as the model; `base_path` points at the custom graph directory:

```json
{
    "model_config_list": [
        {
            "config": {
                "name": "whisper-large-v3-turbo",
                "base_path": "whisper-word-ts"
            }
        }
    ]
}
```

### Step 3: write the custom graph

Create the graph directory and file:

```bash
mkdir -p ./openvino/models/whisper-word-ts
```

Put this in `./openvino/models/whisper-word-ts/graph.pbtxt`. The `enable_word_timestamps: true` line is the part that matters for MinusPod:

```protobuf
# Custom graph - word timestamps on for MinusPod ad-boundary cutting
input_stream: "HTTP_REQUEST_PAYLOAD:input"
output_stream: "HTTP_RESPONSE_PAYLOAD:output"
node {
    name: "whisper-large-v3-turbo"
    calculator: "S2tCalculator"
    input_side_packet: "STT_NODE_RESOURCES:s2t_servable"
    input_stream: "LOOPBACK:loopback"
    input_stream: "HTTP_REQUEST_PAYLOAD:input"
    output_stream: "LOOPBACK:loopback"
    output_stream: "HTTP_RESPONSE_PAYLOAD:output"
    input_stream_info: {
        tag_index: 'LOOPBACK:0',
        back_edge: true
    }
    node_options: {
        [type.googleapis.com / mediapipe.S2tCalculatorOptions]: {
            models_path: "/models/OpenVINO/whisper-large-v3-turbo-int8-ov"
            target_device: "GPU"
            plugin_config: '{"NUM_STREAMS":"1"}'
            enable_word_timestamps: true
        }
    }
    input_stream_handler {
        input_stream_handler: "SyncSetInputStreamHandler",
        options {
            [mediapipe.SyncSetInputStreamHandlerOptions.ext] {
                sync_set {
                    tag_index: "LOOPBACK:0"
                }
            }
        }
    }
}
```

`models_path` points at the weights `--pull` downloaded in Step 1. Because the server runs with `--config_path`, it reads this graph verbatim and skips the auto-download wrapper.

### Step 4: start the transcriber

```bash
docker compose -f docker-compose.cpu.yml up -d
```

Check `docker compose logs minuspod-transcriber`. A healthy start ends with the server reporting the `whisper-large-v3-turbo` model available and the REST endpoint listening on port 8001.

## 4. Point MinusPod at it

In the MinusPod web UI, open **Settings**, expand **Transcription**, and set:

1. **Backend:** Remote API (OpenAI-compatible).
2. **API Base URL:** `http://127.0.0.1:8001/v3` (for the shared-namespace setup above; use the bridge or host-IP address if you chose a different networking option).
3. **Model Name:** `whisper-large-v3-turbo` (the alias from `config.json`).
4. **Skip FLAC Compression:** ON. MinusPod compresses chunks to FLAC by default to keep uploads small for cloud APIs. OVMS expects raw WAV and has no FLAC decoder in its audio pipeline, so transcription fails unless this is on.

Before saving, click **Test connection** under the API Base URL field. It sends a one-second audio sample through the real transcription request, so it catches a wrong path or a wrong model alias without processing a full episode. This matters with OVMS in particular: the server responds on every path (a `curl` to the wrong URL still gets a `400 Bad Request`, which only proves the server is alive), but only the versioned base -- `/v3` here -- accepts transcription requests. The sample is uploaded in the format episodes will use, so if Skip FLAC Compression is still off the test fails the same way a real episode would -- flip the toggle and test again.

Save. The same fields can be set with `WHISPER_BACKEND`, `WHISPER_API_BASE_URL`, `WHISPER_API_MODEL`, and `SKIP_FLAC_COMPRESSION` on first boot, but the UI is the runtime source of truth.

## 5. Verify the GPU is doing the work

Install Intel's GPU monitor on the host:

```bash
# Debian / Ubuntu
sudo apt update && sudo apt install intel-gpu-tools

# Fedora / RHEL
sudo dnf install intel-gpu-tools
```

Run `sudo intel_gpu_top`, then reprocess an episode from the MinusPod dashboard. You will see a short `ffmpeg` CPU spike while the audio is sliced, then the `ovms` process drives the Render/3D engine toward 80-100%. That confirms the Intel GPU is running the Whisper passes with word timestamps, not the CPU.

---

[< Docs index](README.md) | [Project README](../README.md)
