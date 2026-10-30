from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from job_research.canonicalize import canonical_job_url, canonical_linkedin_url
from job_research.browser_attach import attach_page, BrowserAttachError
from job_research.models import Contact, JobRecord, SavedPage
from job_research.storage import RunPaths, append_jsonl, slugify


LINKEDIN_ACCESS_MARKERS = (
    "checkpoint",
    "security verification",
    "verify it's you",
    "verify it’s you",
    "unusual activity",
    "authwall",
    "captcha",
)
LINKEDIN_LOGGED_OUT_MARKERS = (
    "join now",
    "new to linkedin?",
    "email or phone",
    "session_redirect",
)
POSTED_PATTERN = re.compile(
    r"\b(?:(?:reposted|posted)\s+)?(?:\d+\s+(?:minute|hour|day)s?\s+ago|today|yesterday)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LinkedInSearchSpec:
    keywords: str
    location: str
    remote_only: bool
    url: str


class LinkedInBlocked(BrowserAttachError):
    pass


def resolve_linkedin_cdp_port(linkedin_config: dict[str, Any]) -> int:
    """Legacy port-mode hint only; existing-session discovery ignores this value."""
    raw_port = (
        os.environ.get("JOBAPPLIER_CDP_PORT")
        or os.environ.get("JOBAPPLIER_LINKEDIN_CDP_PORT")
        or linkedin_config.get("cdp_port")
        or 9223
    )
    return int(raw_port)


def build_linkedin_search_specs(config: dict[str, Any]) -> list[LinkedInSearchSpec]:
    """Return a bounded set of recent LinkedIn searches matching the user's role families."""
    linkedin_config = config.get("linkedin") or {}
    role_queries = linkedin_config.get("search_queries") or config.get("roles", {}).get("primary", []) or config.get("roles", {}).get("prefer_keywords", [])
    location_modes = linkedin_config.get("locations") or [{"name": loc, "remote_only": False} for loc in config.get("locations", {}).get("include", [])] or [{"name": "", "remote_only": False}]
    posted_within_seconds = int(config.get("search", {}).get("posted_within_hours", 48)) * 3600
    specs: list[LinkedInSearchSpec] = []
    for keywords in role_queries:
        for location_config in location_modes:
            location = str(location_config.get("name") or "")
            remote_only = bool(location_config.get("remote_only"))
            params = {
                "keywords": keywords,
                "location": location,
                "f_TPR": f"r{posted_within_seconds}",
                "sortBy": "DD",
            }
            if remote_only:
                params["f_WT"] = "2"
            url = "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode(params)
            specs.append(LinkedInSearchSpec(keywords, location, remote_only, url))
    return specs


def _snapshot_text(snapshot: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(snapshot.get("url") or ""),
            str(snapshot.get("title") or ""),
            str(snapshot.get("text") or ""),
        ]
    ).lower()


def blocked_reason(snapshot: dict[str, Any]) -> str:
    lowered = _snapshot_text(snapshot)
    for marker in LINKEDIN_ACCESS_MARKERS:
        if marker in lowered:
            return marker
    if any(marker in lowered for marker in LINKEDIN_LOGGED_OUT_MARKERS):
        return "login_required"
    return ""


def extract_linkedin_job_urls(snapshot: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for anchor in snapshot.get("anchors") or []:
        href = str(anchor.get("href") or "")
        if not re.search(r"linkedin\.com/jobs/view/\d+", href):
            continue
        canonical = canonical_job_url(href)
        if canonical in seen:
            continue
        seen.add(canonical)
        urls.append(canonical)
    return urls


def _clean_lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in (text or "").splitlines() if " ".join(line.split())]


def _first_posted_marker(lines: list[str]) -> str:
    for line in lines[:40]:
        match = POSTED_PATTERN.search(line)
        if match:
            return match.group(0)
    return ""


