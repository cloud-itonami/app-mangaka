"""B2 content-addressed blob helpers (AWS SigV4 over urllib).

Mirrors the `kotodama.primitives.training_export._b2_put` pattern: no boto3,
no aiobotocore, just stdlib `urllib` + `hashlib` + `hmac`. Same auth surface so
ops only needs the existing `B2_ACCESS_KEY_ID` / `B2_SECRET_ACCESS_KEY` /
`B2_ENDPOINT` env vars used elsewhere in the LangGraph pod.

Content-addressed: key = `{prefix}/{sha256hex}`. Idempotent PUTs — re-uploading
the same bytes yields the same key with no overwrite cost (HEAD-first skip).

This is the persistence layer for `compose_scene_3d._step_render_keyframes`
and is reusable by any other LangGraph node that needs to stash an artefact
(panel renders, depth maps, outline masks, future PDF exports).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
import urllib.error
import urllib.request

_log = logging.getLogger(__name__)

_B2_BUCKET = os.environ.get("MANGAKA_B2_BUCKET", os.environ.get("TRAINING_B2_BUCKET", "etzhayyim-pds-prod"))
_B2_KEY_ID = os.environ.get("B2_ACCESS_KEY_ID", "").strip()
_B2_KEY = os.environ.get("B2_SECRET_ACCESS_KEY", "").strip()
_B2_ENDPOINT = os.environ.get(
    "B2_ENDPOINT", "https://s3.us-west-004.backblazeb2.com"
).rstrip("/")
_B2_REGION = os.environ.get("B2_REGION", "us-west-004")


class B2NotConfigured(RuntimeError):
    """Raised when B2 credentials are missing. Callers may choose to fall back
    to in-memory blob keys (for tests / dry-run) instead of failing the graph."""


def is_configured() -> bool:
    return bool(_B2_KEY_ID and _B2_KEY)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_key(data: bytes, prefix: str = "blobs/anonymous") -> str:
    """Content-addressed B2 key for `data`. Idempotent — identical content
    always yields the same key, enabling free dedup across iterations."""
    return f"{prefix.rstrip('/')}/{sha256_hex(data)}"


def head(key: str, timeout_s: float = 10.0) -> bool:
    """Return True if `key` already exists in the bucket (skip PUT path)."""
    if not is_configured():
        return False
    try:
        _request("HEAD", key, b"", "application/octet-stream", timeout_s)
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise
    except Exception:
        return False


def get(key: str, timeout_s: float = 60.0) -> bytes | None:
    """GET `key` and return bytes. Returns `None` when B2 is unconfigured,
    the key is missing, or the request fails. Used by the vision-critique
    node to re-hydrate rendered PNGs without bloating the LangGraph state
    channel (which is checkpointed to RisingWave PG)."""
    if not is_configured():
        return None
    try:
        return _request("GET", key, b"", "application/octet-stream", timeout_s)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        _log.warning("B2 GET %s failed: %s", key, e)
        return None
    except Exception as e:
        _log.warning("B2 GET %s exception: %s", key, e)
        return None


def put(key: str, data: bytes, content_type: str = "application/octet-stream", timeout_s: float = 120.0) -> str:
    """PUT `data` at `key`. Returns the s3:// URI on success. Idempotent: a
    HEAD probe avoids the upload when the key already exists, since content
    address means identical bytes."""
    if not is_configured():
        raise B2NotConfigured(
            "B2_ACCESS_KEY_ID / B2_SECRET_ACCESS_KEY not set; cannot PUT"
        )
    if head(key):
        return f"s3://{_B2_BUCKET}/{key}"
    _request("PUT", key, data, content_type, timeout_s)
    return f"s3://{_B2_BUCKET}/{key}"


def put_content_addressed(
    data: bytes,
    prefix: str = "blobs/anonymous",
    content_type: str = "image/png",
) -> tuple[str, str]:
    """Upload `data` keyed by its sha256. Returns `(blob_key, s3_uri)`.
    `blob_key` is the path under the bucket (e.g. `blobs/anonymous/{sha256}`)
    and matches the `render_blob_key` column in `vertex_mangaka_scene_3d`."""
    key = content_key(data, prefix=prefix)
    uri = put(key, data, content_type=content_type)
    return key, uri


# ── internal SigV4 plumbing ────────────────────────────────────────────────

def _request(method: str, key: str, data: bytes, content_type: str, timeout_s: float) -> bytes:
    url = f"{_B2_ENDPOINT}/{_B2_BUCKET}/{key}"
    now = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    date = now[:8]
    host = _B2_ENDPOINT.replace("https://", "").replace("http://", "")
    payload_hash = hashlib.sha256(data).hexdigest()

    if method in ("PUT", "POST"):
        canonical_headers = (
            f"content-type:{content_type}\n"
            f"host:{host}\n"
            f"x-amz-content-sha256:{payload_hash}\n"
            f"x-amz-date:{now}\n"
        )
        signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"
    else:
        canonical_headers = (
            f"host:{host}\n"
            f"x-amz-content-sha256:{payload_hash}\n"
            f"x-amz-date:{now}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"

    canonical_req = (
        f"{method}\n/{_B2_BUCKET}/{key}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )
    scope = f"{date}/{_B2_REGION}/s3/aws4_request"
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n{now}\n{scope}\n"
        + hashlib.sha256(canonical_req.encode()).hexdigest()
    )

    def _sign(k: bytes, msg: str) -> bytes:
        return hmac.new(k, msg.encode(), hashlib.sha256).digest()

    signing_key = _sign(
        _sign(_sign(_sign(f"AWS4{_B2_KEY}".encode(), date), _B2_REGION), "s3"),
        "aws4_request",
    )
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    auth = (
        f"AWS4-HMAC-SHA256 Credential={_B2_KEY_ID}/{scope},"
        f" SignedHeaders={signed_headers},"
        f" Signature={signature}"
    )

    headers: dict[str, str] = {
        "x-amz-date": now,
        "x-amz-content-sha256": payload_hash,
        "Authorization": auth,
    }
    if method in ("PUT", "POST"):
        headers["Content-Type"] = content_type

    req = urllib.request.Request(
        url,
        data=data if method in ("PUT", "POST") else None,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return resp.read()
