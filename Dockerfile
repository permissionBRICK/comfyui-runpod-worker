# RunPod ComfyUI worker/pod image for the SillyTavern image-gen fallback chain.
# cuda12.8.1 base: EU datacenters stock Blackwell GPUs (PRO 4500 / RTX 5090),
# which need cu12.8 torch kernels.
# Custom nodes baked in (network volumes can't hold them):
#   - ComfyUI-GGUF: UnetLoaderGGUF / CLIPLoaderGGUF
#   - comfyui-tooling-nodes: ETN_LoadImageBase64 (avatar + reference image injection)
# Models are downloaded at pod boot by /boot-models.py (MODELS=flux|qwen|all)
# onto the container disk - no network volume needed. Serverless use (dormant
# fallback) keeps the stock /start.sh CMD and can still use a network volume.
FROM runpod/worker-comfyui:5.8.6-base-cuda12.8.1
RUN comfy-node-install comfyui-gguf comfyui-tooling-nodes
COPY boot-models.py /boot-models.py
