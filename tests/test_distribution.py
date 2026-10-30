import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class DistributionTests(unittest.TestCase):
    def test_neutral_examples_do_not_invent_applicant(self):
        import yaml
        profile = yaml.safe_load((ROOT / 'profile/applicant.example.yaml').read_text())
        self.assertEqual(profile['name'], '')
        self.assertEqual(profile['skills'], [])
        cfg = yaml.safe_load((ROOT / 'config/search.example.yaml').read_text())
        self.assertEqual(cfg['roles']['avoid_keywords'], [])
        self.assertEqual(cfg['locations']['include'], [])

    def test_distinct_skill_has_valid_frontmatter(self):
        import yaml
        text = (ROOT / 'skills/career/jobapplier-generic/SKILL.md').read_text()
        self.assertTrue(text.startswith('---\n'))
        data = yaml.safe_load(text.split('---', 2)[1])
        self.assertEqual(data['name'], 'jobapplier-generic')
        self.assertTrue(text.split('---', 2)[2].strip())

    def test_gitignore_protects_private_files(self):
        import shutil
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            shutil.copy(ROOT / '.gitignore', target / '.gitignore')
            subprocess.run(['git', 'init', '-q'], cwd=target, check=True)
            private = ['.env', 'profile/resume.md', 'profile/applicant.yaml', 'config/wellfound_search.yaml', 'runs/a/jobs.jsonl', 'data/jobs.csv', 'reports/html/latest/data.json', 'chrome-profile/Cookies']
            result = subprocess.run(['git', 'check-ignore', '--stdin'], input='\n'.join(private)+'\n', cwd=target, text=True, capture_output=True)
            self.assertEqual(set(result.stdout.splitlines()), set(private))
            public = ['.env.example', 'profile/applicant.example.yaml', 'profile/README.md', 'config/search.example.yaml']
            result = subprocess.run(['git', 'check-ignore', '--stdin'], input='\n'.join(public)+'\n', cwd=target, text=True, capture_output=True)
            self.assertEqual(result.stdout, '')
