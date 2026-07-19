"""kotoba-compatible LangGraph checkpoint saver for mangaka.

This replaces the legacy RisingWave PostgreSQL checkpointer, forwarding
calls to the global KotobaCheckpointSaver as part of the ADR-2605262130
migration.

Usage:
    from lg_mangaka.checkpointer import build_checkpointer

    async with build_checkpointer() as cp:
        graph = compiled_graph.with_config(checkpointer=cp)
        await graph.ainvoke(initial, config={"configurable": {"thread_id": ...}})
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from kotodama.langgraph_checkpoint_kotoba import get_checkpoint_saver

_log = logging.getLogger(__name__)


@asynccontextmanager
async def build_checkpointer() -> AsyncIterator[Any]:
    """Yield a LangGraph checkpoint saver bound to kotoba.

    Transitions mangaka from RisingWave PG to the Kotoba Datom log.
    """
    cp = await get_checkpoint_saver()
    yield cp
