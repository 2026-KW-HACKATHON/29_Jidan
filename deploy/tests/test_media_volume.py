"""B01: uploaded media live on a persistent, per-environment volume that the app user can write.

The backend keeps manual photos and recordings as files (MEDIA_ROOT). Without a mount they sit
in the container layer and vanish when a deployment replaces the container, while the database
still points at them.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / 'deploy/backend/compose.yml').read_text()
DOCKERFILE = (ROOT / 'deploy/backend/Dockerfile').read_text()
MEDIA_ROOT = '/var/lib/jidan/media'


def compose_config(env: dict[str, str]) -> dict | None:
    """`docker compose config` of the real file, or None without a Docker CLI."""
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp)
        (directory / 'compose.yml').write_text(COMPOSE)
        (directory / 'runtime.env').write_text('APP_ENV=dev\nMEDIA_ROOT=/tmp/elsewhere\n')
        (directory / '.env').write_text(''.join(f'{k}={v}\n' for k, v in env.items()))
        try:
            result = subprocess.run(
                ['docker', 'compose', '-p', 'jidan-dev-backend', '--env-file', str(directory / '.env'),
                 '-f', str(directory / 'compose.yml'), 'config', '--format', 'json'],
                capture_output=True, text=True, timeout=60)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return {'error': result.stderr}
        return json.loads(result.stdout)


class MediaVolumeTests(unittest.TestCase):
    def test_compose_mounts_a_named_volume_at_media_root(self):
        self.assertRegex(COMPOSE, rf'MEDIA_ROOT: {re.escape(MEDIA_ROOT)}\n')
        self.assertIn(f'- media:{MEDIA_ROOT}\n', COMPOSE)
        self.assertRegex(COMPOSE, r'volumes:\n  media:\n    name: \$\{MEDIA_VOLUME:\?MEDIA_VOLUME is required\}')

    def test_resolved_compose_uses_the_environment_volume_and_overrides_runtime_env(self):
        config = compose_config({'IMAGE_REF': 'jidan-backend:test', 'APP_PORT': '3021',
                                 'MEDIA_VOLUME': 'jidan-dev-media'})
        if config is None:
            self.skipTest('docker compose is not available')
        self.assertNotIn('error', config, config.get('error'))
        backend = config['services']['backend']
        self.assertEqual(backend['environment']['MEDIA_ROOT'], MEDIA_ROOT)  # not runtime.env's value
        [mount] = backend['volumes']
        self.assertEqual((mount['type'], mount['source'], mount['target']), ('volume', 'media', MEDIA_ROOT))
        self.assertEqual(config['volumes']['media']['name'], 'jidan-dev-media')
        self.assertFalse(config['volumes']['media'].get('external', False))

    def test_compose_refuses_to_start_without_a_volume_name(self):
        config = compose_config({'IMAGE_REF': 'jidan-backend:test', 'APP_PORT': '3021'})
        if config is None:
            self.skipTest('docker compose is not available')
        self.assertIn('MEDIA_VOLUME is required', config.get('error', ''))

    def test_image_creates_media_root_owned_by_the_app_user_before_dropping_root(self):
        runtime = DOCKERFILE[DOCKERFILE.index('AS runtime\n'):]
        create = runtime.index(f'install -d -o app -g app -m 700 {MEDIA_ROOT}')
        self.assertLess(runtime.index('useradd'), create)
        self.assertLess(create, runtime.index('USER app'))


SCRIPT = ROOT / 'deploy/scripts/deploy.sh'
# A runtime.env that passes deploy/scripts/check_runtime_env.py (#151): one origin per environment.
ORIGINS = {'dev': 'https://dev-jidan.leehyowon14.dev', 'production': 'https://jidan.leehyowon14.dev'}
VALID_HASH = 'scrypt$14$8$1$c3Nzc3Nzc3Nzc3Nzc3Nzcw$ZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGQ'
IMAGE = 'ghcr.io/2026-kw-hackathon/29_jidan-backend@sha256:' + 'b' * 64
FAKE = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as f: f.write(json.dumps([Path(sys.argv[0]).name] + args) + '\\n')
if args and args[0] == 'compose':
    if 'up' in args:
        release = Path(args[args.index('-f') + 1]).parent
        Path(os.environ['ENVFILE']).write_text((release / '.env').read_text())
    if 'ps' in args: print('container-id')
elif args[:2] == ['image', 'inspect']: print('expected-id')
elif args and args[0] == 'inspect': print('expected-id')
elif args and args[0] == 'exec' and os.environ.get('FAIL') == 'media' and 'MEDIA_ROOT' in ' '.join(args):
    sys.exit(1)
'''


class DeployMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / 'bin').mkdir()
        for name in ['docker', 'curl']:
            path = self.base / 'bin' / name
            path.write_text(FAKE)
            path.chmod(0o755)
        (self.base / 'deploy/backend').mkdir(parents=True)
        (self.base / 'deploy/backend/compose.yml').write_text('services: {}\n')
        self.env = dict(os.environ, PATH=f"{self.base / 'bin'}:{os.environ['PATH']}",
                        JIDAN_APP_ROOT=str(self.base / 'apps'), CALLS=str(self.base / 'calls'),
                        ENVFILE=str(self.base / 'envfile'), MIGRATED=str(self.base / 'migrated'))

    def deploy(self, environment, failure=''):
        root = self.base / f'apps/{environment}/backend'
        root.mkdir(parents=True)
        (root / 'runtime.env').write_text(f'APP_ENV={environment}\nALLOWED_ORIGINS={ORIGINS[environment]}\n'
                                          f'ADMIN_PASSWORD_HASH={VALID_HASH}\n')
        (root / 'runtime.env').chmod(0o600)
        self.env['FAIL'] = failure
        result = subprocess.run(['bash', str(SCRIPT), environment, 'backend', IMAGE], cwd=self.base,
                                env=self.env, capture_output=True, text=True)
        log = self.base / 'calls'
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        self.assertTrue(calls, f'deploy.sh stopped before any docker call: {result.stderr}')
        return result, calls, root

    def test_media_checks_run_alongside_the_origin_check(self):
        """#151's ALLOWED_ORIGINS check and the media write check both run on the new container."""
        _result, calls, _root = self.deploy('production')
        execs = [' '.join(c) for c in calls if c[:2] == ['docker', 'exec']]
        self.assertTrue(any('ALLOWED_ORIGINS' in c for c in execs), execs)
        self.assertTrue(any('MEDIA_ROOT' in c for c in execs), execs)

    def test_each_environment_gets_its_own_fixed_volume_name(self):
        for environment in ('dev', 'production'):
            result, _calls, _root = self.deploy(environment)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f'MEDIA_VOLUME=jidan-{environment}-media\n', Path(self.env['ENVFILE']).read_text())

    def test_the_running_container_must_be_able_to_write_media_root(self):
        result, calls, root = self.deploy('dev')
        self.assertEqual(result.returncode, 0, result.stderr)
        checks = [c for c in calls if c[:2] == ['docker', 'exec'] and 'MEDIA_ROOT' in ' '.join(c)]
        self.assertEqual(len(checks), 1)
        self.assertNotIn('print', ' '.join(checks[0]))  # reports nothing from the container env

    def test_unwritable_media_root_fails_the_deployment(self):
        result, calls, root = self.deploy('dev', failure='media')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((root / 'current').exists())
        self.assertTrue(any(c[:2] == ['docker', 'compose'] and 'down' in c for c in calls))


if __name__ == '__main__':
    unittest.main()
