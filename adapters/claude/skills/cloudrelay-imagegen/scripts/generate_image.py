#!/usr/bin/env python3
"""Generate raster images through CloudRelay's unified media protocol."""

from __future__ import annotations

import argparse
import base64
import binascii
from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


# The gateway is intentionally fixed. Provider keys are sent only here.
BASE_URL = "https://cloudrelay.cn"
MEDIA_MODELS_PATH = "/v1/media/models"
IMAGE_SUBMIT_PATH = "/v1/images/generations/async"
IMAGE_TASK_PATH = "/v1/images/tasks/{task_id}"

PROVIDERS = ("gpt", "gemini", "grok")
DEFAULT_MODELS = {
    "gpt": "gpt-image-2",
    "grok": "grok-imagine-image-2.0",
}
GEMINI_MODELS = frozenset(
    {
        "gemini-3.1-flash-image-preview",
        "gemini-3-pro-image-preview",
    }
)
KEY_ENV_NAMES = {
    "gpt": ("CLOUDRELAY_GPT_IMAGE_API_KEY",),
    "gemini": ("CLOUDRELAY_GEMINI_API_KEY",),
    "grok": ("CLOUDRELAY_GROK_API_KEY",),
}
KEY_FILE_NAMES = {
    "gpt": "imagegen-api-key-gpt",
    "gemini": "imagegen-api-key-gemini",
    "grok": "imagegen-api-key-grok",
}
GROUP_LABELS = {"gpt": "生图专用", "gemini": "gemini", "grok": "grok"}
ENV_NAME = KEY_ENV_NAMES["gpt"][0]
TERMINAL_STATUSES = {"completed", "failed", "canceled", "cancelled", "done"}


class CloudRelayError(RuntimeError):
    """Raised for CloudRelay request, capability, and response failures."""


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward an API request (and its headers) to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CloudRelayError(
            "CloudRelay returned a redirect; refusing to forward credentials."
        )


_NO_REDIRECT_OPENER = urllib.request.build_opener(_RejectRedirectHandler)


def _skill_version() -> str:
    try:
        return (Path(__file__).resolve().parents[1] / "VERSION").read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        return "unknown"


USER_AGENT = f"Mozilla/5.0 (compatible; cloudrelay-imagegen-skill/{_skill_version()})"


def _is_true_flag(value: object) -> bool:
    return value is True or (
        isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}
    ) or value == 1


def _configure_console_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def _normalize_provider(value: str | None) -> str:
    aliases = {"openai": "gpt", "google": "gemini", "xai": "grok"}
    provider = aliases.get(
        (value or "").strip().lower(), (value or "").strip().lower()
    )
    if provider not in PROVIDERS:
        raise CloudRelayError(
            f"Unknown provider {value!r}; choose one of: {', '.join(PROVIDERS)}."
        )
    return provider


def _provider_from_model(model: str | None) -> str | None:
    value = (model or "").strip().lower()
    if value.startswith("gpt-image-"):
        return "gpt"
    if value.startswith("grok-imagine-image-"):
        return "grok"
    if value.startswith("gemini-"):
        return "gemini"
    return None


def _resolve_provider(requested: str | None, model: str | None) -> str:
    if requested and requested.strip().lower() != "auto":
        provider = _normalize_provider(requested)
        inferred = _provider_from_model(model)
        if inferred and inferred != provider:
            raise CloudRelayError(
                f"Model {model!r} belongs to {inferred}, but provider={provider!r} was requested."
            )
        return provider
    return _provider_from_model(model) or "gpt"


def _resolve_model(provider: str, requested: str | None) -> str:
    model = (requested or "").strip()
    if provider == "gemini":
        if not model:
            raise CloudRelayError(
                "Gemini requires an explicit confirmed model: "
                "gemini-3.1-flash-image-preview or gemini-3-pro-image-preview."
            )
        if model not in GEMINI_MODELS:
            raise CloudRelayError(
                f"Unsupported Gemini image model {model!r}; choose "
                "gemini-3.1-flash-image-preview or gemini-3-pro-image-preview."
            )
    elif not model:
        model = DEFAULT_MODELS[provider]
    inferred = _provider_from_model(model)
    if inferred and inferred != provider:
        raise CloudRelayError(
            f"Model {model!r} belongs to {inferred}, but provider={provider!r} was requested."
        )
    return model


def _secret_root() -> Path:
    config_root = os.environ.get("XDG_CONFIG_HOME")
    return (
        Path(config_root).expanduser() if config_root else Path.home() / ".config"
    ) / "cloudrelay"


