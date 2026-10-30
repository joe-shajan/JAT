import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

from job_research.canonicalize import canonical_job_identity, canonical_job_url
from job_research.config import load_config
from job_research.contacts import (
    build_linkedin_search_urls,
    discover_linkedin_contacts,
    enrich_contacts_from_wellfound_profiles,
    extract_linkedin_profile_urls,
    extract_public_emails,
    extract_wellfound_contacts,
    result_mentions_company,
)
from job_research.cli import infer_location_and_remote, is_compensation_line
from job_research.email_leads import (
    company_domain_candidates,
    email_status,
    extract_public_emails as extract_email_leads_public_emails,
)
from job_research.filters import is_geographically_allowed, is_recent_job, is_role_allowed, role_rejection_reason
from job_research.found_jobs_report import load_found_jobs, render_found_jobs_report
from job_research.html_report import render_dashboard
from job_research.linkedin_jobs import (
    build_linkedin_search_specs,
    extract_linkedin_job_urls,
    extract_linkedin_result_links,
    linkedin_snapshot_from_ax_state,
    parse_linkedin_job_snapshot,
    resolve_linkedin_cdp_port,
)
from job_research.linkedin_leads import extract_company_map
from job_research.models import Contact, JobRecord, OutreachDraft
from job_research.roadblocks import record_roadblock
from job_research.role_learning import load_learned_patterns, matches_learned_role, record_learned_role
from job_research.storage import RunPaths, append_jsonl, create_run_paths, save_text_artifact
from job_research.wellfound import (
    extract_recent_wellfound_job_urls,
    extract_wellfound_job_urls,
    guess_title_company,
    parse_posted_at_text,
    posted_label_is_allowed,
)


