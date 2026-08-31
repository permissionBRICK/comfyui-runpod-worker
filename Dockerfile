# RunPod ComfyUI worker/pod image for the SillyTavern image-gen fallback chain.
# Keep this aligned with the local 3090 installation: CUDA 13, PyTorch 2.13 and
# ComfyUI 0.33.1. CUDA 13 enables comfy-kitchen's optimized CUDA backend; the
# previous CUDA 12.8 image fell back to substantially slower generic kernels.
FROM pytorch/pytorch:2.13.0-cuda13.0-cudnn9-runtime

ARG COMFYUI_VERSION=v0.33.1

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      build-essential git python3-dev python3-venv \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv --system-site-packages /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

RUN git clone -q --branch "${COMFYUI_VERSION}" --depth 1 \
      https://github.com/comfyanonymous/ComfyUI.git /comfyui \
 && python -m pip install --no-cache-dir -r /comfyui/requirements.txt

# Deterministic custom-node install. comfy-node-install (registry mode) claimed
# success for comfyui-gguf but the node never appeared in custom_nodes.
RUN git clone -q --depth 1 https://github.com/city96/ComfyUI-GGUF /comfyui/custom_nodes/ComfyUI-GGUF \
 && git clone -q --depth 1 https://github.com/Acly/comfyui-tooling-nodes /comfyui/custom_nodes/comfyui-tooling-nodes \
 && python -m pip install --no-cache-dir -r /comfyui/custom_nodes/ComfyUI-GGUF/requirements.txt

# Fail the image build if a dependency resolver silently replaced the CUDA 13
# PyTorch runtime or installed a different comfy-kitchen release.
RUN python -c "import importlib.metadata as m, torch; assert torch.__version__.startswith('2.13.0+cu130'), torch.__version__; assert m.version('comfy-kitchen') == '0.2.31'"

WORKDIR /comfyui

# Boot-time model downloader (MODEL_MANIFEST / MODELS env), used by lazy pods.
COPY boot-models.py /boot-models.py

# In-pod model manager: lets the runpod-lazy proxy add/swap models on a RUNNING
# pod (port 8189) instead of recreating it. LRU-evicts old models when the disk
# is full. Started by the pod start command alongside ComfyUI.
COPY model-manager.py /model-manager.py

# Optional independent dead-man switch. Uses RunPod's injected Pod ID plus the
# management key supplied by the controlling server when reaping is enabled.
COPY self-reaper.py /self-reaper.py