def _location_from_lines(lines: list[str], company: str) -> str:
    from job_research.config import load_config
    from job_research.filters import matches_phrase
    locations = load_config().get('locations', {}).get('include', [])
    for line in lines[:25]:
        candidate = line.split('·', 1)[0].strip()
        if not candidate or candidate == company or len(candidate) > 120:
            continue
        if any(matches_phrase(loc, candidate) for loc in locations) or (',' in candidate and not POSTED_PATTERN.search(candidate)) or re.search(r'\b(remote|worldwide|global)\b', candidate, re.I):
            return candidate
    return ''


def _title_company(snapshot: dict[str, Any], lines: list[str]) -> tuple[str, str]:
    title = " ".join(str(snapshot.get("job_title") or "").split())
    company = " ".join(str(snapshot.get("company_name") or "").split())
    browser_title = " ".join(str(snapshot.get("title") or "").split())
    browser_parts = [part.strip() for part in browser_title.split("|") if part.strip()]
    if not title and len(browser_parts) >= 2 and browser_parts[-1].casefold() == "linkedin":
        title = browser_parts[0]
    anchors = snapshot.get("anchors") or []
    if not company:
        for anchor in anchors:
            href = str(anchor.get("href") or "")
            text = " ".join(str(anchor.get("text") or "").split())
            if "/company/" in href and text and len(text) <= 100:
                company = text
                break
    ignored = {
        "linkedin",
        "jobs",
        "about the job",
        "apply",
        "save",
        "show more",
        "meet the hiring team",
    }
    if not title:
        for line in lines[:20]:
            lowered = line.lower()
            if lowered in ignored or line == company or "applicant" in lowered:
                continue
            if POSTED_PATTERN.search(line) or "·" in line:
                continue
            title = line
            break
    if not title:
        title = browser_title.split("|", 1)[0].strip()
    if not company and len(lines) > 1:
        company = next((line for line in lines[1:8] if line != title and "·" not in line), "")
    return title[:180], company[:140]


def _hiring_contacts(snapshot: dict[str, Any], text: str) -> list[Contact]:
    has_hiring_section = "meet the hiring team" in text.lower() or "job poster" in text.lower()
    if not has_hiring_section:
        return []
    contacts: list[Contact] = []
    seen: set[str] = set()
    for anchor in snapshot.get("hiring_contacts") or []:
        href = str(anchor.get("href") or "")
        if "linkedin.com/in/" not in href:
            continue
        linkedin_url = canonical_linkedin_url(href)
        if linkedin_url in seen:
            continue
        name = " ".join(str(anchor.get("text") or "").split())
        if not name or len(name) > 100:
            continue
        seen.add(linkedin_url)
        contacts.append(
            Contact(
                name=name,
                role="Hiring team / job poster",
                linkedin_url=linkedin_url,
                source="linkedin_job_page",
                source_url=canonical_job_url(str(snapshot.get("url") or "")),
                confidence=0.9,
            )
        )
    return contacts[:3]


def parse_linkedin_job_snapshot(snapshot: dict[str, Any]) -> JobRecord:
    text = str(snapshot.get("text") or "")
    lines = _clean_lines(text)
    title, company = _title_company(snapshot, lines)
    location = " ".join(str(snapshot.get("job_location") or "").split()) or _location_from_lines(lines, company)
    posted = " ".join(str(snapshot.get("posted_text") or "").split()) or _first_posted_marker(lines)
    lowered = text.lower()
    remote_status = "Remote/remote-friendly visible" if "remote" in lowered else ""
    now = datetime.now(timezone.utc)
    return JobRecord(
        job_url=canonical_job_url(str(snapshot.get("url") or "")),
        title=title,
        company=company,
        source="linkedin",
        location=location,
        remote_status=remote_status,
        posted_at_text=posted,
        description=text[:15000],
        source_urls=[canonical_job_url(str(snapshot.get("url") or ""))],
        contacts=_hiring_contacts(snapshot, text),
        first_seen=now.date().isoformat(),
        last_seen=now.date().isoformat(),
    )


