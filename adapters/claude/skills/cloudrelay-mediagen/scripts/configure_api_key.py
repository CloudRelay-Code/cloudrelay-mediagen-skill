#!/usr/bin/env python3
"""Configure the CloudRelay image API key without exposing it on the command line."""

from __future__ import annotations

import argparse
import ctypes
import getpass
import os
from pathlib import Path
import sys


# Keep credentials isolated by upstream channel.  In particular, the GPT
# channel intentionally does not consult a generic/legacy key: its key must be
# the one created for image generation (the ``生图专用`` group).
PROVIDER_ENV_NAMES = {
    "gpt": "CLOUDRELAY_GPT_IMAGE_API_KEY",
    "gemini": "CLOUDRELAY_GEMINI_API_KEY",
    "grok": "CLOUDRELAY_GROK_API_KEY",
}
PROVIDERS = tuple(PROVIDER_ENV_NAMES)
DEFAULT_PROVIDER = "gpt"

# ``ENV_NAME`` was part of the original helper's small, importable surface.
# Keep it as an alias for callers that use the default provider, while making
# the new provider-specific variable the only value that is actually read.
ENV_NAME = PROVIDER_ENV_NAMES[DEFAULT_PROVIDER]

_SECRET_FILE_NAMES = {
    "gpt": "imagegen-api-key-gpt",
    "gemini": "imagegen-api-key-gemini",
    "grok": "imagegen-api-key-grok",
}

_PROMPTS = {
    "gpt": "CloudRelay GPT image-generation API key (group 生图专用): ",
    "gemini": "CloudRelay Gemini group API key: ",
    "grok": "CloudRelay Grok group API key: ",
}


def _normalize_provider(provider: str | None) -> str:
    value = (provider or DEFAULT_PROVIDER).strip().lower()
    if value not in PROVIDER_ENV_NAMES:
        choices = ", ".join(PROVIDERS)
        raise ValueError(f"Unsupported provider {provider!r}; choose one of {choices}.")
    return value


def _env_name(provider: str | None = DEFAULT_PROVIDER) -> str:
    return PROVIDER_ENV_NAMES[_normalize_provider(provider)]


def _configure_console_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def _secret_file(provider: str | None = DEFAULT_PROVIDER) -> Path:
    provider_name = _normalize_provider(provider)
    config_root = os.environ.get("XDG_CONFIG_HOME")
    root = Path(config_root).expanduser() if config_root else Path.home() / ".config"
    return root / "cloudrelay" / _SECRET_FILE_NAMES[provider_name]


def _validate_key(key: str) -> str:
    key = key.strip()
    if not key:
        raise ValueError("No API key was provided.")
    if any(character.isspace() for character in key):
        raise ValueError("The API key must not contain whitespace.")
    return key


def _read_key(provider: str | None = DEFAULT_PROVIDER) -> str:
    provider_name = _normalize_provider(provider)
    return _validate_key(getpass.getpass(_PROMPTS[provider_name]))


def _read_key_from_stdin() -> str:
    """Read a user-provided key without placing it in argv or shell history."""
    return _validate_key(sys.stdin.read())


def _read_windows_user_variable(provider: str | None = DEFAULT_PROVIDER) -> str | None:
    provider_name = _normalize_provider(provider)
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as registry_key:
            value, _ = winreg.QueryValueEx(registry_key, _env_name(provider_name))
        return str(value).strip() or None
    except OSError:
        return None


def _is_configured(provider: str | None = DEFAULT_PROVIDER) -> bool:
    provider_name = _normalize_provider(provider)
    env_name = _env_name(provider_name)
    if (os.environ.get(env_name) or "").strip():
        return True
    if _read_windows_user_variable(provider_name):
        return True
    try:
        return bool(_secret_file(provider_name).read_text(encoding="utf-8").strip())
    except OSError:
        return False


def _persist_windows_user_variable(
    key: str, provider: str | None = DEFAULT_PROVIDER
) -> None:
    provider_name = _normalize_provider(provider)
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as registry_key:
        winreg.SetValueEx(
            registry_key, _env_name(provider_name), 0, winreg.REG_SZ, key
        )

    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF,
        0x001A,
        0,
        "Environment",
        0x0002,
        5000,
        None,
    )


def _persist_posix_secret_file(
    key: str, provider: str | None = DEFAULT_PROVIDER
) -> Path:
    path = _secret_file(provider)

    def reject_symlinked_parent() -> None:
        # ``O_NOFOLLOW`` protects only the final component.  Compare the
        # normalized parent path with its real path as well, so an existing
        # symlink (including one several levels up) cannot redirect the key
        # into an unrelated directory.
        parent = os.path.abspath(os.fspath(path.parent))
        real_parent = os.path.realpath(parent)
        if os.path.normcase(parent) != os.path.normcase(real_parent):
            raise OSError(f"Refusing to write through symlinked parent: {path.parent}")

    reject_symlinked_parent()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    # Re-check after mkdir: a path that was created concurrently must still
    # resolve to the configured directory before opening the key file.
    reject_symlinked_parent()
    # Refuse a pre-existing symlink so configuring a key cannot overwrite an
    # unrelated target. O_NOFOLLOW closes the final-component race where it is
    # available (the POSIX platforms supported by this helper expose it).
    if path.is_symlink():
        raise OSError(f"Refusing to write through symlink: {path}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags | nofollow, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(key + "\n")
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    path.chmod(0o600)
    return path


def main() -> int:
    _configure_console_utf8()
    parser = argparse.ArgumentParser(
        description=(
            "Securely configure a provider-specific CloudRelay image API key."
        )
    )
    parser.add_argument(
        "--provider",
        choices=PROVIDERS,
        default=DEFAULT_PROVIDER,
        help="Credential channel to configure (default: gpt).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report whether a key is configured without displaying its value.",
    )
    parser.add_argument(
        "--key-stdin",
        action="store_true",
        help="Read the key from stdin; use only when the user has provided it directly.",
    )
    args = parser.parse_args()
    provider = _normalize_provider(args.provider)

    if args.check:
        if _is_configured(provider):
            print("configured")
            return 0
        print("missing")
        return 1

    try:
        key = _read_key_from_stdin() if args.key_stdin else _read_key(provider)
        env_name = _env_name(provider)
        if os.name == "nt":
            _persist_windows_user_variable(key, provider)
            location = f"the Windows user environment variable {env_name}"
        else:
            path = _persist_posix_secret_file(key, provider)
            location = f"the user-only file {path}"
        # Make the credential available to code that continues in this process
        # only after the persistent write has completed successfully.
        os.environ[env_name] = key
    except (OSError, ValueError) as error:
        print(f"Configuration failed: {error}", file=sys.stderr)
        return 1

    print(f"Configured {location}.")
    print("The key value was not displayed or written to the skill directory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
