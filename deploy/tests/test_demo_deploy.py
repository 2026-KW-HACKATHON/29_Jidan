import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "demo/deploy.sh"
FRONT = "ghcr.io/2026-kw-hackathon/29_jidan-demo-frontend@sha256:" + "a" * 64
BACK = "ghcr.io/2026-kw-hackathon/29_jidan-demo-backend@sha256:" + "b" * 64

DOCKER = r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
root=Path(os.environ['MOCK_ROOT']); args=sys.argv[1:]
with (root/'calls').open('a') as f:f.write(' '.join(args)+'\n')
if args[:2]==['ps','-aq']:sys.exit(0)
if args[0]=='compose':
    env=Path(args[args.index('--env-file')+1]); (root/'last').write_text(str(env))
    command=args[args.index('-f')+2:]
    if command[:2]==['ps','-q']:print(command[2])
    if command[0]=='up' and (root/'fail-once').exists():
        (root/'fail-once').unlink();sys.exit(1)
    if command[0]=='up' and (root/'fail-all').exists():sys.exit(1)
elif args[0]=='inspect':
    values=dict(line.split('=',1) for line in Path((root/'last').read_text()).read_text().splitlines())
    value=values['FRONTEND_IMAGE' if args[-1]=='frontend' else 'BACKEND_IMAGE']
    print('wrong-image' if (root/'wrong-image').exists() else value)
'''
CURL = r'''#!/usr/bin/env python3
import sys
from pathlib import Path
args=sys.argv[1:]
if '-o' in args:
    role='WORKER' if 'ilban' in args[-1] else 'OWNER'
    Path(args[args.index('-o')+1]).write_text('{"role":"'+role+'"}')
'''


class DemoDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.mock = Path(self.temporary.name)
        self.root = self.mock / "demo"
        self.root.mkdir()
        (self.root / "runtime.env").write_text("DB_NAME=test_demo\n")
        (self.root / "mysql.env").write_text("MYSQL_DATABASE=test_demo\n")
        binaries = self.mock / "bin"
        binaries.mkdir()
        for name, source in (("docker", DOCKER), ("curl", CURL), ("flock", "#!/bin/sh\nexit 0\n")):
            file = binaries / name
            file.write_text(source)
            file.chmod(0o755)
        self.environment = {**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                            "MOCK_ROOT": str(self.mock), "JIDAN_DEMO_ROOT": str(self.root)}

    def run_deploy(self, revision="1" * 40, frontend=FRONT):
        return subprocess.run(["bash", str(SCRIPT), revision, frontend, BACK], env=self.environment,
                              capture_output=True, text=True, check=False, timeout=10)

    def test_success_digest_gate_and_private_release_snapshot(self):
        result = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        release = (self.root / "current").resolve()
        self.assertTrue((release / "runtime.env").is_file())
        self.assertFalse((self.root / "pending").exists())
        calls = (self.mock / "calls").read_text()
        self.assertIn("run --rm --no-deps owner", calls)
        self.assertNotIn(" build", calls)
        self.assertIn(FRONT, (release / "images.env").read_text())

    def test_failure_restores_previous_bundle_and_leaves_current_unchanged(self):
        self.assertEqual(self.run_deploy().returncode, 0)
        previous = (self.root / "current").resolve()
        (self.mock / "fail-once").touch()
        result = self.run_deploy("2" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / "current").resolve(), previous)
        self.assertFalse((self.root / "pending").exists())
        self.assertIn(str(previous), (self.mock / "calls").read_text().splitlines()[-1])

    def test_first_deploy_failure_removes_only_demo_project_without_volumes(self):
        (self.mock / "fail-once").touch()
        self.assertNotEqual(self.run_deploy().returncode, 0)
        self.assertFalse((self.root / "current").exists())
        last = (self.mock / "calls").read_text().splitlines()[-1]
        self.assertIn("--project-name jidan-demo", last)
        self.assertTrue(last.endswith(" down"))
        self.assertNotIn("volumes", last)

    def test_rollback_failure_preserves_pending_and_next_run_recovers(self):
        self.assertEqual(self.run_deploy().returncode, 0)
        (self.mock / "fail-all").touch()
        self.assertNotEqual(self.run_deploy("2" * 40).returncode, 0)
        self.assertTrue((self.root / "pending").exists())
        (self.mock / "fail-all").unlink()
        self.assertEqual(self.run_deploy("3" * 40).returncode, 0)
        self.assertFalse((self.root / "pending").exists())

    def test_wrong_image_and_invalid_inputs_never_commit_a_release(self):
        for revision, frontend in (("bad", FRONT), ("1" * 40, "ghcr.io/other@sha256:" + "a" * 64)):
            self.assertNotEqual(self.run_deploy(revision, frontend).returncode, 0)
            self.assertFalse((self.mock / "calls").exists())
        (self.mock / "wrong-image").touch()
        self.assertNotEqual(self.run_deploy().returncode, 0)
        self.assertFalse((self.root / "current").exists())


if __name__ == "__main__":
    unittest.main()
