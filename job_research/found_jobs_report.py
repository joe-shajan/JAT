from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def load_contacts_by_job(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if not path or not path.exists():
        return {}
    contacts_by_job: dict[str, list[dict[str, Any]]] = {}
    for row in _read_jsonl(path):
        job_url = str(row.get("job_url") or "")
        if not job_url:
            continue
        contacts_by_job.setdefault(job_url, []).append(row)
    for rows in contacts_by_job.values():
        rows.sort(key=lambda row: float(row.get("confidence") or 0), reverse=True)
    return contacts_by_job


def load_email_leads_by_job(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if not path or not path.exists():
        return {}
    emails_by_job: dict[str, list[dict[str, Any]]] = {}
    for row in _read_jsonl(path):
        job_url = str(row.get("job_url") or "")
        if not job_url:
            continue
        emails_by_job.setdefault(job_url, []).append(row)
    status_order = {"verified_public": 0, "generic_public": 1, "pattern_inferred": 2, "unverified_candidate": 3}
    for rows in emails_by_job.values():
        rows.sort(key=lambda row: (status_order.get(str(row.get("status")), 9), -float(row.get("confidence") or 0)))
    return emails_by_job


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _join(values: object) -> str:
    if isinstance(values, list):
        return ", ".join(str(value) for value in values if value)
    if values is None:
        return ""
    return str(values)


def _compact(value: object) -> str:
    return " ".join(str(value or "").split())


def _job_flags(job: dict[str, Any]) -> list[str]:
    from job_research.config import load_config
    from job_research.filters import matches_phrase
    text = ' '.join(str(job.get(k, '')) for k in ('title', 'role', 'job_type'))
    return [phrase for phrase in load_config().get('roles', {}).get('avoid_keywords', []) if matches_phrase(phrase, text)]


def _location_label(job: dict[str, Any]) -> str:
    locations = _join(job.get("location_names"))
    accepted_remote = _join(job.get("accepted_remote_location_names"))
    if locations and accepted_remote:
        return f"{locations}; remote accepted: {accepted_remote}"
    if locations:
        return locations
    if accepted_remote:
        return f"Remote accepted: {accepted_remote}"
    if job.get("remote"):
        return "Remote"
    return "not visible"


def _remote_label(job: dict[str, Any]) -> str:
    if job.get("remote") is True:
        return "Remote"
    if job.get("remote") is False:
        return "Onsite / office"
    return "not visible"


def _posted_label(job: dict[str, Any]) -> str:
    posted = _parse_datetime(job.get("posted_at_utc"))
    if posted:
        return posted.strftime("%b %d, %Y %H:%M UTC")
    return str(job.get("posted_label") or "not visible")


def _source_label(job: dict[str, Any], fallback: str) -> str:
    if job.get("posted_at_utc"):
        return "Apollo timestamp"
    if job.get("posted_label"):
        return "Visible label"
    return fallback


def load_found_jobs(path: Path, source_label: str = "Current capture") -> list[dict[str, Any]]:
    rows = _read_jsonl(path)
    jobs: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        title = _compact(row.get("title") or row.get("title_location_comp") or row.get("visible_text") or "Untitled job")
        role = _compact(row.get("primary_role_title") or row.get("primaryRoleTitle") or "not visible")
        posted_at = row.get("posted_at_utc")
        job = {
            "index": index,
            "job_url": row.get("job_url") or "",
            "title": title,
            "role": role,
            "posted_at_utc": posted_at or "",
            "posted_label": _posted_label(row),
            "location": _location_label(row),
            "remote": _remote_label(row),
            "compensation": _compact(row.get("compensation") or "not visible"),
            "job_type": _compact(row.get("job_type") or row.get("jobType") or "not visible"),
            "source": _source_label(row, source_label),
        }
        flags = _job_flags(job)
        job["flags"] = flags
        job["review_status"] = "Review carefully" if flags else "Ready for review"
        job["search_text"] = " ".join(
            str(job.get(key, ""))
            for key in ("title", "role", "location", "remote", "compensation", "job_type", "review_status")
        ).lower()
        jobs.append(job)

    jobs.sort(
        key=lambda job: _parse_datetime(job.get("posted_at_utc")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    for index, job in enumerate(jobs, start=1):
        job["index"] = index
    return jobs


def _metric(label: str, value: object, detail: str = "") -> str:
    return f"""
      <div class="metric">
        <span>{_esc(label)}</span>
        <strong>{_esc(value)}</strong>
        <small>{_esc(detail)}</small>
      </div>
    """


def _badge(text: str, tone: str = "neutral") -> str:
    return f"<span class=\"badge badge--{_esc(tone)}\">{_esc(text)}</span>"


def _stats(jobs: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(jobs),
        "remote": sum(1 for job in jobs if job.get("remote") == "Remote"),
        "flagged": sum(1 for job in jobs if job.get("flags")),
        "visible_timestamp": sum(1 for job in jobs if job.get("posted_at_utc")),
    }


def _contact_list(job: dict[str, Any]) -> str:
    contacts = list(job.get("contacts") or [])
    email_markup = _email_list(job)
    if not contacts:
        return f"""
          <section class="contact-strip">
            <h3>Contacts</h3>
            <p>No contact enrichment has been run for this job yet.</p>
            {email_markup}
          </section>
        """
    items: list[str] = []
    for contact in contacts[:4]:
        confidence = int(float(contact.get("confidence") or 0) * 100)
        profile_link = ""
        if contact.get("linkedin_url"):
            profile_link = f"<a href=\"{_esc(contact.get('linkedin_url'))}\" target=\"_blank\" rel=\"noopener\">LinkedIn</a>"
        manual_links = "".join(
            f"<a href=\"{_esc(url)}\" target=\"_blank\" rel=\"noopener\">Search</a>"
            for url in list(contact.get("manual_search_urls") or [])[:2]
        )
        links = profile_link + manual_links
        if not links:
            links = "<span>not visible</span>"
        items.append(
            f"""
              <li>
                <strong>{_esc(contact.get('name') or 'Manual search needed')}</strong>
                <span>{_esc(contact.get('role') or 'Founder / recruiter / hiring manager')}</span>
                <small>{_esc(contact.get('company_name') or '')} · Confidence {confidence}% · {_esc(contact.get('source') or 'not visible')}</small>
                <div class="contact-links">{links}</div>
              </li>
            """
        )
    return f"""
      <section class="contact-strip">
        <h3>Contacts</h3>
        <ul>{"".join(items)}</ul>
        {email_markup}
      </section>
    """


def _email_list(job: dict[str, Any]) -> str:
    emails = list(job.get("email_leads") or [])
    if not emails:
        return ""
    items: list[str] = []
    for email in emails[:5]:
        confidence = int(float(email.get("confidence") or 0) * 100)
        status = str(email.get("status") or "not_found")
        source_link = ""
        if email.get("source_url"):
            source_link = f"<a href=\"{_esc(email.get('source_url'))}\" target=\"_blank\" rel=\"noopener\">Source</a>"
        mailto = ""
        if email.get("email"):
            mailto = f"<a href=\"mailto:{_esc(email.get('email'))}\">Mailto</a>"
        items.append(
            f"""
              <li>
                <strong>{_esc(email.get('email') or 'not found')}</strong>
                <span>{_esc(email.get('contact_name') or email.get('company_name') or '')}</span>
                <small>{_esc(status)} · Confidence {confidence}% · {_esc(email.get('source') or '')}</small>
                <div class="contact-links">{mailto}{source_link}</div>
              </li>
            """
        )
    return f"""
      <div class="email-leads">
        <h4>Email candidates</h4>
        <ul>{"".join(items)}</ul>
      </div>
    """


def _job_card(job: dict[str, Any]) -> str:
    flags = list(job.get("flags") or [])
    flag_markup = "".join(_badge(flag, "warn") for flag in flags)
    if not flag_markup:
        flag_markup = _badge("manual review", "ok")
    source_tone = "info" if job.get("source") == "Apollo timestamp" else "neutral"
    role_tone = "accent" if "Full" in str(job.get("role", "")) else "neutral"
    return f"""
      <article class="job-row"
        data-search="{_esc(job.get('search_text', ''))}"
        data-remote="{_esc(job.get('remote', ''))}"
        data-flagged="{_esc('yes' if flags else 'no')}"
        data-url="{_esc(job.get('job_url', ''))}">
        <div class="job-row__rank">#{_esc(job.get('index'))}</div>
        <div class="job-row__main">
          <div class="job-row__topline">
            {_badge(str(job.get('source', 'Current capture')), source_tone)}
            {_badge(str(job.get('role', 'not visible')), role_tone)}
            {flag_markup}
          </div>
          <h2>{_esc(job.get('title'))}</h2>
          <dl class="job-facts">
            <div><dt>Posted</dt><dd>{_esc(job.get('posted_label'))}</dd></div>
            <div><dt>Location</dt><dd>{_esc(job.get('location'))}</dd></div>
            <div><dt>Remote</dt><dd>{_esc(job.get('remote'))}</dd></div>
            <div><dt>Compensation</dt><dd>{_esc(job.get('compensation'))}</dd></div>
          </dl>
          {_contact_list(job)}
        </div>
        <div class="job-row__actions">
          <a class="button button--primary" href="{_esc(job.get('job_url'))}" target="_blank" rel="noopener">Open job</a>
          <button class="button" type="button" onclick="copyOneUrl(this)">Copy URL</button>
        </div>
      </article>
    """


def render_found_jobs_report(
    output_dir: Path,
    run_id: str,
    jobs: list[dict[str, Any]],
    *,
    source_path: Path | None = None,
    contacts_by_job: dict[str, list[dict[str, Any]]] | None = None,
    email_leads_by_job: dict[str, list[dict[str, Any]]] | None = None,
    generated_at: datetime | None = None,
    status_note: str = "",
    visible_result_count: str = "",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = generated_at or datetime.now(timezone.utc)
    contacts_by_job = contacts_by_job or {}
    email_leads_by_job = email_leads_by_job or {}
    for job in jobs:
        job_url = str(job.get("job_url") or "")
        job["contacts"] = contacts_by_job.get(job_url, [])
        job["email_leads"] = email_leads_by_job.get(job_url, [])
    stats = _stats(jobs)
    job_cards = "\n".join(_job_card(job) for job in jobs) or """
      <section class="empty-state">
        <h2>No jobs available in the current artifact.</h2>
        <p>Resume the Wellfound scan, then render this report again.</p>
      </section>
    """
    status_note = status_note or "Current capture. External actions are manual only."
    source_text = str(source_path) if source_path else "not recorded"
    result_text = visible_result_count or "not recorded"
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Wellfound 48h Jobs - {_esc(run_id)}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: oklch(10% 0.018 250);
      --bg-soft: oklch(14% 0.018 250);
      --surface: oklch(17% 0.02 250 / 0.86);
      --surface-up: oklch(21% 0.025 250 / 0.92);
      --fg: oklch(95% 0.01 95);
      --fg-muted: oklch(75% 0.018 250);
      --fg-dim: oklch(58% 0.02 250);
      --line: oklch(31% 0.025 250);
      --line-strong: oklch(44% 0.035 250);
      --primary: oklch(76% 0.14 190);
      --primary-ink: oklch(13% 0.022 235);
      --accent: oklch(76% 0.16 145);
      --warn: oklch(82% 0.15 78);
      --danger: oklch(70% 0.18 28);
      --info: oklch(72% 0.14 245);
      --shadow: oklch(0% 0 0 / 0.36);
      --r-card: 8px;
      --r-pill: 999px;
      --ease-out: cubic-bezier(0.22, 1, 0.36, 1);
      --dur-fast: 160ms;
      --dur-base: 260ms;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--fg);
      background:
        linear-gradient(180deg, oklch(13% 0.02 250) 0%, var(--bg) 34rem),
        linear-gradient(135deg, oklch(12% 0.018 230), oklch(9% 0.014 260));
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image: linear-gradient(oklch(100% 0 0 / 0.035) 1px, transparent 1px), linear-gradient(90deg, oklch(100% 0 0 / 0.028) 1px, transparent 1px);
      background-size: 48px 48px;
      mask-image: linear-gradient(to bottom, oklch(0% 0 0 / 0.76), transparent 72%);
    }}
    a {{ color: inherit; }}
    button, input, select {{ font: inherit; }}
    .page {{
      position: relative;
      width: min(1280px, calc(100% - 32px));
      margin: 0 auto;
      padding: 24px 0 64px;
    }}
    .topbar {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 18px;
      align-items: start;
      padding: 22px;
      border: 1px solid var(--line);
      border-radius: var(--r-card);
      background: linear-gradient(135deg, oklch(100% 0 0 / 0.08), oklch(100% 0 0 / 0.025)), var(--surface);
      box-shadow: 0 18px 60px var(--shadow);
      backdrop-filter: blur(20px) saturate(160%);
    }}
    .eyebrow {{
      margin: 0 0 8px;
      color: var(--primary);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0;
      text-transform: uppercase;
    }}
    h1, h2, h3 {{ letter-spacing: 0; }}
    h1 {{
      margin: 0;
      font-size: clamp(28px, 4vw, 48px);
      line-height: 1.04;
      max-width: 820px;
    }}
    .subtitle {{
      max-width: 860px;
      margin: 12px 0 0;
      color: var(--fg-muted);
      line-height: 1.6;
    }}
    .topbar__meta {{
      display: flex;
      justify-content: end;
      gap: 8px;
      flex-wrap: wrap;
      max-width: 360px;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      min-height: 26px;
      border: 1px solid var(--line);
      border-radius: var(--r-pill);
      padding: 4px 9px;
      color: var(--fg-muted);
      background: oklch(100% 0 0 / 0.055);
      font-size: 12px;
      font-weight: 750;
      white-space: nowrap;
    }}
    .badge--ok {{ color: var(--accent); border-color: oklch(76% 0.16 145 / 0.38); background: oklch(76% 0.16 145 / 0.12); }}
    .badge--warn {{ color: var(--warn); border-color: oklch(82% 0.15 78 / 0.38); background: oklch(82% 0.15 78 / 0.12); }}
    .badge--info {{ color: var(--info); border-color: oklch(72% 0.14 245 / 0.36); background: oklch(72% 0.14 245 / 0.12); }}
    .badge--accent {{ color: var(--primary); border-color: oklch(76% 0.14 190 / 0.38); background: oklch(76% 0.14 190 / 0.12); }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin: 16px 0;
    }}
    .metric {{
      min-height: 102px;
      border: 1px solid var(--line);
      border-radius: var(--r-card);
      padding: 16px;
      background: var(--surface);
      box-shadow: 0 12px 40px oklch(0% 0 0 / 0.18);
    }}
    .metric span, .metric small {{
      display: block;
      color: var(--fg-dim);
      font-size: 12px;
    }}
    .metric strong {{
      display: block;
      margin: 8px 0 4px;
      font-size: 30px;
      line-height: 1;
    }}
    .filters {{
      position: sticky;
      top: 10px;
      z-index: 5;
      display: grid;
      grid-template-columns: minmax(240px, 1fr) 180px 180px auto;
      gap: 10px;
      margin: 16px 0;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: var(--r-card);
      background: oklch(12% 0.018 250 / 0.82);
      backdrop-filter: blur(18px) saturate(160%);
      box-shadow: 0 16px 50px oklch(0% 0 0 / 0.24);
    }}
    .filters input, .filters select {{
      min-height: 42px;
      width: 100%;
      border: 1px solid var(--line);
      border-radius: var(--r-card);
      padding: 0 12px;
      color: var(--fg);
      background: oklch(100% 0 0 / 0.06);
      outline: none;
    }}
    .filters input:focus, .filters select:focus {{
      border-color: var(--primary);
      box-shadow: 0 0 0 3px oklch(76% 0.14 190 / 0.16);
    }}
    .button {{
      min-height: 42px;
      border: 1px solid var(--line-strong);
      border-radius: var(--r-card);
      padding: 10px 12px;
      color: var(--fg);
      background: oklch(100% 0 0 / 0.065);
      text-decoration: none;
      cursor: pointer;
      font-weight: 760;
      transition: transform var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out), background var(--dur-fast) var(--ease-out);
    }}
    .button:hover {{
      transform: translateY(-1px);
      border-color: var(--primary);
      background: oklch(100% 0 0 / 0.10);
    }}
    .button--primary {{
      color: var(--primary-ink);
      border-color: transparent;
      background: linear-gradient(135deg, var(--primary), var(--accent));
    }}
    .status-note {{
      margin: 0 0 16px;
      border: 1px solid oklch(82% 0.15 78 / 0.34);
      border-radius: var(--r-card);
      padding: 14px 16px;
      color: oklch(89% 0.08 78);
      background: oklch(82% 0.15 78 / 0.10);
      line-height: 1.55;
    }}
    .job-list {{
      display: grid;
      gap: 10px;
    }}
    .job-row {{
      display: grid;
      grid-template-columns: 58px minmax(0, 1fr) auto;
      gap: 14px;
      align-items: start;
      border: 1px solid var(--line);
      border-radius: var(--r-card);
      padding: 16px;
      background: linear-gradient(180deg, oklch(100% 0 0 / 0.06), oklch(100% 0 0 / 0.025)), var(--surface);
      box-shadow: 0 14px 44px oklch(0% 0 0 / 0.18);
      content-visibility: auto;
      contain-intrinsic-size: 210px;
    }}
    .job-row[hidden] {{ display: none; }}
    .job-row__rank {{
      min-height: 42px;
      display: grid;
      place-items: center;
      border-radius: var(--r-card);
      color: var(--primary);
      background: oklch(76% 0.14 190 / 0.12);
      font-weight: 850;
    }}
    .job-row__topline {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 8px;
    }}
    .job-row h2 {{
      margin: 0 0 12px;
      font-size: clamp(18px, 2vw, 24px);
      line-height: 1.2;
    }}
    .job-facts {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      margin: 0;
    }}
    .job-facts div {{
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: var(--r-card);
      padding: 9px 10px;
      background: oklch(0% 0 0 / 0.12);
    }}
    .job-facts dt {{
      margin: 0 0 4px;
      color: var(--fg-dim);
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
    }}
    .job-facts dd {{
      margin: 0;
      color: var(--fg-muted);
      font-size: 13px;
      line-height: 1.38;
      overflow-wrap: anywhere;
    }}
    .contact-strip {{
      margin-top: 12px;
      border: 1px solid var(--line);
      border-radius: var(--r-card);
      padding: 12px;
      background: oklch(0% 0 0 / 0.10);
    }}
    .contact-strip h3 {{
      margin: 0 0 8px;
      color: var(--fg);
      font-size: 13px;
      text-transform: uppercase;
    }}
    .contact-strip p {{
      margin: 0;
      color: var(--fg-dim);
      font-size: 13px;
    }}
    .contact-strip ul {{
      display: grid;
      gap: 8px;
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    .contact-strip li {{
      display: grid;
      gap: 3px;
      border-top: 1px solid var(--line);
      padding-top: 8px;
    }}
    .contact-strip li:first-child {{
      border-top: 0;
      padding-top: 0;
    }}
    .email-leads {{
      margin-top: 10px;
      border-top: 1px solid var(--line);
      padding-top: 10px;
    }}
    .email-leads h4 {{
      margin: 0 0 8px;
      color: var(--fg-muted);
      font-size: 12px;
      text-transform: uppercase;
    }}
    .contact-strip strong {{ font-size: 14px; }}
    .contact-strip span, .contact-strip small {{
      color: var(--fg-muted);
      font-size: 12px;
    }}
    .contact-links {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .contact-links a {{
      border: 1px solid var(--line-strong);
      border-radius: var(--r-pill);
      padding: 3px 8px;
      color: var(--primary);
      text-decoration: none;
      font-size: 12px;
      font-weight: 750;
    }}
    .job-row__actions {{
      display: grid;
      gap: 8px;
      width: 118px;
    }}
    .empty-state {{
      border: 1px dashed var(--line-strong);
      border-radius: var(--r-card);
      padding: 32px;
      background: var(--surface);
    }}
    .empty-state p {{ color: var(--fg-muted); }}
    .footer-note {{
      margin-top: 18px;
      color: var(--fg-dim);
      font-size: 12px;
      line-height: 1.6;
    }}
    #toast {{
      position: fixed;
      right: 18px;
      bottom: 18px;
      z-index: 20;
      transform: translateY(18px);
      opacity: 0;
      border: 1px solid var(--line);
      border-radius: var(--r-card);
      padding: 11px 13px;
      color: var(--primary-ink);
      background: var(--primary);
      font-weight: 800;
      transition: opacity var(--dur-base) var(--ease-out), transform var(--dur-base) var(--ease-out);
    }}
    #toast.show {{ opacity: 1; transform: translateY(0); }}
    @starting-style {{
      .topbar, .metric, .job-row {{ opacity: 0; transform: translateY(10px); }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      html {{ scroll-behavior: auto; }}
      *, *::before, *::after {{ transition-duration: 1ms !important; animation-duration: 1ms !important; }}
    }}
    @media (max-width: 980px) {{
      .topbar, .filters, .job-row {{ grid-template-columns: 1fr; }}
      .topbar__meta {{ justify-content: start; max-width: none; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .job-facts {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .job-row__actions {{ width: 100%; grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 640px) {{
      .page {{ width: min(100% - 18px, 1280px); padding-top: 10px; }}
      .metrics, .job-facts, .job-row__actions {{ grid-template-columns: 1fr; }}
      .filters {{ position: static; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <header class="topbar">
      <div>
        <p class="eyebrow">Wellfound current capture</p>
        <h1>48-hour job review list</h1>
        <p class="subtitle">Configured role and location filters. External actions stay manual.</p>
      </div>
      <div class="topbar__meta" aria-label="Run metadata">
        {_badge(f"Run {run_id}", "info")}
        {_badge("Manual review only", "ok")}
        {_badge(f"Result count {result_text}", "neutral")}
      </div>
    </header>

    <section class="metrics" aria-label="Report summary">
      {_metric("Jobs shown", stats["total"], "current artifact")}
      {_metric("Remote roles", stats["remote"], "remote flag visible")}
      {_metric("Needs caution", stats["flagged"], "configured exclusion markers")}
      {_metric("Timestamped", stats["visible_timestamp"], "exact posted time in cache")}
    </section>

    <section class="filters" aria-label="Job filters">
      <input id="searchInput" type="search" placeholder="Search title, role, location, compensation" oninput="applyFilters()">
      <select id="remoteFilter" onchange="applyFilters()" aria-label="Remote filter">
        <option value="all">All locations</option>
        <option value="Remote">Remote</option>
        <option value="Onsite / office">Onsite / office</option>
      </select>
      <select id="reviewFilter" onchange="applyFilters()" aria-label="Review filter">
        <option value="all">All review states</option>
        <option value="clean">Ready for review</option>
        <option value="flagged">Needs caution</option>
      </select>
      <button class="button" type="button" onclick="copyVisibleUrls()">Copy visible URLs</button>
    </section>

    <p class="status-note">{_esc(status_note)}</p>

    <section class="job-list" id="jobs" aria-label="Jobs">
      {job_cards}
    </section>

    <p class="footer-note">Generated {_esc(generated_at.isoformat())}. Source: {_esc(source_text)}. This page opens job pages only; it does not apply, save, connect, message, or send emails.</p>
  </main>
  <div id="toast" role="status" aria-live="polite">Copied</div>
  <script>
function toast(message) {{
  const el = document.getElementById('toast');
  el.textContent = message || 'Copied';
  el.classList.add('show');
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => el.classList.remove('show'), 1600);
}}
function fallbackCopy(text, message) {{
  const el = document.createElement('textarea');
  el.value = text;
  document.body.appendChild(el);
  el.select();
  document.execCommand('copy');
  el.remove();
  toast(message);
}}
function copyText(text, message) {{
  if (navigator.clipboard && window.isSecureContext) {{
    navigator.clipboard.writeText(text).then(() => toast(message)).catch(() => fallbackCopy(text, message));
  }} else {{
    fallbackCopy(text, message);
  }}
}}
function cards() {{
  return Array.from(document.querySelectorAll('.job-row'));
}}
function applyFilters() {{
  const query = (document.getElementById('searchInput').value || '').trim().toLowerCase();
  const remote = document.getElementById('remoteFilter').value;
  const review = document.getElementById('reviewFilter').value;
  let visible = 0;
  for (const card of cards()) {{
    const matchesSearch = !query || (card.dataset.search || '').includes(query);
    const matchesRemote = remote === 'all' || card.dataset.remote === remote;
    const matchesReview = review === 'all' || (review === 'flagged' ? card.dataset.flagged === 'yes' : card.dataset.flagged === 'no');
    const show = matchesSearch && matchesRemote && matchesReview;
    card.hidden = !show;
    if (show) visible += 1;
  }}
  document.title = `Wellfound 48h Jobs (${{visible}} visible)`;
}}
function visibleUrls() {{
  return cards().filter(card => !card.hidden).map(card => card.dataset.url).filter(Boolean);
}}
function copyVisibleUrls() {{
  copyText([...new Set(visibleUrls())].join('\\n'), 'Visible URLs copied');
}}
function copyOneUrl(button) {{
  const card = button.closest('.job-row');
  copyText(card.dataset.url || '', 'Job URL copied');
}}
applyFilters();
  </script>
</body>
</html>
"""
    out = output_dir / "index.html"
    out.write_text(html_text, encoding="utf-8")
    return out
