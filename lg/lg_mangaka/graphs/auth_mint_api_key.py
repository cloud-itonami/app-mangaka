"""mangaka `auth_mint_api_key` — pod-side sk_live_* API key minting.

Backs the `studio.mintApiKey` MCP tool. Because lg-mangaka-studio runs in
the mitama-udf namespace with `RW_URL` reachable, it can INSERT to
public.vertex_api_key directly — no chicken-and-egg with `etzhayyim authn signin`.

Authorization: caller identity comes from the CF Access JWT email the
Studio Worker forwards as `user_email` in the input. The mint is scoped
to `productScope` (default "comfyui") and `scopes` (default
"comfyui,comfyui:generate,read") so the resulting key only opens the
intended product surface.

Input:
    user_email     str    — sourced from Cf-Access-Authenticated-User-Email
    name           str    — human label for the key (default "studio-{user}")
    scopes         str    — comma-separated scope list (default cine-friendly)
    product_scope  str    — default "comfyui"
    dry_run        bool   — synth a key but skip the INSERT

Output:
    status         "minted" | "error"
    api_key        sk_live_<product>_<32hex>  ← the raw key (returned ONCE)
    key_prefix     first 16 chars (for display)
    vertex_id      AT URI of the inserted row
    owner_did      derived from user_email
    error          str | None
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

_log = logging.getLogger(__name__)

_APP_DID = os.environ.get("MANGAKA_APP_DID", "did:web:mangaka.etzhayyim.com")
_DEFAULT_SCOPES = "comfyui,comfyui:generate,read"
_DEFAULT_PRODUCT = "comfyui"


class _State(TypedDict, total=False):
    user_email: str
    name: str
    scopes: str
    product_scope: str
    dry_run: bool

    raw_key: str
    key_hash: str
    key_prefix: str
    owner_did: str
    vertex_id: str

    status: str
    api_key: str
    error: str | None


def _email_to_did(email: str) -> str:
    """Map `user@etzhayyim.com` → `did:web:mangaka.etzhayyim.com:user:user_at_etzhayyim_co_jp`.

    DID path segments must be ASCII without `:`/`@`/`.`, so we substitute.
    """
    if not email or "@" not in email:
        return _APP_DID
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", email)
    return f"{_APP_DID}:user:{safe}"


async def _generate(state: _State) -> dict[str, Any]:
    user_email = state.get("user_email") or ""
    product = (state.get("product_scope") or _DEFAULT_PRODUCT).strip().lower()
    raw_key = f"sk_live_{product}_{secrets.token_hex(24)}"
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    owner_did = _email_to_did(user_email)
    return {
        "raw_key": raw_key,
        "key_hash": key_hash,
        "key_prefix": raw_key[:16],
        "owner_did": owner_did,
    }


async def _persist(state: _State) -> dict[str, Any]:
    if state.get("dry_run"):
        return {
            "status": "minted",
            "api_key": state["raw_key"],
            "vertex_id": f"dry-run/{state['key_hash'][:16]}",
        }
    name = state.get("name") or f"studio-{(state.get('user_email') or 'anon').split('@')[0]}"
    scopes = state.get("scopes") or _DEFAULT_SCOPES
    owner = state["owner_did"]
    vid = f"at://{owner}/com.etzhayyim.auth.apiKey/{state['key_hash'][:13]}"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    import asyncio
    from kotodama.kotoba_datomic import get_kotoba_client
    try:
        def _write():
            client = get_kotoba_client()
            client.insert_row("vertex_api_key", {
                "vertex_id": vid,
                "_seq": 0,
                "created_date": now[:10],
                "sensitivity_ord": 0,
                "owner_did": owner,
                "key_hash": state["key_hash"],
                "key_prefix": state["key_prefix"],
                "name": name,
                "scopes": scopes,
                "status": "active",
                "last_used_at": None,
                "created_at": now
            })
        await asyncio.to_thread(_write)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"INSERT failed: {exc!s}"[:300]}

    return {
        "status": "minted",
        "api_key": state["raw_key"],
        "vertex_id": vid,
    }


def _build() -> StateGraph:
    g: StateGraph = StateGraph(_State)
    g.add_node("generate", _generate)
    g.add_node("persist", _persist, retry_policy=RetryPolicy(max_attempts=2))
    g.add_edge(START, "generate")
    g.add_edge("generate", "persist")
    g.add_edge("persist", END)
    return g


GRAPH = _build().compile(name="auth_mint_api_key")
