<script lang="ts">
  import { onMount } from 'svelte';
  import Genko from '../../../../../../40-engine/kami-engine/kami-engine-sdk/src/lib/genko/components/Genko.svelte';
  import RealtimeDrawing from './lib/RealtimeDrawing.svelte';
  import Scene3DPreview from './lib/Scene3DPreview.svelte';

  const nanoid = 'mng4k4x1';
  const name = 'Mangaka';

  let mode = '';
  onMount(() => {
    mode = new URLSearchParams(location.search).get('mode') ?? '';
  });

  // P15 — `?mode=scene-3d` mounts the wasm 3D scene preview directly in
  // the SPA instead of bouncing to /scene-3d-preview.htm. Keeps the
  // standalone HTM around as a fallback / iframe-friendly target.
  const TITLE: Record<string, string> = {
    realtime: 'Mangaka — Realtime Drawing',
    'scene-3d': 'Mangaka — 3D Scene Preview',
  };
</script>

<svelte:head>
  <title>{TITLE[mode] ?? 'Mangaka'}</title>
  <meta
    name="description"
    content="Mangaka is a manga creation appview powered by the KAMI Engine Genko editor."
  />
</svelte:head>

{#if mode === 'realtime'}
  <RealtimeDrawing />
{:else if mode === 'scene-3d'}
  <Scene3DPreview />
{:else}
  <Genko {nanoid} {name} />
{/if}

<style>
  :global(html, body, #app) {
    margin: 0;
    width: 100%;
    height: 100%;
    overflow: hidden;
    background: #111318;
  }
</style>