class LinkedInCdpFetcher:
    """Slow visible reader attached to the verified existing LinkedIn Chrome tab."""

    def __init__(self, paths: RunPaths, cdp_port: int, delay_seconds: float = 3.0) -> None:
        self.paths = paths
        self.cdp_port = cdp_port
        self.delay_seconds = max(2.0, delay_seconds)
        self._ws: Any = None
        self._next_id = 0

    def __enter__(self) -> "LinkedInCdpFetcher":
        self._ws = attach_page("linkedin.com", self.cdp_port)
        self._send("Runtime.enable")
        self._send("Page.enable")
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._ws is not None:
            self._ws.close()

    def _send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        message_id = self._next_id
        self._ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self._ws.recv())
            if message.get("id") != message_id:
                continue
            if "error" in message:
                raise RuntimeError(json.dumps(message["error"]))
            return message.get("result", {})

    def _evaluate(self, expression: str, await_promise: bool = False) -> Any:
        result = self._send(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": await_promise, "returnByValue": True},
        )
        if result.get("exceptionDetails"):
            raise RuntimeError(json.dumps(result["exceptionDetails"]))
        return result.get("result", {}).get("value")

    def _navigate(self, url: str) -> None:
        self._send("Page.navigate", {"url": url})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self._evaluate("document.readyState") in {"interactive", "complete"}:
                break
            time.sleep(0.25)
        time.sleep(random.uniform(self.delay_seconds, self.delay_seconds + 1.5))

    def _snapshot(self, expand_description: bool = False) -> dict[str, Any]:
        expand = """
const showMore = [...document.querySelectorAll('button')].find(button => /show more/i.test(button.innerText || '') && button.offsetParent !== null);
if (showMore) showMore.click();
""" if expand_description else ""
        return self._evaluate(
            f"""
(async () => {{
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  {expand}
  await sleep(600);
  const anchors = [...document.querySelectorAll('a[href]')].map(anchor => ({{
    text: (anchor.innerText || anchor.textContent || '').replace(/\\s+/g, ' ').trim(),
    href: anchor.href,
  }})).filter(anchor => anchor.href);
  const hiringContacts = [...document.querySelectorAll('.hirer-card__hirer-information a[href*="/in/"], .jobs-poster__info a[href*="/in/"], .jobs-poster a[href*="/in/"]')].map(anchor => ({{
    text: (anchor.innerText || anchor.textContent || '').replace(/\\s+/g, ' ').trim(),
    href: anchor.href,
  }})).filter(anchor => anchor.href);
  const textOf = selector => (document.querySelector(selector)?.innerText || '').replace(/\\s+/g, ' ').trim();
  const primary = textOf('.job-details-jobs-unified-top-card__primary-description-container, .jobs-unified-top-card__primary-description');
  return {{
    url: location.href,
    title: document.title,
    text: document.body ? document.body.innerText : '',
    anchors,
    hiring_contacts: hiringContacts,
    job_title: textOf('.job-details-jobs-unified-top-card__job-title h1, .jobs-unified-top-card__job-title, main h1'),
    company_name: textOf('.job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name'),
    job_location: primary.split('·')[0].trim(),
    posted_text: (primary.match(/(?:(?:reposted|posted)\\s+)?(?:\\d+\\s+(?:minute|hour|day)s?\\s+ago|today|yesterday)/i) || [''])[0],
  }};
}})()
""",
            await_promise=True,
        ) or {}

    def _save(self, snapshot: dict[str, Any], kind: str, label: str) -> Path:
        directory = self.paths.search_pages_dir if kind == "linkedin-search" else self.paths.job_pages_dir
        path = directory / f"linkedin-{slugify(label)}.json"
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        saved = SavedPage(
            run_id=self.paths.run_id,
            kind=kind,
            url=str(snapshot.get("url") or ""),
            saved_path=str(path),
            status="saved",
            fetched_at=datetime.now(timezone.utc),
            notes="Visible logged-in LinkedIn CDP snapshot",
        )
        append_jsonl(self.paths.manifest_path, saved.to_dict())
        return path

    def fetch_search(self, spec: LinkedInSearchSpec) -> dict[str, Any]:
        self._navigate(spec.url)
        for _ in range(3):
            self._evaluate(
                """
(() => {
  const pane = document.querySelector('.jobs-search-results-list, .scaffold-layout__list, [data-results-list-top-scroll-sentinel]') || document.scrollingElement;
  pane.scrollTop += Math.max(600, pane.clientHeight * 0.8);
})()
"""
            )
            time.sleep(random.uniform(1.5, 2.5))
        snapshot = self._snapshot()
        reason = blocked_reason(snapshot)
        self._save(snapshot, "linkedin-search", f"{spec.keywords}-{spec.location}")
        if reason:
            raise LinkedInBlocked(reason)
        return snapshot

    def fetch_job(self, url: str) -> tuple[dict[str, Any], Path]:
        self._navigate(url)
        snapshot = self._snapshot(expand_description=True)
        reason = blocked_reason(snapshot)
        path = self._save(snapshot, "linkedin-job", url.rsplit("/", 1)[-1])
        if reason:
            raise LinkedInBlocked(reason)
        return snapshot, path


