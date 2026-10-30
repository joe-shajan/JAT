# Existing-browser setup

Live research requires your own logged-in Chrome/Chromium session and explicit browser permission and identity confirmation. The scripts use job_research.browser_attach and never start a browser. Missing access is a blocker, not a reason to launch a new profile.

1. Open your browser manually and log in to the sites you intend to use.
2. Ask Hermes to inspect the existing-session attachment configuration: python3 -m job_research.browser_attach check-config. Use python3 -m job_research.browser_attach --help for supported inspection/confirmation commands. Connection inspection may trigger a browser permission dialog; approve it yourself.
3. With your permission, verify the visible source tabs/account identities. Set JOBAPPLIER_CONFIRMED_BROWSER only to the exact session identity returned by inspection and confirmed by you. Never guess or copy another person's confirmation.
4. If automatic metadata discovery is unsupported on your OS/browser, use an explicitly configured existing-session endpoint supported by the helper, or ask Hermes to use its separately authorized browser tools. Hermes /browser attachment and the Python scripts' attachment are separate integrations; one does not automatically configure the other.
5. Check visible source filters against your local roles/locations before running. Complete login, access checks and CAPTCHA manually. Stop if blocked.

Browser compatibility depends on browser version and operating system. Linux scripts are the primary target; macOS/Windows support is not certified. No browser credentials, profiles or cookies belong on GitHub. Never expose a debugging endpoint publicly.
