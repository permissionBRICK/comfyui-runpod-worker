# ComfyUI RunPod Worker

Private runtime image used by
[ST-RunPodProxy](https://github.com/permissionBRICK/ST-RunPodProxy) for the
on-demand cloud tier of the SillyTavern image-generation extension.

The image currently aligns its inference stack with the local RTX 3090 worker:

- CUDA 13.0 and PyTorch 2.13.0
- ComfyUI 0.33.1 and comfy-kitchen 0.2.31
- ComfyUI-GGUF and comfyui-tooling-nodes
- boot-time model downloads from a selected catalog manifest
- an in-pod model manager for adding and evicting models without recreating the pod

Models are downloaded at boot and are not included in the image.

## Publishing

Every push to `main` publishes two testable tags:
`cuda13-candidate` and the full Git commit SHA. After the candidate is verified
on a real RunPod GPU, manually run the **Build worker image** workflow with
`publish_latest` enabled to promote the same build to `latest`.
