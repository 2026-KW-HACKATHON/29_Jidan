import json
import os
from pathlib import Path
import subprocess
import shutil
import signal
import time
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/deploy.sh'
IMAGE = 'ghcr.io/2026-kw-hackathon/29_jidan-frontend@sha256:' + 'a' * 64
# A well-formed scrypt ADMIN_PASSWORD_HASH (format only; no real password behind it).
VALID_HASH = 'scrypt$14$8$1$c3Nzc3Nzc3Nzc3Nzc3Nzcw$ZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGQ'
FAKE = '''#!/usr/bin/env python3
import json, os, signal, subprocess, sys, time
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['CALLS'], 'a') as f: f.write(json.dumps([Path(sys.argv[0]).name]+args)+'\\n')
mode=os.environ.get('FAIL', '')
if Path(sys.argv[0]).name == 'curl':
    failed = mode == 'public' and args[-1].startswith('https:')
    failed = failed or (mode == 'docs' and args[-1].endswith('/api/swagger/openapi.json'))
    sys.exit(22 if failed else 0)
if args[0] == 'compose':
    if 'pull' in args and mode == 'pull': sys.exit(1)
    if 'alembic' in args:
        release = Path(args[args.index('-f') + 1]).parent
        name = args[args.index('--name') + 1]
        Path(os.environ['MIGRATION_RECORD']).write_text((release.parent.parent / 'migration.pending').read_text())
        container = Path(os.environ['MIGRATION_CONTAINER'])
        if container.exists(): sys.exit(93)  # Never overlap daemon-owned migration workers.
        Path(os.environ['MIGRATED']).write_text(json.dumps(os.path.lexists(release.parent.parent / 'pending')))
        if mode == 'migrate': sys.exit(1)
        if mode == 'migrate-wait':
            container.write_text(json.dumps({'name': name, 'running': True}))
            Path(os.environ['READY']).write_text(str(os.getpid()))
            time.sleep(60)
        if mode == 'migrate-daemon':
            # Docker runs the container apart from the Compose client: it survives a SIGKILLed runner.
            subprocess.Popen([sys.executable, os.environ['WORKER'], name],
                             start_new_session=True, close_fds=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 5
            while not container.exists():
                if time.monotonic() > deadline: sys.exit(94)
                time.sleep(0.01)
            Path(os.environ['READY']).write_text(str(os.getpid()))
            time.sleep(60)
    if 'up' in args and mode == 'up' and '/previous/' not in ' '.join(args): sys.exit(1)
    if 'up' in args and mode == 'rollback': sys.exit(1)
    if 'up' in args and mode == 'wait' and '/previous/' not in ' '.join(args):
        Path(os.environ['READY']).touch()
        time.sleep(60)
    if 'ps' in args: print('container-id')
elif args[:2] == ['container', 'ls']:
    if mode == 'recovery-list': sys.exit(1)
    container = Path(os.environ['MIGRATION_CONTAINER'])
    if mode == 'recovery-delayed-create' and not container.exists():
        checks = Path(os.environ['RECOVERY_CHECKS'])
        count = int(checks.read_text()) + 1 if checks.exists() else 1
        checks.write_text(str(count))
        if count == 3:
            name = next(a for a in args if a.startswith('name=^/'))[7:-1]
            container.write_text(json.dumps({'name': name}))
    if container.exists():
        name = json.loads(container.read_text())['name']
        if 'name=^/' + name + '$' in args: print(name)
elif args[:2] == ['container', 'inspect']:
    # A container is running when it says so, or when a live worker process backs it.
    if mode == 'recovery-inspect': sys.exit(1)
    container = Path(os.environ['MIGRATION_CONTAINER'])
    if not container.exists(): sys.exit(1)
    data = json.loads(container.read_text())
    if data['name'] != args[-1]: sys.exit(1)
    print('true' if data.get('running', 'pid' in data) else 'false', data.get('exit', 0))
elif args[0] == 'wait':
    container = Path(os.environ['MIGRATION_CONTAINER'])
    if not container.exists(): sys.exit(1)
    data = json.loads(container.read_text())
    if data['name'] != args[1]: sys.exit(1)
    if mode == 'wait-hang': time.sleep(30)
    if 'pid' in data:
        # The migration finishes: the worker exits and --rm removes its container.
        os.kill(data['pid'], signal.SIGTERM)
        deadline = time.monotonic() + 5
        while container.exists():
            if time.monotonic() > deadline: sys.exit(1)
            time.sleep(0.01)
        print(0)
    else:
        code = 1 if mode == 'wait-fail' else 0
        container.write_text(json.dumps({**data, 'running': False, 'exit': code}))
        print(code)
elif args[0] == 'rm':
    # Plain `docker rm` refuses a running container; the script never forces it.
    if mode == 'recovery-remove': sys.exit(1)
    container = Path(os.environ['MIGRATION_CONTAINER'])
    if container.exists():
        data = json.loads(container.read_text())
        if data['name'] != args[-1] or '-f' in args or '--force' in args: sys.exit(1)
        if data.get('running', 'pid' in data): sys.exit(1)
        if mode != 'recovery-still-present': container.unlink()
elif args[0] == 'exec':
    # Run the check itself against the effective container environment it would see.
    effective = dict(os.environ, ALLOWED_ORIGINS=args[-1], ADMIN_PASSWORD_HASH=os.environ['TEST_ADMIN_HASH'],
                     MEDIA_ROOT=os.environ['MEDIA_DIR'])
    if mode == 'runtime': effective['ALLOWED_ORIGINS'] = 'https://wrong.example.com'
    if mode == 'runtime-password': effective['ADMIN_PASSWORD_HASH'] = 'private-do-not-print'
    if mode == 'runtime-password-missing': del effective['ADMIN_PASSWORD_HASH']
    sys.exit(subprocess.run([sys.executable, *args[3:]], env=effective).returncode)
elif args[:2] == ['image', 'inspect']: print('expected-id')
elif args[0] == 'inspect': print('wrong-id' if mode == 'image' else 'expected-id')
'''

