<script lang="ts">
  // P15 of ADR-2605141200 — Svelte wrapper around the `kami_mangaka_scene`
  // wasm bundle. Mirrors `static/scene-3d-preview.htm` (the standalone
  // shell from P5) so the same WebGPU pipeline can run inside the Genko
  // canvas SPA without an iframe / new tab.
  //
  // Route: visit `?mode=scene-3d`. App.svelte dispatches on `mode` and
  // mounts this component instead of <Genko/> or <RealtimeDrawing/>.
  //
  // The wasm bundle lives at `static/scene-3d/kami_mangaka_scene.js`
  // (SvelteKit static-assets layout). Loaded dynamically so the wasm
  // initialiser doesn't run on routes that don't need it.

  import { onMount, onDestroy } from 'svelte';

  let canvas: HTMLCanvasElement;
  let textarea: HTMLTextAreaElement;
  let status: HTMLDivElement;

  let preview: any = null;          // ScenePreview wasm handle
  let rafId = 0;
  let resizeObserver: ResizeObserver | null = null;

  // Orbit camera state — same defaults as the standalone HTM shell.
  let yaw = 0.0;
  let pitch = -0.05;
  let distance = 3.8;

  // Seed scene — matches the standalone shell so the two routes show
  // identical content at load time.
  const SEED_SCENE = `{
  "@context": "https://kami.etzhayyim.com/mangaka-scene/v1",
  "characters": [],
  "props": [],
  "camera": null,
  "lights": [],
  "environment": {
    "biome": "Plains",
    "weather": "overcast",
    "seed": 42,
    "ground_size_m": 64.0,
    "layout_anchors": []
  }
}`;

  function setStatus(msg: string, err = false) {
    if (!status) return;
    status.textContent = msg;
    status.classList.toggle('err', err);
  }

  function applyCamera() {
    if (!preview) return;
    preview.set_orbit_camera(yaw, pitch, distance);
  }

  // ── pointer / wheel handlers ─────────────────────────────────────────
  let dragging = false;
  let lastX = 0;
  let lastY = 0;

  function onPointerDown(e: PointerEvent) {
    dragging = true;
    lastX = e.clientX;
    lastY = e.clientY;
    (e.target as Element).setPointerCapture(e.pointerId);
  }
  function onPointerUp(e: PointerEvent) {
    dragging = false;
    (e.target as Element).releasePointerCapture(e.pointerId);
  }
  function onPointerMove(e: PointerEvent) {
    if (!dragging) return;
    yaw += (e.clientX - lastX) * 0.008;
    pitch += (e.clientY - lastY) * 0.005;
    pitch = Math.max(-1.4, Math.min(1.4, pitch));
    lastX = e.clientX;
    lastY = e.clientY;
    applyCamera();
  }
  function onWheel(e: WheelEvent) {
    e.preventDefault();
    distance = Math.max(0.6, Math.min(20.0, distance * (1.0 + Math.sign(e.deltaY) * 0.07)));
    applyCamera();
  }

  function onApply() {
    if (!preview) return;
    try {
      preview.load_scene_jsonld(textarea.value);
      applyCamera();
      setStatus(`scene applied · ${new Date().toLocaleTimeString()}`);
    } catch (e: any) {
      setStatus(`scene jsonld error: ${e?.message ?? e}`, true);
    }
  }
  function onCopy() {
    if (!preview) return;
    try {
      textarea.value = preview.to_scene_jsonld();
      setStatus('scene state copied to textarea');
    } catch (e: any) {
      setStatus(`to_scene_jsonld error: ${e?.message ?? e}`, true);
    }
  }

  onMount(async () => {
    textarea.value = SEED_SCENE;
    setStatus('loading wasm…');
    try {
      // Dynamic import keeps the wasm + 56 KB js glue off other routes.
      // Build via runtime string + new Function so rolldown doesn't try to resolve it.
      const dynImport = new Function('p', 'return import(p)') as (p: string) => Promise<any>;
      const mod = await dynImport('/scene-3d/kami_mangaka_scene.js');
      await mod.default();
      setStatus('initialising GPU…');
      preview = await mod.ScenePreview.create('mangaka-scene-canvas');
      try { preview.load_scene_jsonld(textarea.value); } catch (_) {}
      applyCamera();
      setStatus('ready · drag to orbit · scroll to zoom');

      // ResizeObserver → wasm.resize(css_w, css_h, dpr).
      resizeObserver = new ResizeObserver(() => {
        if (!preview) return;
        const dpr = Math.max(1.0, window.devicePixelRatio);
        preview.resize(canvas.clientWidth, canvas.clientHeight, dpr);
      });
      resizeObserver.observe(canvas);

      // RAF loop — JS owns the cadence (per wasm guidance in scene.rs).
      const tick = () => {
        try {
          preview.render_frame();
        } catch (e: any) {
          setStatus(`render error: ${e?.message ?? e}`, true);
          return;
        }
        rafId = requestAnimationFrame(tick);
      };
      rafId = requestAnimationFrame(tick);
    } catch (e: any) {
      setStatus(`init failed: ${e?.message ?? e}`, true);
      console.error(e);
    }
  });

  onDestroy(() => {
    if (rafId) cancelAnimationFrame(rafId);
    resizeObserver?.disconnect();
    // wasm ScenePreview has Symbol.dispose — call free if available.
    try {
      preview?.free?.();
    } catch (_) {
      /* ignore */
    }
  });
