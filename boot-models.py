#!/usr/bin/env python3
"""Pod boot downloader: fetches the selected model set onto the container disk,
then hands off to ComfyUI (started by the wrapper command).

MODELS env: comma list of groups or "all" (default "all"). Groups: flux, qwen.
HF_TOKEN env: needed for the gated flux text encoder repo.
Idempotent and checksum-gated; files land in /comfyui/models/<kind>/.
"""
import hashlib
import os
import time
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get('COMFY_MODELS_ROOT', '/comfyui/models'))
HF_TOKEN = os.environ.get('HF_TOKEN', '')
GROUPS = {g.strip() for g in os.environ.get('MODELS', 'all').lower().split(',')}

MANIFEST = [
    {
        'group': 'flux',
        'url': 'https://huggingface.co/unsloth/FLUX.2-klein-9B-GGUF/resolve/main/flux-2-klein-9b-BF16.gguf',
        'dest': 'unet/flux-2-klein-9b-BF16.gguf',
        'sha256': '12a86c9755cb1e14e7ebaff48fe56da9a912477cd5a200ae552512f5e957786e',
        'size': 18157197600, 'auth': False,
    },
    {
        'group': 'flux',
        'url': 'https://huggingface.co/ponpoke/flux2-klein-9b-uncensored-text-encoder/resolve/main/flux2-klein-9b-uncensored-f16.gguf',
        'dest': 'clip/flux2-klein-9b-uncensored-f16.gguf',
        'sha256': '7a91f2c8eeed1302164830cd2f5ac22c2c25f1681b4f454478edccf28faf820c',
        'size': 16388043744, 'auth': True,
    },
    {
        'group': 'flux',
        'url': 'https://huggingface.co/ai-toolkit/flux2_vae/resolve/main/ae.safetensors',
        'dest': 'vae/flux2-vae.safetensors',
        'sha256': '868fe7b343cc8f3a19dbcfcafbc3d5f888802be3f89bd81b65b3621a066ce8f3',
        'size': 336211292, 'auth': False,
    },
    {
        'group': 'flux',
        'url': 'https://civitai.com/api/download/models/3030169',
        'dest': 'loras/FLUX2_KLEIN_UNLOCKED_V2.safetensors',
        'sha256': '9a38cb177bdd829ebfe6953916f29ed7d9db249c967b7542d8f03050c9cf7592',
        'size': 331378000, 'auth': False,
    },
    {
        'group': 'qwen',
        'url': 'https://huggingface.co/Phr00t/Qwen-Image-Edit-Rapid-AIO/resolve/main/v10/Qwen-Rapid-AIO-NSFW-v10.2.safetensors',
        'dest': 'checkpoints/Qwen-Rapid-AIO-NSFW-v10.2.safetensors',
        'sha256': 'b8f6a2d91475ca487cae7e9347cedea4b96bfb06e6c7914dc1680607047a0c33',
        'size': 28431829023, 'auth': False,
    },
]


def wanted(entry):
    return 'all' in GROUPS or entry['group'] in GROUPS


def download(entry):
    final = ROOT / entry['dest']
    if final.exists() and final.stat().st_size == entry['size']:
        print(f"boot-models: SKIP {entry['dest']}", flush=True)
        return
    final.parent.mkdir(parents=True, exist_ok=True)
    part = final.with_suffix(final.suffix + '.part')
    req = urllib.request.Request(entry['url'])
    if entry['auth'] and HF_TOKEN:
        req.add_header('Authorization', f'Bearer {HF_TOKEN}')
    digest = hashlib.sha256()
    done = 0
    started = time.time()
    print(f"boot-models: GET {entry['dest']} ({entry['size'] / 1e9:.1f}GB)", flush=True)
    with urllib.request.urlopen(req, timeout=120) as resp, open(part, 'wb') as out:
        while True:
            chunk = resp.read(16 * 1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
    if done != entry['size'] or digest.hexdigest() != entry['sha256']:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"{entry['dest']}: size/sha mismatch")
    part.rename(final)
    print(f"boot-models: OK {entry['dest']} in {time.time() - started:.0f}s", flush=True)


def main():
    todo = [e for e in MANIFEST if wanted(e)]
    print(f"boot-models: groups={sorted(GROUPS)} -> {len(todo)} files", flush=True)
    for entry in todo:
        download(entry)
    print('boot-models: READY', flush=True)


if __name__ == '__main__':
    main()
