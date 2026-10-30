import csv
import tempfile
import unittest
from pathlib import Path

from job_research.action_ledger import ActionLedger
from job_research.canonicalize import canonical_email, canonical_job_url, canonical_linkedin_url, stable_hash
from job_research.cli import load_job_urls_by_status
from job_research.models import Contact, JobRecord, OutreachDraft


class ActionLedgerTests(unittest.TestCase):
    def test_canonicalizers_strip_tracking_and_normalize_targets(self):
        self.assertEqual(
            canonical_job_url("https://wellfound.com/jobs/123-full-stack?utm_source=x"),
            "https://wellfound.com/jobs/123-full-stack",
        )
        self.assertEqual(
            canonical_linkedin_url("https://linkedin.com/in/Jane-Founder/?trk=public"),
            "https://www.linkedin.com/in/Jane-Founder",
        )
        self.assertEqual(canonical_email(" Jane@Example.COM, "), "jane@example.com")
        self.assertEqual(stable_hash("hello   world"), stable_hash("hello world"))

    def test_ledger_creates_pending_actions_once_per_job_contact_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "jobapplier.sqlite3"
            job = JobRecord(
                job_url="https://wellfound.com/jobs/123-full-stack?utm_source=x",
                title="Full Stack Engineer",
                company="Acme AI",
                contacts=[
                    Contact(
                        name="Jane Founder",
                        role="Founder",
                        linkedin_url="https://linkedin.com/in/jane-founder/?trk=x",
                        email="Jane@Acme.ai",
                        email_status="verified_public",
                    )
                ],
                outreach=OutreachDraft(
                    email_subject="Application — Example Applicant",
                    email_body="Hi Jane",
                    cover_letter="Dear Acme",
                    linkedin_dm="Hi Jane, interested in the role.",
                    linkedin_connection="Hi Jane, I'd like to connect.",
                ),
            )
            with ActionLedger(ledger_path) as ledger:
                first = ledger.create_pending_actions_for_job(job, "run-1")
                second = ledger.create_pending_actions_for_job(job, "run-2")
                self.assertEqual(len(first), 4)
                self.assertEqual(len(second), 4)
                self.assertEqual(ledger.action_counts()["pending_approval"], 4)
                self.assertEqual(job.ledger_status, "pending_approval")
                self.assertIn("pending approval", job.action_summary)

    def test_applied_jobs_are_loaded_from_local_data_for_filtering(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "jobs.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["job_url", "status"])
                writer.writeheader()
                writer.writerow({"job_url": "https://wellfound.com/jobs/1-ai-engineer?x=y", "status": "applied"})
                writer.writerow({"job_url": "https://wellfound.com/jobs/2-full-stack", "status": "new"})
            self.assertEqual(load_job_urls_by_status(csv_path, {"applied"}), {"https://wellfound.com/jobs/1-ai-engineer"})


if __name__ == "__main__":
    unittest.main()
