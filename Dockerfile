# RunPod serverless ComfyUI worker for the SillyTavern image-gen fallback chain.
# Base image provides ComfyUI + the RunPod handler ({input:{workflow}} -> base64 images).
# We only add the custom nodes the flux2-klein / Qwen workflows need:
#   - ComfyUI-GGUF: UnetLoaderGGUF / CLIPLoaderGGUF
#   - comfyui-tooling-nodes: ETN_LoadImageBase64 (avatar + reference image injection)
# Models are NOT baked in - they live on the RunPod network volume under
# /runpod-volume/models/{unet,clip,vae,loras,checkpoints} and are auto-detected.
FROM runpod/worker-comfyui:5.8.6-base-cuda12.8.1
RUN comfy-node-install comfyui-gguf comfyui-tooling-nodes