def _clean_ax_label(value: Any) -> str:
    return " ".join(str(value or "").replace("￼", " ").split())


def _linkedin_document_elements(state: dict[str, Any]) -> list[dict[str, Any]]:
    elements = list(state.get("elements") or [])
    by_index = {item.get("element_index"): item for item in elements}
    document = next(
        (
            item
            for item in elements
            if item.get("role") == "document web" and "linkedin" in str(item.get("label") or "").lower()
        ),
        None,
    )
    if not document:
        return []
    document_index = document.get("element_index")

    def belongs_to_document(item: dict[str, Any]) -> bool:
        current: dict[str, Any] | None = item
        while current is not None:
            if current.get("element_index") == document_index:
                return True
            current = by_index.get(current.get("parent_index"))
        return False

    return [item for item in elements if belongs_to_document(item)]


def extract_linkedin_result_links(state: dict[str, Any]) -> list[tuple[str, int]]:
    """Return visible job-title links from the LinkedIn results list."""
    elements = _linkedin_document_elements(state)
    by_index = {item.get("element_index"): item for item in elements}
    found: list[tuple[str, int]] = []
    seen: set[str] = set()
    for item in elements:
        if item.get("role") != "link":
            continue
        current = by_index.get(item.get("parent_index"))
        in_result_item = False
        while current is not None:
            in_result_item = in_result_item or current.get("role") == "list item"
            current = by_index.get(current.get("parent_index"))
        if not in_result_item:
            continue
        label = re.sub(r"\s+with verification$", "", _clean_ax_label(item.get("label")), flags=re.IGNORECASE)
        lowered = label.casefold()
        if (
            lowered in {"home", "my network", "jobs", "messaging", "notifications"}
            or "notification" in lowered
            or "premium" in lowered
            or "feed updates" in lowered
        ):
            continue
        if label and label.casefold() not in seen:
            seen.add(label.casefold())
            found.append((label, int(item["element_index"])))
    return found


