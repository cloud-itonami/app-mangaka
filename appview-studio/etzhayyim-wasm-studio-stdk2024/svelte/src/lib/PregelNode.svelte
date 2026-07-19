<script lang="ts">
  import { Handle, Position, type NodeProps } from "@xyflow/svelte";
  import type { StageCard, StageStatus } from "./NodeGraph.svelte";

  type NodeData = { id: string; label: string; stage?: StageCard };
  type Props = NodeProps & { data: NodeData };

  const { data, selected }: Props = $props();
  const stage = $derived(data.stage);
  const status = $derived<StageStatus>(stage?.status ?? "pending");
  const invocations = $derived(stage?.invocations ?? 0);
  const durMs = $derived(
    stage?.startedAt && stage?.completedAt ? stage.completedAt - stage.startedAt : 0,
  );

  const color = $derived(
    status === "done" ? "#238636"
    : status === "running" ? "#d29922"
    : status === "error" ? "#f85149"
    : "#30363d",
  );

  // Pregel structural cues. `__start__` / `__end__` are LangGraph sentinels
  // emitted by the /graph endpoint; Send-fan-out nodes show ×N invocations.
  const isStart = $derived(data.id === "__start__");
  const isEnd = $derived(data.id === "__end__");
  const sentinel = $derived(isStart || isEnd);
</script>

<div
  class="pregel"
  class:sentinel
  class:selected
  style:border-color={color}
  style:--accent={color}
>
  <Handle type="target" position={Position.Top} />
  <header>
    <span class="dot"></span>
    <span class="label">{data.label}</span>
    {#if invocations > 1}<span class="badge">×{invocations}</span>{/if}
  </header>
  <footer>
    <code class="id">{data.id}</code>
    {#if durMs > 0}<span class="dur">{durMs}ms</span>{/if}
  </footer>
  <Handle type="source" position={Position.Bottom} />
</div>

<style>
  .pregel {
    width: 220px;
    background: #161b22;
    border: 2px solid var(--accent, #30363d);
    border-radius: 6px;
    box-shadow: 0 1px 0 rgba(0,0,0,0.4), 0 0 0 1px #0d1117 inset;
    overflow: hidden;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }
  .pregel.selected { box-shadow: 0 0 0 2px #1f6feb, 0 1px 0 rgba(0,0,0,0.4); }
  .pregel.sentinel { background: #0d1117; border-style: dashed; }
  header {
    display: flex; align-items: center; gap: 6px;
    padding: 6px 8px;
    background: linear-gradient(180deg, color-mix(in srgb, var(--accent) 18%, #161b22), #161b22);
    border-bottom: 1px solid #30363d;
  }
  .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--accent); flex: 0 0 auto;
    box-shadow: 0 0 6px var(--accent);
  }
  .label {
    color: #e6edf3; font-weight: 600; font-size: 12px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    flex: 1 1 auto;
  }
  .badge {
    flex: 0 0 auto;
    background: var(--accent); color: #0d1117;
    font-size: 10px; font-weight: 700;
    padding: 1px 5px; border-radius: 8px;
  }
  footer {
    display: flex; justify-content: space-between; align-items: center;
    padding: 4px 8px;
    color: #8b949e; font-size: 10px;
  }
  .id { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .dur { color: #58a6ff; }
  :global(.svelte-flow__handle) {
    width: 8px; height: 8px;
    background: #58a6ff;
    border: 2px solid #0d1117;
  }
</style>
