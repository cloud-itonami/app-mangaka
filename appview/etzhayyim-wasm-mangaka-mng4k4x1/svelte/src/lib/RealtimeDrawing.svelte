<script lang="ts">
  import SketchCanvas from './SketchCanvas.svelte';

  let sketchCanvas: SketchCanvas;
  let aiImage = '';
  let prompt = '1girl, black hair, school uniform, bob cut, manga style, black and white, lineart';
  let strengthPct = 60;
  let brushSize = 6;
  let status = 'draw something';
  let generating = false;
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  let lastLatencyMs = 0;

  // Resize the sketch to 512×512 before sending to keep payload small (~30KB vs ~500KB).
  // Must await img.onload — drawImage on an unloaded Image produces a blank white canvas.
  async function getResizedDataUrl(): Promise<string> {
    if (!sketchCanvas) return '';
    const src = sketchCanvas.getDataUrl();
    if (!src) return '';
    const offscreen = document.createElement('canvas');
    offscreen.width = 512;
    offscreen.height = 512;
    const ctx2d = offscreen.getContext('2d');
    if (!ctx2d) return src;
    const img = new Image();
    await new Promise<void>((resolve) => {
      img.onload = () => resolve();
      img.onerror = () => resolve();
      img.src = src;
    });
    ctx2d.fillStyle = '#ffffff';
    ctx2d.fillRect(0, 0, 512, 512);
    ctx2d.drawImage(img, 0, 0, 512, 512);
    return offscreen.toDataURL('image/png');
  }

  async function generate() {
    if (!sketchCanvas || generating) return;
    const dataUrl = await getResizedDataUrl();
    if (!dataUrl) return;

    generating = true;
    status = 'generating…';
    const t0 = Date.now();

    try {
      const resp = await fetch('/xrpc/com.etzhayyim.mangaka.realtimeDraw', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          sketchBase64: dataUrl,
          prompt,
          strength: strengthPct / 100,
          steps: 4,
        }),
      });
      const data = await resp.json() as { imageBase64?: string; latencyMs?: number; status?: string; error?: string };
      if (data.imageBase64) {
        aiImage = `data:image/png;base64,${data.imageBase64}`;
        lastLatencyMs = data.latencyMs ?? Date.now() - t0;
        status = `ok • ${lastLatencyMs}ms`;
      } else {
        status = `error: ${data.error ?? 'unknown'}`;
      }
    } catch (e) {
      status = String(e);
    } finally {
      generating = false;
    }
  }

  function onStrokeEnd() {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(generate, 300);
  }

  function clearAll() {
    if (sketchCanvas) sketchCanvas.clear();
    aiImage = '';
    status = 'draw something';
    if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null; }
  }

  function download() {
    if (!aiImage) return;
    const a = document.createElement('a');
    a.href = aiImage;
    a.download = `mangaka-realtime-${Date.now()}.png`;
    a.click();
  }
</script>

