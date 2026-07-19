"""Fire-and-forget BPMN `generic.audit.emit` shim.

Per user choice 2026-05-08: keep OCEL audit trail by dispatching
`generic.audit.emit` to bpmn-dispatcher from each LangGraph node.
This preserves `vertex_repo_commit` OCEL traces, process mining
dashboards, RACI evidence — without re-implementing OCEL schema.

The dispatch is fire-and-forget: failure to emit MUST NOT block
the LangGraph node. Audit loss is logged but not raised.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

_log = logging.getLogger(__name__)

_DISPATCHER_URL = os.environ.get(
    "BPMN_DISPATCHER_INTERNAL_URL",
    "http://bpmn-dispatcher.mitama-udf.svc.cluster.local:8080",
).rstrip("/")
_INTERNAL_SECRET = os.environ.get("BPMN_DISPATCHER_INTERNAL_SECRET", "").strip()
_AUDIT_TIMEOUT_SEC = float(os.environ.get("LG_AUDIT_TIMEOUT_SEC", "3.0"))
_AUDIT_DISABLED = os.environ.get("LG_AUDIT_DISABLED", "0") == "1"


async def emit_audit(
    *,
    actor: str,
    activity: str,
    object_id: str,
    object_type: str,
    attributes: dict[str, Any] | None = None,
) -> None:
    """Send one OCEL event to BPMN dispatcher's `generic.audit.emit`.

    Args:
        actor: did:web:mng4k4x1.etzhayyim.com (or sub-actor DID for path-based scope)
        activity: e.g. "scene.bulkSeed.start", "scene.render.success"
        object_id: e.g. "melina-elden-ring:scene-3"
        object_type: e.g. "mangaka.scene"
        attributes: free-form OCEL attributes (sceneType, slug, blobCid, ...)

    Failure is logged but not raised. Audit loss is acceptable —
    LangGraph state checkpoint is the source of truth for resumability.
    """
    if _AUDIT_DISABLED:
        return

    payload = {
        "actor": actor,
        "activity": activity,
        "objectId": object_id,
        "objectType": object_type,
        "attributes": attributes or {},
    }
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if _INTERNAL_SECRET:
        headers["x-internal-trust"] = _INTERNAL_SECRET

    url = f"{_DISPATCHER_URL}/xrpc/com.etzhayyim.generic.audit.emit"

    try:
        async with httpx.AsyncClient(timeout=_AUDIT_TIMEOUT_SEC) as client:
            await client.post(url, json=payload, headers=headers)
    except Exception as exc:  # noqa: BLE001
        _log.warning("audit.emit failed (non-fatal): %s | activity=%s id=%s", exc, activity, object_id)


def emit_audit_bg(
    *,
    actor: str,
    activity: str,
    object_id: str,
    object_type: str,
    attributes: dict[str, Any] | None = None,
) -> asyncio.Task:
    """Schedule emit_audit() on the running loop without awaiting it."""
    return asyncio.create_task(
        emit_audit(
            actor=actor,
            activity=activity,
            object_id=object_id,
            object_type=object_type,
            attributes=attributes,
        )
    )
