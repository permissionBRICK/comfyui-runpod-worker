import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def load_module(tmp):
    with mock.patch.dict(os.environ, {
        'RUNPOD_REAPER_ACTIVITY_FILE': str(Path(tmp) / 'activity'),
        'RUNPOD_REAPER_ARMED_FILE': str(Path(tmp) / 'armed'),
        'RUNPOD_SELF_REAP_SECONDS': '900',
        'RUNPOD_SELF_REAP_BOOT_GRACE_SECONDS': '2400',
    }):
        spec = importlib.util.spec_from_file_location('self_reaper', Path(__file__).parents[1] / 'self-reaper.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class SelfReaperTests(unittest.TestCase):
    def test_unarmed_pod_uses_long_boot_grace(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_module(tmp)
            self.assertFalse(module.should_reap(1000, now=3399))
            self.assertTrue(module.should_reap(1000, now=3400))

    def test_armed_pod_uses_activity_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_module(tmp)
            module.ACTIVITY_FILE.touch()
            module.ARMED_FILE.touch()
            os.utime(module.ACTIVITY_FILE, (1000, 1000))
            self.assertFalse(module.should_reap(0, now=1899))
            self.assertTrue(module.should_reap(0, now=1900))

    def test_injected_pod_scoped_key_does_not_enable_reaper(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_module(tmp)
            with mock.patch.dict(os.environ, {
                'RUNPOD_POD_ID': 'pod-123',
                'RUNPOD_API_KEY': 'injected-pod-scoped-key',
            }, clear=True), mock.patch.object(module, 'log') as log:
                module.main()
            log.assert_called_once_with(
                'disabled (RUNPOD_POD_ID or dedicated RUNPOD_TERMINATE_API_KEY is missing)'
            )

    def test_delete_uses_dedicated_bearer_token_and_pod_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_module(tmp)
            seen = {}

            class Response:
                status = 204
                def __enter__(self): return self
                def __exit__(self, *_): pass
                def read(self): return b''

            def open_request(request, timeout):
                seen.update(url=request.full_url,
                            authorization=request.headers['Authorization'],
                            method=request.method, timeout=timeout)
                return Response()

            self.assertTrue(module.terminate_self('pod-123', 'restricted-key', open_request))
            self.assertEqual(seen, {
                'url': 'https://rest.runpod.io/v1/pods/pod-123',
                'authorization': 'Bearer restricted-key',
                'method': 'DELETE',
                'timeout': 60,
            })


if __name__ == '__main__':
    unittest.main()
