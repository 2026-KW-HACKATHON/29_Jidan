import json
import os
from pathlib import Path
import subprocess
import signal
import time
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/deploy.sh'
IMAGE = 'ghcr.io/2026-kw-hackathon/29_jidan-frontend@sha256:' + 'a' * 64
FAKE = '''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['CALLS'], 'a') as f: f.write(json.dumps([Path(sys.argv[0]).name]+args)+'\\n')
mode=os.environ.get('FAIL', '')
if Path(sys.argv[0]).name == 'curl':
    sys.exit(22 if mode == 'public' and args[-1].startswith('https:') else 0)
if args[0] == 'compose':
    if 'pull' in args and mode == 'pull': sys.exit(1)
    if 'up' in args and mode == 'up' and '/previous/' not in ' '.join(args): sys.exit(1)
    if 'up' in args and mode == 'rollback': sys.exit(1)
    if 'up' in args and mode == 'wait' and '/previous/' not in ' '.join(args):
        Path(os.environ['READY']).touch()
        time.sleep(60)
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
                        JIDAN_APP_ROOT=str(self.base / 'apps'), CALLS=str(self.calls), READY=str(self.base / 'ready'))

    def previous(self):
        prev = self.root / 'previous'
        prev.mkdir()
        (prev / 'compose.yml').write_text('services: {}\n')
        (prev / '.env').write_text('IMAGE_REF=previous\n')
        (self.root / 'current').symlink_to(prev)
        return prev

    def run_deploy(self, failure='', image=IMAGE, environment='dev', component='frontend'):
        self.env['FAIL'] = failure
        result = subprocess.run(['bash', str(SCRIPT), environment, component, image],
                                cwd=self.base, env=self.env, capture_output=True, text=True)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []
        return result, calls

    def backend(self, environment='dev'):
        (self.base / 'deploy/backend').mkdir(parents=True, exist_ok=True)
        (self.base / 'deploy/backend/compose.yml').write_text('services: {}\n')
        self.root = self.base / f'apps/{environment}/backend'
        self.root.mkdir(parents=True, exist_ok=True)
        origin = ('https://dev-jidan.leehyowon14.dev' if environment == 'dev'
                  else 'https://jidan.leehyowon14.dev')
        (self.root / 'runtime.env').write_text(f'APP_ENV={environment}\nALLOWED_ORIGINS={origin}\n')
        (self.root / 'runtime.env').chmod(0o600)
        return IMAGE.replace('-frontend@', '-backend@')

    def test_backend_copies_validated_snapshot_with_private_permissions(self):
        for environment in ('dev', 'production'):
            with self.subTest(environment=environment):
                image = self.backend(environment)
                result, calls = self.run_deploy(environment=environment, component='backend', image=image)
                self.assertEqual(result.returncode, 0, result.stderr)
                current = (self.root / 'current').resolve()
                snapshot = current / 'runtime.env'
                self.assertEqual(snapshot.read_bytes(), (self.root / 'runtime.env').read_bytes())
                self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
                self.assertTrue((current / 'verified').exists())

    def test_invalid_backend_origin_never_pulls_or_changes_containers(self):
        for environment in ('dev', 'production'):
            image = self.backend(environment)
            prev = self.previous()
            path = self.root / 'runtime.env'
            for content in ('APP_ENV=' + environment,
                            'ALLOWED_ORIGINS=', 'ALLOWED_ORIGINS=*',
                            'ALLOWED_ORIGINS=https://wrong.example.com',
                            'ALLOWED_ORIGINS=https://dev-jidan.leehyowon14.dev,https://jidan.leehyowon14.dev'):
                with self.subTest(environment=environment, content=content):
                    self.calls.write_text('')
                    path.write_text(content + '\nDB_PASSWORD=private-do-not-print\n')
                    result, calls = self.run_deploy(environment=environment, component='backend', image=image)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(calls, [])
                    self.assertEqual((self.root / 'current').resolve(), prev)
                    self.assertFalse((self.root / 'pending').exists())
                    self.assertNotIn('private-do-not-print', result.stdout + result.stderr)
                    self.assertNotIn('https://wrong.example.com', result.stdout + result.stderr)
            path.unlink()
            self.calls.write_text('')
            result, calls = self.run_deploy(environment=environment, component='backend', image=image)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(calls, [])
            self.assertEqual((self.root / 'current').resolve(), prev)

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

    def interrupt(self, sig):
        self.env['FAIL'] = 'wait'
        proc = subprocess.Popen(['bash', str(SCRIPT), 'dev', 'frontend', IMAGE],
                                cwd=self.base, env=self.env, start_new_session=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not (self.base / 'ready').exists():
                if proc.poll() is not None or time.monotonic() > deadline:
                    self.fail('Deployment never reached container start')
                time.sleep(0.02)
            if sig == signal.SIGKILL:
                os.killpg(proc.pid, sig)
            else:
                proc.send_signal(sig)
            proc.communicate(timeout=10)
            return proc.returncode
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()

    def test_sigterm_restores_previous(self):
        prev = self.previous()
        self.assertEqual(self.interrupt(signal.SIGTERM), 143)
        self.assertEqual((self.root / 'current').resolve(), prev)
        self.assertFalse((self.root / 'pending').exists())
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertTrue(any('up' in c and str(prev / 'compose.yml') in c for c in calls))

    def test_sigint_on_first_deployment_cleans_container(self):
        self.assertEqual(self.interrupt(signal.SIGINT), 130)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertTrue(any('down' in c for c in calls))
        self.assertFalse((self.root / 'pending').exists())

    def test_sigkill_is_recovered_before_next_deployment(self):
        prev = self.previous()
        self.assertEqual(self.interrupt(signal.SIGKILL), -signal.SIGKILL)
        self.assertTrue((self.root / 'pending').exists())
        self.calls.write_text('')
        result, calls = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        starts = [c for c in calls if 'up' in c]
        self.assertIn(str(prev / 'compose.yml'), starts[0])
        self.assertFalse((self.root / 'pending').exists())

    def test_failed_recovery_preserves_journal(self):
        self.previous()
        result, _ = self.run_deploy('rollback')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.root / 'pending').exists())
        count = len(list((self.root / 'releases').iterdir()))
        result, _ = self.run_deploy('rollback')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(list((self.root / 'releases').iterdir())), count)
        result, _ = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_committed_journal_is_only_cleared(self):
        prev = self.previous()
        (self.root / 'pending').symlink_to(prev)
        (self.root / 'current.next').symlink_to(prev)
        result, calls = self.run_deploy('pull')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('up' in c or 'down' in c for c in calls))
        self.assertFalse((self.root / 'pending').exists())
        self.assertFalse((self.root / 'current.next').exists())


if __name__ == '__main__':
    unittest.main()
