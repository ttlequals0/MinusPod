# Documentation gap review

Date: 2026-10-16

The installation guide omitted Parakeet capacity guidance. The setup path also had a functional gap: both Compose files left transcription-backend variables commented out. Putting `WHISPER_BACKEND=parakeet` in `.env` did not pass that value into either container.

This review covers installation, transcription, deployment, environment defaults, storage references, documentation navigation, and instructions affected by the UI changes. It compares those instructions with the loader, configuration registry, Dockerfiles, Compose files, and current frontend. External provider tutorials and every API example were not executed.

## Findings and changes

| ID | Priority | Gap | Change or remaining work |
| --- | --- | --- | --- |
| DOC-01 | High | Installation lists Whisper VRAM estimates but no Parakeet requirements. | Added a CPU/CUDA table, fixed-model and quantization rules, exact download totals, application RAM baseline, and measurement commands. Measured peak RAM/VRAM remains open. |
| DOC-02 | High | `.env` backend/API examples do not reach containers because Compose forwarding is commented out. | Both Compose files now forward backend, model, device, compute type, language, and remote API settings. GPU and CPU defaults remain distinct. |
| DOC-03 | High | Copying `.env.example` forces `WHISPER_DEVICE=cuda` and `WHISPER_MODEL=small`, overriding the CPU Compose defaults. | The example leaves model/device overrides commented so the chosen Compose file supplies its defaults. |
| DOC-04 | Medium | Transcription names `.cache/hub` as the Parakeet cache, while the loader uses a dedicated plain directory. | Corrected the path and documented persistent storage, device-specific weights, and preparing a complete cache before offline use. |
| DOC-05 | Medium | The guide says Parakeet memory does not grow with episode length. `decode()` reads its input WAV before windowing. | Clarified model-call limits versus total RAM; documented the outer chunk size separately. |
| DOC-06 | Medium | The guide says the built-in backend does not run on Apple Silicon, despite a Linux arm64 CPU image and CPU provider path. | Distinguished CPU execution from unsupported Metal/CoreML selection. Physical Mac performance remains unverified. Marked the original design's platform/cache claims as historical. |
| DOC-07 | Medium | Deployment repeats resource estimates that disagree with Installation, including Whisper medium VRAM. Its OOM instructions imply every backend can shrink `WHISPER_MODEL`. | Installation is now the capacity reference. Deployment has backend-specific OOM actions and distinguishes saved settings from environment defaults. |
| DOC-08 | Medium | General language guidance implies that all backends can force English. | Documented that Parakeet detects language and the `en` setting filters scripts afterward. |
| DOC-09 | Medium | Installation's quick start launches only the NVIDIA configuration, despite CPU hosts being supported. | Added both launch commands. Updated the docs index and project README to expose sizing guidance. |
| DOC-10 | Medium | The UI guide describes typing any character or `/` to open search. That conflicts with the new shortcut behavior. | Documented Ctrl/Cmd+K, save ownership, registered draft protection, explicit confirmation, and update prompts. |
| DOC-11 | Medium | Link checks cannot detect incorrect examples, copied defaults, or unsupported performance claims. | The Compose render check validates the changed setup examples. Broader documentation tests and reproducible hardware benchmarks remain follow-up work. |

## Parakeet capacity evidence

MinusPod's [loader](../../../src/parakeet_transcriber.py) selects int8 files on CPU and unquantized files on CUDA. [Configuration](../../../src/config.py) fixes the model name and 30-second inference windows with 2-second overlap.

The [upstream ONNX files](https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx/tree/8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce) total:

| Selected files | Bytes | Decimal GB |
| --- | --- | --- |
| CPU int8 model, vocabulary, and configuration | 670,480,039 bytes | About 0.670 GB |
| CUDA model, external weights, vocabulary, and configuration | 2,549,805,955 bytes | About 2.550 GB |

The [evidence JSON](parakeet-model-evidence.json) records each file and the upstream revision. These are download/storage totals. They do not establish the runtime minimum for RAM or VRAM.

The installation table retains the project's existing 8 GB host RAM baseline. That is not a new Parakeet benchmark. This audit had a database backup without retained audio and did not run Parakeet on a GPU. A fixed VRAM recommendation would be unsupported by the evidence collected here.

## Open documentation work

| Work | Required evidence or acceptance |
| --- | --- |
| Publish measured Parakeet capacity | Record image and model revisions, CPU/GPU, device provider, episode duration, concurrency, cold-load peak, transcription peak, and total processing time. Publish CPU and CUDA results separately. |
| Validate existing RAM and Whisper estimates | Run the same measurement protocol for the documented model sizes. Replace duration-only allowances with measured scenarios; retain explicit workload limits. |
| Reproduce the Whisper/Parakeet speed comparison | The transcription guide reports a Tesla T4 comparison across three episodes but does not link reproducible benchmark artifacts. Add the episode/test identifiers, settings, image revision, timings, and detection comparison. |
| Validate alternate-platform setup | Run the CPU image on Linux arm64 and Docker on Apple Silicon. Treat GPU acceleration as a separate capability and record the provider actually used. |
| Exercise remote-server tutorials | Validate the pinned whisper.cpp/OpenVINO commands against the shipped examples, including networking and word timestamps. Configuration rendering alone does not establish successful transcription. |
| Prevent configuration-example drift | Add maintained checks for `.env.example`, Compose forwarding, and the settings registry. Update installation, runtime reference, and operational guidance together when a backend changes. |

## Validation

- Rendered both Compose files for `local`, `parakeet`, and `openai-api`: six configuration combinations passed. The check used isolated public marker values and no running containers.
- Confirmed forwarding for backend, compute type, language, remote base URL, key, and model. Confirmed GPU defaults of `small`/`cuda` and CPU defaults of `tiny`/`cpu`.
- The maintained-document Markdown link and anchor check passes.
- Checked cache selection, quantization, language behavior, and windowing against application source.
- Read upstream file metadata at the recorded revision; no model weights were downloaded for this review.

Updated reader guides: [Installation](../../installation.md), [Transcription](../../transcription.md), [Deployment](../../DEPLOYMENT.md), [Environment variables](../../environment-variables.md), and [Web interface](../../web-interface.md).
