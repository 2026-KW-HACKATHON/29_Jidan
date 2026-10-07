"""Validate the actual preview build flag shell, without Docker."""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]

class FrontendPreviewTests(unittest.TestCase):
    def test_only_frontend_dev_image_contains_previews(self):
        text = (ROOT / '.github/workflows/component.yml').read_text()
        step = text.split('- name: Configure image', 1)[1].split('- name: Set up hosted builder', 1)[0]
        script = textwrap.dedent(step.split('run: |', 1)[1]).strip()
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for component, environment in [('frontend', 'dev'), ('frontend', 'production'), ('backend', 'dev'), ('backend', 'production')]:
                with self.subTest(component=component, environment=environment):
                    output = folder / 'output'
                    output.write_text('')
                    env = {**os.environ, 'COMPONENT': component, 'ENVIRONMENT': environment, 'GITHUB_OUTPUT': str(output)}
                    subprocess.run(['bash', '-c', script], env=env, check=True, capture_output=True)
                    values = dict(line.split('=', 1) for line in output.read_text().splitlines())
                    expected = 'true' if component == 'frontend' and environment == 'dev' else 'false'
                    self.assertEqual(values['preview'], expected)
                    self.assertEqual(values['image'], f'ghcr.io/2026-kw-hackathon/29_jidan-{component}')
        self.assertIn('VITE_ENABLE_PREVIEW=${{ steps.config.outputs.preview }}', text)

    def test_container_checks_both_artifact_modes_and_defaults_to_no_preview(self):
        text = (ROOT / 'deploy/frontend/Dockerfile').read_text()
        self.assertIn('ARG VITE_ENABLE_PREVIEW=false', text)
        test_stage = text.split('FROM dependencies AS test', 1)[1].split('FROM dependencies AS build', 1)[0]
        self.assertIn('node scripts/verify-preview-build.mjs', test_stage)
