# Changelog

## 2.0.1 — 2026-09-29 (legacy compatibility)

- Preserve package, adapter and frozen-fixture bytes across Git platforms; use LF for the media POSIX update wrapper.

- Keep ImageGen and MediaGen identities, repositories, assets, and latest release channels separate; restore the exact legacy repository name.
- Add explicit, default-read-only migration reports and an opt-in metadata receipt; never read or copy API keys or move generated output.
- Require media target identity, exact release URL/tag/asset, safe HTTPS redirects, regular archive files, and complete image/video/migration runtime manifests.
- Retain validated semantic-version build identifiers; require release tag, metadata version, and archive VERSION to match including build metadata while keeping SemVer precedence unchanged.
- Replace the unconditional local-variant stop with tested media-only checks; preserve unknown files and rollback behavior.
- Add pinned v1.5.0 historical fixtures, channel/migration/parallel-install tests, deterministic packaging and release digest evidence.
- Provide an independent image-only legacy 1.5.1 source tree in legacy/cloudrelay-imagegen and a fixed tools/package_legacy_skill.py builder; preserve the historical ten-file manifest and byte-identical image generator/credential configurator.
- Verify the legacy archive with the frozen v1.5.0 updater offline; separately record real Release metadata, digest and upgrade acceptance after publication.
- Reload or restart the Agent after installing the new identity; the migration guide covers all five supported clients.

## 1.5.0 - 2026-09-24

- Accept explicit positive pixel dimensions and case-insensitive square 2K/4K aliases.
- Report actual PNG, JPEG, GIF, and WebP dimensions and warn when requested dimensions are not met.
- Document resolution examples by aspect ratio and measured upstream output limits.
- Add offline resolution parameter regression tests.

## 1.4.1 - 2026-09-09

- Change the default GPT image model to `gpt-image-2.5-flare`.

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
