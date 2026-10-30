from __future__ import annotations

import html
import json
import os
import re

import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote_plus


from job_research.models import SavedPage
from job_research.browser_attach import BrowserAttachError
from job_research.storage import RunPaths, append_jsonl, save_text_artifact


BLOCK_MARKERS = (
    "captcha",
    "are you human",
    "verify you are human",
    "too many requests",
    "rate limit",
    "access denied",
)
RECENT_POSTED_LABEL_PATTERNS = (
    r"\bposted\s+today\b",
    r"\bposted\s+yesterday\b",
    r"\bposted\s+1\s+day\s+ago\b",
    r"\bposted\s+2\s+days?\s+ago\b",
    r"\b2\s+days?\s+ago\b",
)
ANY_POSTED_LABEL_PATTERN = r"\b(?:posted\s+)?(?:today|yesterday|\d+\s+(?:hours?|days?|weeks?|months?)\s+ago)\b"


class WellfoundBlocked(BrowserAttachError):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        data = html.unescape(data).strip()
        if data:
            self.parts.append(data)

    def text(self) -> str:
        return "\n".join(self.parts)


def html_to_text(markup: str) -> str:
    parser = _TextExtractor()
    parser.feed(markup)
    return parser.text()


def build_search_urls(query: str) -> list[str]:
    from job_research.config import load_config
    locations = load_config().get('locations', {}).get('include', [])
    base = f"https://wellfound.com/jobs?query={quote_plus(query)}"
    return [base + '&location=' + quote_plus(loc) for loc in locations] if locations else [base]


def page_looks_blocked(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in BLOCK_MARKERS)


def extract_wellfound_job_urls(text: str) -> list[str]:
    absolute = re.findall(r"https://wellfound\.com/jobs/\d[A-Za-z0-9_./?=&%-]*", text)
    relative = re.findall(r"(?<![A-Za-z0-9_./?=&%-])/jobs/\d[A-Za-z0-9_./?=&%-]*", text)
    urls = absolute + [f"https://wellfound.com{path}" for path in relative]
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        url = url.rstrip('.,)\"\'')
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


def posted_label_is_allowed(value: str, hours: int = 48) -> bool:
    from job_research.filters import is_recent_job
    from job_research.models import JobRecord
    return is_recent_job(JobRecord(job_url='', title='', company='', posted_at_text=value), hours=hours)


def _has_posted_label(value: str) -> bool:
    text = " ".join((value or "").lower().split())
    return bool(re.search(ANY_POSTED_LABEL_PATTERN, text))