def _secret_file(provider: str = "gpt") -> Path:
    return _secret_root() / KEY_FILE_NAMES[_normalize_provider(provider)]


def _read_windows_user_environment(name: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
        return str(value).strip() or None
    except OSError:
        return None


def _read_posix_secret_file(provider: str = "gpt") -> str | None:
    """Compatibility helper retained for callers of the original script."""
    try:
        return _secret_file(provider).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _api_key(provider: str = "gpt", *, with_source: bool = False):
    provider = _normalize_provider(provider)
    names = KEY_ENV_NAMES[provider]
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return (value, name) if with_source else value
    for name in names:
        value = _read_windows_user_environment(name)
        if value:
            return (value, name) if with_source else value
    path = _secret_file(provider)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value:
        return (value, str(path)) if with_source else value
    canonical = names[0]
    provider_label = "GPT" if provider == "gpt" else provider.capitalize()
    raise CloudRelayError(
        f"{provider_label} image key is missing. Set {canonical} with a key "
        f"from the {GROUP_LABELS[provider]!r} group, or configure it with "
        f"configure_api_key.py --provider {provider}."
    )


def _parse_json(raw: bytes) -> dict:
    text = raw.decode("utf-8", "replace") if raw else ""
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {"error": {"message": text.strip()[:500] or "Empty response"}}
    return value if isinstance(value, dict) else {"data": value}


def _error_message(payload: dict) -> str:
    if not isinstance(payload, Mapping):
        return "Malformed non-object response"
    error = payload.get("error", payload)
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error)


def _safe_error_message(payload: dict, api_key: str | None = None) -> str:
    message = _error_message(payload)
    if api_key:
        message = message.replace(api_key, "[REDACTED]")
    return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", message)


def _redact_text(value: object, api_key: str | None = None) -> str:
    text = str(value)
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", text)


def _redact_value(value: object, api_key: str | None = None) -> object:
    """Recursively remove selected and recognizable key-shaped strings."""
    if isinstance(value, Mapping):
        return {
            _redact_text(key, api_key): _redact_value(item, api_key)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item, api_key) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item, api_key) for item in value]
    if isinstance(value, str):
        return _redact_text(value, api_key)
    return value


def _raise_http(
    provider: str,
    status: int,
    payload: dict,
    operation: str,
    api_key: str | None = None,
) -> None:
    if status in (401, 403):
        env_name = KEY_ENV_NAMES[provider][0]
        group = GROUP_LABELS[provider]
        raise CloudRelayError(
            f"{operation} rejected {env_name} (HTTP {status}). Verify that the key "
            f"is active and belongs to the {group!r} group."
        )
    if status == 429:
        raise CloudRelayError(
            f"{operation} hit the rate or quota limit (HTTP 429). Retry on request."
        )
    raise CloudRelayError(
        f"{operation} failed (HTTP {status}): {_safe_error_message(payload, api_key)}"
    )


def _request(
    method: str,
    path: str,
    api_key: str,
    body: dict | None = None,
    timeout: float = 60,
) -> tuple[int, dict]:
    """Make one unified gateway request with Bearer authentication."""
    data = (
        json.dumps(body, ensure_ascii=False).encode("utf-8")
        if body is not None
        else None
    )
    request = urllib.request.Request(BASE_URL + path, data=data, method=method)
    request.add_header("Authorization", "Bearer " + api_key)
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with _NO_REDIRECT_OPENER.open(request, timeout=timeout) as response:
            return response.status, _parse_json(response.read())
    except urllib.error.HTTPError as error:
        return error.code, _parse_json(error.read())
    except urllib.error.URLError as error:
        raise CloudRelayError(f"Network request failed: {error.reason}") from error
    except TimeoutError as error:
        raise CloudRelayError("Network request timed out.") from error


def _model_summary(spec: dict, api_key: str | None = None) -> str:
    summary = (
        f"id={spec.get('id')} kind={spec.get('kind')} "
        f"endpoint={spec.get('endpoint')} async={spec.get('async')}"
    )
    return _redact_text(summary, api_key)