<div class="rt-root">
  <div class="rt-header">
    <div class="rt-tools">
      <button class="rt-btn" title="Clear canvas" on:click={clearAll}>
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <path d="M2 4h12M6 4V2h4v2M5 4l1 10h4l1-10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        </svg>
      </button>
      <label class="rt-slider-label">
        <span>Brush</span>
        <input type="range" min="2" max="24" step="1" bind:value={brushSize} />
        <span class="rt-val">{brushSize}px</span>
      </label>
    </div>
    <div class="rt-strength-wrap">
      <label class="rt-slider-label">
        <span>AI Strength</span>
        <input type="range" min="0" max="100" step="5" bind:value={strengthPct} />
        <span class="rt-val">{strengthPct}%</span>
      </label>
    </div>
    <div class="rt-status" class:generating>
      {#if generating}
        <span class="rt-spinner"></span> generating…
      {:else}
        {status}
      {/if}
    </div>
    <button class="rt-btn" title="Download result" disabled={!aiImage} on:click={download}>
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <path d="M8 2v8m0 0l-3-3m3 3l3-3M3 12h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
      </svg>
    </button>
  </div>

  <div class="rt-panels">
    <div class="rt-panel">
      <div class="rt-panel-label">Your Sketch</div>
      <div class="rt-canvas-wrap">
        <SketchCanvas bind:this={sketchCanvas} {brushSize} on:strokeend={onStrokeEnd} />
      </div>
    </div>
    <div class="rt-divider"></div>
    <div class="rt-panel">
      <div class="rt-panel-label">AI Output</div>
      <div class="rt-canvas-wrap rt-output">
        {#if aiImage}
          <img src={aiImage} alt="AI generated" class="rt-ai-img" />
        {:else}
          <div class="rt-placeholder">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" opacity="0.3">
              <rect x="3" y="3" width="18" height="18" rx="2" stroke="currentColor" stroke-width="1.5"/>
              <circle cx="8.5" cy="8.5" r="1.5" fill="currentColor"/>
              <path d="M3 16l5-5 4 4 3-3 6 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>
            <p>AI output appears here<br/>after you draw</p>
          </div>
        {/if}
      </div>
    </div>
  </div>

  <div class="rt-footer">
    <input
      class="rt-prompt"
      type="text"
      bind:value={prompt}
      placeholder="Describe your drawing…"
    />
  </div>
</div>

<style>
  .rt-root {
    display: flex;
    flex-direction: column;
    width: 100%;
    height: 100%;
    background: #111318;
    color: #e8eaf0;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 13px;
  }

  .rt-header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 12px;
    background: #1a1d24;
    border-bottom: 1px solid #2c2f3a;
    flex-shrink: 0;
    flex-wrap: wrap;
  }

  .rt-tools {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .rt-strength-wrap {
    margin-left: auto;
    display: flex;
    align-items: center;
  }

  .rt-slider-label {
    display: flex;
    align-items: center;
    gap: 6px;
    white-space: nowrap;
  }

  .rt-slider-label input[type="range"] {
    width: 80px;
    accent-color: #7c8cff;
  }

  .rt-val {
    min-width: 32px;
    text-align: right;
    color: #9ba3c0;
  }

  .rt-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    background: #23263a;
    border: 1px solid #3a3f56;
    border-radius: 6px;
    color: #9ba3c0;
    cursor: pointer;
    flex-shrink: 0;
  }

  .rt-btn:hover:not(:disabled) { background: #2e3349; color: #e8eaf0; }
  .rt-btn:disabled { opacity: 0.4; cursor: default; }

  .rt-status {
    font-size: 12px;
    color: #6b7399;
    min-width: 120px;
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .rt-status.generating { color: #7c8cff; }

  .rt-spinner {
    display: inline-block;
    width: 12px;
    height: 12px;
    border: 2px solid #7c8cff;
    border-top-color: transparent;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  .rt-panels {
    display: flex;
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }

  .rt-panel {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }

  .rt-panel-label {
    padding: 6px 12px;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: #5a6180;
    background: #13161e;
    border-bottom: 1px solid #1e2130;
  }

  .rt-canvas-wrap {
    flex: 1;
    position: relative;
    min-height: 0;
    overflow: hidden;
  }

  .rt-output {
    background: #0d0f15;
  }

  .rt-ai-img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: block;
  }

  .rt-placeholder {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    gap: 12px;
    color: #4a5070;
    text-align: center;
    line-height: 1.5;
  }

  .rt-divider {
    width: 1px;
    background: #1e2130;
    flex-shrink: 0;
  }

  .rt-footer {
    padding: 8px 12px;
    background: #1a1d24;
    border-top: 1px solid #2c2f3a;
    flex-shrink: 0;
  }

  .rt-prompt {
    width: 100%;
    box-sizing: border-box;
    background: #0d0f15;
    border: 1px solid #2c2f3a;
    border-radius: 6px;
    color: #e8eaf0;
    padding: 7px 10px;
    font-size: 13px;
    outline: none;
  }

  .rt-prompt:focus { border-color: #7c8cff; }
  .rt-prompt::placeholder { color: #4a5070; }
</style>
