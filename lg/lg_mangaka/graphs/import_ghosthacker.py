"""mangaka `import_ghosthacker` graph — normalize a ghosthacker episode into
separate `vertex_mangaka` rows with parent_rkey hierarchy and props cross-refs.

Output rows (all kind values populated in same vertex_mangaka table):

  kind=work             rkey=gh-work-{workId}             parent_rkey=null
  kind=chapter          rkey=gh-chap-{episodeId}          parent_rkey=workRkey
  kind=page             rkey=gh-page-{episodeId}-p{n}     parent_rkey=chapterRkey   page_number
  kind=panel            rkey=gh-panel-{episodeId}-p{pg}-pn{n}   parent_rkey=pageRkey  panel_number
                        props={shot, visual, characters:[charRkey,...], environment:envRkey, sdxlPrompt,...}
  kind=character        rkey=gh-char-{charId}             standalone (no parent)
  kind=environment      rkey=gh-env-{envId}               standalone
  kind=organization     rkey=gh-org-{orgId}               standalone
  kind=generatedImage   rkey=gh-img-{episodeId}-p{pg}-pn{n}     parent_rkey=panelRkey    cid

Idempotent: delete-then-insert per row (RisingWave shape).

Input schema (matches `com.etzhayyim.mangaka.importGhosthacker` lexicon):
    workId           str
    workName         str
    episodeId        str
    chapterTitle     str
    arc              str
    episodeIndex     int
    mainCharacter    str
    tagline          str
    sinContrast      str
    industry         str
    pages            list[ { pageNumber, pageName, panels: list[panelObj] } ]
    characters       dict[charId, { name, role, organization }]
    environments     dict[envId, { name, description }]
    organizations    dict[orgId, { name, description }]
    actorDid, orgDid str
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

_log = logging.getLogger(__name__)

_APP_DID = os.environ.get("MANGAKA_APP_DID", "did:web:mangaka.etzhayyim.com")
_DEFAULT_ORG_DID = os.environ.get(
    "MANGAKA_DEFAULT_ORG_DID",
    "did:erc725:etzhayyim:260425:etzhayyim-japan",
)

def _coll(kind: str) -> str:
    return f"com.etzhayyim.mangaka.{kind}"


def _vid(kind: str, rkey: str) -> str:
    return f"at://{_APP_DID}/{_coll(kind)}/{rkey}"


class _State(TypedDict, total=False):
    # server.py xrpc shim converts incoming camelCase body keys to snake_case
    # before invoking the graph; TypedDict declared here uses snake_case so
    # LangGraph's StateGraph keeps the keys around.
    work_id: str
    work_name: str
    episode_id: str
    chapter_title: str
    arc: str
    episode_index: int
    main_character: str
    tagline: str
    sin_contrast: str
    industry: str
    pages: list
    characters: dict
    environments: dict
    organizations: dict
    actor_did: str
    org_did: str
    status: str
    error: str | None
    insertedCounts: dict


async def _node_import(state: _State) -> dict[str, Any]:
    # server.py xrpc shim converts camelCase keys to snake_case; tolerate both.
    def g(*keys: str, default=None):
        for k in keys:
            v = state.get(k)  # type: ignore[arg-type]
            if v is not None and v != "":
                return v
        return default
    episode_id = (g("episode_id", "episodeId") or "").strip()
    work_id = (g("work_id", "workId") or "ghost-hacker").strip()
    if not episode_id:
        return {"status": "error", "error": "episodeId required"}

    actor_did = (g("actor_did", "actorDid") or _APP_DID).strip()
    org_did = (g("org_did", "orgDid") or _DEFAULT_ORG_DID).strip()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    now_date = now_iso[:10]

    inserted = {"work": 0, "chapter": 0, "page": 0, "panel": 0,
                "character": 0, "environment": 0, "organization": 0, "generatedImage": 0}

    try:
        from kotodama.kotoba_datomic import get_kotoba_client
        import asyncio
        client = get_kotoba_client()

        async def upsert(*, kind: str, rkey: str, name: str | None = None,
                         title: str | None = None, parent_rkey: str | None = None,
                         page_number: int | None = None, panel_number: int | None = None,
                         cid: str | None = None, props_obj: dict | None = None) -> str:
            vid = _vid(kind, rkey)
            row_dict = {
                "vertex_id": vid,
                "created_date": now_date,
                "sensitivity_ord": 0,
                "owner_did": _APP_DID,
                "rkey": rkey,
                "repo": _APP_DID,
                "did": _APP_DID,
                "collection": _coll(kind),
                "label": kind,
                "title": title or name or rkey,
                "name": name or rkey,
                "display_name": name or rkey,
                "kind": kind,
                "status": "saved",
                "created_at": now_iso,
                "props": json.dumps(props_obj or {}, ensure_ascii=False),
                "parent_rkey": parent_rkey,
                "page_number": page_number,
                "panel_number": panel_number,
                "cid": cid,
                "actor_did": actor_did,
                "org_did": org_did,
            }
            await asyncio.to_thread(client.insert_row, "vertex_mangaka", row_dict)
            inserted[kind] = inserted.get(kind, 0) + 1
            return rkey

        # 1. Work
            work_rkey = f"gh-work-{work_id}"
            await upsert(kind="work", rkey=work_rkey,
                         name=g("work_name", "workName") or "Ghost Hacker",
                         title=g("work_name", "workName") or "Ghost Hacker",
                         props_obj={"industry": g("industry") or ""})

            # 2. Chapter (= episode)
            chap_rkey = f"gh-chap-{episode_id}"
            chapter_title = g("chapter_title", "chapterTitle") or episode_id
            await upsert(kind="chapter", rkey=chap_rkey, parent_rkey=work_rkey,
                         name=chapter_title, title=chapter_title,
                         props_obj={
                             "arc": g("arc") or "",
                             "episodeIndex": g("episode_index", "episodeIndex") or 0,
                             "mainCharacter": g("main_character", "mainCharacter") or "",
                             "tagline": g("tagline") or "",
                             "sinContrast": g("sin_contrast", "sinContrast") or "",
                             "industry": g("industry") or "",
                         })

            # 3. Characters (shared, idempotent)
            for char_id, cdata in (g("characters") or {}).items():
                ck = char_id.replace("character:", "").replace(":", "-")
                char_rkey = f"gh-char-{ck}"
                await upsert(kind="character", rkey=char_rkey,
                             name=(cdata or {}).get("name") or ck,
                             title=(cdata or {}).get("name") or ck,
                             props_obj=cdata or {})

            # 4. Environments
            for env_id, edata in (g("environments") or {}).items():
                ek = env_id.replace("env:", "").replace(":", "-")
                env_rkey = f"gh-env-{ek}"
                await upsert(kind="environment", rkey=env_rkey,
                             name=(edata or {}).get("name") or ek,
                             title=(edata or {}).get("name") or ek,
                             props_obj=edata or {})

            # 5. Organizations
            for org_id, odata in (g("organizations") or {}).items():
                ok = org_id.replace("org:", "").replace(":", "-")
                org_rkey = f"gh-org-{ok}"
                await upsert(kind="organization", rkey=org_rkey,
                             name=(odata or {}).get("name") or ok,
                             title=(odata or {}).get("name") or ok,
                             props_obj=odata or {})

            # 6. Pages + panels + generatedImage
            # Pages list also gets snake_cased keys at top level (page_number, page_name),
            # but nested dicts inside the list are NOT recursively converted by server.py.
            # So inside pg/pnl we still see camelCase like pg.get("pageNumber").
            for pg in g("pages") or []:
                page_num = int(pg.get("pageNumber") or pg.get("page_number") or 0)
                page_rkey = f"gh-page-{episode_id}-p{page_num}"
                page_name = pg.get("pageName") or pg.get("page_name") or f"Page {page_num}"
                await upsert(kind="page", rkey=page_rkey, parent_rkey=chap_rkey,
                             page_number=page_num,
                             name=page_name, title=page_name,
                             props_obj={"pageName": page_name})
                for pnl in pg.get("panels") or []:
                    panel_num = int(pnl.get("panelNumber") or pnl.get("panel_number") or 0)
                    panel_rkey = f"gh-panel-{episode_id}-p{page_num}-pn{panel_num}"
                    char_rkeys = []
                    for c in pnl.get("characters") or []:
                        ck = str(c).replace("character:", "").replace(":", "-")
                        char_rkeys.append(f"gh-char-{ck}")
                    env_rk = None
                    if pnl.get("environment"):
                        ek = str(pnl["environment"]).replace("env:", "").replace(":", "-")
                        env_rk = f"gh-env-{ek}"
                    sdxl = pnl.get("sdxlPrompt") or pnl.get("sdxl_prompt") or ""
                    gen_cid = pnl.get("generatedImageCid") or pnl.get("generated_image_cid") or ""
                    panel_props = {
                        "shot": pnl.get("shot") or "",
                        "visual": pnl.get("visual") or "",
                        "characters": char_rkeys,
                        "environment": env_rk,
                        "sdxlPrompt": sdxl,
                        "dialogue": pnl.get("dialogue") or [],
                    }
                    await upsert(kind="panel", rkey=panel_rkey, parent_rkey=page_rkey,
                                 page_number=page_num, panel_number=panel_num,
                                 name=f"Panel {panel_num}", title=f"Panel {panel_num}",
                                 props_obj=panel_props)
                    if gen_cid:
                        img_rkey = f"gh-img-{episode_id}-p{page_num}-pn{panel_num}"
                        await upsert(kind="generatedImage", rkey=img_rkey, parent_rkey=panel_rkey,
                                     page_number=page_num, panel_number=panel_num,
                                     cid=gen_cid,
                                     name=f"GenImage {panel_num}",
                                     props_obj={"prompt": sdxl})
    except Exception as exc:  # noqa: BLE001
        _log.exception("import_ghosthacker failed (episodeId=%s)", episode_id)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc!s}"[:300]}

    return {"status": "imported", "insertedCounts": inserted, "error": None}


def _build():
    g: StateGraph = StateGraph(_State)
    g.add_node("import", _node_import, retry_policy=RetryPolicy(max_attempts=2))
    g.add_edge(START, "import")
    g.add_edge("import", END)
    return g


GRAPH = _build().compile(name="import_ghosthacker")