def _discover_model(provider: str, model: str, api_key: str) -> tuple[dict, dict]:
    status, payload = _request("GET", MEDIA_MODELS_PATH, api_key, timeout=60)
    if status != 200:
        _raise_http(
            provider,
            status,
            payload,
            "CloudRelay media-model discovery",
            api_key,
        )

    allowed = payload.get("image_generation_allowed")
    if allowed is False or str(allowed).strip().lower() in {"false", "0", "no"}:
        raise CloudRelayError(
            f"The {GROUP_LABELS[provider]!r} key is not allowed to generate images."
        )
    raw_models = payload.get("data")
    models = (
        [item for item in raw_models if isinstance(item, dict)]
        if isinstance(raw_models, list)
        else []
    )
    print(
        "Discovered platform=%s image_generation_allowed=%s models=%d"
        % (
            _redact_text(payload.get("platform"), api_key),
            _redact_text(allowed, api_key),
            len(models),
        ),
        flush=True,
    )
    for spec in models:
        print(f"  {_model_summary(spec, api_key)}", flush=True)
    if not models:
        raise CloudRelayError(
            "This key has no available media image models. Check the account's "
            "model mapping, image permission, and selected key group."
        )

    matching = next((item for item in models if item.get("id") == model), None)
    if matching is None:
        safe_model = _redact_text(model, api_key)
        available = ", ".join(
            _redact_text(item.get("id"), api_key) for item in models
        )
        raise CloudRelayError(
            f"Model {safe_model!r} is not available for the selected key. "
            f"Discovered models: {available}."
        )

    kind = str(matching.get("kind") or "").strip().lower()
    if kind != "image":
        raise CloudRelayError(
            f"Model {_redact_text(model, api_key)!r} is discovered as "
            f"kind={_redact_text(kind, api_key)!r}, not an image model."
        )
    endpoint = str(matching.get("endpoint") or "").strip().lower()
    if endpoint != "images":
        raise CloudRelayError(
            f"Model {_redact_text(model, api_key)!r} is routed to unsupported "
            f"endpoint {_redact_text(endpoint, api_key)!r}; "
            "the image skill requires the unified images endpoint."
        )
    async_value = matching.get("async")
    if not _is_true_flag(async_value):
        raise CloudRelayError(
            f"Model {_redact_text(model, api_key)!r} is not enabled for "
            "asynchronous image tasks. "
            "The unified image workflow does not fall back to provider-specific synchronous APIs."
        )
    return matching, payload


def _image_payload(args: argparse.Namespace) -> dict:
    payload = {"model": args.model, "prompt": args.prompt, "n": args.count}
    if args.size != "auto":
        payload["size"] = args.size
    if args.quality != "auto":
        payload["quality"] = args.quality
    return payload


def _submit_image(
    provider: str, args: argparse.Namespace, api_key: str
) -> tuple[str, dict]:
    status, task = _request(
        "POST",
        IMAGE_SUBMIT_PATH,
        api_key,
        _image_payload(args),
        timeout=60,
    )
    if status not in (200, 202):
        _raise_http(provider, status, task, "Unified image submission", api_key)
    task_id = str(task.get("task_id") or task.get("id") or "").strip()
    if not task_id:
        raise CloudRelayError("Image submission succeeded but returned no task ID.")
    print(
        "Submitted unified image task=%s status=%s"
        % (
            _redact_text(task_id, api_key),
            _redact_text(task.get("status", "unknown"), api_key),
        ),
        flush=True,
    )
    return task_id, task


def _normalized_status(value: object) -> str:
    status = str(value or "").strip().lower()
    return "completed" if status == "done" else status


def _poll_image(
    provider: str,
    task_id: str,
    api_key: str,
    initial_state: dict,
    timeout: float,
) -> dict:
    if not isinstance(initial_state, Mapping):
        raise CloudRelayError("Image submission returned a malformed task state.")
    state = initial_state
    state_status = _normalized_status(state.get("status"))
    if state_status in TERMINAL_STATUSES:
        return state

    delay = 2.0
    deadline = time.monotonic() + timeout
    last_status = state_status or "unknown"
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        if remaining <= 0:
            break
        time.sleep(min(delay, remaining))
        remaining = max(0.1, min(60.0, deadline - time.monotonic()))
        status, state = _request(
            "GET",
            IMAGE_TASK_PATH.format(
                task_id=urllib.parse.quote(str(task_id), safe="")
            ),
            api_key,
            timeout=remaining,
        )
        if status == 200:
            if not isinstance(state, Mapping):
                raise CloudRelayError(
                    "Unified image task %s returned a malformed state response."
                    % _redact_text(task_id, api_key)
                )
            last_status = _normalized_status(state.get("status")) or "unknown"
            print(
                "task=%s status=%s"
                % (
                    _redact_text(task_id, api_key),
                    _redact_text(last_status, api_key),
                ),
                flush=True,
            )
            if last_status in TERMINAL_STATUSES:
                return state
        elif status in (401, 403, 429):
            _raise_http(provider, status, state, "Unified image task polling", api_key)
        else:
            print(
                f"Unified task polling warning (HTTP {status}): "
                f"{_safe_error_message(state, api_key)}; retrying",
                file=sys.stderr,
                flush=True,
            )
        delay = min(delay * 1.5, 10.0)
    raise CloudRelayError(
        "Polling timed out for unified image task %s; last status=%s."
        % (_redact_text(task_id, api_key), _redact_text(last_status, api_key))
    )