def linkedin_snapshot_from_ax_state(state: dict[str, Any], url: str) -> dict[str, Any]:
    """Convert a cua-driver accessibility state into the existing snapshot contract."""
    elements = _linkedin_document_elements(state)
    labels = [_clean_ax_label(item.get("label")) for item in elements]
    labels = [label for label in labels if label]
    posted_position = next(
        (position for position, item in enumerate(elements) if POSTED_PATTERN.search(_clean_ax_label(item.get("label")))),
        0,
    )
    headings = [
        (_clean_ax_label(item.get("label")), position)
        for position, item in enumerate(elements)
        if item.get("role") == "heading" and _clean_ax_label(item.get("label"))
    ]
    excluded = {
        "about the job",
        "about the company",
        "meet the hiring team",
        "your profile is missing required qualifications",
        "people also viewed",
    }
    title, title_position = ("", 0)
    for position in range(posted_position - 1, max(-1, posted_position - 15), -1):
        item = elements[position]
        label = _clean_ax_label(item.get("label"))
        if (
            item.get("role") in {"paragraph", "heading"}
            and label
            and label.casefold() not in excluded
            and "applicant" not in label.casefold()
            and not re.search(r"\b(?:Remote|Worldwide|Global)\b|,", label, re.IGNORECASE)
        ):
            title, title_position = label, position
            break
    if not title:
        title, title_position = next(
            ((label, position) for label, position in headings if label.casefold() not in excluded and "notification" not in label.casefold()),
            ("", 0),
        )
    company = ""
    for position in range(title_position - 1, -1, -1):
        item = elements[position]
        label = _clean_ax_label(item.get("label"))
        if item.get("role") == "link" and label and "logo" not in label.casefold() and label.casefold() != title.casefold():
            company = label
            break
    metadata = _clean_ax_label(elements[posted_position].get("label")) if elements else ""
    posted_match = POSTED_PATTERN.search(metadata)
    location = ""
    for position in range(posted_position, max(-1, posted_position - 10), -1):
        candidate = _clean_ax_label(elements[position].get("label"))
        if re.search(r"\b(?:Remote|Worldwide|Global)\b|,", candidate, re.IGNORECASE):
            location = candidate.split("·", 1)[0].strip()
            break
    return {
        "url": canonical_job_url(url),
        "title": next((_clean_ax_label(item.get("label")) for item in elements if item.get("role") == "document web"), ""),
        "text": "\n".join(labels),
        "anchors": [],
        "hiring_contacts": [],
        "job_title": title,
        "company_name": company,
        "job_location": location,
        "posted_text": posted_match.group(0) if posted_match else "",
    }


