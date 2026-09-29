# Historical v1.5.0 regression fixture

These are byte-for-byte Git blobs from commit
`2f2d6a077dc2acda21fff2896f144a075d9a3f72` (peeled commit of tag `v1.5.0`).
The annotated tag object is `1a3d213f841cdb93131dfa42f5803473554f9f39`; it is not the commit ID.
`provenance.json` records each original path, stored path, byte length, and SHA-256.
Only the explicit runtime/install/package source allowlist is retained. No Git
metadata, API keys, generated media, or user state is included.

The `.fixture` suffix is intentional: the legacy SKILL.md is test data, not a
discoverable nested skill. Tests materialize the original names only inside
a temporary runtime directory, and execute the historical updater unmodified.
The tests do not need Git, network access, or a checkout containing old tags.
Constructed release metadata in tests is synthetic; it is not presented as
a historical GitHub API capture.

Known historical limitations are preserved, not silently fixed:

- ZIP extraction rejects symlinks, but other ZIP special mode bits are not
  rejected (they are written as ordinary files). TAR rejects nonregular entries.
- The historical apply function validates the staged identity but only checks
  the target VERSION. A mismatched target identity with a non-newer version can
  be overwritten. Therefore the old updater must never target the media skill.
- The historical installer uses its fixed `cloudrelay-imagegen` destination,
  but does not validate source identity. Its historical package builder likewise
  trusts the source identity. Neither is a current compatibility package builder.

The production media runtime and package must exclude this fixture entirely.
