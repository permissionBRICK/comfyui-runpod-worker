# ComfyUI RunPod Worker

Private runtime image used by the managed RunPod backend in
[ST-ImageProviderExtensions](https://github.com/permissionBRICK/ST-ImageProviderExtensions)
for SillyTavern's on-demand cloud image-generation tier.

The image currently aligns its inference stack with the local RTX 3090 worker:

- CUDA 13.0 and PyTorch 2.13.0
- ComfyUI 0.33.1 and comfy-kitchen 0.2.31
- ComfyUI-GGUF and comfyui-tooling-nodes
- boot-time model downloads from a selected catalog manifest
- an in-pod model manager for adding and evicting models without recreating the pod
- an optional independent idle self-reaper using RunPod's injected Pod ID and a dedicated restricted Pod-management key

Models are downloaded at boot and are not included in the image.

The SillyTavern server plugin remains the authoritative idle watchdog. The
worker self-reaper is a longer-delay dead-man switch for a lost server or
network partition. RunPod's injected pod-scoped key cannot delete its own Pod,
so this is enabled only when the manager supplies `RUNPOD_TERMINATE_API_KEY`.
Use a separate Restricted key with only Pod access, never a general account key.

## Publishing

Every push to `main` publishes two testable tags:
`cuda13-candidate` and the full Git commit SHA. After the candidate is verified
on a real RunPod GPU, manually run the **Build worker image** workflow with
`publish_latest` enabled to promote the same build to `latest`.
