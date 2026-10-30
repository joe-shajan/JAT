from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from job_research.canonicalize import canonical_email, canonical_job_url, canonical_linkedin_url, stable_hash
from job_research.models import Contact, JobRecord

TERMINAL_JOB_STATUSES = {"applied", "rejected", "stale", "cancelled"}
FINAL_ACTION_STATUSES = {"sent", "applied", "connected"}
SENDABLE_EMAIL_STATUSES = {"verified_public", "verified_api", "generic_public"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class LedgerAction:
    action_id: str
    action_key: str
    kind: str
    target: str
    status: str


class ActionLedger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ActionLedger":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS jobs (
                job_url TEXT PRIMARY KEY,
                company TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'wellfound',
                status TEXT NOT NULL DEFAULT 'new',
                latest_run_id TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS contacts (
                contact_id TEXT PRIMARY KEY,
                company TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT '',
                linkedin_url TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_linkedin
                ON contacts(linkedin_url) WHERE linkedin_url != '';
            CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_email
                ON contacts(email) WHERE email != '';
            CREATE TABLE IF NOT EXISTS drafts (
                draft_id TEXT PRIMARY KEY,
                job_url TEXT NOT NULL,
                contact_id TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL,
                subject TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                body_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending_approval',
                UNIQUE(job_url, contact_id, kind, body_hash)
            );
            CREATE TABLE IF NOT EXISTS actions (
                action_id TEXT PRIMARY KEY,
                action_key TEXT NOT NULL UNIQUE,
                job_url TEXT NOT NULL,
                contact_id TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL,
                target TEXT NOT NULL,
                payload_hash TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending_approval',
                approved_at TEXT NOT NULL DEFAULT '',
                approved_by TEXT NOT NULL DEFAULT '',
                executed_at TEXT NOT NULL DEFAULT '',
                run_id TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT ''
            );
            """
        )
        self.conn.commit()

    def terminal_job_urls(self) -> set[str]:
        placeholders = ",".join("?" for _ in TERMINAL_JOB_STATUSES)
        rows = self.conn.execute(f"SELECT job_url FROM jobs WHERE status IN ({placeholders})", tuple(TERMINAL_JOB_STATUSES)).fetchall()
        return {row["job_url"] for row in rows}

    def upsert_job(self, job: JobRecord, run_id: str, status: str = "pending_approval") -> str:
        url = canonical_job_url(job.job_url)
        now = utc_now()
        existing = self.conn.execute("SELECT status, first_seen FROM jobs WHERE job_url = ?", (url,)).fetchone()
        if existing and existing["status"] in TERMINAL_JOB_STATUSES:
            status = existing["status"]
        elif existing and existing["status"] in {"approved", "approved_to_apply"}:
            status = existing["status"]
        self.conn.execute(
            """
            INSERT INTO jobs(job_url, company, title, first_seen, last_seen, source, status, latest_run_id, notes)
            VALUES (?, ?, ?, ?, ?, 'wellfound', ?, ?, ?)
            ON CONFLICT(job_url) DO UPDATE SET
                company=excluded.company,
                title=excluded.title,
                last_seen=excluded.last_seen,
                latest_run_id=excluded.latest_run_id,
                notes=excluded.notes,
                status=CASE
                    WHEN jobs.status IN ('applied','rejected','stale','cancelled','approved','approved_to_apply') THEN jobs.status
                    ELSE excluded.status
                END
            """,
            (url, job.company, job.title, existing["first_seen"] if existing else now, now, status, run_id, job.notes),
        )
        self.conn.commit()
        return status

    def upsert_contact(self, job: JobRecord, contact: Contact) -> str:
        linkedin_url = canonical_linkedin_url(contact.linkedin_url) if contact.linkedin_url else ""
        email = canonical_email(contact.email) if contact.email else ""
        key_row = None
        if linkedin_url:
            key_row = self.conn.execute("SELECT contact_id FROM contacts WHERE linkedin_url = ?", (linkedin_url,)).fetchone()
        if key_row is None and email:
            key_row = self.conn.execute("SELECT contact_id FROM contacts WHERE email = ?", (email,)).fetchone()
        contact_id = key_row["contact_id"] if key_row else str(uuid.uuid4())
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO contacts(contact_id, company, name, role, linkedin_url, email, source, confidence, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(contact_id) DO UPDATE SET
                company=excluded.company,
                name=excluded.name,
                role=excluded.role,
                linkedin_url=CASE WHEN excluded.linkedin_url != '' THEN excluded.linkedin_url ELSE contacts.linkedin_url END,
                email=CASE WHEN excluded.email != '' THEN excluded.email ELSE contacts.email END,
                source=excluded.source,
                confidence=max(contacts.confidence, excluded.confidence),
                last_seen=excluded.last_seen
            """,
            (contact_id, job.company, contact.name, contact.role, linkedin_url, email, contact.source, float(contact.confidence or 0), now, now),
        )
        self.conn.commit()
        return contact_id

    def upsert_draft(self, job_url: str, contact_id: str, kind: str, subject: str, body: str) -> str:
        body_hash = stable_hash(subject + "\n" + body)
        existing = self.conn.execute(
            "SELECT draft_id FROM drafts WHERE job_url = ? AND contact_id = ? AND kind = ? AND body_hash = ?",
            (job_url, contact_id, kind, body_hash),
        ).fetchone()
        draft_id = existing["draft_id"] if existing else str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO drafts(draft_id, job_url, contact_id, kind, subject, body, body_hash, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending_approval')
            ON CONFLICT(job_url, contact_id, kind, body_hash) DO NOTHING
            """,
            (draft_id, job_url, contact_id, kind, subject, body, body_hash, utc_now()),
        )
        self.conn.commit()
        return body_hash

    def make_action_key(self, kind: str, job_url: str, target: str, payload_hash: str = "") -> str:
        canonical_target = canonical_email(target) if kind == "send_email" else canonical_linkedin_url(target) if kind.startswith("linkedin_") else canonical_job_url(target)
        if kind == "apply":
            return f"apply|{canonical_job_url(job_url)}"
        return f"{kind}|{canonical_job_url(job_url)}|{canonical_target}|{payload_hash}"

    def upsert_action(self, job_url: str, contact_id: str, kind: str, target: str, payload_hash: str, run_id: str) -> LedgerAction:
        canonical_target = canonical_email(target) if kind == "send_email" else canonical_linkedin_url(target) if kind.startswith("linkedin_") else canonical_job_url(target)
        action_key = self.make_action_key(kind, job_url, canonical_target, payload_hash)
        existing = self.conn.execute("SELECT action_id, status FROM actions WHERE action_key = ?", (action_key,)).fetchone()
        action_id = existing["action_id"] if existing else str(uuid.uuid4())
        status = existing["status"] if existing else "pending_approval"
        self.conn.execute(
            """
            INSERT INTO actions(action_id, action_key, job_url, contact_id, kind, target, payload_hash, status, run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_approval', ?)
            ON CONFLICT(action_key) DO NOTHING
            """,
            (action_id, action_key, canonical_job_url(job_url), contact_id, kind, canonical_target, payload_hash, run_id),
        )
        self.conn.commit()
        return LedgerAction(action_id=action_id, action_key=action_key, kind=kind, target=canonical_target, status=status)

    def create_pending_actions_for_job(self, job: JobRecord, run_id: str) -> list[LedgerAction]:
        job_url = canonical_job_url(job.job_url)
        self.upsert_job(job, run_id, status="pending_approval")
        actions: list[LedgerAction] = []

        cover_hash = self.upsert_draft(job_url, "", "cover_letter", "", job.outreach.cover_letter)
        actions.append(self.upsert_action(job_url, "", "apply", job_url, cover_hash, run_id))

        for contact in job.contacts:
            contact_id = self.upsert_contact(job, contact)
            contact.contact_id = contact_id
            if contact.email and contact.email_status in SENDABLE_EMAIL_STATUSES:
                email_body = f"Subject: {job.outreach.email_subject}\n\n{job.outreach.email_body}"
                email_hash = self.upsert_draft(job_url, contact_id, "email", job.outreach.email_subject, job.outreach.email_body)
                action = self.upsert_action(job_url, contact_id, "send_email", contact.email, email_hash, run_id)
                contact.email_action_status = action.status
                actions.append(action)
            if contact.linkedin_url:
                dm_hash = self.upsert_draft(job_url, contact_id, "linkedin_dm", "", job.outreach.linkedin_dm)
                dm_action = self.upsert_action(job_url, contact_id, "linkedin_dm", contact.linkedin_url, dm_hash, run_id)
                contact.linkedin_dm_status = dm_action.status
                actions.append(dm_action)
                connect_hash = self.upsert_draft(job_url, contact_id, "linkedin_connection", "", job.outreach.linkedin_connection)
                connect_action = self.upsert_action(job_url, contact_id, "linkedin_connect", contact.linkedin_url, connect_hash, run_id)
                contact.linkedin_connection_status = connect_action.status
                actions.append(connect_action)

        job.ledger_status = "pending_approval"
        job.action_summary = f"{len(actions)} pending approval action(s); no applications, emails, or LinkedIn messages sent automatically."
        self.conn.commit()
        return actions

    def action_counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) AS count FROM actions GROUP BY status").fetchall()
        return {row["status"]: int(row["count"]) for row in rows}
