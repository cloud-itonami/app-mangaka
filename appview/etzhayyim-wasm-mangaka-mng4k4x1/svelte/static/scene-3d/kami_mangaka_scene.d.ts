/* tslint:disable */
/* eslint-disable */

/**
 * Stateful browser preview handle. JS holds one per canvas.
 *
 * Typical lifecycle:
 * ```ts
 * import init, { ScenePreview } from "./scene-3d/kami_mangaka_scene.js";
 * await init();
 * const preview = await ScenePreview.create("scene-canvas");
 * preview.load_scene_jsonld(serverSceneJsonld);
 * // RAF loop from JS:
 * requestAnimationFrame(function frame() {
 *   preview.render_frame();
 *   requestAnimationFrame(frame);
 * });
 * ```
 */
export class ScenePreview {
    private constructor();
    free(): void;
    [Symbol.dispose](): void;
    /**
     * Construct a preview bound to the given canvas. Returned as a Promise
     * because adapter / device init are async on the browser.
     */
    static create(canvas_id: string): Promise<ScenePreview>;
    /**
     * Replace the entire scene state from a JSON-LD payload — the same
     * shape `MangakaScene::to_jsonld()` emits on the server. Returns an
     * error string if parsing fails.
     */
    load_scene_jsonld(jsonld: string): void;
    /**
     * Render one frame to the canvas. JS owns the `requestAnimationFrame`
     * loop — keeps `ScenePreview` thread-affined to the JS event loop and
     * avoids reaching for `web_sys::Window::request_animation_frame` from
     * Rust (which needs `Closure` plumbing).
     */
    render_frame(): void;
    /**
     * Resize the surface — call from JS `ResizeObserver` when the canvas
     * CSS box changes.
     */
    resize(css_w: number, css_h: number, dpr: number): void;
    /**
     * Orbit camera helper: yaw + pitch (radians) at `distance` metres from
     * (0, 1.4, 0). For interactive mouse-drag in the editor.
     */
    set_orbit_camera(yaw_rad: number, pitch_rad: number, distance_m: number): void;
    /**
     * Round-trip the current scene back out for the editor to ship to the
     * LangGraph pod.
     */
    to_scene_jsonld(): string;
}

export type InitInput = RequestInfo | URL | Response | BufferSource | WebAssembly.Module;

export interface InitOutput {
    readonly memory: WebAssembly.Memory;
    readonly __wbg_scenepreview_free: (a: number, b: number) => void;
    readonly scenepreview_create: (a: number, b: number) => any;
    readonly scenepreview_load_scene_jsonld: (a: number, b: number, c: number) => [number, number];
    readonly scenepreview_render_frame: (a: number) => [number, number];
    readonly scenepreview_resize: (a: number, b: number, c: number, d: number) => void;
    readonly scenepreview_set_orbit_camera: (a: number, b: number, c: number, d: number) => void;
    readonly scenepreview_to_scene_jsonld: (a: number) => [number, number];
    readonly wasm_bindgen__closure__destroy__h1fc64ebb5598a658: (a: number, b: number) => void;
    readonly wasm_bindgen__convert__closures_____invoke__he5b12a5e6eb39525: (a: number, b: number, c: any) => [number, number];
    readonly wasm_bindgen__convert__closures_____invoke__h383bd871f37058d8: (a: number, b: number, c: any, d: any) => void;
    readonly __wbindgen_malloc: (a: number, b: number) => number;
    readonly __wbindgen_realloc: (a: number, b: number, c: number, d: number) => number;
    readonly __wbindgen_exn_store: (a: number) => void;
    readonly __externref_table_alloc: () => number;
    readonly __wbindgen_externrefs: WebAssembly.Table;
    readonly __wbindgen_free: (a: number, b: number, c: number) => void;
    readonly __externref_table_dealloc: (a: number) => void;
    readonly __wbindgen_start: () => void;
}

export type SyncInitInput = BufferSource | WebAssembly.Module;

/**
 * Instantiates the given `module`, which can either be bytes or
 * a precompiled `WebAssembly.Module`.
 *
 * @param {{ module: SyncInitInput }} module - Passing `SyncInitInput` directly is deprecated.
 *
 * @returns {InitOutput}
 */
export function initSync(module: { module: SyncInitInput } | SyncInitInput): InitOutput;

/**
 * If `module_or_path` is {RequestInfo} or {URL}, makes a request and
 * for everything else, calls `WebAssembly.instantiate` directly.
 *
 * @param {{ module_or_path: InitInput | Promise<InitInput> }} module_or_path - Passing `InitInput` directly is deprecated.
 *
 * @returns {Promise<InitOutput>}
 */
export default function __wbg_init (module_or_path?: { module_or_path: InitInput | Promise<InitInput> } | InitInput | Promise<InitInput>): Promise<InitOutput>;