WORKER = '''import json, os, signal, sys, time
from pathlib import Path
container = Path(os.environ['MIGRATION_CONTAINER'])
def stop(*_):
    container.unlink(missing_ok=True)
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
Path(os.environ['WORKER_PID']).write_text(str(os.getpid()))
container.write_text(json.dumps({'name': sys.argv[1], 'pid': os.getpid()}))
time.sleep(60)
stop()
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
        worker = self.base / 'worker.py'
        worker.write_text(WORKER)
        (self.base / 'media').mkdir()
        self.env = dict(os.environ, PATH=f"{self.base / 'bin'}:{os.environ['PATH']}",
                        TEST_ADMIN_HASH=VALID_HASH, MEDIA_DIR=str(self.base / 'media'),
                        PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'back-end'),
                        JIDAN_APP_ROOT=str(self.base / 'apps'), CALLS=str(self.calls), READY=str(self.base / 'ready'),
                        MIGRATED=str(self.base / 'migrated'), MIGRATION_RECORD=str(self.base / 'migration-record'),
                        MIGRATION_CONTAINER=str(self.base / 'migration-container'), WORKER=str(worker),
                        WORKER_PID=str(self.base / 'worker-pid'), RECOVERY_CHECKS=str(self.base / 'recovery-checks'),
                        JIDAN_MIGRATION_WAIT_SECONDS='3')
        self.addCleanup(self.stop_worker)

    def stop_worker(self):
        pid_file = self.base / 'worker-pid'
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass

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
        (self.root / 'runtime.env').write_text(
            f'APP_ENV={environment}\nALLOWED_ORIGINS={origin}\nADMIN_PASSWORD_HASH={VALID_HASH}\n')
        (self.root / 'runtime.env').chmod(0o600)
        return IMAGE.replace('-frontend@', '-backend@')

    def test_development_backend_verifies_public_and_local_swagger_before_commit(self):
        image = self.backend()
        result, calls = self.run_deploy(image=image, component='backend')
        self.assertEqual(result.returncode, 0, result.stderr)
        urls = [c[-1] for c in calls if c[0] == 'curl']
        for base in ['http://127.0.0.1:3021', 'https://dev-jidan.leehyowon14.dev']:
            for path in ['/api/swagger/', '/api/swagger/openapi.json']:
                self.assertIn(base + path, urls)
        self.assertTrue((self.root / 'current').exists())

    def test_remote_pbkdf2_runtime_hash_does_not_require_rotation(self):
        image = self.backend()
        path = self.root / 'runtime.env'
        remote_hash = 'pbkdf2_sha256$600000$c3Nzc3Nzc3Nzc3Nzc3Nzcw==$ZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGQ='
        self.env['TEST_ADMIN_HASH'] = remote_hash
        path.write_text(path.read_text().replace(VALID_HASH, remote_hash))
        before = path.read_bytes()
        result, calls = self.run_deploy(image=image, component='backend')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any('pull' in call for call in calls))
        self.assertTrue((self.root / 'current').exists())
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn(remote_hash, result.stdout + result.stderr)

    def test_production_backend_never_probes_development_swagger(self):
        image = self.backend('production')
        result, calls = self.run_deploy(image=image, environment='production', component='backend')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any('/api/swagger' in ' '.join(c) for c in calls))

    def test_missing_swagger_rolls_back_previous_backend(self):
        image = self.backend()
        prev = self.previous()
        result, calls = self.run_deploy('docs', image=image, component='backend')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / 'current').resolve(), prev)
        self.assertTrue(any('up' in c and str(prev / 'compose.yml') in c for c in calls))
        self.assertFalse((self.root / 'pending').exists())

    def test_missing_swagger_on_first_backend_deployment_cleans_container(self):
        image = self.backend()
        result, calls = self.run_deploy('docs', image=image, component='backend')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any('down' in c for c in calls))
        self.assertFalse((self.root / 'current').exists())

    def test_backend_validates_snapshot_and_running_value_before_commit(self):
        for environment in ('dev', 'production'):
            with self.subTest(environment=environment):
                image = self.backend(environment)
                result, calls = self.run_deploy(environment=environment, component='backend', image=image)
                self.assertEqual(result.returncode, 0, result.stderr)
                current = (self.root / 'current').resolve()
                snapshot = current / 'runtime.env'
                self.assertEqual(snapshot.read_bytes(), (self.root / 'runtime.env').read_bytes())
                self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
                checks = [c for c in calls if c[:2] == ['docker', 'exec']]
                origin = ('https://dev-jidan.leehyowon14.dev' if environment == 'dev'
                          else 'https://jidan.leehyowon14.dev')
                self.assertEqual(checks[-1][-1], origin)
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
                    path.write_text(content + f'\nADMIN_PASSWORD_HASH={VALID_HASH}\nDB_PASSWORD=private-do-not-print\n')
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

    def test_invalid_admin_password_hash_never_pulls_or_changes_containers(self):
        for environment in ('dev', 'production'):
            image = self.backend(environment)
            prev = self.previous()
            origin = ('https://dev-jidan.leehyowon14.dev' if environment == 'dev'
                      else 'https://jidan.leehyowon14.dev')
            for line in ('', 'ADMIN_PASSWORD_HASH=', 'ADMIN_PASSWORD_HASH=private-do-not-print',
                         'ADMIN_PASSWORD_HASH=pbkdf2_sha256$599999$c3Nzc3Nzc3Nzc3Nzc3Nzcw==$'
                         'ZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGQ=',
                         f'ADMIN_PASSWORD_HASH="{VALID_HASH}"',
                         f'ADMIN_PASSWORD_HASH={VALID_HASH}\nADMIN_PASSWORD_HASH={VALID_HASH}',
                         f'# ADMIN_PASSWORD_HASH={VALID_HASH}'):
                with self.subTest(environment=environment, line=line):
                    self.calls.write_text('')
                    (self.root / 'runtime.env').write_text(
                        f'ALLOWED_ORIGINS={origin}\n{line}\nDB_PASSWORD=private-do-not-print\n')
                    result, calls = self.run_deploy(environment=environment, component='backend', image=image)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(calls, [])
                    self.assertIn('ADMIN_PASSWORD_HASH', result.stderr)
                    self.assertEqual((self.root / 'current').resolve(), prev)
                    self.assertFalse((self.root / 'pending').exists())
                    self.assertNotIn('private-do-not-print', result.stdout + result.stderr)
                    self.assertNotIn('c3Nzc3Nz', result.stdout + result.stderr)

    def test_running_admin_password_hash_failure_restores_previous_release(self):
        for environment in ('dev', 'production'):
            for failure in ('runtime-password', 'runtime-password-missing'):
                with self.subTest(environment=environment, failure=failure):
                    image = self.backend(environment)
                    prev = self.previous()
                    self.calls.write_text('')
                    result, calls = self.run_deploy(failure, environment=environment, component='backend', image=image)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('Invalid backend administrator password configuration', result.stderr)
                    self.assertNotIn('private-do-not-print', result.stdout + result.stderr)
                    self.assertEqual((self.root / 'current').resolve(), prev)
                    self.assertTrue(any('up' in c and str(prev / 'compose.yml') in c for c in calls))
                    self.assertFalse((self.root / 'pending').exists())
                    (self.root / 'current').unlink()
                    shutil.rmtree(prev)

    def test_backend_runtime_mismatch_restores_previous_release(self):
        for environment in ('dev', 'production'):
            with self.subTest(environment=environment):
                image = self.backend(environment)
                prev = self.previous()
                self.calls.write_text('')
                result, calls = self.run_deploy('runtime', environment=environment, component='backend', image=image)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((self.root / 'current').resolve(), prev)
                self.assertTrue(any('up' in c and str(prev / 'compose.yml') in c for c in calls))
                self.assertFalse((self.root / 'pending').exists())
                self.assertFalse(any(p.exists() for p in (self.root / 'releases').glob('*/verified')))

    def test_first_backend_runtime_mismatch_removes_failed_container(self):
        image = self.backend()
        result, calls = self.run_deploy('runtime', component='backend', image=image)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any('down' in c for c in calls))
        self.assertFalse((self.root / 'current').exists())
        self.assertFalse((self.root / 'pending').exists())

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

    def interrupt(self, sig, failure='wait', component='frontend', image=IMAGE):
        self.env['FAIL'] = failure
        proc = subprocess.Popen(['bash', str(SCRIPT), 'dev', component, image],
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
            _, self.interrupt_stderr = proc.communicate(timeout=10)
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


    # dev backend applies Alembic migrations once, after pull and before any container change.
    def migration_calls(self, calls):
        return [c for c in calls if 'alembic' in c]

    def test_development_backend_migrates_between_pull_and_start(self):
        image = self.backend()
        self.previous()
        result, calls = self.run_deploy(image=image, component='backend')
        self.assertEqual(result.returncode, 0, result.stderr)
        migrations = self.migration_calls(calls)
        self.assertEqual(len(migrations), 1)
        migration = migrations[0]
        self.assertEqual(migration[migration.index('run'):migration.index('--name')],
                         ['run', '--rm', '--no-deps', '-T'])
        name = migration[migration.index('--name') + 1]
        self.assertRegex(name, r'^jidan-dev-backend-migrate-[a-zA-Z0-9]{8}$')
        self.assertEqual(migration[migration.index('--name') + 2:],
                         ['backend', 'python', '-m', 'alembic', 'upgrade', 'head'])
        release = (self.root / 'current').resolve()
        self.assertIn(str(release / 'compose.yml'), migration)
        index = calls.index(migration)
        pull = next(i for i, c in enumerate(calls) if 'pull' in c)
        up = next(i for i, c in enumerate(calls) if 'up' in c)
        self.assertLess(pull, index)
        self.assertLess(index, up)
        self.assertFalse(json.loads((self.base / 'migrated').read_text()), 'pending existed during migration')
        # The journal names the container before it exists and is cleared once the client succeeds.
        self.assertEqual((self.base / 'migration-record').read_text().strip(), name)
        self.assertFalse((self.root / 'migration.pending').exists())
        self.assertFalse((self.root / 'migration.next').exists())

    def test_migration_failure_keeps_previous_release_running(self):
        image = self.backend()
        prev = self.previous()
        result, calls = self.run_deploy('migrate', image=image, component='backend')
        self.assertEqual(result.returncode, 1)
        self.assertIn('DB migration failed', result.stderr)
        self.assertIn('alembic current', result.stderr)
        self.assertEqual(len(self.migration_calls(calls)), 1)
        self.assertFalse(any('up' in c or 'down' in c or 'stop' in c for c in calls))
        self.assertEqual((self.root / 'current').resolve(), prev)
        self.assertFalse(os.path.lexists(self.root / 'pending'))
        self.assertFalse(os.path.lexists(self.root / 'current.next'))
        self.assertNotIn('APP_ENV', result.stdout + result.stderr)
        # A failed client cannot prove how far the daemon got: keep the record for the operator.
        self.assertTrue((self.root / 'migration.pending').exists())
        self.assertIn('Migration creation/completion is uncertain', result.stderr)
        self.assertFalse(any(c[:2] == ['docker', 'rm'] for c in calls))

    def test_migration_failure_on_first_deployment_starts_nothing(self):
        image = self.backend()
        result, calls = self.run_deploy('migrate', image=image, component='backend')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('up' in c for c in calls))
        self.assertIn('--rm', self.migration_calls(calls)[0])
        self.assertFalse(os.path.lexists(self.root / 'current'))
        self.assertFalse(os.path.lexists(self.root / 'pending'))
        self.assertTrue((self.root / 'migration.pending').exists())

    def test_pull_failure_skips_migration(self):
        image = self.backend()
        result, calls = self.run_deploy('pull', image=image, component='backend')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.migration_calls(calls), [])

    def test_other_targets_never_migrate(self):
        for environment, component in [('production', 'backend'), ('dev', 'frontend'),
                                       ('production', 'frontend')]:
            with self.subTest(environment=environment, component=component):
                self.calls.unlink(missing_ok=True)
                if component == 'backend':
                    image = self.backend(environment)
                else:
                    image = IMAGE
                    self.root = self.base / f'apps/{environment}/frontend'
                    self.root.mkdir(parents=True, exist_ok=True)
                result, calls = self.run_deploy(image=image, environment=environment, component=component)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.migration_calls(calls), [])
                self.assertFalse(any('run' in c for c in calls if c[0] == 'docker'))

    def assert_interrupted_migration_cleaned(self, sig, code):
        image = self.backend()
        prev = self.previous()
        self.assertEqual(self.interrupt(sig, 'migrate-wait', 'backend', image), code)
        pid = int((self.base / 'ready').read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        name = self.migration_calls(calls)[0]
        name = name[name.index('--name') + 1]
        self.assertRegex(name, r'^jidan-dev-backend-migrate-[a-zA-Z0-9]{8}$')
        # The client stops, but the container may be mid-DDL: it is neither killed nor waited for.
        self.assertFalse(any(c[:2] in (['docker', 'rm'], ['docker', 'wait']) for c in calls))
        self.assertIn('still running and was left alone', self.interrupt_stderr)
        self.assertFalse(any('up' in c or 'down' in c for c in calls))
        self.assertEqual((self.root / 'current').resolve(), prev)
        self.assertFalse(os.path.lexists(self.root / 'pending'))
        self.assertEqual((self.root / 'migration.pending').read_text().strip(), name)
        self.assertTrue((self.base / 'migration-container').exists())
        # The next deployment waits for it to finish, then removes the stopped container.
        self.calls.write_text('')
        result, calls = self.run_deploy(image=image, component='backend')
        self.assertEqual(result.returncode, 0, result.stderr)
        waited = calls.index(['docker', 'wait', name])
        self.assertLess(waited, calls.index(['docker', 'rm', name]))
        self.assertLess(waited, min(i for i, c in enumerate(calls) if c[:2] == ['docker', 'compose']))
        self.assertFalse((self.root / 'migration.pending').exists())

    def test_sigterm_during_migration_stops_child_and_keeps_previous(self):
        self.assert_interrupted_migration_cleaned(signal.SIGTERM, 143)

    def test_sigint_during_migration_stops_child_and_keeps_previous(self):
        self.assert_interrupted_migration_cleaned(signal.SIGINT, 130)

    def test_sigkill_recovers_daemon_worker_before_new_migration(self):
        image = self.backend()
        prev = self.previous()
        self.assertEqual(self.interrupt(signal.SIGKILL, 'migrate-daemon', 'backend', image), -signal.SIGKILL)
        journal = self.root / 'migration.pending'
        name = journal.read_text().strip()
        self.assertEqual(journal.stat().st_mode & 0o777, 0o600)
        worker_pid = int((self.base / 'worker-pid').read_text())
        os.kill(worker_pid, 0)  # The worker survived the deployment and Compose client's SIGKILL.
        self.assertEqual((self.root / 'current').resolve(), prev)
        self.assertFalse((self.root / 'pending').exists())
        self.calls.write_text('')
        result, calls = self.run_deploy(image=image, component='backend')
        self.assertEqual(result.returncode, 0, result.stderr)
        # The recorded worker is waited for (never killed) before any Compose call, so two
        # migrations never overlap. --rm removed it when it finished.
        waited = calls.index(['docker', 'wait', name])
        self.assertLess(waited, min(i for i, c in enumerate(calls) if c[:2] == ['docker', 'compose']))
        self.assertFalse(any(c[:2] == ['docker', 'rm'] for c in calls))
        self.assertFalse((self.base / 'migration-container').exists())
        state = Path(f'/proc/{worker_pid}/stat')
        self.assertTrue(not state.exists() or state.read_text().split()[2] == 'Z')
        self.assertFalse(journal.exists())
        self.assertNotEqual(self.migration_calls(calls)[0][self.migration_calls(calls)[0].index('--name') + 1], name)

    def test_sigkill_after_migration_keeps_media_and_origin_checks(self):
        # Recovery must not bypass the later backend checks of the new release.
        image = self.backend()
        prev = self.previous()
        self.assertEqual(self.interrupt(signal.SIGKILL, 'migrate-daemon', 'backend', image), -signal.SIGKILL)
        self.calls.write_text('')
        result, calls = self.run_deploy('runtime', image=image, component='backend')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'migration.pending').exists())
        self.assertEqual((self.root / 'current').resolve(), prev)
        self.assertTrue(any(c[:2] == ['docker', 'exec'] for c in calls))
        env = (self.root / 'releases').glob('*/.env')
        self.assertTrue(any('MEDIA_VOLUME=jidan-dev-media' in p.read_text() for p in env))

    def seed_migration(self, container=True):
        name = 'jidan-dev-backend-migrate-abcdefgh'
        (self.root / 'migration.pending').write_text(name + '\n')
        if container:
            (self.base / 'migration-container').write_text(json.dumps({'name': name}))
        return name

    def test_absent_container_retains_uncertain_record_and_blocks_new_migration(self):
        image = self.backend()
        name = self.seed_migration(container=False)
        result, calls = self.run_deploy(image=image, component='backend')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Migration creation/completion is uncertain', result.stderr)
        self.assertFalse(any(c[:2] == ['docker', 'rm'] for c in calls))
        self.assertEqual(self.migration_calls(calls), [])
        self.assertFalse(any(c[:2] == ['docker', 'compose'] for c in calls))
        self.assertEqual((self.root / 'migration.pending').read_text().strip(), name)
        # Startup recovery and exit cleanup each re-check the full window before giving up.
        self.assertGreaterEqual(len([c for c in calls if c[:3] == ['docker', 'container', 'ls']]), 10)

    def test_delayed_container_creation_is_removed_before_new_migration(self):
        image = self.backend()
        prev = self.previous()
        name = self.seed_migration(container=False)
        result, calls = self.run_deploy('recovery-delayed-create', image=image, component='backend')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.base / 'recovery-checks').read_text(), '4')
        self.assertLess(calls.index(['docker', 'rm', name]),
                        calls.index(self.migration_calls(calls)[0]))
        self.assertEqual(len(self.migration_calls(calls)), 1)
        self.assertNotEqual((self.root / 'current').resolve(), prev)
        self.assertFalse((self.root / 'migration.pending').exists())

    def test_failed_migration_recovery_retains_record_and_blocks_deployment(self):
        image = self.backend()
        prev = self.previous()
        for failure in ('recovery-list', 'recovery-inspect', 'recovery-remove', 'recovery-still-present'):
            with self.subTest(failure=failure):
                name = self.seed_migration()
                self.calls.write_text('')
                result, calls = self.run_deploy(failure, image=image, component='backend')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('MIGRATION RECOVERY FAILED', result.stderr)
                self.assertEqual((self.root / 'migration.pending').read_text().strip(), name)
                self.assertTrue((self.base / 'migration-container').exists())
                self.assertEqual((self.root / 'current').resolve(), prev)
                self.assertFalse(any(c[:2] == ['docker', 'compose'] for c in calls))
                self.assertEqual(list((self.root / 'releases').iterdir()), [])
        self.calls.write_text('')
        result, _ = self.run_deploy(image=image, component='backend')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'migration.pending').exists())

    def test_migration_recovery_precedes_interrupted_container_recovery(self):
        # Both journals survive a SIGKILL during start; the migration worker goes first.
        image = self.backend()
        prev = self.previous()
        name = self.seed_migration()
        (self.root / 'pending').symlink_to(self.root / 'releases')
        self.calls.write_text('')
        result, calls = self.run_deploy(image=image, component='backend')
        self.assertEqual(result.returncode, 0, result.stderr)
        removal = calls.index(['docker', 'rm', name])
        restart = next(i for i, c in enumerate(calls) if 'up' in c and str(prev / 'compose.yml') in c)
        self.assertLess(removal, restart)
        self.assertFalse((self.root / 'migration.pending').exists())

    def seed_running(self):
        name = self.seed_migration()
        (self.base / 'migration-container').write_text(json.dumps({'name': name, 'running': True}))
        return name

    def test_running_recorded_migration_is_awaited_not_killed(self):
        image = self.backend()
        prev = self.previous()
        name = self.seed_running()
        result, calls = self.run_deploy(image=image, component='backend')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Waiting up to 3s', result.stderr)
        waited = calls.index(['docker', 'wait', name])
        self.assertLess(calls.index(['docker', 'container', 'inspect', '--format',
                                     '{{.State.Running}} {{.State.ExitCode}}', name]), waited)
        self.assertLess(waited, calls.index(['docker', 'rm', name]))
        self.assertLess(calls.index(['docker', 'rm', name]), calls.index(self.migration_calls(calls)[0]))
        self.assertNotEqual((self.root / 'current').resolve(), prev)

    def test_recorded_migration_that_fails_while_awaited_blocks_deployment(self):
        image = self.backend()
        prev = self.previous()
        name = self.seed_running()
        result, calls = self.run_deploy('wait-fail', image=image, component='backend')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Recorded migration failed', result.stderr)
        self.assertIn('alembic current', result.stderr)
        self.assertFalse(any(c[:2] == ['docker', 'rm'] for c in calls))
        self.assertFalse(any(c[:2] == ['docker', 'compose'] for c in calls))
        self.assertEqual((self.root / 'migration.pending').read_text().strip(), name)
        self.assertEqual((self.root / 'current').resolve(), prev)

    def test_recorded_migration_still_running_after_the_limit_blocks_without_killing(self):
        image = self.backend()
        prev = self.previous()
        name = self.seed_running()
        started = time.monotonic()
        result, calls = self.run_deploy('wait-hang', image=image, component='backend')
        elapsed = time.monotonic() - started
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('did not finish in time', result.stderr)
        self.assertGreaterEqual(elapsed, 3)
        self.assertLess(elapsed, 25)  # bounded by JIDAN_MIGRATION_WAIT_SECONDS, not the 30s hang
        self.assertFalse(any(c[:2] == ['docker', 'rm'] for c in calls))
        self.assertFalse(any(c[:2] == ['docker', 'compose'] for c in calls))
        self.assertTrue(json.loads((self.base / 'migration-container').read_text())['running'])
        self.assertEqual((self.root / 'migration.pending').read_text().strip(), name)
        self.assertEqual((self.root / 'current').resolve(), prev)

    def test_exited_failed_recorded_migration_is_not_cleared(self):
        # The client was killed, then the container failed on its own before this deployment.
        image = self.backend()
        name = self.seed_migration()
        (self.base / 'migration-container').write_text(json.dumps({'name': name, 'running': False, 'exit': 1}))
        result, calls = self.run_deploy(image=image, component='backend')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Recorded migration failed', result.stderr)
        self.assertFalse(any(c[:2] == ['docker', 'wait'] or c[:2] == ['docker', 'rm'] for c in calls))
        self.assertEqual((self.root / 'migration.pending').read_text().strip(), name)

    def test_invalid_migration_wait_is_rejected_before_docker(self):
        image = self.backend()
        for value in ('', '0', '-1', '10s', '99999', '1 2'):
            with self.subTest(value=value):
                self.env['JIDAN_MIGRATION_WAIT_SECONDS'] = value
                self.calls.unlink(missing_ok=True)
                result, calls = self.run_deploy(image=image, component='backend')
                if value == '':
                    continue  # empty means the default limit
                self.assertEqual(result.returncode, 2)
                self.assertEqual(calls, [])

    def test_invalid_migration_record_never_removes_unrelated_container(self):
        image = self.backend()
        for name in ('', 'jidan-production-backend-migrate-abcdefgh', '--force',
                     'jidan-dev-backend-migrate', 'jidan-dev-backend-migrate-abcdefgh\nother'):
            with self.subTest(name=name):
                (self.root / 'migration.pending').write_text(name + '\n')
                self.calls.write_text('')
                result, calls = self.run_deploy(image=image, component='backend')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Invalid migration recovery record', result.stderr)
                self.assertEqual(calls, [])
                self.assertTrue((self.root / 'migration.pending').exists())

    def test_invalid_backend_input_rejected_before_docker(self):
        backend_image = self.backend()
        for kwargs in [dict(component='../backend', image=backend_image),
                       dict(component='backend', image=IMAGE),
                       dict(component='backend', image=backend_image[:-1]),
                       dict(environment='staging', component='backend', image=backend_image)]:
            with self.subTest(kwargs=kwargs):
                result, calls = self.run_deploy(**kwargs)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
