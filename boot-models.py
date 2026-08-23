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
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get('COMFY_MODELS_ROOT', '/comfyui/models'))
HF_TOKEN = os.environ.get('HF_TOKEN', '')
CIVITAI_TOKEN = os.environ.get('CIVITAI_TOKEN', '')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
# CDNs serve ~10-20MB/s per connection for cache-cold files; ranged parallel
# segments multiply that. 1 disables.
SEGMENTS = int(os.environ.get('DOWNLOAD_SEGMENTS', '8'))
# Segment parallel downloads for anything this big or larger; leave small
# files (VAE, LoRA) on a single stream.
SEGMENT_MIN_BYTES = int(os.environ.get('DOWNLOAD_SEGMENT_MIN_BYTES', 1024 ** 3))
# Cloudflare on civitai rejects Python's default UA (error 1010 -> 403).
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'


class _Redirect(Exception):
    def __init__(self, url):
        self.url = url


class _RaiseOnRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise _Redirect(newurl)


# Segment idle timeout: if a ranged read doesn't yield data for this long,
# kill the segment and retry it. Prevents the whole download from hanging on
# a stalled connection.
SEGMENT_IDLE_TIMEOUT = 45  # seconds


def _read_with_timeout(resp, chunk_size=8 * 1024 * 1024):
    """Read from response with idle timeout. Uses a background thread + queue
    to enforce the timeout since urllib responses don't expose a fileno()."""
    import queue
    q = queue.Queue()

    def _read():
        try:
            q.put(resp.read(chunk_size))
        except Exception as e:
            q.put(e)

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    try:
        result = q.get(timeout=SEGMENT_IDLE_TIMEOUT)
    except queue.Empty:
        raise TimeoutError('segment read timed out')
    if isinstance(result, Exception):
        raise result
    return result


def open_stream(url, headers=None):
    """Opens a download stream with per-host auth. GitHub release assets 302 to
    a signed CDN URL that must be fetched WITHOUT the Authorization header."""
    extra = {'User-Agent': UA, **(headers or {})}
    if CIVITAI_TOKEN and ('civitai.com' in url or 'civitai.red' in url):
        url += ('&' if '?' in url else '?') + 'token=' + CIVITAI_TOKEN
    if GITHUB_TOKEN and 'api.github.com' in url:
        req = urllib.request.Request(url, headers={
            'Authorization': f'Bearer {GITHUB_TOKEN}',
            'Accept': 'application/octet-stream',
            **extra,
        })
        opener = urllib.request.build_opener(_RaiseOnRedirect())
        try:
            return opener.open(req, timeout=180)
        except _Redirect as r:
            return urllib.request.urlopen(urllib.request.Request(r.url, headers=extra), timeout=180)
    req = urllib.request.Request(url, headers=extra)
    if HF_TOKEN and 'huggingface.co' in url:
        req.add_header('Authorization', f'Bearer {HF_TOKEN}')
    return urllib.request.urlopen(req, timeout=180)

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


def _probe(url):
    """Returns (size, accepts_ranges) via a 1-byte ranged request."""
    try:
        with open_stream(url, {'Range': 'bytes=0-0'}) as resp:
            cr = resp.headers.get('Content-Range', '')
            if resp.status == 206 and '/' in cr:
                return int(cr.split('/')[-1]), True
            return int(resp.headers.get('Content-Length') or 0), False
    except Exception:
        return 0, False


def _download_single(entry, part, dest):
    digest = hashlib.sha256()
    done = 0
    started = time.time()
    with open_stream(entry['url']) as resp, open(part, 'wb') as out:
        while True:
            chunk = resp.read(16 * 1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            prev, done = done, done + len(chunk)
            if done // 1_000_000_000 != prev // 1_000_000_000:
                print(f'boot-models: ... {dest} {done / 1e9:.1f}GB @ {done / 1e6 / max(1, time.time() - started):.0f}MB/s', flush=True)
    return done, digest.hexdigest()


def _download_segmented(entry, part, dest, size):
    """Ranged parallel download into a preallocated file (os.pwrite per
    segment). Returns (bytes, sha256-of-file-reread)."""
    started = time.time()
    progress = {'done': 0}
    errors = []
    lock = threading.Lock()
    bounds = [(size * i // SEGMENTS, size * (i + 1) // SEGMENTS - 1) for i in range(SEGMENTS)]
    with open(part, 'wb') as f:
        f.truncate(size)
    fd = os.open(part, os.O_WRONLY)
    try:
        def fetch(lo, hi):
            try:
                offset = lo
                with open_stream(entry['url'], {'Range': f'bytes={lo}-{hi}'}) as resp:
                    if resp.status != 206:
                        raise RuntimeError(f'expected 206, got {resp.status}')
                    while True:
                        chunk = _read_with_timeout(resp, 8 * 1024 * 1024)
                        if not chunk:
                            break
                        os.pwrite(fd, chunk, offset)
                        offset += len(chunk)
                        with lock:
                            prev = progress['done']
                            progress['done'] += len(chunk)
                            if progress['done'] // 1_000_000_000 != prev // 1_000_000_000:
                                print(f'boot-models: ... {dest} {progress["done"] / 1e9:.1f}GB @ {progress["done"] / 1e6 / max(1, time.time() - started):.0f}MB/s ({SEGMENTS} streams)', flush=True)
                if offset != hi + 1:
                    raise RuntimeError(f'segment {lo}-{hi} short: ended at {offset}')
            except Exception as err:
                errors.append(f'{lo}-{hi}: {err}')
        threads = [threading.Thread(target=fetch, args=b) for b in bounds]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        os.close(fd)
    if errors:
        raise RuntimeError(f'{dest}: segment failures: {errors[:3]}')
    sha = ''
    if entry.get('sha256'):
        digest = hashlib.sha256()
        with open(part, 'rb') as f:
            for chunk in iter(lambda: f.read(64 * 1024 * 1024), b''):
                digest.update(chunk)
        sha = digest.hexdigest()
    return progress['done'], sha


def download(entry):
    dest = entry['dest'].lstrip('/')
    final = ROOT / dest
    size = entry.get('size')
    if final.exists() and (final.stat().st_size == size if size else final.stat().st_size > 0):
        print(f'boot-models: SKIP {dest}', flush=True)
        return
    final.parent.mkdir(parents=True, exist_ok=True)
    part = final.with_suffix(final.suffix + '.part')
    started = time.time()
    real_size, ranged = _probe(entry['url'])
    size = size or real_size or None
    print(f'boot-models: GET {dest} <- {entry["url"]} ({(size or 0) / 1e9:.1f}GB, ranges={ranged})', flush=True)
    if ranged and size and size >= SEGMENT_MIN_BYTES and SEGMENTS > 1:
        done, sha = _download_segmented(entry, part, dest, size)
    else:
        done, sha = _download_single(entry, part, dest)
    if size and done != size:
        part.unlink(missing_ok=True)
        raise RuntimeError(f'{dest}: size mismatch {done} != {size}')
    if entry.get('sha256') and sha and sha != entry['sha256']:
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
    print('boot-models: READY - handing over to ComfyUI (torch import takes 1-3 min with no output)', flush=True)


if __name__ == '__main__':
    main()
