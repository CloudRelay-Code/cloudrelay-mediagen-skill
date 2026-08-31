# Changelog

## 1.4.0 - 2026-08-31

- Unify GPT, Gemini, and Grok image generation on the CloudRelay media discovery and asynchronous task API.
- Add provider-specific credentials for the GPT `生图专用`, Gemini `gemini`, and Grok `grok` groups.
- Add Gemini model confirmation, strict capability validation, URL/base64 result handling, and credential-free parameter reports.
- Harden redirects, task polling, output downloads, and provider key-file configuration.

## 1.2.0 - 2026-08-11

- Add a read-only GitHub Releases version check.
- Add an explicit, cross-platform self-update command.
- Verify release asset SHA-256 digests before staging updates.
- Validate archive paths, skill identity, Python syntax, and managed files before replacement.
- Preserve API-key storage, generated images, and unknown user files during updates.
- Add shell and PowerShell wrappers for installation and updates.

## 1.1.1 - 2026-08-01

- Document the asynchronous CloudRelay image workflow and timeout rationale.
