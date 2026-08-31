#!/usr/bin/env python3
"""In-pod model manager: download model files into a RUNNING pod.

Runs next to ComfyUI (default port 8189, exposed via the runpod HTTP proxy) so
the runpod-lazy proxy can switch model sets without recreating the pod:

  GET  /status -> {"present": [dests], "downloading": {dest: pct}, "errors": {},
                   "free_gb": float, "busy": bool}
  POST /ensure {"files": [{"dest": "unet/x.gguf", "url": "...", "sha256"?, "size"?}]}
       -> queues the missing files (auth + retries via boot-models.py) and
          returns immediately; poll /status until present covers the set.

If the disk is too small for a download, the least-recently-requested model
files NOT part of the current request are deleted first (LRU by /ensure usage,
falling back to file mtime). ComfyUI picks up added/removed files without a
restart (its folder listing cache invalidates on directory mtime).
"""
import importlib.util
import json
import os
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_spec = importlib.util.spec_from_file_location('boot_models', '/boot-models.py')
boot_models = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(boot_models)

ROOT = Path(os.environ.get('COMFY_MODELS_ROOT', '/comfyui/models'))
PORT = int(os.environ.get('MM_PORT', '8189'))
USAGE_FILE = ROOT / '.mm-usage.json'
REAPER_ACTIVITY_FILE = Path(os.environ.get('RUNPOD_REAPER_ACTIVITY_FILE', '/tmp/runpod-self-reaper.hb'))
REAPER_ARMED_FILE = Path(os.environ.get('RUNPOD_REAPER_ARMED_FILE', '/tmp/runpod-self-reaper.armed'))
MARGIN_BYTES = 2 * 1024 ** 3  # keep this much free after a download
MIN_EVICT_BYTES = 50 * 1024 ** 2  # only files this large count as models

# Files that must never be LRU-evicted (the boot manifest / active set).
# Populated from MODEL_MANIFEST at startup; also extended by /ensure priority
# calls so the active pod's files are always protected.
def _load_protected():
    manifest = os.environ.get('MODEL_MANIFEST', '').strip()
    if manifest:
        try:
            return {f['dest'].lstrip('/') for f in json.loads(manifest)}
        except Exception:
            pass
    return set()

state = {
    'queue': [],        # entries waiting for the worker
    'downloading': {},  # dest -> percent (or -1 while size unknown)
    'errors': {},       # dest -> last error string
    'lock': threading.Lock(),
    'protected': _load_protected(),
}


def log(*args):
    print('model-manager:', *args, flush=True)


def touch_reaper():
    """Arm and refresh the independent in-pod dead-man switch."""
    try:
        REAPER_ACTIVITY_FILE.touch()
        REAPER_ARMED_FILE.touch()
    except OSError as err:
        log('could not touch self-reaper heartbeat:', err)


def load_usage():
    try:
        return json.loads(USAGE_FILE.read_text())
    except Exception:
        return {}


def mark_used(dests):
    usage = load_usage()
    now = time.time()
    for dest in dests:
        usage[dest] = now
    try:
        USAGE_FILE.write_text(json.dumps(usage))
    except Exception as err:
        log('usage write failed:', err)


def present_files():
    out = []
    for path in ROOT.rglob('*'):
        if path.is_file() and not path.name.startswith('.') and not path.name.endswith('.part'):
            out.append(str(path.relative_to(ROOT)))
    return sorted(out)


def free_bytes():
    return shutil.disk_usage(ROOT).free


def evict_for(needed_bytes, keep):
    """Deletes least-recently-used model files (not in `keep` or `state['protected']`)
    until `needed_bytes` fit. Returns the list of deleted dests."""
    if free_bytes() >= needed_bytes + MARGIN_BYTES:
        return []
    keep = keep | state['protected']
    usage = load_usage()
    candidates = []
    for dest in present_files():
        path = ROOT / dest
        if dest in keep or path.stat().st_size < MIN_EVICT_BYTES:
            continue
        candidates.append((usage.get(dest, path.stat().st_mtime), dest))
    deleted = []
    for _, dest in sorted(candidates):
        if free_bytes() >= needed_bytes + MARGIN_BYTES:
            break
        path = ROOT / dest
        size = path.stat().st_size
        path.unlink()
        deleted.append(dest)
        log(f'evicted {dest} ({size / 1e9:.1f}GB) for disk space')
    return deleted


def remote_size(entry):
    if entry.get('size'):
        return int(entry['size'])
    try:
        with boot_models.open_stream(entry['url']) as resp:
            return int(resp.headers.get('Content-Length') or 0)
    except Exception:
        return 0


def worker():
    while True:
        with state['lock']:
            entry = state['queue'].pop(0) if state['queue'] else None
        if entry is None:
            time.sleep(1)
            continue
        dest = entry['dest'].lstrip('/')
        try:
            size = remote_size(entry)
            evict_for(size, {dest} | {e['dest'].lstrip('/') for e in state['queue']})
            state['downloading'][dest] = -1 if not size else 0
            boot_models.download_with_retries(entry)
            state['errors'].pop(dest, None)
        except Exception as err:
            log(f'download failed for {dest}: {err}')
            state['errors'][dest] = str(err)
        finally:
            state['downloading'].pop(dest, None)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def _reply(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        touch_reaper()
        if self.path.split('?')[0] != '/status':
            return self._reply(404, {'error': 'not found'})
        with state['lock']:
            queued = [e['dest'].lstrip('/') for e in state['queue']]
        self._reply(200, {
            'present': present_files(),
            'downloading': state['downloading'],
            'queued': queued,
            'errors': state['errors'],
            'free_gb': round(free_bytes() / 1e9, 1),
            'busy': bool(state['downloading'] or queued),
        })

    def do_POST(self):
        touch_reaper()
        path = self.path.split('?')[0]
        if path == '/activity':
            return self._reply(200, {'ok': True})
        if path != '/ensure':
            return self._reply(404, {'error': 'not found'})
        try:
            length = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(length) or b'{}')
            files = body.get('files', [])
            priority = bool(body.get('priority'))
        except Exception as err:
            return self._reply(400, {'error': str(err)})
        dests = [f['dest'].lstrip('/') for f in files]
        mark_used(dests)
        present = set(present_files())
        queued = []
        with state['lock']:
            pending = {e['dest'].lstrip('/') for e in state['queue']} | set(state['downloading'])
            fresh = []
            for f in files:
                dest = f['dest'].lstrip('/')
                if dest in present or dest in pending:
                    continue
                state['errors'].pop(dest, None)
                fresh.append(f)
                queued.append(dest)
        if priority:
            # A model switch jumps ahead of background prefetch downloads.
            state['queue'][:0] = fresh
            # Also protect the newly requested files from eviction.
            state['protected'] |= {f['dest'].lstrip('/') for f in files}
        else:
            state['queue'].extend(fresh)
        if queued:
            log('queued:', ', '.join(queued))
        self._reply(200, {'queued': queued, 'present': sorted(present & set(dests))})


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    mark_used([])  # touch the usage file
    threading.Thread(target=worker, daemon=True).start()
    log(f'listening on :{PORT} (root {ROOT})')
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()


if __name__ == '__main__':
    main()
