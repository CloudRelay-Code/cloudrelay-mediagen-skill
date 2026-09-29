# Changelog

## 1.5.1 — 2026-09-29

- Preserve the v1.5.0 image workflow and provider credential configuration byte-for-byte.
- Fix the updater to the independent legacy repository, cloudrelay-imagegen.skill, the legacy Skill identity, and the 1.5.x release family.
- Harden URL and redirect checks, SHA-256 verification, archive validation, identity checks, replacement, and rollback while retaining the original ten-file manifest.
- Keep key storage, generated image/video directories, and all unknown user files outside the update manifest.
- Validate nonempty semantic-version build identifiers and retain them in reported versions. Release metadata and archive versions must match including build metadata; version precedence still ignores it.
- Prepare the independent legacy release for the restored repository; publication and online historical-updater verification are separate release steps.