def extract_recent_wellfound_job_urls(text: str, context_lines: int = 10, hours: int = 48) -> list[str]:
    """Return only job URLs whose nearby search-card text has an allowed posted label."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    recent_urls: list[str] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        urls = extract_wellfound_job_urls(line)
        if not urls:
            continue
        start = max(0, index - context_lines)
        end = min(len(lines), index + context_lines + 1)
        posted_labels = [
            (abs(label_index - index), label_line)
            for label_index, label_line in enumerate(lines[start:end], start=start)
            if _has_posted_label(label_line)
        ]
        if not posted_labels:
            continue
        _, closest_posted_label = min(posted_labels, key=lambda item: item[0])
        if not posted_label_is_allowed(closest_posted_label, hours=hours):
            continue
        for url in urls:
            if url not in seen:
                seen.add(url)
                recent_urls.append(url)
    return recent_urls


def parse_posted_at_text(value: str, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(timezone.utc)
    text = value.strip().lower()
    if text.startswith("posted today"):
        return now
    if text.startswith("posted yesterday"):
        return now - timedelta(days=1)
    match = re.search(r"(?:posted\s+)?(\d+)\s+days?\s+ago", text)
    if match:
        days = int(match.group(1))
        if days >= 0:
            return now - timedelta(days=days)
    return None


def find_posted_marker(text: str) -> tuple[str, datetime | None]:
    for line in [line.strip() for line in text.splitlines() if line.strip()]:
        if _has_posted_label(line):
            return line, parse_posted_at_text(line)
    return "", None


def guess_title_company(text: str, url: str) -> tuple[str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        match = re.match(r"(.+?)\s+at\s+(.+)$", line)
        if match and len(match.group(1)) <= 140 and len(match.group(2)) <= 140:
            return match.group(1).strip(), match.group(2).strip()
    noisy = {"wellfound", "jobs", "apply", "save", "log in", "sign up", "ready to interview"}
    clean = [line for line in lines if line.lower() not in noisy and len(line) <= 140]
    title = clean[0] if clean else url.rsplit("/", 1)[-1].replace("-", " ").title()
    company = "Unknown company"
    for line in clean[1:8]:
        if line != title and not re.search(r"today|yesterday|hours ago|days ago", line, re.I):
            company = line
            break
    return title, company


class ChromeDumpFetcher:
    def __init__(self, workspace: Path, run_paths: RunPaths, headless: bool = True) -> None:
        self.workspace = workspace
        self.run_paths = run_paths
        self.headless = headless
        self.cdp_port = os.environ.get("JOBAPPLIER_CDP_PORT")

    def fetch_url(self, url: str, kind: str, title: str, timeout: int = 75) -> str:
        # The historical class name is retained for callers; there is no dump-dom
        # or headless fallback. Missing metadata/permission is a hard blocker.
        text = self._fetch_url_via_cdp(url, timeout=timeout)
        if page_looks_blocked(text):
            saved = self._save(kind="blocked", title=f"blocked-{title}", url=url, text=f"# Blocked page\n\nURL: {url}\n\nVisible text suggested blocking.\n\n{text[:2000]}", status="blocked", notes="CAPTCHA/rate-limit/login/anti-bot marker found")
            raise WellfoundBlocked(f"Blocked page saved to {saved.saved_path}")
        self._save(kind=kind, title=title, url=url, text=text, status="saved", notes="Visible page text extracted via verified existing Chrome CDP session")
        return text

    def _fetch_url_via_cdp(self, url: str, timeout: int = 75) -> str:
        from job_research.browser_attach import attach_page
        ws = attach_page("wellfound.com", self.cdp_port)
        counter = 0

        def command(method: str, params: dict | None = None) -> dict:
            nonlocal counter
            counter += 1
            ws.send(json.dumps({"id": counter, "method": method, "params": params or {}}))
            while True:
                message = json.loads(ws.recv())
                if message.get("id") == counter:
                    if "error" in message:
                        raise RuntimeError(f"CDP {method} failed: {message['error']}")
                    return message

        try:
            command("Page.enable")
            command("Runtime.enable")
            command("Page.navigate", {"url": url})
            deadline = time.time() + timeout
            while time.time() < deadline:
                state = command("Runtime.evaluate", {"expression": "document.readyState", "returnByValue": True})
                if state.get("result", {}).get("result", {}).get("value") == "complete":
                    break
                time.sleep(0.5)
            time.sleep(float(os.environ.get("JOBAPPLIER_CDP_DELAY_SECONDS", "2.5")))
            if "wellfound.com/jobs" in url and os.environ.get("JOBAPPLIER_SORT_MOST_RECENT", "1") != "0":
                open_sort_expression = r'''
(() => {
  const buttons = Array.from(document.querySelectorAll('button, [role="button"]'));
  const current = buttons.find((el) => {
    const text = (el.innerText || el.textContent || '').trim();
    return text === 'Recommended' || text === 'Last Active' || text === 'Most recent';
  });
  if (!current) return false;
  current.click();
  return true;
})()
'''
                command("Runtime.evaluate", {"expression": open_sort_expression, "returnByValue": True})
                time.sleep(float(os.environ.get("JOBAPPLIER_CDP_SORT_MENU_DELAY_SECONDS", "0.8")))
                sort_expression = r'''
(() => {
  const candidates = Array.from(document.querySelectorAll('button, [role="button"], [role="option"], div, span'));
  const target = candidates.find((el) => {
    const text = (el.innerText || el.textContent || '').trim();
    return text === 'Most recent' || text.startsWith('Most recent\n');
  });
  if (!target) return false;
  target.click();
  return true;
})()
'''
                command("Runtime.evaluate", {"expression": sort_expression, "returnByValue": True})
                time.sleep(float(os.environ.get("JOBAPPLIER_CDP_SORT_DELAY_SECONDS", "2.5")))
                for _ in range(int(os.environ.get("JOBAPPLIER_CDP_SCROLL_STEPS", "4"))):
                    command("Runtime.evaluate", {"expression": "window.scrollBy(0, Math.floor(window.innerHeight * 0.85))", "returnByValue": True})
                    time.sleep(float(os.environ.get("JOBAPPLIER_CDP_SCROLL_DELAY_SECONDS", "0.8")))
            expression = r'''
(() => {
  const jobBlocks = [];
  const seen = new Set();
  for (const anchor of Array.from(document.querySelectorAll('a[href*="/jobs/"]'))) {
    const href = new URL(anchor.getAttribute('href'), location.href).href;
    if (seen.has(href)) continue;
    seen.add(href);
    let node = anchor;
    let best = anchor.innerText || href;
    for (let i = 0; i < 8 && node; i++, node = node.parentElement) {
      const text = (node.innerText || '').trim();
      if (/posted\s+(today|yesterday|\d+\s+days?\s+ago)|\b\d+\s+days?\s+ago\b/i.test(text)) {
        best = text;
        break;
      }
      if (text.length > best.length && text.length < 2500) best = text;
    }
    jobBlocks.push(`${best}\n${href}`);
  }
  const anchors = Array.from(document.querySelectorAll('a[href]'))
    .map(a => {
      const href = new URL(a.getAttribute('href'), location.href).href.split('?')[0];
      const label = (a.innerText || a.textContent || '').trim().replace(/\s+/g, ' ');
      return label ? `${label}\n${href}` : href;
    })
    .filter(value => /wellfound\.com\/(?:u|users|profile|people|company)|linkedin\.com\/in\//i.test(value));
  return [document.body.innerText || '', ...jobBlocks, ...anchors].join('\n\n');
})()
'''
            result = command("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
            return result.get("result", {}).get("result", {}).get("value", "")
        finally:
            ws.close()

    def _save(self, kind: str, title: str, url: str, text: str, status: str, notes: str) -> SavedPage:
        target = {
            "search": self.run_paths.search_pages_dir,
            "job": self.run_paths.job_pages_dir,
            "company": self.run_paths.company_pages_dir,
            "contact": self.run_paths.contact_pages_dir,
            "blocked": self.run_paths.fetched_pages_dir,
        }.get(kind, self.run_paths.fetched_pages_dir)
        saved_path = save_text_artifact(target, title, text)
        try:
            relative = str(saved_path.relative_to(self.workspace))
        except ValueError:
            relative = str(saved_path)
        saved = SavedPage(
            run_id=self.run_paths.run_id,
            kind=kind,
            url=url,
            saved_path=relative,
            status=status,
            fetched_at=datetime.now(timezone.utc),
            notes=notes,
        )
        append_jsonl(self.run_paths.manifest_path, saved.to_dict())
        return saved
