# CloudRelay ImageGen — legacy 1.5.x source

Version: **1.5.1 release source**, prepared on 2026-09-29. Release availability and online verification are tracked separately from these source files.

This independent source tree contains the original image-only Skill and a hardened updater for the restored CloudRelay-Code/cloudrelay-imagegen-skill repository. Its release channel uses only cloudrelay-imagegen.skill and 1.5.x versions. Source/tag pushes and the v1.5.1 release are authorized. Preparing this source does not itself prove that a release is online or that the historical updater has completed an online upgrade.

The image generator, credential configurator, Skill instructions, agent metadata, and launcher scripts are preserved byte-for-byte from the peeled v1.5.0 commit 2f2d6a077dc2acda21fff2896f144a075d9a3f72. Its annotated tag object is 1a3d213f841cdb93131dfa42f5803473554f9f39. See PROVENANCE.json for hashes. The independently maintained cloudrelay-mediagen Skill remains in its own repository and release channel.

## Runtime and packaging

Python 3.10 or later is required; runtime scripts use only the standard library. Use this directory directly as an installed legacy Skill, or package the fixed ten runtime files using the parent repository's tools/package_legacy_skill.py. This tree has no dependency on media runtime files. Copying this directory to a standalone legacy repository preserves its runtime behavior.

    python scripts/generate_image.py --help
    python scripts/configure_api_key.py --provider gpt --check
    python scripts/check_update.py --json
    python scripts/update.py --apply

The default update command only checks. Writing requires --apply or explicitly authorized --auto. The updater fixes the repository, asset, Skill identity, and release version family; validates every redirect hop, HTTPS URL/tag/asset path, GitHub SHA-256 digest, archive version, Python syntax, archive paths, links and non-regular files; and rejects target identity changes and downgrades. A failed replacement rolls back managed files. The GitHub digest is an integrity check under GitHub/repository trust, not an independent publisher signature.

Only the historical ten managed runtime files may be replaced. The three imagegen-api-key-* files, external key storage, generated-images, generated-videos, and unknown user files are never in that manifest. Updating this legacy Skill does not install video or migration files.

After installation or update, start a new Codex task or restart Codex; start a new Claude Code session; run /skills reload in Gemini CLI; start a new OpenClaw session or use its configured skill watcher; start a new Cursor Agent conversation and reload its window if needed.

## Release verification

The independent legacy repository is restored, and source/tag pushes and the v1.5.1 release are authorized. After publication, verify real legacy /releases/latest metadata, the exact old asset URL, and the uploaded GitHub digest. Confirm that the frozen v1.5.0 updater succeeds against the real legacy release before describing automatic compatibility as restored. The separately versioned media channel retains its own identity, asset and latest release.
