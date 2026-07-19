<script lang="ts">
  // WebGL2 sketch canvas — black ink on white, preserveDrawingBuffer for capture.
  // NO canvas.getContext('2d') per KAMI Engine prohibition.
  import { onMount, createEventDispatcher } from 'svelte';

  const dispatch = createEventDispatcher<{ strokeend: void }>();

  export let brushSize = 6;

  let canvas: HTMLCanvasElement;
  let gl: WebGL2RenderingContext | null = null;
  let prog: WebGLProgram | null = null;
  let posLoc = -1;
  let vbo: WebGLBuffer | null = null;
  let isDrawing = false;
  let lastPt: { x: number; y: number } | null = null;

  const VS = `#version 300 es
in vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }`;

  const FS = `#version 300 es
precision mediump float;
out vec4 fragColor;
void main() { fragColor = vec4(0.0, 0.0, 0.0, 1.0); }`;

  function mkShader(g: WebGL2RenderingContext, type: number, src: string): WebGLShader {
    const s = g.createShader(type)!;
    g.shaderSource(s, src);
    g.compileShader(s);
    return s;
  }

  // Convert pixel coord to WebGL clip space
  function toNdc(px: number, py: number): [number, number] {
    return [(px / canvas.width) * 2 - 1, 1 - (py / canvas.height) * 2];
  }

  function uploadAndDraw(verts: Float32Array) {
    if (!gl || !vbo || posLoc < 0) return;
    gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
    gl.bufferData(gl.ARRAY_BUFFER, verts, gl.DYNAMIC_DRAW);
    gl.enableVertexAttribArray(posLoc);
    gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0);
    gl.drawArrays(gl.TRIANGLES, 0, verts.length / 2);
  }

  function drawCap(pt: { x: number; y: number }) {
    const [cx, cy] = toNdc(pt.x, pt.y);
    const rx = brushSize / canvas.width;
    const ry = brushSize / canvas.height;
    const N = 16;
    const v: number[] = [];
    for (let i = 0; i < N; i++) {
      const a0 = (i / N) * Math.PI * 2;
      const a1 = ((i + 1) / N) * Math.PI * 2;
      v.push(cx, cy, cx + Math.cos(a0) * rx, cy + Math.sin(a0) * ry, cx + Math.cos(a1) * rx, cy + Math.sin(a1) * ry);
    }
    uploadAndDraw(new Float32Array(v));
  }

  function drawSeg(a: { x: number; y: number }, b: { x: number; y: number }) {
    const [ax, ay] = toNdc(a.x, a.y);
    const [bx, by] = toNdc(b.x, b.y);
    const dx = bx - ax, dy = by - ay;
    const len = Math.sqrt(dx * dx + dy * dy);
    if (len < 1e-6) return;
    const nx = (-dy / len) * (brushSize / canvas.width);
    const ny = (dx / len) * (brushSize / canvas.height);
    uploadAndDraw(new Float32Array([
      ax + nx, ay + ny, ax - nx, ay - ny, bx + nx, by + ny,
      ax - nx, ay - ny, bx - nx, by - ny, bx + nx, by + ny,
    ]));
  }

  function initGL() {
    const ctx = canvas.getContext('webgl2', { preserveDrawingBuffer: true, antialias: true });
    if (!ctx) { console.warn('[SketchCanvas] WebGL2 not available'); return; }
    gl = ctx;

    const vs = mkShader(gl, gl.VERTEX_SHADER, VS);
    const fs = mkShader(gl, gl.FRAGMENT_SHADER, FS);
    prog = gl.createProgram()!;
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    gl.useProgram(prog);
    posLoc = gl.getAttribLocation(prog, 'aPos');
    vbo = gl.createBuffer();

    gl.clearColor(1, 1, 1, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.viewport(0, 0, canvas.width, canvas.height);
  }

  function canvasPt(e: PointerEvent): { x: number; y: number } {
    const r = canvas.getBoundingClientRect();
    return {
      x: (e.clientX - r.left) * (canvas.width / r.width),
      y: (e.clientY - r.top) * (canvas.height / r.height),
    };
  }

  function onPointerDown(e: PointerEvent) {
    canvas.setPointerCapture(e.pointerId);
    isDrawing = true;
    const pt = canvasPt(e);
    lastPt = pt;
    if (gl) drawCap(pt);
  }

  function onPointerMove(e: PointerEvent) {
    if (!isDrawing || !lastPt || !gl) return;
    const pt = canvasPt(e);
    drawSeg(lastPt, pt);
    drawCap(pt);
    lastPt = pt;
  }

  function onPointerUp() {
    if (!isDrawing) return;
    isDrawing = false;
    lastPt = null;
    dispatch('strokeend');
  }

  export function clear() {
    if (!gl) return;
    gl.clearColor(1, 1, 1, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
  }

  export function getDataUrl(): string {
    return canvas?.toDataURL('image/png') ?? '';
  }

  onMount(() => {
    canvas.width = canvas.clientWidth || 512;
    canvas.height = canvas.clientHeight || 512;
    initGL();

    const ro = new ResizeObserver(() => {
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (w !== canvas.width || h !== canvas.height) {
        canvas.width = w;
        canvas.height = h;
        if (gl) {
          gl.viewport(0, 0, w, h);
          gl.clearColor(1, 1, 1, 1);
          gl.clear(gl.COLOR_BUFFER_BIT);
        }
      }
    });
    ro.observe(canvas);
    return () => ro.disconnect();
  });
</script>

<canvas
  bind:this={canvas}
  style="display:block;width:100%;height:100%;cursor:crosshair;touch-action:none;background:#fff"
  on:pointerdown={onPointerDown}
  on:pointermove={onPointerMove}
  on:pointerup={onPointerUp}
  on:pointerleave={onPointerUp}
></canvas>
