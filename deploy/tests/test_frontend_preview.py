"""Validate the actual preview build flag shell, without Docker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]

class FrontendPreviewTests(unittest.TestCase):
    def test_only_frontend_dev_image_contains_previews(self):
        text = (ROOT / '.github/workflows/component.yml').read_text()
        step = text.split('- name: Test and build ARM64 image', 1)[1].split('- name: Publish image', 1)[0]
        script = textwrap.dedent(step.split('run: |', 1)[1]).strip()
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            docker = folder / 'docker'
            docker.write_text(f'#!{sys.executable}\nimport json,os,sys\nwith open(os.environ["DOCKER_CALLS"],"a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n')
            docker.chmod(0o755)
            for component, environment in [('frontend', 'dev'), ('frontend', 'production'), ('backend', 'dev'), ('backend', 'production')]:
                with self.subTest(component=component, environment=environment):
                    calls = folder / 'calls.jsonl'
                    calls.unlink(missing_ok=True)
                    env = {**os.environ, 'PATH': f'{folder}{os.pathsep}{os.environ["PATH"]}', 'SOURCE': 'front-end', 'COMPONENT': component, 'ENVIRONMENT': environment, 'GITHUB_SHA': 'test-sha', 'DOCKER_CALLS': str(calls)}
                    subprocess.run(['bash', '-c', script], env=env, check=True, capture_output=True)
                    commands = [json.loads(line) for line in calls.read_text().splitlines()]
                    runtime = next(command for command in commands if '--load' in command)
                    flag = runtime[runtime.index('--build-arg') + 1]
                    expected = 'true' if component == 'frontend' and environment == 'dev' else 'false'
                    self.assertEqual(flag, f'VITE_ENABLE_PREVIEW={expected}')

    def test_container_checks_both_artifact_modes_and_defaults_to_no_preview(self):
        text = (ROOT / 'deploy/frontend/Dockerfile').read_text()
        self.assertIn('ARG VITE_ENABLE_PREVIEW=false', text)
        test_stage = text.split('FROM dependencies AS test', 1)[1].split('FROM dependencies AS build', 1)[0]
        self.assertIn('node scripts/verify-preview-build.mjs', test_stage)
