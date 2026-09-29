# CloudRelay MediaGen Skill v2.0.1

Release date: 2026-09-29.

This patch keeps the media channel fixed to CloudRelay-Code/cloudrelay-mediagen-skill,
cloudrelay-mediagen and cloudrelay-mediagen.skill. The independent legacy repository
CloudRelay-Code/cloudrelay-imagegen-skill serves ImageGen 1.5.x under its original identity.
Install MediaGen separately when migrating from ImageGen; automatic updates never switch
Skill identity. If an existing media v2.0.0 updater reports local-variant, reinstall the
media identity to obtain this corrected updater.

Changes:
- Add explicit, standard-library migration reporting with default no-write behavior and
  --source-dir, --target-dir, --dry-run and --yes. An opted-in receipt contains only metadata.
- Enforce installed target identity before network access and before managed replacement.
- Validate exact repository/asset/tag URLs, safe HTTPS redirects, GitHub SHA-256 digest,
  archive identity, paths and regular entries, and complete image/video/migration manifests.
- Preserve valid SemVer build identifiers and match tag/metadata/archive versions exactly.
- Use atomic managed-file replacement and preserve unknown and hard-linked user files.
- Add frozen historical updater fixtures, dual-track and migration tests, deterministic
  independent packages, and Ubuntu/Windows validation.

ImageGen and MediaGen have separate identities, assets, repositories and latest release
channels. They can coexist. Keep imagegen-api-key-gpt, imagegen-api-key-gemini and
imagegen-api-key-grok in their existing storage. Managed updates retain generated-images,
generated-videos and custom user files. Migration does not read or copy key contents or
move generated results.

Reload after installation: Codex normally discovers changes automatically; restart if
missing. Restart Claude Code or use /reload-skills for newly created top-level skill
folders. Run /skills reload and /skills list in Gemini CLI. Let the OpenClaw skill watcher
refresh for the next Agent turn, or restart its Gateway if watching is disabled. Restart
Cursor and confirm skill discovery.

Validation: 170 main tests plus 14 video tests, with complementary platform-specific skips;
all 184 unique tests execute successfully on Windows and/or native Linux. Both runtime
packages pass clean-copy skill validation. Offline legacy protocol tests use the unchanged
v1.5.0 updater; real post-publication metadata/digest/upgrade evidence is recorded separately
in docs/compatibility/published-release-verification.json and the compatibility report.
