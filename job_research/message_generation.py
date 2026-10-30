from __future__ import annotations
import json
import os
import shlex
import subprocess
from pathlib import Path
import yaml
from job_research.models import JobRecord, OutreachDraft

WORKSPACE = Path(__file__).resolve().parents[1]
PROMPT_PATH = WORKSPACE / 'prompts' / 'outreach_generation.md'
RESUME_MD = WORKSPACE / 'profile' / 'resume.md'
APPLICANT_PATH = WORKSPACE / 'profile' / 'applicant.yaml'


def read_applicant() -> dict:
    if not APPLICANT_PATH.exists():
        return {}
    value = yaml.safe_load(APPLICANT_PATH.read_text(encoding='utf-8')) or {}
    if not isinstance(value, dict):
        raise ValueError('applicant.yaml must be a mapping')
    for key in ('name', 'headline', 'summary'):
        if not isinstance(value.get(key, ''), str):
            raise ValueError(f'applicant.{key} must be text')
    if not isinstance(value.get('skills', []), list) or any(not isinstance(s, str) for s in value.get('skills', [])):
        raise ValueError('applicant.skills must be a list of strings')
    return value


def read_resume_text() -> str:
    return RESUME_MD.read_text(encoding='utf-8') if RESUME_MD.exists() else ''


def template_outreach(job: JobRecord) -> OutreachDraft:
    applicant = read_applicant()
    name = applicant.get('name', '').strip() or '[Your name]'
    summary = applicant.get('summary', '').strip() or '[Add a factual summary of your relevant experience before sending.]'
    headline = applicant.get('headline', '').strip()
    introduction = f'I’m {name}' + (f', {headline}' if headline else '') + '.'
    # Only applicant-supplied facts: job requirements never become applicant claims.
    email = f'Hi team,\n\n{introduction}\n\nI’m interested in the {job.title} role at {job.company}. {summary}\n\nI would welcome a conversation about the role.\n\nBest,\n{name}'
    cover = f'Dear {job.company} team,\n\nI’m applying for the {job.title} role. {summary}\n\nThank you for considering my application.\n\n{name}'
    dm = f'Hi, I’m {name}. I’m interested in the {job.title} role at {job.company} and would welcome a conversation.'
    connection = f'Hi, I’m {name}. I’m interested in opportunities at {job.company} and would like to connect.'
    return OutreachDraft(email_subject=f'{job.title} application — {name}', email_body=email, cover_letter=cover, linkedin_dm=dm[:600], linkedin_connection=connection[:280])


def generate_outreach(job: JobRecord, use_ai: bool = False) -> OutreachDraft:
    command = os.environ.get('JOBAPPLIER_OUTREACH_COMMAND', '').strip()
    if not use_ai or not command or not PROMPT_PATH.exists():
        return template_outreach(job)
    payload = {'applicant': read_applicant(), 'resume': read_resume_text(), 'job': job.to_dict()}
    prompt = PROMPT_PATH.read_text(encoding='utf-8') + '\n\nInput JSON:\n' + json.dumps(payload, ensure_ascii=False)
    try:
        result = subprocess.run(shlex.split(command), input=prompt, cwd=str(WORKSPACE), text=True, capture_output=True, timeout=300, check=True)
        text = result.stdout.strip()
        data = json.loads(text[text.find('{'):text.rfind('}')+1])
        expected = {'email_subject', 'email_body', 'cover_letter', 'linkedin_dm', 'linkedin_connection'}
        if set(data) != expected or any(not isinstance(v, str) for v in data.values()):
            raise ValueError('Invalid outreach response schema')
        data['linkedin_dm'] = data['linkedin_dm'][:600]
        data['linkedin_connection'] = data['linkedin_connection'][:280]
        return OutreachDraft(**data)
    except Exception:
        job.notes += ' AI outreach failed; used template fallback. Review drafts before use.'
        return template_outreach(job)
