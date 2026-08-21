# RunPod ComfyUI worker/pod image for the SillyTavern image-gen fallback chain.
# cuda12.8.1 base: Blackwell + Ada GPU support.
FROM runpod/worker-comfyui:5.8.6-base-cuda12.8.1

# Pin ComfyUI to match the PC instance (flux2 node classes need >= 0.28;
# the base ships 0.25).
# Install into /opt/venv explicitly - that's the env ComfyUI runs from; a bare
# `uv pip install` targeted a different env, leaving the aux packages
# (comfyui-frontend-package etc.) at the base image's older pins.
RUN cd /comfyui && git fetch -q --tags origin && git checkout -q v0.28.0 \
 && /opt/venv/bin/python -m pip install --no-cache-dir -r requirements.txt

# Deterministic custom-node install. comfy-node-install (registry mode) claimed
# success for comfyui-gguf but the node never appeared in custom_nodes.
RUN git clone -q --depth 1 https://github.com/city96/ComfyUI-GGUF /comfyui/custom_nodes/ComfyUI-GGUF \
 && git clone -q --depth 1 https://github.com/Acly/comfyui-tooling-nodes /comfyui/custom_nodes/comfyui-tooling-nodes \
 && /opt/venv/bin/python -m pip install --no-cache-dir -r /comfyui/custom_nodes/ComfyUI-GGUF/requirements.txt

# C toolchain for torch.compile (inductor builds triton kernels via gcc at
# first sampling call; otherwise the TorchCompileModel node fails with
# "Failed to find C compiler").
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

# Boot-time model downloader (MODEL_MANIFEST / MODELS env), used by lazy pods.
COPY boot-models.py /boot-models.py

# In-pod model manager: lets the runpod-lazy proxy add/swap models on a RUNNING
# pod (port 8189) instead of recreating it. LRU-evicts old models when the disk
# is full. Started by the pod start command alongside ComfyUI.
COPY model-manager.py /model-manager.py