def _run_unified_image(
    provider: str, args: argparse.Namespace, api_key: str
) -> tuple[str, list[dict], object, dict]:
    task_id, submitted = _submit_image(provider, args, api_key)
    completed = _poll_image(
        provider, task_id, api_key, submitted, args.poll_timeout
    )
    if not isinstance(completed, Mapping):
        raise CloudRelayError(
            "Unified image task %s returned a malformed terminal response."
            % _redact_text(task_id, api_key)
        )
    final_status = _normalized_status(completed.get("status"))
    if final_status != "completed":
        raise CloudRelayError(
            "Unified image task %s ended with status=%s: %s"
            % (
                _redact_text(task_id, api_key),
                _redact_text(final_status or "unknown", api_key),
                _safe_error_message(completed, api_key),
            )
        )
    result = completed.get("result")
    if not isinstance(result, Mapping):
        raise CloudRelayError(
            "Completed unified image task %s returned malformed result data."
            % _redact_text(task_id, api_key)
        )
    images = result.get("data") or []
    if not isinstance(images, list) or not images:
        raise CloudRelayError(
            "Completed unified image task %s returned no images."
            % _redact_text(task_id, api_key)
        )
    return task_id, images, result.get("usage"), completed


def _safe_job_id(job_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", job_id).strip("._")
    return safe or "cloudrelay-image"


def _download_url(url: str) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise CloudRelayError("Image URL must use an HTTP or HTTPS URL.")
    if parsed.username or parsed.password:
        raise CloudRelayError("Image URL must not contain embedded credentials.")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "image/*,*/*;q=0.8"},
    )
    try:
        with _NO_REDIRECT_OPENER.open(request, timeout=180) as response:
            return response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
        # Do not echo a signed URL (which may contain sensitive query values).
        raise CloudRelayError("Image download failed due to an HTTP or network error.") from error


def _image_bytes(image: dict, index: int) -> bytes:
    encoded = image.get("b64_json") or image.get("b64")
    if encoded:
        encoded = str(encoded)
        if encoded.startswith("data:") and "," in encoded:
            encoded = encoded.split(",", 1)[1]
        try:
            return base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise CloudRelayError(f"Image {index} contains invalid base64 data.") from error
    url = str(image.get("url") or "")
    if url.startswith("data:") and "," in url:
        try:
            return base64.b64decode(url.split(",", 1)[1], validate=True)
        except (binascii.Error, ValueError) as error:
            raise CloudRelayError(f"Image {index} contains invalid data-URL data.") from error
    if url:
        return _download_url(url)
    raise CloudRelayError(f"Image {index} contains neither b64_json nor url.")


def _extension(data: bytes, mime: str = "", url: str = "") -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return ".mp4"
    low_mime = str(mime or "").lower()
    for needle, extension in (
        ("jpeg", ".jpg"),
        ("jpg", ".jpg"),
        ("webp", ".webp"),
        ("png", ".png"),
        ("gif", ".gif"),
    ):
        if needle in low_mime:
            return extension
    url_path = urllib.parse.urlparse(url or "").path.lower()
    for extension in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        if url_path.endswith(extension):
            return ".jpg" if extension == ".jpeg" else extension
    return ".png"


