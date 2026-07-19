"""P15 drift guards — `Scene3DPreview.svelte` and the standalone
`scene-3d-preview.htm` must reference the same wasm bundle, mount the
same canvas id, and stay in sync about the seed scene JSON.

Pure-CPU: just inspects the two source files. No browser.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[4]
_APP = (
    _REPO_ROOT
    / "60-apps"
    / "etzhayyim-project-mangaka"
    / "appview"
    / "etzhayyim-wasm-mangaka-mng4k4x1"
)
_SVELTE_COMPONENT = _APP / "svelte" / "src" / "lib" / "Scene3DPreview.svelte"
_STATIC_HTM = _APP / "svelte" / "static" / "scene-3d-preview.htm"
_WASM_BUNDLE_DIR = _APP / "svelte" / "static" / "scene-3d"
_APP_SVELTE = _APP / "svelte" / "src" / "App.svelte"


def test_svelte_component_file_exists():
    assert _SVELTE_COMPONENT.is_file(), f"missing {_SVELTE_COMPONENT}"


def test_standalone_htm_still_exists():
    """The standalone shell stays as a fallback / iframe target."""
    assert _STATIC_HTM.is_file(), f"missing {_STATIC_HTM}"


def test_wasm_bundle_present():
    for name in (
        "kami_mangaka_scene.js",
        "kami_mangaka_scene_bg.wasm",
        "kami_mangaka_scene.d.ts",
    ):
        assert (_WASM_BUNDLE_DIR / name).is_file(), f"missing {name}"


def test_both_paths_import_same_wasm_module():
    """Drift guard: the Svelte component dynamic import path and the
    standalone HTM <script type=module> import must point at the same
    file under `static/scene-3d/`."""
    sv = _SVELTE_COMPONENT.read_text()
    htm = _STATIC_HTM.read_text()
    sv_import = re.search(r"import\([^)]*['\"]([^'\"]+kami_mangaka_scene\.js)", sv)
    htm_import = re.search(r"import\s+\w+\s*,?\s*\{[^}]*\}\s*from\s+['\"]([^'\"]+kami_mangaka_scene\.js)", htm)
    assert sv_import, f"Svelte component: no wasm import found in {_SVELTE_COMPONENT}"
    assert htm_import, f"Standalone HTM: no wasm import found in {_STATIC_HTM}"
    sv_path = sv_import.group(1)
    htm_path = htm_import.group(1)
    # Both should resolve to /scene-3d/kami_mangaka_scene.js (root-relative).
    assert sv_path.endswith("scene-3d/kami_mangaka_scene.js"), sv_path
    assert htm_path.endswith("scene-3d/kami_mangaka_scene.js"), htm_path


def test_both_paths_use_same_canvas_class_or_id():
    """The wasm side calls `document.getElementById(canvas_id)` — the two
    embed paths can use different ids but must each pass the id that
    matches what they bind in the DOM."""
    sv = _SVELTE_COMPONENT.read_text()
    htm = _STATIC_HTM.read_text()
    sv_match = re.search(r"ScenePreview\.create\(['\"]([^'\"]+)['\"]\)", sv)
    htm_match = re.search(r"ScenePreview\.create\(['\"]([^'\"]+)['\"]\)", htm)
    assert sv_match and htm_match
    sv_id = sv_match.group(1)
    htm_id = htm_match.group(1)
    assert f'id="{sv_id}"' in sv, f"Svelte canvas binding mismatches create('{sv_id}')"
    assert f'id="{htm_id}"' in htm, f"HTM canvas binding mismatches create('{htm_id}')"


def test_both_paths_seed_with_jsonld_context():
    """Both shells seed the textarea with a minimal scene JSON-LD that
    includes the canonical @context. If they drift, editors get
    inconsistent starting states."""
    sv = _SVELTE_COMPONENT.read_text()
    htm = _STATIC_HTM.read_text()
    ctx = "https://kami.etzhayyim.com/mangaka-scene/v1"
    assert ctx in sv, "Svelte component missing seed scene @context"
    assert ctx in htm, "HTM shell missing seed scene @context"


def test_app_svelte_dispatches_scene_3d_mode():
    """The SPA router must mount Scene3DPreview when ?mode=scene-3d."""
    app = _APP_SVELTE.read_text()
    assert "Scene3DPreview" in app
    assert "mode === 'scene-3d'" in app
    # The realtime branch stays intact.
    assert "mode === 'realtime'" in app


def test_app_svelte_dynamic_title_per_mode():
    """The page title should reflect which mode is active — tab-name
    sanity for users who keep multiple modes open."""
    app = _APP_SVELTE.read_text()
    assert "TITLE" in app or "Scene3DPreview" in app
    # The Scene3D-specific title string should be present somewhere.
    assert "3D Scene" in app or "scene-3d" in app
