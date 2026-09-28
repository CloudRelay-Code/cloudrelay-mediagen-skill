#!/usr/bin/env python3
"""Generate, edit, extend, or resume CloudRelay Grok video tasks."""
from __future__ import annotations

import argparse
import base64
import http.client
import json
import math
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import generate_image as shared

Error = shared.CloudRelayError
RESOLUTIONS = ("480p", "720p", "1080p")
RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3")
ENDPOINTS = {"generate": "/v1/videos/generations", "edit": "/v1/videos/edits", "extend": "/v1/videos/extensions"}
FAILED = {"failed", "expired", "canceled", "cancelled"}


def positive_timeout(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=ENDPOINTS, default="generate")
    parser.add_argument("--provider", choices=("grok",), default="grok")
    parser.add_argument("--prompt")
    parser.add_argument("--model", help="Defaults to discovered grok-imagine-video when available.")
    parser.add_argument("--image", help="First-frame local PNG/JPEG/WebP or public HTTP(S) URL.")
    parser.add_argument("--video", help="Source local MP4 or public HTTP(S) URL for edit/extend.")
    parser.add_argument("--duration", type=int, help="Generation: 1-15 seconds (default 8); extension: 2-10 added seconds (default 6).")
    parser.add_argument("--resolution", choices=RESOLUTIONS)
    parser.add_argument("--aspect-ratio", choices=RATIOS)
    parser.add_argument("--request-id", help="Resume polling/download only, without a new paid submission.")
    parser.add_argument("--output-dir", type=Path, default=Path.cwd() / "generated-videos")
    parser.add_argument("--poll-timeout", type=positive_timeout, default=900.0)
    return parser


def validate_url(value):
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise Error("Media URL must be HTTP(S) without embedded credentials.")
    try:
        parsed.port
    except ValueError as exc:
        raise Error("Invalid media URL port.") from exc
    return parsed


def media_ref(value, kind):
    if value.lower().startswith(("http:", "https:")):
        validate_url(value)
        return value
    path = Path(value).expanduser()
    if not path.is_file():
        raise Error(f"Missing local {kind} file: {path}")
    data = path.read_bytes()
    if kind == "video":
        if len(data) < 12 or data[4:8] != b"ftyp":
            raise Error("Source video must be an MP4 file (H.264/H.265/AV1 per upstream); codec is not verified locally.")
        mime = "video/mp4"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise Error("First frame must be PNG, JPEG, or WebP.")
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def build_payload(args):
    if not args.prompt or not args.prompt.strip():
        raise Error("--prompt is required for a new task.")
    payload = {"prompt": args.prompt}
    if args.action == "generate":
        if args.video:
            raise Error("--video is only accepted for edit/extend.")
        duration = args.duration if args.duration is not None else 8
        if not 1 <= duration <= 15:
            raise Error("Generation duration must be 1-15 seconds.")
        payload.update(duration=duration, resolution=args.resolution or "480p")
        if args.aspect_ratio:
            payload["aspect_ratio"] = args.aspect_ratio
        if args.image:
            payload["image"] = {"url": media_ref(args.image, "image")}
    else:
        if args.image or args.resolution or args.aspect_ratio:
            raise Error("--image, --resolution and --aspect-ratio apply only to generation.")
        if not args.video:
            raise Error("--video is required for edit/extend.")
        if args.action == "edit" and args.duration is not None:
            raise Error("Video edits do not accept --duration.")
        if args.action == "extend":
            duration = args.duration if args.duration is not None else 6
            if not 2 <= duration <= 10:
                raise Error("Extension duration must be 2-10 added seconds.")
            payload["duration"] = duration
        payload["video"] = {"url": media_ref(args.video, "video")}
    return payload


def image_only(model):
    return "video-1.5" in model.lower()


def discover(args, key):
    status, discovery = shared._request("GET", shared.MEDIA_MODELS_PATH, key)
    if status != 200:
        shared._raise_http("grok", status, discovery, "Video model discovery", key)
    models = discovery.get("data")
    models = [m for m in models if isinstance(m, dict) and m.get("kind") == "video"] if isinstance(models, list) else []
    if args.model:
        spec = next((m for m in models if m.get("id") == args.model), None)
        if spec is None:
            raise Error("Requested video model is unavailable for this Grok key.")
    else:
        usable = [m for m in models if isinstance(m.get("id"), str) and m["id"] and (not image_only(m["id"]) or (args.action == "generate" and args.image))]
        spec = next((m for m in usable if m["id"] == "grok-imagine-video"), None)
        if spec is None and len(usable) == 1:
            spec = usable[0]
        if spec is None:
            available = ", ".join(str(m.get("id")) for m in models)
            raise Error(f"Choose --model from discovered video models: {available or '(none)'}. A video-1.5 model requires a first frame and cannot edit/extend.")
    if spec.get("endpoint") != "grok_videos":
        raise Error("Video model must use the discovered grok_videos endpoint.")
    if "async" in spec and not shared._is_true_flag(spec["async"]):
        raise Error("Discovered video model explicitly disables async tasks.")
    if image_only(spec["id"]) and (args.action != "generate" or not args.image):
        raise Error("video-1.5 supports image-to-video only; provide --image for generation.")
    return spec, discovery


