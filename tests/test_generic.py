import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import yaml
from job_research.config import load_config
from job_research.filters import is_role_allowed, is_geographically_allowed, is_recent_job, has_user_excluded_role_keyword
from job_research.models import JobRecord
from job_research.message_generation import template_outreach
from job_research.linkedin_jobs import build_linkedin_search_specs

ROOT = Path(__file__).resolve().parents[1]

class GenericTests(unittest.TestCase):
    def config(self):
        return yaml.safe_load((ROOT/'config/search.example.yaml').read_text())

    def job(self, title, **kwargs):
        return JobRecord(job_url='https://example.com/job', title=title, company='Fictional Example', **kwargs)

    def test_no_implicit_role_exclusions(self):
        cfg=self.config()
        for title in ['QA Engineer','Java Developer','Data Scientist','Staff Nurse','Accountant','Graphic Designer','Sales Intern']:
            with self.subTest(title=title): self.assertTrue(is_role_allowed(self.job(title), config=cfg))

    def test_titles_are_configurable_and_keywords_not_a_hidden_gate(self):
        cfg=self.config(); cfg['roles']['primary']=['Product Designer','UX Researcher']; cfg['roles']['prefer_keywords']=['Figma']
        self.assertTrue(is_role_allowed(self.job('Senior Product Designer'), config=cfg))
        self.assertTrue(is_role_allowed(self.job('UX Researcher'), config=cfg))
        self.assertFalse(is_role_allowed(self.job('Java Developer'), config=cfg))

    def test_explicit_exclusions_use_literal_word_boundaries(self):
        cfg=self.config();cfg['roles']['avoid_keywords']=['Java','Data Scientist','C++']
        self.assertTrue(has_user_excluded_role_keyword(self.job('Java Developer'),cfg))
        self.assertTrue(has_user_excluded_role_keyword(self.job('Senior Data Scientist'),cfg))
        self.assertTrue(has_user_excluded_role_keyword(self.job('C++ Developer'),cfg))
        self.assertFalse(has_user_excluded_role_keyword(self.job('JavaScript Developer'),cfg))

    def test_geography_neutral_and_configured(self):
        cfg=self.config();job=self.job('Nurse',location='London')
        self.assertTrue(is_geographically_allowed(job,cfg))
        cfg['locations']['include']=['London']
        self.assertTrue(is_geographically_allowed(job,cfg))
        self.assertFalse(is_geographically_allowed(self.job('Nurse',location='Paris',remote_status='Remote'),cfg))
        self.assertFalse(is_geographically_allowed(self.job('Nurse',description='Company offices in London'),cfg))
        cfg['locations']['include_if_visible']=['visa sponsorship']
        self.assertTrue(is_geographically_allowed(self.job('Nurse',location='Paris',description='Visa sponsorship available'),cfg))

    def test_freshness_configurable_no_special_old_role_exceptions(self):
        for source in ['wellfound','linkedin']:
            self.assertTrue(is_recent_job(self.job('Role',source=source,posted_at_text='12 hours ago'),hours=24))
            self.assertFalse(is_recent_job(self.job('Role',source=source,posted_at_text='2 days ago'),hours=24))
            self.assertTrue(is_recent_job(self.job('Role',source=source,posted_at_text='Posted 1 week ago'),hours=168))
            self.assertFalse(is_recent_job(self.job('Founding Core App Builder',source=source,posted_at_text='Posted 1 month ago')))
            self.assertFalse(is_recent_job(self.job('Role',source=source,posted_at_text='unknown')))

    def test_drafts_only_use_applicant_facts(self):
        from job_research import message_generation as messages
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'applicant.yaml';p.write_text(yaml.safe_dump({'name':'Example Applicant','headline':'a designer','summary':'I have experience with user interviews.','skills':['Research']}))
            with mock.patch.object(messages,'APPLICANT_PATH',p):
                draft=template_outreach(self.job('Marketing Manager',matched_skills=['React'],description='Must know Node.js'))
            self.assertIn('Example Applicant',draft.email_body)
            self.assertIn('user interviews',draft.cover_letter)
            self.assertNotIn('React',draft.email_body)
            self.assertNotIn('Node.js',draft.cover_letter)
            with mock.patch.object(messages,'APPLICANT_PATH',Path(td)/'missing'):
                self.assertIn('[Your name]',template_outreach(self.job('Nurse')).email_body)

    def test_queries_use_configured_roles_and_freshness(self):
        cfg=self.config();cfg['roles']['primary']=['Accountant'];cfg['locations']['include']=['Toronto'];cfg['search']['posted_within_hours']=168
        specs=build_linkedin_search_specs(cfg)
        self.assertEqual(len(specs),1)
        self.assertIn('Accountant',specs[0].url);self.assertIn('Toronto',specs[0].url);self.assertIn('r604800',specs[0].url)

    def test_setup_and_installer_are_isolated_and_nonoverwriting(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);checkout=base/'checkout'
            for folder in ['scripts','config','profile','skills']:
                shutil.copytree(ROOT/folder,checkout/folder)
            for p in [checkout/'config/wellfound_search.yaml',checkout/'profile/applicant.yaml']:
                if p.exists():p.unlink()
            command=[sys.executable,str(checkout/'scripts/setup.py')]
            result=subprocess.run(command+['--roles','Accountant,Auditor','--keywords','Excel','--locations','Toronto','--name','Example'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            cfg=yaml.safe_load((checkout/'config/wellfound_search.yaml').read_text())
            self.assertEqual(cfg['roles']['primary'],['Accountant','Auditor'])
            self.assertEqual(cfg['linkedin']['search_queries'],['Accountant Excel','Auditor Excel'])
            old=(checkout/'config/wellfound_search.yaml').read_bytes()
            self.assertNotEqual(subprocess.run(command+['--roles','Nurse'],capture_output=True).returncode,0)
            self.assertEqual((checkout/'config/wellfound_search.yaml').read_bytes(),old)
            env={**os.environ,'HERMES_HOME':str(base/'hermes')}
            personal=base/'hermes/skills/career/jobapplier/SKILL.md';personal.parent.mkdir(parents=True);personal.write_text('KEEP')
            install=['bash',str(checkout/'scripts/install-hermes-skills.sh')]
            self.assertEqual(subprocess.run(install,env=env,capture_output=True).returncode,0)
            self.assertNotEqual(subprocess.run(install,env=env,capture_output=True).returncode,0)
            self.assertEqual(subprocess.run(install+['--force'],env=env,capture_output=True).returncode,0)
            self.assertEqual(personal.read_text(),'KEEP')

    def test_mocked_acquisition_pipeline_accepts_nonengineering_and_enforces_exclusions(self):
        from job_research import cli
        cfg=self.config();cfg['roles']['primary']=['Accountant'];cfg['roles']['avoid_keywords']=['Intern'];cfg['linkedin']['enabled']=False
        search='https://wellfound.com/jobs/123-accountant\nhttps://wellfound.com/jobs/124-intern'
        pages={'https://wellfound.com/jobs':search,'https://wellfound.com/jobs/123-accountant':'Accountant at Fictional Example\nPosted today\nLocation: London\nFinancial reporting.','https://wellfound.com/jobs/124-intern':'Accountant Intern at Fictional Example\nPosted today\nLocation: London'}
        with tempfile.TemporaryDirectory() as td:
            workspace=Path(td);shutil.copytree(ROOT/'templates',workspace/'templates')
            # render_dashboard resolves its template from the installed checkout; acquisition is mocked, never live evidence.
            args=cli.parse_args
            argv=['jobapplier','--use-current-wellfound-filters','--skip-linkedin','--skip-local-filters','--ledger-path',str(workspace/'ledger.sqlite3')]
            with mock.patch.object(cli,'WORKSPACE',workspace),mock.patch.object(cli,'load_config',return_value=cfg),mock.patch('sys.argv',argv),mock.patch('job_research.browser_attach.attach_page') as attach,mock.patch.object(cli.ChromeDumpFetcher,'fetch_url',side_effect=lambda url,*a,**k:pages[url]),mock.patch.object(cli,'discover_linkedin_contacts',return_value=[]):
                summary=cli.run()
            self.assertEqual(summary['jobs'],1)
            self.assertEqual(summary['jobs_checked'],2)
            rows=[json.loads(x) for x in (workspace/'runs'/summary['run_id']/'jobs.jsonl').read_text().splitlines()]
            self.assertEqual(rows[0]['title'],'Accountant')
            self.assertTrue(Path(summary['dashboard']).exists())