def _save_images(
    task_id: str,
    images_or_job: list[dict] | dict,
    output_dir: Path,
    api_key: str | None = None,
) -> list[Path]:
    # Accept the old completed-job wrapper as well as the unified result.data
    # list so callers that imported this helper do not break during migration.
    if isinstance(images_or_job, Mapping):
        if _normalized_status(images_or_job.get("status")) != "completed":
            raise CloudRelayError(
                "Task %s ended with status=%s."
                % (
                    _redact_text(task_id, api_key),
                    _redact_text(images_or_job.get("status", "unknown"), api_key),
                )
            )
        result = images_or_job.get("result")
        if not isinstance(result, Mapping):
            raise CloudRelayError(
                "Task %s returned malformed result data."
                % _redact_text(task_id, api_key)
            )
        images = result.get("data") or []
    else:
        images = images_or_job
    if not isinstance(images, list) or not images:
        raise CloudRelayError(
            "Completed unified image task %s returned no images."
            % _redact_text(task_id, api_key)
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_job_id(_redact_text(task_id, api_key))
    saved: list[Path] = []
    for index, image in enumerate(images):
        if not isinstance(image, Mapping):
            raise CloudRelayError(f"Image {index} has an unexpected response shape.")
        data = _image_bytes(image, index)
        if not data:
            raise CloudRelayError(f"Image {index} is empty.")
        extension = _extension(data, image.get("mime_type"), image.get("url"))
        path = (output_dir / f"{stem}_{index}{extension}").resolve()
        path.write_bytes(data)
        saved.append(path)
    print(f"Completed {len(saved)} image(s)")
    for path in saved:
        print(f"Saved {path}")
    return saved


def _save_and_report(
    args: argparse.Namespace,
    provider: str,
    key_source: str,
    task_id: str,
    images: list[dict],
    usage: object,
    model_spec: dict,
    discovery: dict,
    api_key: str | None = None,
) -> list[Path]:
    saved = _save_images(task_id, images, args.output_dir, api_key)
    summary = {
        "provider": provider,
        "model": args.model,
        "prompt": args.prompt,
        "size": args.size if args.size != "auto" else "auto (omitted)",
        "quality": args.quality if args.quality != "auto" else "auto (omitted)",
        "count_requested": args.count,
        "count_saved": len(saved),
        "response_format": "gateway data (url or b64_json)",
        "input_image": None,
        "endpoint": IMAGE_SUBMIT_PATH,
        "transport": "async",
        "request_id": task_id,
        "credential_source": key_source,
        "credential_group": GROUP_LABELS[provider],
        "usage": usage,
        "discovered_platform": discovery.get("platform"),
        "image_generation_allowed": discovery.get("image_generation_allowed"),
        "model_kind": model_spec.get("kind"),
        "model_endpoint": model_spec.get("endpoint"),
        "model_async": model_spec.get("async"),
        "outputs": [str(path) for path in saved],
    }
    safe_summary = _redact_value(summary, api_key)
    print("Generation parameters:")
    for label, key in (
        ("provider", "provider"),
        ("model", "model"),
        ("prompt", "prompt"),
        ("size", "size"),
        ("quality", "quality"),
        ("count requested", "count_requested"),
        ("count saved", "count_saved"),
        ("response format", "response_format"),
        ("input image", "input_image"),
        ("endpoint", "endpoint"),
        ("transport", "transport"),
        ("request/task id", "request_id"),
        ("credential source", "credential_source"),
        ("credential group", "credential_group"),
        ("usage", "usage"),
        ("discovered platform", "discovered_platform"),
        ("model capability", "model_kind"),
        ("outputs", "outputs"),
    ):
        print(f"  {label}: {safe_summary[key]}")
    print(
        "PARAMETERS_JSON="
        + json.dumps(safe_summary, ensure_ascii=False, default=str, sort_keys=True)
    )
    return saved


def _positive_timeout(value: str) -> float:
    timeout = float(value)
    if timeout <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return timeout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate images through CloudRelay's unified GPT, Gemini, and Grok API."
    )
    parser.add_argument(
        "--provider",
        choices=("auto", "gpt", "gemini", "grok"),
        default="auto",
        help="Image channel; auto infers it from --model and otherwise uses GPT.",
    )
    parser.add_argument("--prompt", required=True, help="Image-generation prompt.")
    parser.add_argument(
        "--model",
        default=None,
        help="GPT/Grok use their channel defaults; Gemini requires an explicit confirmed choice.",
    )
    parser.add_argument(
        "--size",
        choices=("auto", "1024x1024", "1536x1024", "1024x1536", "2048x2048"),
        default="auto",
        help="Image size; auto omits the field and lets the gateway/upstream choose.",
    )
    parser.add_argument(
        "--quality",
        choices=("auto", "low", "medium", "high"),
        default="auto",
        help="Image quality; auto omits the field.",
    )
    parser.add_argument("--count", type=int, choices=range(1, 5), default=1)
    parser.add_argument("--output-dir", type=Path, default=Path.cwd())
    parser.add_argument("--poll-timeout", type=_positive_timeout, default=300.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_console_utf8()
    args = build_parser().parse_args(argv)
    try:
        provider = _resolve_provider(args.provider, args.model)
        args.model = _resolve_model(provider, args.model)
        api_key, key_source = _api_key(provider, with_source=True)
        print(f"Using channel={provider} model={args.model} key_source={key_source}")
        model_spec, discovery = _discover_model(provider, args.model, api_key)
        task_id, images, usage, _ = _run_unified_image(provider, args, api_key)
        _save_and_report(
            args,
            provider,
            key_source,
            task_id,
            images,
            usage,
            model_spec,
            discovery,
            api_key,
        )
    except CloudRelayError as error:
        print(f"CloudRelay error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted while waiting for CloudRelay.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
