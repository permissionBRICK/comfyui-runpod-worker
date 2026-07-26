# RunPod ComfyUI worker/pod image for the SillyTavern image-gen fallback chain.
# cuda12.8.1 base: Blackwell + Ada GPU support.
FROM runpod/worker-comfyui:5.8.6-base-cuda12.8.1

# Pin ComfyUI to match the PC instance (flux2 node classes need >= 0.28;
# the base ships 0.25).
RUN cd /comfyui && git fetch -q --tags origin && git checkout -q v0.28.0 \
 && uv pip install -r requirements.txt

# Deterministic custom-node install. comfy-node-install (registry mode) claimed
# success for comfyui-gguf but the node never appeared in custom_nodes.
RUN git clone -q --depth 1 https://github.com/city96/ComfyUI-GGUF /comfyui/custom_nodes/ComfyUI-GGUF \
 && git clone -q --depth 1 https://github.com/Acly/comfyui-tooling-nodes /comfyui/custom_nodes/comfyui-tooling-nodes \
 && uv pip install -r /comfyui/custom_nodes/ComfyUI-GGUF/requirements.txt

# Boot-time model downloader (MODEL_MANIFEST / MODELS env), used by lazy pods.
COPY boot-models.py /boot-models.py
