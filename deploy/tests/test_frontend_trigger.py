import fnmatch
from pathlib import Path
import re
import unittest
class FrontendTriggerTests(unittest.TestCase):
 def test_stacked_prs_are_checked_and_feature_pushes_do_not_deploy(self):
  text=(Path(__file__).resolve().parents[2]/'.github/workflows/frontend.yml').read_text()
  groups=re.findall(r'branches:\s*\[([^]]+)\]',text)
  pull,push=[[v.strip().strip("\"'") for v in g.split(',')] for g in groups]
  for branch in ['main','front-end/dev','front-end/feat/47-worker-profile','front-end/fix/45-auth-boundary','front-end/hotfix/1-example']:
   self.assertTrue(any(fnmatch.fnmatchcase(branch,p) for p in pull),branch)
  self.assertEqual(push,['main','front-end/dev'])
  self.assertIn("deploy: ${{ github.event_name != 'pull_request' }}",text)
