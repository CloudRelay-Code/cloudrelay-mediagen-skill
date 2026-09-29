# CloudRelay ImageGen Skill v1.5.1

Release date: 2026-09-29.

This image-only compatibility release uses the restored exact repository
CloudRelay-Code/cloudrelay-imagegen-skill, identity cloudrelay-imagegen and asset
cloudrelay-imagegen.skill. The original v1.5.0 updater can consume this original-name release
protocol without relaxing its trusted URL rules. Only the historical ten runtime files are
managed. The image generator and credential configurator retain their v1.5.0 bytes.

Changes:
- Keep the release channel strictly 1.5.x and reject media archives and installed targets.
- Verify exact legacy asset URLs, safe HTTPS redirects and GitHub SHA-256 digests.
- Reject unsafe paths, links, non-regular entries and missing required runtime files.
- Retain SemVer build metadata and require exact tag/metadata/archive version equality.
- Preserve unknown files and roll back managed replacements on failure.

No video runtime or media migration script is added to this legacy package. To use media
features, install cloudrelay-mediagen from its separate repository. Never point the old
updater at a media installation or rename the media archive to the legacy asset name.

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

The unchanged v1.5.0 updater validates and installs the actual archive in offline regression
tests. Real latest metadata, uploaded asset digest and live upgrade acceptance are verified
after publication and recorded in the media repository's compatibility report and
published-release-verification.json; simulated metadata is not presented as online proof.
