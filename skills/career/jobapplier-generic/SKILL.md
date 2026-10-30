---
name: jobapplier-generic
description: "Use when researching jobs for configurable applicant roles."
version: 1.0.0
metadata:
  hermes:
    tags: [career, jobs, research, applications, supervised]
---

# JobApplier Generic

Operate from the user's checkout of JobApplier Generic. Locate the checkout from the current directory or ask where it is; never assume a fixed home path. Read its AGENTS.md first. This is a research and supervised application assistant, not an unattended bulk-apply system.

## Setup

Read README.md and run the documented setup script if local configuration is missing. The single search configuration is config/wellfound_search.yaml, seeded from config/search.example.yaml. Applicant facts come from profile/applicant.yaml and optional profile/resume.md, not the operator's Hermes memories. Do not install or overwrite another jobapplier skill. This skill has a distinct name so personal and generic workflows can coexist.

Ask for target role titles, preferred keywords, explicit exclusions, locations/remote eligibility and applicant facts when missing. Do not infer engineering, India, seniority or any exclusions. Avoid keywords are explicit exclusions; preferred keywords are not evidence of applicant experience. Read the actual configuration rather than deriving filters from an old prompt.

## Research

1. Reuse the user's already-open browser only after permission and intended source/account confirmation. The Python scripts use their own shared existing-session attachment. Hermes browser attachment does not automatically configure that client. Consult docs/browser.md and the helper's --help. Never launch a browser, switch profiles, copy cookies, auto-confirm identity or bypass site restrictions. Stop and ask the user on any connection, login, CAPTCHA or access blocker.
2. Verify visible Wellfound role/location filters match the requested configuration; its role taxonomy may not cover every occupation. Explain mismatches instead of silently scanning the old/default role family. Use configured LinkedIn queries and locations. Other sites may be researched manually through authorized visible/public tools, but are not claimed as implemented scripted adapters.
3. Research slowly, preserve evidence, deduplicate and apply configured filters. Save jobs and roadblocks rather than inventing missing requirements, dates, eligibility, salaries or contacts. Do not treat a limited scroll run as exhaustive.
4. Generate application materials using only supplied applicant facts and visible job/company facts. Missing facts must remain placeholders or explicit questions. Render a local dashboard and preserve data.json plus data.js for direct file viewing.
5. Report per-source counts, filtered/skipped reasons, blockers, outcomes, elapsed time and the actual dashboard path. Offline samples are fictional verification, not live results.

## Applications and outreach

Research and drafts are the default. A dashboard approval is a review state, not authority to click final action buttons. For an application, inspect the exact job and application form, prepare answers from verified applicant facts, show the final content and obtain separate explicit authorization before submission. Do not guess screening answers. For emails, messages and connections, require exact target/content/send authorization. Use authorized visible Hermes tools and manually supervised site flow; site-specific application forms are not universally automated. Record actual outcomes, and never mark applied/sent on a click receipt alone. Scheduled research never submits or sends.

## Verification

Run python3 -m unittest discover -s tests -v for code changes and the README's offline sample command for dashboard checks. Test installers in a temporary HERMES_HOME, not the user's real profile. Keep resumes, search config, browser data, acquired pages, runs, ledgers and reports untracked. Before sharing, inspect git ls-files and scan the actual release archive for personal data and secrets. Do not publish a repository without owner/name confirmation.
