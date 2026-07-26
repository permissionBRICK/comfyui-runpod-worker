#!/usr/bin/env python3
"""Pod boot downloader.

Preferred input: MODEL_MANIFEST env - JSON [{"dest": "unet/x.gguf", "url": "...",
"sha256": optional, "size": optional}] pushed by the runpod-lazy proxy from the
SillyTavern model catalog. Files land in /comfyui/models/<dest>.
HF_TOKEN is attached to huggingface.co URLs (gated repos).
Idempotent: existing files with matching size (or non-zero size when unknown)
are kept. Legacy MODELS=flux|qwen|all group mode remains as fallback.
"""
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get('COMFY_MODELS_ROOT', '/comfyui/models'))
HF_TOKEN = os.environ.get('HF_TOKEN', '')

LEGACY = [
    {'group': 'flux', 'url': 'https://huggingface.co/unsloth/FLUX.2-klein-9B-GGUF/resolve/main/flux-2-klein-9b-BF16.gguf',
     'dest': 'unet/flux-2-klein-9b-BF16.gguf', 'sha256': '12a86c9755cb1e14e7ebaff48fe56da9a912477cd5a200ae552512f5e957786e', 'size': 18157197600},
    {'group': 'flux', 'url': 'https://huggingface.co/ponpoke/flux2-klein-9b-uncensored-text-encoder/resolve/main/flux2-klein-9b-uncensored-f16.gguf',
     'dest': 'clip/flux2-klein-9b-uncensored-f16.gguf', 'sha256': '7a91f2c8eeed1302164830cd2f5ac22c2c25f1681b4f454478edccf28faf820c', 'size': 16388043744},
    {'group': 'flux', 'url': 'https://huggingface.co/ai-toolkit/flux2_vae/resolve/main/ae.safetensors',
     'dest': 'vae/flux2-vae.safetensors', 'sha256': '868fe7b343cc8f3a19dbcfcafbc3d5f888802be3f89bd81b65b3621a066ce8f3', 'size': 336211292},
    {'group': 'flux', 'url': 'https://civitai.com/api/download/models/3030169',
     'dest': 'loras/FLUX2_KLEIN_UNLOCKED_V2.safetensors', 'sha256': '9a38cb177bdd829ebfe6953916f29ed7d9db249c967b7542d8f03050c9cf7592', 'size': 331378000},
    {'group': 'qwen', 'url': 'https://huggingface.co/Phr00t/Qwen-Image-Edit-Rapid-AIO/resolve/main/v10/Qwen-Rapid-AIO-NSFW-v10.2.safetensors',
     'dest': 'checkpoints/Qwen-Rapid-AIO-NSFW-v10.2.safetensors', 'sha256': 'b8f6a2d91475ca487cae7e9347cedea4b96bfb06e6c7914dc1680607047a0c33', 'size': 28431829023},
]


def entries():
    manifest = os.environ.get('MODEL_MANIFEST', '').strip()
    if manifest:
        return json.loads(manifest)
    groups = {g.strip() for g in os.environ.get('MODELS', 'all').lower().split(',')}
    return [e for e in LEGACY if 'all' in groups or e['group'] in groups]


def download(entry):
    dest = entry['dest'].lstrip('/')
    final = ROOT / dest
    size = entry.get('size')
    if final.exists() and (final.stat().st_size == size if size else final.stat().st_size > 0):
        print(f'boot-models: SKIP {dest}', flush=True)
        return
    final.parent.mkdir(parents=True, exist_ok=True)
    part = final.with_suffix(final.suffix + '.part')
    req = urllib.request.Request(entry['url'])
    if HF_TOKEN and 'huggingface.co' in entry['url']:
        req.add_header('Authorization', f'Bearer {HF_TOKEN}')
    digest = hashlib.sha256()
    done = 0
    started = time.time()
    print(f'boot-models: GET {dest} <- {entry["url"]}', flush=True)
    with urllib.request.urlopen(req, timeout=180) as resp, open(part, 'wb') as out:
        while True:
            chunk = resp.read(16 * 1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
    if size and done != size:
        part.unlink(missing_ok=True)
        raise RuntimeError(f'{dest}: size mismatch {done} != {size}')
    if entry.get('sha256') and digest.hexdigest() != entry['sha256']:
        part.unlink(missing_ok=True)
        raise RuntimeError(f'{dest}: sha256 mismatch')
    if done == 0:
        part.unlink(missing_ok=True)
        raise RuntimeError(f'{dest}: empty download')
    part.rename(final)
    print(f'boot-models: OK {dest} ({done / 1e9:.1f}GB in {time.time() - started:.0f}s)', flush=True)


def download_with_retries(entry, attempts=3):
    for attempt in range(1, attempts + 1):
        try:
            return download(entry)
        except Exception as err:
            print(f'boot-models: attempt {attempt}/{attempts} failed for {entry["dest"]}: {err}', flush=True)
            if attempt == attempts:
                raise
            time.sleep(10 * attempt)


def main():
    todo = entries()
    print(f'boot-models: {len(todo)} files', flush=True)
    for entry in todo:
        download_with_retries(entry)
    print('boot-models: READY', flush=True)


if __name__ == '__main__':
    main()
