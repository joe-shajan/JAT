#!/usr/bin/env python3
"""Non-overwriting local onboarding. Lists are comma-separated literal phrases."""
import argparse
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('roles', 'keywords', 'exclude', 'locations'):
        parser.add_argument('--' + flag, help='Comma-separated values')
    parser.add_argument('--name', help='Applicant name')
    args = parser.parse_args()
    config_path = ROOT / 'config/wellfound_search.yaml'
    profile_path = ROOT / 'profile/applicant.yaml'
    if config_path.exists() and any(getattr(args, key) is not None for key in ('roles', 'keywords', 'exclude', 'locations')):
        parser.error('Local search config already exists; edit it directly. Nothing overwritten.')
    if profile_path.exists() and args.name is not None:
        parser.error('Local applicant profile already exists; edit it directly. Nothing overwritten.')
    if not config_path.exists():
        cfg = yaml.safe_load((ROOT / 'config/search.example.yaml').read_text())
        for flag, section, key in [('roles','roles','primary'), ('keywords','roles','prefer_keywords'), ('exclude','roles','avoid_keywords'), ('locations','locations','include')]:
            value = getattr(args, flag)
            if value is not None:
                cfg[section][key] = [v.strip() for v in value.split(',') if v.strip()]
        roles = cfg['roles']['primary']
        keywords = cfg['roles']['prefer_keywords']
        cfg['linkedin']['search_queries'] = [' '.join([role] + keywords) for role in roles] if roles else ([' '.join(keywords)] if keywords else [])
        cfg['linkedin']['locations'] = [{'name': loc, 'remote_only': loc.casefold() in ('remote','worldwide','global')} for loc in cfg['locations']['include']] or [{'name': '', 'remote_only': False}]
        cfg['linkedin']['posted_within_seconds'] = cfg['search']['posted_within_hours'] * 3600
        with config_path.open('x', encoding='utf-8') as out:
            yaml.safe_dump(cfg, out, sort_keys=False, allow_unicode=True)
        config_path.chmod(0o600)
        print('Created', config_path)
    else:
        print('Kept existing', config_path)
    if not profile_path.exists():
        applicant = yaml.safe_load((ROOT / 'profile/applicant.example.yaml').read_text())
        if args.name is not None:
            applicant['name'] = args.name
        with profile_path.open('x', encoding='utf-8') as out:
            yaml.safe_dump(applicant, out, sort_keys=False, allow_unicode=True)
        profile_path.chmod(0o600)
        print('Created', profile_path)
    else:
        print('Kept existing', profile_path)
    print('Next: fill factual applicant details, verify site filters and browser attachment, then use Hermes with jobapplier-generic. No external action was taken.')

if __name__ == '__main__':
    main()