class LinkedInBrowserFetcher:
    """Slow, read-only AX reader for LinkedIn in the running default Chrome."""

    def __init__(self, paths: RunPaths, delay_seconds: float = 3.0, command: str = "cua-driver") -> None:
        self.paths = paths
        self.delay_seconds = max(2.0, delay_seconds)
        self.command = command
        self.pid = 0
        self.window_id = 0

    def __enter__(self) -> "LinkedInBrowserFetcher":
        if not shutil.which(self.command):
            raise RuntimeError("cua-driver is required for default-profile LinkedIn discovery")
        windows = self._call("list_windows", {}).get("windows") or []
        chrome = next((item for item in windows if "google chrome" in str(item.get("title") or "").lower()), None)
        if not chrome:
            raise RuntimeError("No visible default Chrome window found")
        self.pid = int(chrome["pid"])
        self.window_id = int(chrome["window_id"])
        self._activate_linkedin_tab()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        completed = subprocess.run(
            [self.command, "call", tool, json.dumps(arguments)],
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
        return json.loads(completed.stdout or "{}")

    def _input_args(self) -> dict[str, Any]:
        return {"pid": self.pid, "window_id": self.window_id, "delivery_mode": "foreground"}

    def _state(self) -> dict[str, Any]:
        return self._call("get_window_state", {"pid": self.pid, "window_id": self.window_id})

    def _activate_linkedin_tab(self) -> None:
        tab = next(
            (
                item
                for item in self._state().get("elements") or []
                if item.get("role") == "page tab" and "linkedin" in str(item.get("label") or "").lower()
            ),
            None,
        )
        if not tab:
            raise RuntimeError("No authenticated LinkedIn tab found in default Chrome")
        self._call("click", {**self._input_args(), "element_index": int(tab["element_index"])})
        time.sleep(1.0)
        if not _linkedin_document_elements(self._state()):
            raise RuntimeError("Could not activate the authenticated LinkedIn tab")

    def _navigate(self, url: str) -> None:
        self._call("hotkey", {**self._input_args(), "keys": ["ctrl", "l"]})
        self._call("type_text", {**self._input_args(), "text": url})
        self._call("press_key", {**self._input_args(), "key": "ENTER"})
        time.sleep(random.uniform(self.delay_seconds, self.delay_seconds + 1.5))

    def _current_url(self) -> str:
        previous = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True).stdout
        try:
            self._call("hotkey", {**self._input_args(), "keys": ["ctrl", "l"]})
            self._call("hotkey", {**self._input_args(), "keys": ["ctrl", "c"]})
            time.sleep(0.15)
            current = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
            self._call("press_key", {**self._input_args(), "key": "ESC"})
            return current
        finally:
            owner = subprocess.Popen(
                ["xclip", "-selection", "clipboard", "-i"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            owner.communicate(previous, timeout=5)

    @staticmethod
    def _job_url_from_current_url(url: str) -> str:
        match = re.search(r"linkedin\.com/jobs/view/(\d+)", url)
        if not match:
            job_id = next(iter(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("currentJobId") or []), "")
            match = re.match(r"(\d+)", job_id)
        return f"https://www.linkedin.com/jobs/view/{match.group(1)}" if match else ""

    def _save(self, snapshot: dict[str, Any], kind: str, label: str) -> Path:
        directory = self.paths.search_pages_dir if kind == "linkedin-search" else self.paths.job_pages_dir
        path = directory / f"linkedin-{slugify(label)}.json"
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        append_jsonl(
            self.paths.manifest_path,
            SavedPage(
                run_id=self.paths.run_id,
                kind=kind,
                url=str(snapshot.get("url") or ""),
                saved_path=str(path),
                status="saved",
                fetched_at=datetime.now(timezone.utc),
                notes="Visible logged-in default-Chrome LinkedIn AX snapshot",
            ).to_dict(),
        )
        return path

    def fetch_search(self, spec: LinkedInSearchSpec) -> dict[str, Any]:
        self._navigate(spec.url)
        state = self._state()
        snapshot = linkedin_snapshot_from_ax_state(state, spec.url)
        reason = blocked_reason(snapshot)
        anchors: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for scroll_round in range(5):
            visible: list[tuple[str, int]] = []
            for _ in range(6):
                visible = extract_linkedin_result_links(self._state())
                if visible:
                    break
                time.sleep(2.0)
            for title, _ in visible:
                target = next((item for item in extract_linkedin_result_links(self._state()) if item[0] == title), None)
                if not target:
                    continue
                self._call("click", {**self._input_args(), "element_index": target[1]})
                time.sleep(random.uniform(1.0, 1.8))
                job_url = self._job_url_from_current_url(self._current_url())
                if job_url and job_url not in seen_urls:
                    seen_urls.add(job_url)
                    anchors.append({"href": job_url, "text": title})
            if scroll_round < 4 and visible:
                # The result pane virtualizes cards below the initial viewport.  Keep
                # focus on its last visible card, then page down to expose more jobs.
                refreshed = extract_linkedin_result_links(self._state())
                last = refreshed[-1] if refreshed else None
                if last:
                    self._call("click", {**self._input_args(), "element_index": last[1]})
                    self._call("press_key", {**self._input_args(), "key": "PAGEDOWN"})
                    time.sleep(self.delay_seconds)
        snapshot["anchors"] = anchors
        self._save(snapshot, "linkedin-search", f"{spec.keywords}-{spec.location}")
        if reason:
            raise LinkedInBlocked(reason)
        return snapshot

    def fetch_job(self, url: str) -> tuple[dict[str, Any], Path]:
        self._navigate(url)
        snapshot = linkedin_snapshot_from_ax_state(self._state(), url)
        reason = blocked_reason(snapshot)
        path = self._save(snapshot, "linkedin-job", url.rsplit("/", 1)[-1])
        if reason:
            raise LinkedInBlocked(reason)
        return snapshot, path
