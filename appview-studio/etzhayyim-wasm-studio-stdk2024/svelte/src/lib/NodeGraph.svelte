<script lang="ts" module>
  // ComfyUI-style node graph view of a Pregel DAG.
  // Layout: @dagrejs/dagre TB. Render: @xyflow/svelte.
  export type GraphNode = { id: string; type?: string };
  export type GraphEdge = { source: string; target: string; conditional?: boolean };
  export type GraphData = { nodes: GraphNode[]; edges: GraphEdge[] };
  export type StageStatus = "pending" | "running" | "done" | "error";
  export type StageCard = {
    node: string;
    status: StageStatus;
    invocations: number;
    delta: unknown;
    startedAt?: number;
    completedAt?: number;
    error?: string;
  };
</script>

<script lang="ts">
  import {
    SvelteFlow,
    Background,
    Controls,
    MiniMap,
    type Node,
    type Edge,
    Position,
  } from "@xyflow/svelte";
  import dagre from "@dagrejs/dagre";
  import PregelNode from "./PregelNode.svelte";
  import "@xyflow/svelte/dist/style.css";

  type Props = {
    graphData: GraphData | null;
    stages: Record<string, StageCard>;
    stageLabel: Record<string, string>;
    onSelect?: (nodeId: string) => void;
  };
  const { graphData, stages, stageLabel, onSelect }: Props = $props();

  const NODE_W = 220;
  const NODE_H = 72;

  // Stable layout derived from graphData. Recomputes when the selected
  // graph changes; stages updates only mutate per-node data.
  function layout(g: GraphData): { nodes: Node[]; edges: Edge[] } {
    const dg = new dagre.graphlib.Graph();
    dg.setDefaultEdgeLabel(() => ({}));
    dg.setGraph({ rankdir: "TB", nodesep: 36, ranksep: 56, marginx: 16, marginy: 16 });

    const seen = new Set<string>();
    for (const n of g.nodes) {
      if (seen.has(n.id)) continue;
      seen.add(n.id);
      dg.setNode(n.id, { width: NODE_W, height: NODE_H });
    }
    for (const e of g.edges) dg.setEdge(e.source, e.target);
    dagre.layout(dg);

    const nodes: Node[] = [];
    for (const id of seen) {
      const p = dg.node(id);
      nodes.push({
        id,
        type: "pregel",
        position: { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 },
        data: { id, label: stageLabel[id] ?? id, stage: stages[id] },
        sourcePosition: Position.Bottom,
        targetPosition: Position.Top,
      });
    }
    const edges: Edge[] = g.edges.map((e, i) => ({
      id: `e${i}-${e.source}-${e.target}`,
      source: e.source,
      target: e.target,
      type: "smoothstep",
      animated: !!e.conditional,
      style: e.conditional ? "stroke: #d29922; stroke-dasharray: 4 3;" : "stroke: #58a6ff;",
    }));
    return { nodes, edges };
  }

  // Base layout (positions + edges) — recomputes only when graphData changes.
  const laid = $derived(graphData ? layout(graphData) : { nodes: [], edges: [] });

  // Merge live `stages` into the static positions without re-laying-out.
  let nodes = $derived(
    laid.nodes.map((n) => ({ ...n, data: { ...n.data, stage: stages[n.id] } })),
  );
  let edges = $derived(laid.edges);

  const nodeTypes = { pregel: PregelNode } as any;

  function handleNodeClick(_e: Event, n: Node) { onSelect?.(n.id); }
</script>

<div class="wrap">
  {#if graphData}
    <SvelteFlow
      {nodes}
      {edges}
      {nodeTypes}
      fitView
      minZoom={0.2}
      maxZoom={2.5}
      proOptions={{ hideAttribution: true }}
      onnodeclick={(detail) => handleNodeClick(detail.event, detail.node)}
    >
      <Background bgColor="#010409" patternColor="#21262d" />
      <Controls position="bottom-right" />
      <MiniMap
        position="top-right"
        nodeColor={(n) => {
          const s = (n.data as any)?.stage?.status as StageStatus | undefined;
          return s === "done" ? "#238636"
               : s === "running" ? "#d29922"
               : s === "error" ? "#f85149"
               : "#30363d";
        }}
        maskColor="rgba(13,17,23,0.7)"
        style="background:#0d1117"
      />
    </SvelteFlow>
  {:else}
    <div class="empty">select a graph to render its Pregel topology</div>
  {/if}
</div>

<style>
  .wrap { height: 100%; min-height: 360px; }
  .empty { color: #8b949e; font-size: 11px; padding: 12px; }
  :global(.svelte-flow) {
    background: #010409;
    --xy-edge-stroke-default: #58a6ff;
    --xy-edge-stroke-selected-default: #1f6feb;
    --xy-attribution-background-color-default: transparent;
  }
  :global(.svelte-flow__controls button) {
    background: #161b22 !important;
    border-bottom: 1px solid #30363d !important;
    fill: #c9d1d9 !important;
  }
  :global(.svelte-flow__controls button:hover) { background: #1f6feb !important; }
</style>
