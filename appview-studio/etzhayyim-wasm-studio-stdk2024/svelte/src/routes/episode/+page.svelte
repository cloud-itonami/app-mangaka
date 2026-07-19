<script lang="ts">
  /**
   * Episode renderer — purpose-built UI for the ghost-hacker arc 0-1
   * mangaka_render_episode_page LangGraph node. Picks a page index (0..45),
   * kicks off the render, streams progress, and shows the final composite.
   *
   * Backend: same langgraph dev / pod that the main Studio page hits.
   */
  import { onMount } from "svelte";

  type StageEvent = { node: string; payload: unknown };

  // Page picker — every page in arc 0-1
  let pageNum = $state<number>(0);
  // Last-known assistant id for mangaka_render_episode_page
  let assistantId = $state<string>("");
  let threadId = $state<string>("");
  let running = $state<boolean>(false);
  let elapsedMs = $state<number>(0);
  let nPanels = $state<number>(0);
  let nNodes = $state<number>(0);
  let promptId = $state<string>("");
  let finalImageB64 = $state<string>("");
  let stageEvents = $state<StageEvent[]>([]);
  let errorMsg = $state<string>("");
  let comfyUrl = $state<string>("http://192.168.1.22:8188");
  let timeoutSeconds = $state<number>(1800);

  // Range mode — batch a contiguous range of pages
  let rangeStart = $state<number>(0);
  let rangeEnd = $state<number>(4);
  let rangeProgress = $state<number>(0);
  let rangeRunning = $state<boolean>(false);

  // Per-page label so the dropdown is useful
  const PAGE_LABELS: Record<number, string> = {
    0: "プレタイトル / 恐怖の夜",
    1: "教室・誘惑 / Nei + Ren",
    2: "教室・スニーカー / Saki + Akira + Mei",
    3: "下校・Nei観察 / Nei + Ren",
    4: "Yuto部屋・スマホ詐欺 (v12)",
    5: "Yuto決断・カード盗用",
    6: "Ren書斎・研究壁",
    7: "Ren書斎・nueメッセージ",
    8: "Yuto部屋・配送待ち",
  };
  const pageLabel = (n: number) =>
    PAGE_LABELS[n] ? `Page ${n} — ${PAGE_LABELS[n]}` : `Page ${n}`;

  async function ensureAssistant() {
    if (assistantId) return;
    const resp = await fetch("/api/assistants/search", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ limit: 100 }),
    });
    const list: { assistant_id: string; graph_id?: string }[] = await resp.json();
    const a = list.find((x) => x.graph_id === "mangaka_render_episode_page");
    if (!a) throw new Error("mangaka_render_episode_page assistant not found");
    assistantId = a.assistant_id;
  }

  async function ensureThread() {
    if (threadId) return;
    const resp = await fetch("/api/threads", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{}",
    });
    const j: { thread_id: string } = await resp.json();
    threadId = j.thread_id;
  }

  async function runOnce(targetPage: number) {
    errorMsg = "";
    stageEvents = [];
    finalImageB64 = "";
    nPanels = 0;
    nNodes = 0;
    promptId = "";
    elapsedMs = 0;
    running = true;
    const t0 = Date.now();
    try {
      await ensureAssistant();
      await ensureThread();
      const body = {
        assistant_id: assistantId,
        stream_mode: ["updates"],
        input: {
          page_num: targetPage,
          comfy_url: comfyUrl,
          timeout_seconds: timeoutSeconds,
        },
      };
      const resp = await fetch(`/api/threads/${threadId}/runs/stream`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        // SSE blocks separated by \n\n (or \r\n\r\n)
        for (const block of buf.split(/\r?\n\r?\n/).slice(0, -1)) {
          handleSseBlock(block);
        }
        buf = buf.split(/\r?\n\r?\n/).pop() ?? "";
      }
    } catch (e: any) {
      errorMsg = e?.message ?? String(e);
    } finally {
      running = false;
      elapsedMs = Date.now() - t0;
    }
  }

  function handleSseBlock(block: string) {
    let event = "";
    let data = "";
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (event !== "updates" || !data) return;
    let obj: Record<string, any>;
    try { obj = JSON.parse(data); } catch { return; }
    for (const [node, payload] of Object.entries(obj)) {
      stageEvents = [...stageEvents, { node, payload }];
      if (node === "build" && (payload as any)?.n_panels !== undefined) {
        nPanels = (payload as any).n_panels ?? 0;
        nNodes = (payload as any).n_nodes ?? 0;
      }
      if (node === "submit" && (payload as any)?.prompt_id) {
        promptId = (payload as any).prompt_id;
      }
      if (node === "poll" && (payload as any)?.image_b64) {
        finalImageB64 = (payload as any).image_b64;
      }
    }
  }

  async function runRange() {
    rangeRunning = true;
    rangeProgress = 0;
    for (let n = rangeStart; n <= rangeEnd && rangeRunning; n++) {
      pageNum = n;
      // Force a fresh thread so each page is isolated
      threadId = "";
      await runOnce(n);
      rangeProgress++;
    }
    rangeRunning = false;
  }

  function cancelRange() { rangeRunning = false; }

  onMount(() => { void ensureAssistant().catch(() => {}); });
