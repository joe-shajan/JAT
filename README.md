# JobApplier Generic for Hermes Agent

A configurable replica of JobApplier for job seekers in any occupation. Search for role titles and keywords you choose, review matching jobs, and prepare application materials from your own profile. No built-in engineering, country or seniority requirement; no hidden QA, Java or Data Scientist exclusions.

## What it does

- Scripted research adapters for Wellfound and LinkedIn, using your existing authorized browser session.
- Configurable role titles, preferred keywords, exclusions, locations and posting age.
- Local evidence capture, deduplication, job/action ledger and review dashboards.
- Draft cover letters, emails and LinkedIn messages for human review.
- A separate `jobapplier-generic` Hermes skill; it does not replace another JobApplier installation.

This is not a universal unattended auto-apply bot. It prepares applications; Hermes can assist with individual application forms under supervision. Final submission or outreach requires separate approval for the exact target and content. Site-specific forms, screening questions and external applicant-tracking systems are not universally scripted. Wellfound's available role taxonomy may not cover every occupation; verify the actual visible filters rather than assuming keyword configuration changes the website UI.

## Requirements

- Python 3.10+ and Git.
- Hermes Agent installed and configured with your own model/provider: https://hermes-agent.nousresearch.com/docs/
- For live research, an already-open supported Chrome/Chromium session with your own logins and manually approved attachment. See [browser setup](docs/browser.md).

Linux is the primary tested platform. macOS/Windows browser discovery and shell runners are not certified; Windows users can use WSL for the Python/shell tooling. No paid enrichment service is required. Optional public-search enrichment can use your own Brave API key.

## Quick start

Clone this repository, then set up your local environment:

```bash
git clone https://github.com/joe-shajan/JAT.git
cd JAT
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/setup.py --roles "Product Designer,UX Researcher" --keywords "Figma,user research" --locations "London,Worldwide"
bash scripts/install-hermes-skills.sh
hermes -s jobapplier-generic
```

Tell Hermes:

> Read AGENTS.md. Help me complete my applicant profile and verify my search configuration. Research the configured jobs using my existing browser, build the review dashboard, and draft application materials. Do not submit applications or send anything.

Setup creates `config/wellfound_search.yaml` and `profile/applicant.yaml` without overwriting existing files. Edit those files to change roles or applicant facts. Add optional plain-text resume content to `profile/resume.md`. These local files are ignored by Git. Never put passwords into prompts or resume files.

If another Hermes profile is active, set `HERMES_HOME` to that profile's home when installing. The installer refuses to overwrite an existing generic skill unless explicitly passed `--force`. Start a fresh Hermes session or run `/reload-skills` after installation.

## Filter configuration

The tracked `config/search.example.yaml` is a neutral template; local configuration is in `config/wellfound_search.yaml`.

1. `roles.primary`: accepted role-title phrases. Empty means no title restriction.
2. `roles.prefer_keywords`: desired context/skills for searching and matching; not invented applicant skills.
3. `roles.avoid_keywords`: explicit exclusions only. Empty means no role exclusions.
4. `locations.include`: acceptable locations; empty means no local geography restriction. A remote label alone does not prove country eligibility.
5. `locations.include_if_visible`: optional visible relocation/visa exceptions. Leave empty unless you want them.
6. `search.posted_within_hours`: posting-age window from visible age labels, not a guarantee of precise publication time. Unknown ages remain unknown.
7. `linkedin.search_queries` and `linkedin.locations`: actual LinkedIn search inputs, seeded by setup. After manually changing role/location filters, keep these search inputs aligned as well.

Examples of valid role sets: accounting, nursing, marketing, design, QA, Java development, data science, sales, teaching or engineering. Job-board coverage and site filters still limit what can actually be discovered.

## Offline verification

```bash
python -m unittest discover -s tests -v
bash scripts/run-wellfound-48h-dashboard.sh --sample
```

The sample is fictional, runs without a browser, and proves only local processing/dashboard generation. Its output is not a live job result. Use the printed dashboard path to open it directly; dashboards include both `data.json` and `data.js` for direct file viewing.

## Live operation

Read `docs/browser.md`, confirm the intended existing browser/session, and ask Hermes to verify source filters before running the workflow. A reachable endpoint is not proof of the correct account. Live commands must fail closed when attachment confirmation, login or access is missing. Never bypass CAPTCHA, access checks or rate limits.

The deterministic research entry point is:

```bash
bash scripts/run-wellfound-48h-dashboard.sh --limit 50
```

Do not pass `--skip-local-filters` for ordinary research: it intentionally relaxes local checks. Do not mistake job approval in the dashboard for submission permission. Contact enrichment and drafts should be reviewed for accuracy before use. Schedules are optional and are not installed automatically; scheduled research must never submit or send.

## Private artifacts

Runtime outputs belong in `runs/`, `fetched_pages/`, `reports/`, `logs/` and `data/`; all are ignored. They may contain sensitive applicant/contact information. Review and remove them before handing your working folder to someone else. Share the clean release archive or Git repository, not a folder containing your browser profile or resume.

## Upload to your own GitHub repository

No repository is created or published by setup. Before your first upload:

```bash
python tools/check_release.py
python -m unittest discover -s tests -v
git init -b main
git add .
git diff --cached --stat
git diff --cached
```

Inspect the staged files carefully, then commit and create a repository under the owner/name you choose. If using GitHub CLI:

```bash
git commit -m "feat: configurable JobApplier for Hermes Agent"
gh auth login
gh repo create YOUR-OWNER/YOUR-REPOSITORY --public --source=. --remote=origin --push
```

Replace both placeholders. Public publication is your choice; use `--private` instead if needed. Never add private configuration with `git add -f`. GitHub Actions runs offline unit tests on pushes and pull requests. Local test results do not imply remote CI has run.

## License

MIT; see LICENSE. You are responsible for site terms, local privacy obligations, factual application content and final external actions.
