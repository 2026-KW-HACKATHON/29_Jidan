import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/deploy.sh'
IMAGE = 'ghcr.io/2026-kw-hackathon/29_jidan-frontend@sha256:' + 'a' * 64
FAKE = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['CALLS'], 'a') as f: f.write(json.dumps([Path(sys.argv[0]).name]+args)+'\\n')
mode=os.environ.get('FAIL', '')
if Path(sys.argv[0]).name == 'curl':
    sys.exit(22 if mode == 'public' and args[-1].startswith('https:') else 0)
if args[0] == 'compose':
    if 'pull' in args and mode == 'pull': sys.exit(1)
    if 'up' in args and mode == 'up' and '/previous/' not in ' '.join(args): sys.exit(1)
    if 'ps' in args: print('container-id')
elif args[:2] == ['image', 'inspect']: print('expected-id')
elif args[0] == 'inspect': print('wrong-id' if mode == 'image' else 'expected-id')
'''


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / 'bin').mkdir()
        (self.base / 'deploy/frontend').mkdir(parents=True)
        (self.base / 'deploy/frontend/compose.yml').write_text('services: {}\n')
        for name in ['docker', 'curl']:
            path = self.base / 'bin' / name
            path.write_text(FAKE)
            path.chmod(0o755)
        self.root = self.base / 'apps/dev/frontend'
        self.root.mkdir(parents=True)
        self.calls = self.base / 'calls'
        self.env = dict(os.environ, PATH=f"{self.base / 'bin'}:{os.environ['PATH']}",
                        JIDAN_APP_ROOT=str(self.base / 'apps'), CALLS=str(self.calls))

    def previous(self):
        prev = self.root / 'previous'
        prev.mkdir()
        (prev / 'compose.yml').write_text('services: {}\n')
        (prev / '.env').write_text('IMAGE_REF=previous\n')
        (self.root / 'current').symlink_to(prev)
        return prev

    def run_deploy(self, failure='', image=IMAGE, environment='dev'):
        self.env['FAIL'] = failure
        result = subprocess.run(['bash', str(SCRIPT), environment, 'frontend', image],
                                cwd=self.base, env=self.env, capture_output=True, text=True)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []
        return result, calls

    def test_success_updates_pointer(self):
        prev = self.previous()
        result, _ = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual((self.root / 'current').resolve(), prev)

    def test_pull_failure_does_not_restart_previous(self):
        prev = self.previous()
        result, calls = self.run_deploy('pull')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / 'current').resolve(), prev)
        self.assertFalse(any('up' in c or 'down' in c for c in calls))

    def test_failed_start_rolls_back(self):
        prev = self.previous()
        result, calls = self.run_deploy('up')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any('up' in c and str(prev / 'compose.yml') in c for c in calls))
        self.assertEqual((self.root / 'current').resolve(), prev)

    def test_public_failure_rolls_back(self):
        prev = self.previous()
        result, calls = self.run_deploy('public')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any('up' in c and str(prev / 'compose.yml') in c for c in calls))

    def test_wrong_image_first_deployment_is_removed(self):
        result, calls = self.run_deploy('image')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any('down' in c for c in calls))
        self.assertFalse((self.root / 'current').exists())

    def test_invalid_environment_or_image_rejected_before_docker(self):
        for kwargs in [dict(environment='../production'), dict(image='nginx:latest')]:
            with self.subTest(kwargs=kwargs):
                result, calls = self.run_deploy(**kwargs)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