def poll(request_id, key, timeout):
    deadline = time.monotonic() + timeout
    delay = 3.0
    path = "/v1/videos/" + urllib.parse.quote(request_id, safe="")
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            status, state = shared._request("GET", path, key, timeout=min(60.0, remaining))
        except Error:
            print("Temporary polling network failure; continuing existing task.", file=sys.stderr, flush=True)
        else:
            if status in (401, 403, 429):
                shared._raise_http("grok", status, state, "Video polling", key)
            if status in (200, 202):
                st = str(state.get("status", "")).lower()
                print(shared._redact_text(f"request_id={request_id} status={st or 'pending'} progress={state.get('progress')}", key), flush=True)
                if st == "done":
                    return state
                if st in FAILED:
                    raise Error(f"Video task {st}: {shared._safe_error_message(state, key)}")
            elif status not in (408, 425) and status < 500:
                shared._raise_http("grok", status, state, "Video polling", key)
            else:
                print(f"Temporary polling HTTP {status}; continuing existing task.", file=sys.stderr, flush=True)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(delay, remaining))
        delay = min(delay * 1.4, 10.0)
    raise Error("Video polling timed out; resume with --request-id, do not resubmit.")


def download_video(url, key, path):
    absolute = urllib.parse.urljoin(shared.BASE_URL + "/", url)
    parsed = validate_url(absolute)
    headers = {"User-Agent": shared.USER_AGENT, "Accept": "video/mp4,*/*;q=0.8"}
    if (parsed.scheme, parsed.hostname, parsed.port or 443) == ("https", "cloudrelay.cn", 443):
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(absolute, headers=headers)
    partial = path.with_suffix(".mp4.part")
    try:
        with shared._NO_REDIRECT_OPENER.open(request, timeout=300) as response:
            first = response.read(64)
            if len(first) < 12 or first[4:8] != b"ftyp":
                raise Error("Video download did not return an MP4 container.")
            size = len(first)
            with partial.open("wb") as output:
                output.write(first)
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    size += len(chunk)
            expected = response.headers.get("Content-Length")
            if expected and size != int(expected):
                raise Error("Video download is incomplete; resume download using --request-id.")
        partial.replace(path)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as exc:
        raise Error("Video download failed; resume using --request-id. No new submission is needed.") from exc
    finally:
        partial.unlink(missing_ok=True)
    return size


def main(argv=None):
    shared._configure_console_utf8()
    args = build_parser().parse_args(argv)
    key = None
    request_id = args.request_id
    try:
        if request_id:
            if any(x is not None for x in (args.prompt, args.model, args.image, args.video, args.duration, args.resolution, args.aspect_ratio)) or args.action != "generate":
                raise Error("--request-id resumes an existing task; omit generation/edit options.")
            if not request_id.strip():
                raise Error("--request-id cannot be blank.")
            payload = {}
        else:
            payload = build_payload(args)  # All local checks precede discovery/submission.
        args.output_dir = args.output_dir.expanduser().resolve()
        args.output_dir.mkdir(parents=True, exist_ok=True)
        key, key_source = shared._api_key("grok", with_source=True)
        spec = discovery = {}
        if not request_id:
            spec, discovery = discover(args, key)
            payload["model"] = spec["id"]
            status, submitted = shared._request("POST", ENDPOINTS[args.action], key, payload, timeout=300)
            if status not in (200, 202):
                shared._raise_http("grok", status, submitted, "Video submission (not retried)", key)
            request_id = str(submitted.get("request_id") or submitted.get("id") or "").strip()
            if not request_id:
                raise Error("Submission returned no request_id; do not resubmit automatically.")
            print(shared._redact_text(f"Submitted request_id={request_id}", key), flush=True)
        state = poll(request_id, key, args.poll_timeout)
        video = state.get("video")
        if not isinstance(video, dict) or not isinstance(video.get("url"), str) or not video["url"]:
            raise Error("Task is done but has no video.url.")
        stem = shared._safe_job_id(shared._redact_text(request_id, key))
        path = args.output_dir / f"video_{stem}.mp4"
        size = download_video(video["url"], key, path)
        summary = {
            "provider": "grok", "credential_group": "grok", "credential_source": key_source,
            "action": "resume" if args.request_id else args.action,
            "request_id": request_id, "transport": "async",
            "endpoint": None if args.request_id else ENDPOINTS[args.action],
            "model": payload.get("model"), "prompt": payload.get("prompt"),
            "duration_requested": payload.get("duration"),
            "duration_is_extension": args.action == "extend" and not bool(args.request_id),
            "resolution_requested": payload.get("resolution"), "aspect_ratio_requested": payload.get("aspect_ratio"),
            "input_image": bool(args.image), "input_video": bool(args.video),
            "server_reported_duration": video.get("duration"),
            "server_reported_resolution": video.get("resolution"),
            "container_verified": "mp4", "playback_verified": False,
            "usage": state.get("usage"), "model_capability": spec,
            "discovered_platform": discovery.get("platform"), "bytes_saved": size,
            "outputs": [str(path)],
        }
        print("PARAMETERS_JSON=" + json.dumps(shared._redact_value(summary, key), ensure_ascii=False, sort_keys=True))
        return 0
    except (Error, OSError, ValueError) as exc:
        print("CloudRelay error: " + shared._redact_text(exc, key), file=sys.stderr)
        if request_id:
            print(shared._redact_text(f"Existing request_id={request_id}; resume with --request-id. No replacement submitted.", key), file=sys.stderr)
        else:
            print("No automatic retry. If submission was interrupted, check gateway task records before submitting again.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(shared._redact_text(f"Interrupted; existing request_id={request_id or 'unknown'}. Do not resubmit automatically.", key), file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