class WorkflowCoreTests(unittest.TestCase):


    def test_recent_filter_uses_allowed_posted_label_keywords(self):
        allowed = JobRecord(
            job_url="https://wellfound.com/jobs/1",
            title="Full Stack Engineer",
            company="Example",
            posted_at_text="posted 2 days ago",
        )
        rejected = JobRecord(
            job_url="https://wellfound.com/jobs/2",
            title="Full Stack Engineer",
            company="Example",
            posted_at_text="12 hours ago",
        )
        self.assertTrue(is_recent_job(allowed))
        self.assertTrue(is_recent_job(rejected))

    def test_recent_filter_accepts_linkedin_age_labels_within_48_hours(self):
        self.assertTrue(is_recent_job(JobRecord(job_url="u", title="t", company="c", source="linkedin", posted_at_text="3 hours ago")))
        self.assertTrue(is_recent_job(JobRecord(job_url="u", title="t", company="c", source="linkedin", posted_at_text="2 days ago")))
        self.assertFalse(is_recent_job(JobRecord(job_url="u", title="t", company="c", source="linkedin", posted_at_text="3 days ago")))



    def test_compensation_parser_ignores_wellfound_referral_bounty(self):
        text = """
        Software Engineer at ExampleCo
        Earn $200
        Remote
        India
        """
        location, remote_status, compensation = infer_location_and_remote(text)
        self.assertEqual(compensation, "")
        self.assertIn("Remote", location)
        self.assertIn("Remote", remote_status)
        self.assertFalse(is_compensation_line("Earn $200"))
        self.assertFalse(is_compensation_line("Referral bonus $500"))
        self.assertFalse(is_compensation_line("We're on a mission to transform the $7 trillion insurance industry"))
        self.assertTrue(is_compensation_line("Salary $120k - $180k"))
        self.assertTrue(is_compensation_line("$80k - $130k"))

    def test_location_parser_does_not_use_company_summary_as_location(self):
        text = """
        Junior Software Engineer at Health-Tech Software Boutique
        $50k – $90k • No equity
        POSTED TODAY
        Must be U.S. based
        About Invene:
        Invene is a boutique consultancy that helps payers, providers, and HealthTech companies create transformational software. Our entirely U.S. development based team is highly specialized.
        Remote work policy
        Remote only
        Company Location
        United States
        """
        location, remote_status, compensation = infer_location_and_remote(text)
        self.assertNotIn("boutique consultancy", location)
        self.assertIn("U.S. based", location)
        self.assertIn("Remote", remote_status)
        self.assertEqual(compensation, "$50k – $90k • No equity")

    def test_location_parser_prefers_explicit_location_labels(self):
        text = """
        Full Stack Developer at Emplay
        About the Role
        Join the engineering team building ZingerX, an Agentic AI Sales Enablement Platform.
        Location: Remote/Work from Home, Job Type: Full-time
        Hires remotely in
        India
        """
        location, remote_status, _ = infer_location_and_remote(text)
        self.assertEqual(location, "Location: Remote/Work from Home, Job Type: Full-time")
        self.assertIn("Remote", remote_status)




    def test_extract_wellfound_job_urls_dedupes_and_normalizes(self):
        text = """
        https://wellfound.com/jobs/123-full-stack-engineer
        /jobs/456-ai-product-engineer
        https://wellfound.com/jobs/123-full-stack-engineer
        """
        self.assertEqual(
            extract_wellfound_job_urls(text),
            [
                "https://wellfound.com/jobs/123-full-stack-engineer",
                "https://wellfound.com/jobs/456-ai-product-engineer",
            ],
        )

    def test_canonical_job_url_supports_linkedin_job_ids(self):
        self.assertEqual(
            canonical_job_url("https://www.linkedin.com/jobs/view/4250123456/?trackingId=abc"),
            "https://www.linkedin.com/jobs/view/4250123456",
        )

    def test_cross_source_job_identity_normalizes_company_and_title(self):
        wellfound = JobRecord(job_url="wf", title="Senior Full-Stack Engineer", company="Acme, Inc.")
        linkedin = JobRecord(job_url="li", title="senior full stack engineer", company="ACME INC", source="linkedin")
        self.assertEqual(canonical_job_identity(wellfound), canonical_job_identity(linkedin))


    def test_linkedin_uses_the_shared_wellfound_cdp_port(self):
        with mock.patch.dict(
            os.environ,
            {"JOBAPPLIER_CDP_PORT": "9223", "JOBAPPLIER_LINKEDIN_CDP_PORT": "9999"},
            clear=False,
        ):
            self.assertEqual(resolve_linkedin_cdp_port({"cdp_port": 9222}), 9223)

    def test_extract_linkedin_result_links_uses_only_results_list(self):
        state = {
            "elements": [
                {"element_index": 1, "role": "document web", "label": "Jobs | LinkedIn"},
                {"element_index": 2, "parent_index": 1, "role": "list", "label": "results"},
                {"element_index": 3, "parent_index": 2, "role": "list item", "label": ""},
                {"element_index": 4, "parent_index": 3, "role": "link", "label": "Senior Full Stack Engineer with verification"},
                {"element_index": 5, "parent_index": 1, "role": "link", "label": "LinkedIn"},
                {"element_index": 6, "parent_index": 1, "role": "heading", "label": "Senior Full Stack Engineer"},
            ]
        }
        self.assertEqual(extract_linkedin_result_links(state), [("Senior Full Stack Engineer", 4)])

    def test_linkedin_snapshot_from_ax_state_extracts_detail_fields(self):
        state = {
            "elements": [
                {"element_index": 1, "role": "document web", "label": "Senior Full Stack Engineer | LinkedIn"},
                {"element_index": 2, "parent_index": 1, "role": "link", "label": "Acme Technologies"},
                {"element_index": 3, "parent_index": 1, "role": "heading", "label": "Senior Full Stack Engineer"},
                {"element_index": 4, "parent_index": 1, "role": "static", "label": "Bengaluru, Karnataka, India · Reposted 2 hours ago · 31 applicants"},
                {"element_index": 5, "parent_index": 1, "role": "heading", "label": "About the job"},
                {"element_index": 6, "parent_index": 1, "role": "static", "label": "Build React and Node.js products in a remote-friendly team."},
            ]
        }
        snapshot = linkedin_snapshot_from_ax_state(state, "https://www.linkedin.com/jobs/view/4434273217")
        self.assertEqual(snapshot["job_title"], "Senior Full Stack Engineer")
        self.assertEqual(snapshot["company_name"], "Acme Technologies")
        self.assertEqual(snapshot["job_location"], "Bengaluru, Karnataka, India")
        self.assertEqual(snapshot["posted_text"], "Reposted 2 hours ago")
        self.assertIn("Build React and Node.js", snapshot["text"])

    def test_extract_linkedin_job_urls_dedupes_tracking_variants(self):
        snapshot = {
            "anchors": [
                {"href": "https://www.linkedin.com/jobs/view/4250123456/?trackingId=one", "text": "Full Stack Engineer"},
                {"href": "https://www.linkedin.com/jobs/view/4250123456?trackingId=two", "text": "Full Stack Engineer"},
                {"href": "https://www.linkedin.com/jobs/view/4250654321/", "text": "Backend Engineer"},
                {"href": "https://www.linkedin.com/company/example", "text": "Example"},
            ]
        }
        self.assertEqual(
            extract_linkedin_job_urls(snapshot),
            [
                "https://www.linkedin.com/jobs/view/4250123456",
                "https://www.linkedin.com/jobs/view/4250654321",
            ],
        )

    def test_parse_linkedin_job_snapshot_extracts_job_and_hiring_contact(self):
        snapshot = {
            "url": "https://www.linkedin.com/jobs/view/4250123456/?trackingId=abc",
            "title": "Senior Full Stack Engineer | Example AI | LinkedIn",
            "text": """
Senior Full Stack Engineer
Example AI
Bengaluru, Karnataka, India · 1 day ago · 42 applicants
Remote
About the job
Build production AI applications with React, Node.js, Python, and FastAPI.
Meet the hiring team
Jane Recruiter
Talent Partner at Example AI
""",
            "anchors": [
                {"href": "https://www.linkedin.com/company/example-ai/", "text": "Example AI"},
                {"href": "https://www.linkedin.com/in/jane-recruiter/", "text": "Jane Recruiter"},
            ],
            "hiring_contacts": [
                {"href": "https://www.linkedin.com/in/jane-recruiter/", "text": "Jane Recruiter"},
            ],
        }
        job = parse_linkedin_job_snapshot(snapshot)
        self.assertEqual(job.source, "linkedin")
        self.assertEqual(job.job_url, "https://www.linkedin.com/jobs/view/4250123456")
        self.assertEqual(job.title, "Senior Full Stack Engineer")
        self.assertEqual(job.company, "Example AI")
        self.assertEqual(job.location, "Bengaluru, Karnataka, India")
        self.assertEqual(job.posted_at_text, "1 day ago")
        self.assertEqual(job.contacts[0].name, "Jane Recruiter")
        self.assertEqual(job.contacts[0].linkedin_url, "https://www.linkedin.com/in/jane-recruiter")

    def test_parse_linkedin_job_snapshot_uses_browser_title_when_detail_selectors_are_empty(self):
        snapshot = {
            "url": "https://www.linkedin.com/jobs/view/4440057708/",
            "title": "Senior Software Engineer ( Fullstack development) | A.P. Moller - Maersk | LinkedIn",
            "text": "0 notifications\nJobs\nA.P. Moller - Maersk\nSenior Software Engineer ( Fullstack development)\nBengaluru, Karnataka, India · 19 minutes ago",
            "anchors": [
                {"href": "https://www.linkedin.com/company/maersk-group/life/", "text": "A.P. Moller - Maersk"},
            ],
            "job_title": "",
            "company_name": "",
            "job_location": "",
            "posted_text": "",
        }
        job = parse_linkedin_job_snapshot(snapshot)
        self.assertEqual(job.title, "Senior Software Engineer ( Fullstack development)")
        self.assertEqual(job.company, "A.P. Moller - Maersk")
        self.assertEqual(job.location, "Bengaluru, Karnataka, India")
        self.assertEqual(job.posted_at_text, "19 minutes ago")

    def test_guess_title_company_prefers_wellfound_role_heading(self):
        text = """
        RECENT NOTIFICATIONS
        Ready to interview
        Rado-flex
        Actively Hiring
        Full Stack Engineer at Rado-flex
        POSTED TODAY
        """
        self.assertEqual(guess_title_company(text, "https://wellfound.com/jobs/4436931"), ("Full Stack Engineer", "Rado-flex"))

    def test_extract_recent_wellfound_job_urls_requires_allowed_posted_label_near_card(self):
        text = """
        Full Stack Engineer
        https://wellfound.com/jobs/123-full-stack-engineer
        posted today

        Old Role
        https://wellfound.com/jobs/999-old-role
        posted 3 days ago

        AI Product Engineer
        2 days ago
        /jobs/456-ai-product-engineer
        """
        self.assertEqual(
            extract_recent_wellfound_job_urls(text, context_lines=2),
            [
                "https://wellfound.com/jobs/123-full-stack-engineer",
                "https://wellfound.com/jobs/456-ai-product-engineer",
            ],
        )

    def test_parse_posted_at_text(self):
        now = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(parse_posted_at_text("posted today", now=now).date().isoformat(), "2026-06-12")
        self.assertEqual(parse_posted_at_text("posted yesterday", now=now).date().isoformat(), "2026-06-11")
        self.assertEqual(parse_posted_at_text("posted 1 day ago", now=now).date().isoformat(), "2026-06-11")
        self.assertEqual(parse_posted_at_text("posted 2 days ago", now=now).date().isoformat(), "2026-06-10")
        self.assertTrue(posted_label_is_allowed("2 days ago"))
        self.assertTrue(posted_label_is_allowed("12 hours ago"))
        self.assertFalse(posted_label_is_allowed("posted 3 days ago"))
        self.assertTrue(posted_label_is_allowed("today"))
        self.assertIsNone(parse_posted_at_text("12 hours ago", now=now))
        self.assertIsNone(parse_posted_at_text("3 weeks ago", now=now))

    def test_storage_creates_paths_and_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = create_run_paths(root, "2026-06-12-120000")
            self.assertIsInstance(paths, RunPaths)
            self.assertTrue(paths.job_pages_dir.exists())
            saved = save_text_artifact(paths.job_pages_dir, "Example Job", "hello")
            append_jsonl(paths.manifest_path, {"url": "https://wellfound.com/jobs/1", "saved_path": str(saved)})
            self.assertTrue(saved.exists())
            self.assertIn("wellfound.com/jobs/1", paths.manifest_path.read_text())

    def test_filtered_job_roadblock_persists_fields_needed_for_move_to_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roadblocks.jsonl"
            job = JobRecord(
                job_url="https://www.linkedin.com/jobs/view/4440064427",
                title="Senior MLOps Engineer",
                company="ExampleCo",
                location="Bengaluru, Karnataka, India",
                posted_at_text="19 minutes ago",
            )
            record_roadblock(path, "run-1", "job_filter", "No target context marker matched", url=job.job_url, job=job)

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["title"], job.title)
            self.assertEqual(saved["company"], job.company)
            self.assertEqual(saved["location"], job.location)
            self.assertEqual(saved["posted_at_text"], job.posted_at_text)

    def test_contacts_helpers(self):
        emails = extract_public_emails("Email careers@example.com and hello@example.com")
        self.assertEqual(emails, ["careers@example.com", "hello@example.com"])
        urls = build_linkedin_search_urls("Acme AI")
        self.assertTrue(any("founder" in url for url in urls))
        self.assertTrue(any("recruiter" in url or "talent" in url for url in urls))
        self.assertTrue(result_mentions_company("Jane Doe - Founder at ExampleCo", "Building AI systems", "ExampleCo"))
        self.assertTrue(result_mentions_company("Paul - CEO and Co-Founder at Kalepa", "", "Kalepa"))
        self.assertTrue(result_mentions_company("Divyaa Sharma - BDIPlus", "", "BDIPlus"))
        self.assertTrue(result_mentions_company("Grace Park - SingleStore | LinkedIn", "", "SingleStore"))
        self.assertTrue(result_mentions_company("Nishant Budia - Atom11", "", "Atom11"))
        self.assertFalse(result_mentions_company("Karla Aldaco - Talent at Flex", "Recruiting at Flex", "Rado-flex"))
        self.assertFalse(result_mentions_company("Sharon Obuobi - Founder & CEO, Alt/Finance", "", "Alt"))
        self.assertFalse(result_mentions_company("Aimee Taylor - CEO/Owner at Alt Digital Marketing", "", "Alt"))

    def test_wellfound_contacts_are_prioritized_before_public_search(self):
        text = """
        Full Stack Engineer at Acme AI
        Meet your team
        People you would work with in this role
        Jane Founder
        Founder
        https://www.linkedin.com/in/jane-founder
        Rahul Recruiter
        Talent
        https://www.linkedin.com/in/rahul-recruiter
        Similar jobs to Full Stack Engineer at Acme AI
        """
        wellfound_contacts = extract_wellfound_contacts(text, "Acme AI", "https://wellfound.com/jobs/1")
        contacts = discover_linkedin_contacts("Acme AI", wellfound_contacts=wellfound_contacts)
        linkedin_urls = {contact.linkedin_url for contact in contacts}
        self.assertIn("https://www.linkedin.com/in/jane-founder", linkedin_urls)
        self.assertIn("https://www.linkedin.com/in/rahul-recruiter", linkedin_urls)
        self.assertTrue(all(contact.source == "wellfound_visible_profile" for contact in contacts if contact.linkedin_url))
        self.assertFalse(any(contact.source.startswith("manual_search") for contact in contacts))

    def test_wellfound_names_seed_manual_linkedin_fallback_when_profiles_missing(self):
        text = """
        Full Stack Engineer at Emplay
        Meet your team
        People you would work with in this role
        Biplab BiswaKalyan
        Employee
        India
        Similar jobs to Full Stack Developer at Emplay
        """
        with mock.patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": ""}, clear=False):
            contacts = discover_linkedin_contacts("Emplay", wellfound_contacts=extract_wellfound_contacts(text, "Emplay", "https://wellfound.com/jobs/2"))
        self.assertTrue(any(contact.name == "Biplab BiswaKalyan" and contact.source == "wellfound_job_page" for contact in contacts))
        manual = next(contact for contact in contacts if contact.source == "manual_search")
        self.assertTrue(any("Biplab+BiswaKalyan" in url for url in manual.manual_search_urls))

    def test_hiring_contact_section_seeds_wellfound_profile_and_public_fallback(self):
        text = """
        Claude Code Tooling & Evaluation Engineer at BDIPlus
        Hiring contact
        Divyaa Sharma
        Employee
        Culture
        View all photos and perks
        Divyaa Sharma
        https://wellfound.com/u/divyaa-sharma-3
        """
        contacts = extract_wellfound_contacts(text, "BDIPlus", "https://wellfound.com/jobs/4437233")
        by_name = {contact.name: contact for contact in contacts}
        self.assertEqual(by_name["Divyaa Sharma"].source_url, "https://wellfound.com/u/divyaa-sharma-3")

    def test_wellfound_profile_pages_replace_manual_search_with_real_linkedin(self):
        text = """
        Senior Backend Engineer at LearnTube.ai
        Meet your team
        People you would work with in this role
        Gargi Ruparelia
        Employee
        https://wellfound.com/u/gargi-ruparelia-1
        Shronit Ladhani
        Employee
        https://wellfound.com/u/shronit-ladhani
        Similar jobs to Senior Backend Engineer at LearnTube.ai
        """
        profile_pages = {
            "https://wellfound.com/u/gargi-ruparelia-1": "Website\nwww.linkedin.com/in/gargi-ruparelia/\nLocation\nIndia",
            "https://wellfound.com/u/shronit-ladhani": "https://www.linkedin.com/in/shronit-ladhani-b12b8227",
        }
        wellfound_contacts = extract_wellfound_contacts(text, "LearnTube.ai", "https://wellfound.com/jobs/1")
        self.assertTrue(all(contact.source == "wellfound_profile_link" for contact in wellfound_contacts))
        enriched = enrich_contacts_from_wellfound_profiles(wellfound_contacts, profile_pages.get)
        contacts = discover_linkedin_contacts("LearnTube.ai", wellfound_contacts=enriched)
        linkedin_urls = {contact.linkedin_url for contact in contacts}
        self.assertIn("https://www.linkedin.com/in/gargi-ruparelia/", linkedin_urls)
        self.assertIn("https://www.linkedin.com/in/shronit-ladhani-b12b8227", linkedin_urls)
        self.assertFalse(any(contact.manual_search_urls for contact in contacts if contact.linkedin_url))

    def test_wellfound_profile_urls_can_be_appended_after_visible_team_text(self):
        text = """
        Senior Backend Engineer at LearnTube.ai
        Meet your team
        People you would work with in this role
        Gargi Ruparelia
        Co-founder
        Mumbai
        Co-founder: LearnTube.ai | Backed by Google
        Shronit Ladhani
        Founder
        Mumbai
        Founder @LearnTube.ai (backed by Google)
        Culture
        View all photos and perks
        Work from home flexibility
        Similar jobs to Senior Backend Engineer - FastAPI at LearnTube.ai
        Gargi Ruparelia
        https://wellfound.com/u/gargi-ruparelia-1
        Shronit Ladhani
        https://wellfound.com/u/shronit-ladhani
        """
        contacts = extract_wellfound_contacts(text, "LearnTube.ai", "https://wellfound.com/jobs/3301948")
        by_name = {contact.name: contact for contact in contacts}
        self.assertEqual(by_name["Gargi Ruparelia"].source_url, "https://wellfound.com/u/gargi-ruparelia-1")
        self.assertEqual(by_name["Shronit Ladhani"].source_url, "https://wellfound.com/u/shronit-ladhani")
        self.assertNotIn("View all photos and perks", by_name)
        self.assertNotIn("Work from home flexibility", by_name)

    def test_extract_linkedin_profile_urls_normalizes_bare_wellfound_profile_links(self):
        self.assertEqual(
            extract_linkedin_profile_urls("Website\nwww.linkedin.com/in/iambcarroll/."),
            ["https://www.linkedin.com/in/iambcarroll/"],
        )

    def test_email_leads_extracts_public_emails_and_skips_placeholders(self):
        text = "Reach careers@acme.ai, ignore jane@email.com, name@company.com, dev@schema.org, and asset@cdn.png."
        self.assertEqual(extract_email_leads_public_emails(text), ["careers@acme.ai"])


    def test_email_leads_marks_shared_public_mailboxes_as_generic(self):
        self.assertEqual(email_status("support@example.com", "public"), "generic_public")
        self.assertEqual(email_status("jane@example.com", "public"), "verified_public")

    def test_dashboard_contains_essential_job_data_and_copy_buttons(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = JobRecord(
                job_url="https://www.linkedin.com/jobs/view/1",
                title="Full Stack Engineer",
                company="ExampleCo",
                source="linkedin",
                contacts=[Contact(name="Jane", role="Founder", linkedin_url="https://linkedin.com/in/jane")],
                outreach=OutreachDraft(
                    email_subject="Application — Example Applicant",
                    email_body="Hello ExampleCo",
                    cover_letter="Dear ExampleCo",
                    linkedin_dm="Hi Jane",
                    linkedin_connection="Hi Jane, I'd like to connect.",
                ),
            )
            out = render_dashboard(
                Path(tmp),
                "run-1",
                [job],
                roadblocks=[
                    {
                        "stage": "job_filter",
                        "url": "https://wellfound.com/jobs/2-mobile-role",
                        "title": "Mobile Engineer",
                        "reason": "Skipped because role is not an Applied AI, AI/product, full-stack engineer/developer, or relevant software engineer/developer role",
                        "action_taken": "Excluded from dashboard",
                        "next_fallback": "Edit filters if this role should be allowed",
                    }
                ],
                stats={"jobs_checked": 7, "jobs_displayed": 1},
            )
            data_path = out.parent / "data.json"
            data_js_path = out.parent / "data.js"
            self.assertTrue(out.exists())
            data = json.loads(data_path.read_text())
            self.assertTrue(data_js_path.read_text().startswith('window.JOBAPPLIER_DASHBOARD_DATA = '))
            self.assertEqual(data['run_summary']['jobs_checked'], 7)
            self.assertEqual(data['run_summary']['jobs_not_shown'], 6)
            self.assertEqual(data['jobs'][0]['company'], 'ExampleCo')
            self.assertIn('Hello ExampleCo', data['jobs'][0]['email_text'])
            html = out.read_text()
            for token in ['src="data.js"', "fetch('data.json'", 'Approve', 'Reject', 'View reasons', 'Copy ', 'Export decisions']:
                self.assertIn(token, html)
            self.assertNotIn('/api/linkedin-approved-actions', html)
            before = out.read_bytes()
            render_dashboard(out.parent, 'next-run', [job])
            self.assertEqual(out.read_bytes(), before)

    def test_found_jobs_report_renders_current_capture_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "jobs.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "job_url": "https://wellfound.com/jobs/1-full-stack-engineer",
                        "title": "Full Stack Engineer",
                        "primary_role_title": "Full-Stack Engineer",
                        "posted_at_utc": "2026-06-24T10:00:00Z",
                        "remote": True,
                        "location_names": ["Bengaluru"],
                        "accepted_remote_location_names": ["India"],
                        "compensation": "not visible",
                        "job_type": "full-time",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            jobs = load_found_jobs(source)
            out = render_found_jobs_report(
                root / "html",
                "run-1",
                jobs,
                source_path=source,
                status_note="Current verified capture.",
                visible_result_count="1,128 results",
            )

            html = out.read_text(encoding="utf-8")
            self.assertIn("Wellfound 48h Jobs", html)
            self.assertIn("Full Stack Engineer", html)
            self.assertIn("Copy visible URLs", html)
            self.assertIn("Manual review only", html)
            self.assertIn("Current verified capture.", html)
            self.assertIn("1,128 results", html)
            self.assertIn("https://wellfound.com/jobs/1-full-stack-engineer", html)

    def test_extract_company_map_pairs_company_anchor_before_job_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_dir = root / "snapshots"
            snapshot_dir.mkdir()
            (snapshot_dir / "snapshot-000.json").write_text(
                json.dumps(
                    {
                        "anchors": [
                            {"href": "https://wellfound.com/company/acme-ai-2", "text": "Acme AI Actively Hiring"},
                            {
                                "href": "https://wellfound.com/jobs/123-full-stack-engineer",
                                "text": "Full Stack Engineer POSTED TODAY",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            company_map = extract_company_map(snapshot_dir)

            self.assertEqual(company_map["https://wellfound.com/jobs/123-full-stack-engineer"]["company_name"], "Acme AI")
            self.assertEqual(
                company_map["https://wellfound.com/jobs/123-full-stack-engineer"]["company_url"],
                "https://wellfound.com/company/acme-ai-2",
            )

    def test_found_jobs_report_renders_linkedin_contacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "jobs.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "job_url": "https://wellfound.com/jobs/1-full-stack-engineer",
                        "title": "Full Stack Engineer",
                        "posted_label": "POSTED TODAY",
                        "remote": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            jobs = load_found_jobs(source)
            out = render_found_jobs_report(
                root / "html",
                "run-contacts",
                jobs,
                source_path=source,
                contacts_by_job={
                    "https://wellfound.com/jobs/1-full-stack-engineer": [
                        {
                            "name": "Jane Founder",
                            "role": "Founder / Co-founder",
                            "company_name": "Acme AI",
                            "linkedin_url": "https://www.linkedin.com/in/jane-founder",
                            "confidence": 0.9,
                            "source": "bing_html",
                        }
                    ]
                },
            )

            html = out.read_text(encoding="utf-8")
            self.assertIn("Contacts", html)
            self.assertIn("Jane Founder", html)
            self.assertIn("https://www.linkedin.com/in/jane-founder", html)

    def test_found_jobs_report_renders_email_leads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "jobs.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "job_url": "https://wellfound.com/jobs/1-full-stack-engineer",
                        "title": "Full Stack Engineer",
                        "posted_label": "POSTED TODAY",
                        "remote": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            jobs = load_found_jobs(source)
            out = render_found_jobs_report(
                root / "html",
                "run-emails",
                jobs,
                source_path=source,
                email_leads_by_job={
                    "https://wellfound.com/jobs/1-full-stack-engineer": [
                        {
                            "email": "careers@acme.ai",
                            "contact_name": "Jane Founder",
                            "company_name": "Acme AI",
                            "status": "generic_public",
                            "confidence": 0.82,
                            "source": "first_party_website",
                            "source_url": "https://acme.ai/careers",
                        }
                    ]
                },
            )

            html = out.read_text(encoding="utf-8")
            self.assertIn("Email candidates", html)
            self.assertIn("careers@acme.ai", html)
            self.assertIn("mailto:careers@acme.ai", html)
            self.assertIn("generic_public", html)


if __name__ == "__main__":
    unittest.main()
