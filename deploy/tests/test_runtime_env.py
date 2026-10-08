import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check_runtime_env.py'
SPEC = importlib.util.spec_from_file_location('check_runtime_env', SCRIPT)
runtime_env = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime_env)
# A well-formed scrypt ADMIN_PASSWORD_HASH (format only; no real password behind it).
VALID_HASH = 'scrypt$14$8$1$c3Nzc3Nzc3Nzc3Nzc3Nzcw$ZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGQ'


class RuntimeEnvTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'runtime.env'

    def test_each_environment_accepts_only_its_own_origin(self):
        for environment, origin in runtime_env.ORIGINS.items():
            with self.subTest(environment=environment):
                self.path.write_text(f'# ALLOWED_ORIGINS=ignored\nDB_PASSWORD=secret\nALLOWED_ORIGINS={origin}\n')
                self.path.chmod(0o600)
                before = self.path.read_bytes()
                self.assertTrue(runtime_env.valid_allowed_origins(environment, self.path))
                other = 'production' if environment == 'dev' else 'dev'
                self.assertFalse(runtime_env.valid_allowed_origins(other, self.path))
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_missing_empty_duplicate_mixed_and_noncanonical_values_are_rejected(self):
        for environment, origin in runtime_env.ORIGINS.items():
            other = next(value for value in runtime_env.ORIGINS.values() if value != origin)
            cases = [
                '', '# ALLOWED_ORIGINS=' + origin, 'ALLOWED_ORIGINS', 'ALLOWED_ORIGINS=',
                'ALLOWED_ORIGINS= , ,', 'ALLOWED_ORIGINS=*',
                f'ALLOWED_ORIGINS={other}', f'ALLOWED_ORIGINS={origin},{other}',
                f'ALLOWED_ORIGINS={origin},{origin}', f'ALLOWED_ORIGINS={origin},',
                'ALLOWED_ORIGINS=https://evil.example.com',
                f'ALLOWED_ORIGINS={origin}/', f'ALLOWED_ORIGINS={origin}/path',
                f'ALLOWED_ORIGINS={origin}:443', f'ALLOWED_ORIGINS={origin}:8443',
                f'ALLOWED_ORIGINS={origin}?query=1', f'ALLOWED_ORIGINS={origin}#fragment',
                f'ALLOWED_ORIGINS={origin}.evil.example.com',
                f'ALLOWED_ORIGINS="{origin}"', f"ALLOWED_ORIGINS='{origin}'",
                'ALLOWED_ORIGINS=${ORIGIN}', f'export ALLOWED_ORIGINS={origin}',
                *[f'ALLOWED_ORIGINS={origin}{separator}UNRELATED=value'
                  for separator in ('\v', '\f', '\x1c', '\x85', '\u2028', '\u2029')],
                f'ALLOWED_ORIGINS= {origin}', f'ALLOWED_ORIGINS={origin} ',
                f'ALLOWED_ORIGINS={origin} # comment',
                f'ALLOWED_ORIGINS={origin}\nALLOWED_ORIGINS={origin}',
                f'ALLOWED_ORIGINS={other}\nALLOWED_ORIGINS={origin}',
                f'ALLOWED_ORIGINS\nALLOWED_ORIGINS={origin}',
            ]
            for content in cases:
                with self.subTest(environment=environment, content=content):
                    self.path.write_text(content + '\n')
                    self.assertFalse(runtime_env.valid_allowed_origins(environment, self.path))

    def test_crlf_file_and_unrelated_values_are_accepted(self):
        origin = runtime_env.ORIGINS['dev']
        self.path.write_bytes(f'DB_PASSWORD=a=b $c #d\r\n\r\nALLOWED_ORIGINS={origin}\r\n'.encode())
        self.assertTrue(runtime_env.valid_allowed_origins('dev', self.path))

    def test_unreadable_or_invalid_file_and_unknown_environment_fail_without_leaking(self):
        cases = [('dev', b'DB_PASSWORD=do-not-print\nALLOWED_ORIGINS=do-not-print\n'),
                 ('dev', b'\xffdo-not-print'), ('unknown', b'do-not-print')]
        for environment, content in cases:
            with self.subTest(environment=environment, content=content):
                self.path.write_bytes(content)
                self.assert_safe_failure(environment)
        self.path.unlink()
        self.assert_safe_failure('dev')
        self.path.mkdir()
        self.assert_safe_failure('dev')

    def assert_safe_failure(self, environment):
        result = subprocess.run([sys.executable, str(SCRIPT), environment, str(self.path)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')
        self.assertNotIn('do-not-print', result.stderr)
        self.assertNotIn(str(self.path), result.stderr)

    def test_cli_success_and_invalid_arguments(self):
        self.path.write_text('ALLOWED_ORIGINS=' + runtime_env.ORIGINS['production'] + '\n'
                             f'ADMIN_PASSWORD_HASH={VALID_HASH}\n')
        result = subprocess.run([sys.executable, str(SCRIPT), 'production', str(self.path)],
                                capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        for args in [[], ['dev'], ['dev', str(self.path), 'unexpected']]:
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True)
                self.assertEqual(result.returncode, 2)


class AdminPasswordHashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'runtime.env'

    def write(self, hash_lines):
        origin = runtime_env.ORIGINS['dev']
        self.path.write_text(f'ALLOWED_ORIGINS={origin}\n{hash_lines}\nDB_PASSWORD=do-not-print\n')

    def test_existing_scrypt_hash_is_accepted(self):
        for line in (f'ADMIN_PASSWORD_HASH={VALID_HASH}', f'# ADMIN_PASSWORD_HASH=old\nADMIN_PASSWORD_HASH={VALID_HASH}',
                     f'ADMIN_PASSWORD_HASH={VALID_HASH}\r'):
            with self.subTest(line=line):
                self.write(line)
                self.assertTrue(runtime_env.valid_admin_password_hash(self.path))
                result = subprocess.run([sys.executable, str(SCRIPT), 'dev', str(self.path)],
                                        capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))

    def test_remote_pbkdf2_hash_is_accepted_unchanged(self):
        stored = 'pbkdf2_sha256$600000$c3Nzc3Nzc3Nzc3Nzc3Nzcw==$ZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGQ='
        for ending in ('\n', '\r\n'):
            with self.subTest(ending=ending):
                content = ('ALLOWED_ORIGINS=' + runtime_env.ORIGINS['dev'] + ending
                           + 'ADMIN_PASSWORD_HASH=' + stored + ending)
                self.path.write_bytes(content.encode())
                before = self.path.read_bytes()
                result = subprocess.run([sys.executable, str(SCRIPT), 'dev', str(self.path)],
                                        capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
                self.assertEqual(self.path.read_bytes(), before)

    def test_missing_duplicate_and_malformed_hashes_are_rejected_without_leaking(self):
        salt, key = VALID_HASH.split('$')[4:]
        cases = [
            '', 'ADMIN_PASSWORD_HASH', 'ADMIN_PASSWORD_HASH=', '# ADMIN_PASSWORD_HASH=' + VALID_HASH,
            f'ADMIN_PASSWORD_HASH={VALID_HASH}\nADMIN_PASSWORD_HASH={VALID_HASH}',
            f'ADMIN_PASSWORD_HASH="{VALID_HASH}"', f"ADMIN_PASSWORD_HASH='{VALID_HASH}'",
            'ADMIN_PASSWORD_HASH=${ADMIN_HASH}', 'ADMIN_PASSWORD_HASH=do-not-print',
            # PBKDF2 below the approved minimum is invalid.
            'ADMIN_PASSWORD_HASH=pbkdf2_sha256$599999$c3Nzc3Nzc3Nzc3Nzc3Nzcw==$ZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGQ=',
            f'ADMIN_PASSWORD_HASH=scrypt$13$8$1${salt}${key}', f'ADMIN_PASSWORD_HASH=scrypt$18$8$1${salt}${key}',
            f'ADMIN_PASSWORD_HASH=scrypt$16$1$1${salt}${key}',  # OpenSSL scrypt refuses N >= 2**(16 * r)
            f'ADMIN_PASSWORD_HASH=scrypt$14$8$1${salt}${key[:-1]}',
        ]
        for content in cases:
            with self.subTest(content=content):
                self.write(content)
                self.assertFalse(runtime_env.valid_admin_password_hash(self.path))
                result = subprocess.run([sys.executable, str(SCRIPT), 'dev', str(self.path)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertIn('ADMIN_PASSWORD_HASH', result.stderr)
                for secret in ('do-not-print', 'c3Nzc3Nz', 'ZGRkZGRk', str(self.path)):
                    self.assertNotIn(secret, result.stderr)

    def test_origin_check_still_runs_first(self):
        self.path.write_text(f'ALLOWED_ORIGINS=https://wrong.example.com\nADMIN_PASSWORD_HASH={VALID_HASH}\n')
        result = subprocess.run([sys.executable, str(SCRIPT), 'dev', str(self.path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('ALLOWED_ORIGINS', result.stderr)

    def test_checkout_without_backend_fails_closed(self):
        self.write(f'ADMIN_PASSWORD_HASH={VALID_HASH}')
        original = runtime_env.BACK_END
        runtime_env.BACK_END = Path(self.temp.name) / 'missing-back-end'
        saved = {name: module for name, module in sys.modules.items() if name == 'app' or name.startswith('app.')}
        for name in saved:
            del sys.modules[name]
        try:
            self.assertFalse(runtime_env.valid_admin_password_hash(self.path))
        finally:
            runtime_env.BACK_END = original
            sys.modules.update(saved)
        self.assertNotIn(str(Path(self.temp.name) / 'missing-back-end'), sys.path)


if __name__ == '__main__':
    unittest.main()