</script>

<div class="wrap">
  <div class="canvas-col">
    <canvas
      id="mangaka-scene-canvas"
      bind:this={canvas}
      on:pointerdown={onPointerDown}
      on:pointerup={onPointerUp}
      on:pointercancel={() => (dragging = false)}
      on:pointermove={onPointerMove}
      on:wheel={onWheel}
    ></canvas>
    <div class="hud">mangaka.scene3d · drag = orbit · scroll = zoom</div>
  </div>
  <aside class="side">
    <h1>scene jsonld</h1>
    <label for="scene-json">paste output of <code>MangakaScene::to_jsonld()</code></label>
    <textarea id="scene-json" bind:this={textarea} spellcheck="false"></textarea>
    <button on:click={onApply}>apply scene</button>
    <button on:click={onCopy}>copy current state</button>
    <div class="status" bind:this={status}>initialising…</div>
    <p class="hint">
      Same wasm bundle as <a href="/scene-3d-preview.htm" target="_blank" rel="noreferrer">/scene-3d-preview.htm</a>.
      Loaded dynamically — other Genko routes don't pay the wasm cost.
    </p>
  </aside>
</div>

<style>
  .wrap {
    display: grid;
    grid-template-columns: 1fr 320px;
    height: 100vh;
    background: #0c0e14;
    color: #d8dde8;
    font-family: system-ui, -apple-system, sans-serif;
  }
  .canvas-col {
    position: relative;
    overflow: hidden;
  }
  canvas {
    display: block;
    width: 100%;
    height: 100%;
    touch-action: none;
    cursor: grab;
  }
  canvas:active {
    cursor: grabbing;
  }
  .hud {
    position: absolute;
    top: 8px;
    left: 8px;
    background: rgba(0, 0, 0, 0.55);
    padding: 6px 8px;
    border-radius: 4px;
    font: 11px/1.4 ui-monospace, monospace;
    color: #b8c4d8;
    pointer-events: none;
  }
  aside.side {
    padding: 12px;
    background: #151823;
    border-left: 1px solid #232739;
    overflow-y: auto;
    box-sizing: border-box;
  }
  aside.side h1 {
    font-size: 13px;
    font-weight: 600;
    margin: 0 0 10px;
    color: #e8edf7;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  aside.side label {
    display: block;
    font-size: 11px;
    color: #8a94a8;
    margin: 12px 0 4px;
  }
  textarea {
    width: 100%;
    min-height: 240px;
    background: #0c0e14;
    color: #d8dde8;
    border: 1px solid #232739;
    border-radius: 4px;
    padding: 6px;
    font: 11px/1.4 ui-monospace, monospace;
    box-sizing: border-box;
    resize: vertical;
  }
  button {
    background: #2a3349;
    color: #e8edf7;
    border: 1px solid #3a4663;
    padding: 6px 10px;
    border-radius: 4px;
    font-size: 12px;
    cursor: pointer;
    margin-top: 8px;
    margin-right: 6px;
  }
  button:hover {
    background: #344163;
  }
  .status {
    margin-top: 8px;
    font-size: 11px;
    color: #6b7589;
    min-height: 14px;
  }
  .status.err {
    color: #ff7f7f;
  }
  .hint {
    margin-top: 16px;
    font-size: 11px;
    color: #6b7589;
    line-height: 1.5;
  }
  .hint a {
    color: #9ec0ff;
  }
</style>
