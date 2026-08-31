#!/usr/bin/env python3
"""Independent dead-man switch for a volumeless RunPod Pod.

RunPod injects RUNPOD_POD_ID, but its injected pod-scoped API key cannot delete
the Pod (verified against the REST API). The manager instead passes its existing
management key as RUNPOD_TERMINATE_API_KEY when this watchdog is enabled. A
stale heartbeat then causes a full DELETE, not the UI's storage-retaining Stop.
"""
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = os.environ.get('RUNPOD_API_BASE', 'https://rest.runpod.io/v1')
ACTIVITY_FILE = Path(os.environ.get('RUNPOD_REAPER_ACTIVITY_FILE', '/tmp/runpod-self-reaper.hb'))
ARMED_FILE = Path(os.environ.get('RUNPOD_REAPER_ARMED_FILE', '/tmp/runpod-self-reaper.armed'))
IDLE_SECONDS = int(os.environ.get('RUNPOD_SELF_REAP_SECONDS', '0'))
BOOT_GRACE_SECONDS = int(os.environ.get('RUNPOD_SELF_REAP_BOOT_GRACE_SECONDS', '2400'))
POLL_SECONDS = max(1, int(os.environ.get('RUNPOD_SELF_REAP_POLL_SECONDS', '30')))


def log(*args):
    print('self-reaper:', *args, flush=True)


def terminate_self(pod_id, api_key, urlopen=urllib.request.urlopen):
    req = urllib.request.Request(
        f'{API_BASE}/pods/{pod_id}',
        headers={'Authorization': f'Bearer {api_key}'},
        method='DELETE',
    )
    try:
        with urlopen(req, timeout=60) as response:
            response.read()
            log(f'DELETE accepted for pod {pod_id} (HTTP {response.status})')
            return True
    except urllib.error.HTTPError as err:
        if err.code == 404:
            log(f'pod {pod_id} is already gone')
            return True
        detail = err.read().decode(errors='replace')[:300]
        log(f'DELETE failed for pod {pod_id}: HTTP {err.code} {detail}')
    except Exception as err:
        # The control plane can tear this container down before delivering the
        # response. Retry if the process survives.
        log(f'DELETE request for pod {pod_id} ended with {err!r}')
    return False


def should_reap(started, now=None):
    now = time.time() if now is None else now
    if ARMED_FILE.exists():
        try:
            last = ACTIVITY_FILE.stat().st_mtime
        except OSError:
            last = started
        return now - last >= IDLE_SECONDS
    return now - started >= BOOT_GRACE_SECONDS


def main():
    if IDLE_SECONDS <= 0:
        log('disabled (RUNPOD_SELF_REAP_SECONDS is 0)')
        return
    pod_id = os.environ.get('RUNPOD_POD_ID', '')
    api_key = os.environ.get('RUNPOD_TERMINATE_API_KEY', '')
    if not pod_id or not api_key:
        log('disabled (RUNPOD_POD_ID or RUNPOD_TERMINATE_API_KEY is missing)')
        return

    started = time.time()
    ACTIVITY_FILE.touch()
    log(f'watching pod {pod_id}: idle={IDLE_SECONDS}s boot-grace={BOOT_GRACE_SECONDS}s')
    while True:
        time.sleep(POLL_SECONDS)
        if not should_reap(started):
            continue
        mode = 'idle' if ARMED_FILE.exists() else 'unclaimed boot'
        log(f'{mode} timeout reached; terminating pod {pod_id}')
        terminate_self(pod_id, api_key)


if __name__ == '__main__':
    main()