</script>

<style>
  .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
  h1 { font-size: 24px; margin: 0 0 16px; }
  .row { display: flex; gap: 16px; align-items: flex-end; flex-wrap: wrap; margin-bottom: 16px; }
  .field { display: flex; flex-direction: column; gap: 4px; }
  label { font-size: 12px; color: #666; }
  select, input { font: inherit; padding: 6px 10px; border: 1px solid #ccc; border-radius: 6px; }
  button { padding: 8px 16px; font: inherit; cursor: pointer; border: 1px solid #444; background: #333; color: #fff; border-radius: 6px; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .grid { display: grid; grid-template-columns: 1fr 320px; gap: 24px; }
  .preview { background: #f5f5f5; padding: 8px; border-radius: 8px; min-height: 400px; display: flex; align-items: center; justify-content: center; }
  .preview img { max-width: 100%; height: auto; border: 1px solid #ddd; }
  .stages { background: #fafafa; padding: 12px; border-radius: 8px; max-height: 600px; overflow-y: auto; font-family: ui-monospace, "SF Mono", monospace; font-size: 12px; }
  .stage-event { margin-bottom: 4px; padding: 4px 6px; border-radius: 4px; }
  .stage-event.build { background: #e3f2fd; }
  .stage-event.submit { background: #fff3e0; }
  .stage-event.poll { background: #f3e5f5; }
  .err { color: #c00; background: #ffe5e5; padding: 8px; border-radius: 6px; }
  .stats { font-size: 14px; color: #444; margin-bottom: 8px; }
  .stats span { display: inline-block; margin-right: 12px; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 12px; background: #eee; font-size: 11px; }
  .pill.running { background: #fff3e0; color: #c66; }
  .pill.done { background: #e8f5e9; color: #2a7; }
</style>

<div class="container">
  <h1>Episode Renderer — Ghost Hacker Arc 0-1</h1>

  <div class="row">
    <label class="field">
      <span>Page</span>
      <select bind:value={pageNum}>
        {#each Array(46) as _, n}
          <option value={n}>{pageLabel(n)}</option>
        {/each}
      </select>
    </label>
    <label class="field">
      <span>Comfy URL</span>
      <input bind:value={comfyUrl} style="width: 220px" />
    </label>
    <label class="field">
      <span>Timeout (s)</span>
      <input type="number" bind:value={timeoutSeconds} style="width: 90px" />
    </label>
    <button disabled={running || rangeRunning} onclick={() => runOnce(pageNum)}>
      {running ? "Rendering…" : "Render Page"}
    </button>
  </div>

  <div class="row">
    <label class="field">
      <span>Batch from page</span>
      <input type="number" min="0" max="45" bind:value={rangeStart} style="width: 70px" />
    </label>
    <label class="field">
      <span>through</span>
      <input type="number" min="0" max="45" bind:value={rangeEnd} style="width: 70px" />
    </label>
    {#if rangeRunning}
      <span class="pill running">batch: {rangeProgress}/{rangeEnd - rangeStart + 1}</span>
      <button onclick={cancelRange}>Cancel batch</button>
    {:else}
      <button disabled={running} onclick={runRange}>Render Range</button>
    {/if}
  </div>

  <div class="stats">
    <span>panels: {nPanels}</span>
    <span>nodes: {nNodes}</span>
    <span>prompt_id: {promptId.slice(0, 8)}</span>
    <span>elapsed: {(elapsedMs / 1000).toFixed(1)}s</span>
    {#if running}
      <span class="pill running">running</span>
    {:else if finalImageB64}
      <span class="pill done">done</span>
    {/if}
  </div>

  {#if errorMsg}
    <div class="err">{errorMsg}</div>
  {/if}

  <div class="grid">
    <div class="preview">
      {#if finalImageB64}
        <img src={"data:image/png;base64," + finalImageB64} alt={`page ${pageNum}`} />
      {:else}
        <span style="color: #999">No preview yet</span>
      {/if}
    </div>
    <div class="stages">
      {#each stageEvents as ev}
        <div class="stage-event {ev.node}">
          <strong>{ev.node}</strong>
          {#if ev.node === "build"}
            — {(ev.payload as any)?.n_panels} panels / {(ev.payload as any)?.n_nodes} nodes
          {:else if ev.node === "submit"}
            — {(ev.payload as any)?.prompt_id?.slice(0, 8)}
          {:else if ev.node === "poll"}
            — {(ev.payload as any)?.status} {(ev.payload as any)?.elapsed_ms}ms
          {/if}
        </div>
      {/each}
    </div>
  </div>
</div>
