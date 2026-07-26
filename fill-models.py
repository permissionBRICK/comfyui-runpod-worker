#!/usr/bin/env python3
"""One-time network volume fill for the RunPod ComfyUI worker.

Downloads the model set for the SillyTavern workflows onto /runpod-volume,
verifying sha256 while streaming. Idempotent: files that already exist with
the right size are skipped, so re-runs are cheap no-ops.
Gated HF repos authenticate via the HF_TOKEN env var (never stored on disk).
"""
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path

VOLUME = Path(os.environ.get('VOLUME_ROOT', '/runpod-volume'))
HF_TOKEN = os.environ.get('HF_TOKEN', '')

MANIFEST = [
    {
        'url': 'https://huggingface.co/unsloth/FLUX.2-klein-9B-GGUF/resolve/main/flux-2-klein-9b-BF16.gguf',
        'dest': 'models/unet/flux-2-klein-9b-BF16.gguf',
        'sha256': '12a86c9755cb1e14e7ebaff48fe56da9a912477cd5a200ae552512f5e957786e',
        'size': 18157197600,
        'auth': False,
    },
    {
        'url': 'https://huggingface.co/ponpoke/flux2-klein-9b-uncensored-text-encoder/resolve/main/flux2-klein-9b-uncensored-f16.gguf',
        'dest': 'models/clip/flux2-klein-9b-uncensored-f16.gguf',
        'sha256': '7a91f2c8eeed1302164830cd2f5ac22c2c25f1681b4f454478edccf28faf820c',
        'size': 16388043744,
        'auth': True,  # gated repo
    },
    {
        'url': 'https://huggingface.co/ai-toolkit/flux2_vae/resolve/main/ae.safetensors',
        'dest': 'models/vae/flux2-vae.safetensors',
        'sha256': '868fe7b343cc8f3a19dbcfcafbc3d5f888802be3f89bd81b65b3621a066ce8f3',
        'size': 336211292,
        'auth': False,
    },
    {
        'url': 'https://civitai.com/api/download/models/3030169',
        'dest': 'models/loras/FLUX2_KLEIN_UNLOCKED_V2.safetensors',
        'sha256': '9a38cb177bdd829ebfe6953916f29ed7d9db249c967b7542d8f03050c9cf7592',
        'size': 331378000,
        'auth': False,
    },
    {
        'url': 'https://huggingface.co/Phr00t/Qwen-Image-Edit-Rapid-AIO/resolve/main/v10/Qwen-Rapid-AIO-NSFW-v10.2.safetensors',
        'dest': 'models/checkpoints/Qwen-Rapid-AIO-NSFW-v10.2.safetensors',
        'sha256': 'b8f6a2d91475ca487cae7e9347cedea4b96bfb06e6c7914dc1680607047a0c33',
        'size': 28431829023,
        'auth': False,
    },
]


def download(entry):
    final = VOLUME / entry['dest']
    if final.exists() and final.stat().st_size == entry['size']:
        print(f"SKIP {entry['dest']} (already present)", flush=True)
        return {'dest': entry['dest'], 'status': 'skipped'}

    final.parent.mkdir(parents=True, exist_ok=True)
    part = final.with_suffix(final.suffix + '.part')
    req = urllib.request.Request(entry['url'])
    if entry['auth'] and HF_TOKEN:
        req.add_header('Authorization', f'Bearer {HF_TOKEN}')

    digest = hashlib.sha256()
    done = 0
    started = time.time()
    print(f"GET  {entry['dest']} ({entry['size'] / 1e9:.1f}GB)", flush=True)
    with urllib.request.urlopen(req, timeout=120) as response, open(part, 'wb') as out:
        while True:
            chunk = response.read(16 * 1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            prev, done = done, done + len(chunk)
            if done // 5_000_000_000 != prev // 5_000_000_000:
                print(f"  ... {done / 1e9:.0f}GB @ {done / 1e6 / (time.time() - started):.0f}MB/s", flush=True)

    if done != entry['size']:
        raise RuntimeError(f"{entry['dest']}: size mismatch {done} != {entry['size']}")
    if digest.hexdigest() != entry['sha256']:
        raise RuntimeError(f"{entry['dest']}: sha256 mismatch {digest.hexdigest()}")
    part.rename(final)
    secs = time.time() - started
    print(f"OK   {entry['dest']} sha256 verified in {secs:.0f}s", flush=True)
    return {'dest': entry['dest'], 'status': 'downloaded', 'seconds': round(secs)}


def main():
    results = []
    failed = False
    for entry in MANIFEST:
        try:
            results.append(download(entry))
        except Exception as err:
            print(f"FAIL {entry['dest']}: {err}", flush=True)
            results.append({'dest': entry['dest'], 'status': 'failed', 'error': str(err)})
            failed = True
    (VOLUME / '.fill-status.json').write_text(json.dumps({'ok': not failed, 'results': results, 'time': time.time()}, indent=2))
    print('FILL FAILED' if failed else 'FILL COMPLETE', flush=True)
    # Keep the container alive so the pod doesn't restart-loop; terminated via API.
    time.sleep(10**9)


if __name__ == '__main__':
    main()
