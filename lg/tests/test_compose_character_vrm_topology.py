"""Structural validator for `compose_character_vrm.topology.yaml`.

P16-a of ADR-2605141200. Same shape as
`test_compose_scene_3d_topology.py` — the YAML is the SSoT for the
Phase C topology assistant row even before any of the 5 pod images
ship. Drift in node count, kind mix, or pending_mcp_tools alignment
must surface immediately so we don't push a broken topology into
`vertex_langgraph_assistant_node`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_LG_DIR = Path(__file__).resolve().parents[1]
_TOPOLOGY_PATH = (
    _LG_DIR / "lg_mangaka" / "graphs" / "compose_character_vrm.topology.yaml"
)


@pytest.fixture(scope="module")
def spec() -> dict:
    return yaml.safe_load(_TOPOLOGY_PATH.read_text(encoding="utf-8"))


def test_topology_file_exists() -> None:
    assert _TOPOLOGY_PATH.is_file(), f"missing: {_TOPOLOGY_PATH}"


def test_top_level_required_fields(spec: dict) -> None:
    for key in ("assistant_id", "version", "kind", "entry", "state_keys", "nodes", "edges"):
        assert key in spec, f"missing top-level key: {key}"
    assert spec["kind"] == "topology"


def test_assistant_id_nsid_shape(spec: dict) -> None:
    assert spec["assistant_id"] == "com.etzhayyim.mangaka.composeCharacterVrm"


def test_node_count_matches_pipeline(spec: dict) -> None:
    """7-step pipeline + 1 attach = 8 nodes + 1 LLM prompt assembler.
    Locking the count so silent additions land via an explicit migration."""
    assert len(spec["nodes"]) == 9


def test_every_node_kind_is_data_resolved(spec: dict) -> None:
    """ADR-2605082000 §2 — kind must be mcp_tool / llm / sql_udf /
    py_ext_udf / llm_vision / foreach. No py_primitive smuggled in."""
    allowed = {"mcp_tool", "sql_udf", "py_ext_udf", "llm", "llm_vision", "foreach"}
    for n in spec["nodes"]:
        assert n["kind"] in allowed, f"node {n['id']}: kind={n['kind']!r} not allowed"


def test_mcp_tool_refs_use_mcp_scheme(spec: dict) -> None:
    for n in spec["nodes"]:
        if n["kind"] == "mcp_tool":
            assert isinstance(n["ref"], str) and n["ref"].startswith("mcp://"), (
                f"mcp_tool node {n['id']}: ref must start with mcp://, got {n['ref']!r}"
            )


def test_state_keys_cover_pipeline_channels(spec: dict) -> None:
    """Each downstream node reads previous nodes' outputs through these
    channel names. Locking them prevents silent rename drift."""
    keys = set(spec["state_keys"])
    required = {
        "character_rkey", "profile", "reference_image_b64",
        "prompt_pack", "multiview_images", "mesh_glb_b64",
        "rigged_glb_b64", "blendshape_pack", "vrm_b64",
        "blob_key", "vertex_id", "status", "error",
    }
    missing = required - keys
    assert not missing, f"state_keys missing: {sorted(missing)}"


def test_entry_node_is_defined(spec: dict) -> None:
    ids = {n["id"] for n in spec["nodes"]}
    assert spec["entry"] in ids


def test_edges_reference_defined_nodes(spec: dict) -> None:
    ids = {n["id"] for n in spec["nodes"]}
    for e in spec.get("edges") or []:
        assert e["from"] in ids, f"edge from={e['from']!r} not defined"
        assert e["to"] == "END" or e["to"] in ids, f"edge to={e['to']!r} not defined"


def test_validate_vrm_has_conditional_retry(spec: dict) -> None:
    """The retry edge from validate_vrm → bind_vrm is the only loop in
    this Pregel. Surface that explicitly so future migrations don't
    accidentally make it linear."""
    conds = spec.get("conditional_edges") or []
    by_from = {ce["from"]: ce for ce in conds}
    assert "validate_vrm" in by_from
    ce = by_from["validate_vrm"]
    assert "condition_ref" in ce
    assert ce["condition_ref"].startswith("dmn:")
    paths = ce.get("paths") or {}
    assert "retry" in paths and paths["retry"] == "bind_vrm"
    assert "accept" in paths and paths["accept"] == "attach_vrm"


def test_attach_vrm_reuses_p13_tool(spec: dict) -> None:
    """The terminal step MUST be the existing P13 attachCharacterVrm
    tool — no duplicate registration."""
    by_id = {n["id"]: n for n in spec["nodes"]}
    assert "attach_vrm" in by_id
    assert by_id["attach_vrm"]["ref"] == "mcp://com.etzhayyim.mangaka.tools.attachCharacterVrm"


def test_pending_mcp_tools_align_with_node_refs(spec: dict) -> None:
    """Every mcp:// ref in nodes[] (except the already-shipped
    attachCharacterVrm) MUST appear in pending_mcp_tools so the artist /
    infra teams have a clear shopping list."""
    declared = sorted(spec.get("pending_mcp_tools") or [])
    referenced = sorted({
        n["ref"].removeprefix("mcp://")
        for n in spec["nodes"]
        if n["kind"] == "mcp_tool" and n["ref"].startswith("mcp://")
        and n["ref"] != "mcp://com.etzhayyim.mangaka.tools.attachCharacterVrm"
    })
    assert declared == referenced, (
        "pending_mcp_tools out of sync with mcp_tool refs.\n"
        f"  declared but not in nodes[]: {sorted(set(declared) - set(referenced))}\n"
        f"  in nodes[] but not declared: {sorted(set(referenced) - set(declared))}"
    )


def test_gpu_required_only_on_heavy_steps(spec: dict) -> None:
    """generate_multiview + reconstruct_mesh are the only GPU-bound
    steps. Locking this prevents `bind_vrm` etc. accidentally claiming
    GPU resources and saturating the render pool."""
    by_id = {n["id"]: n for n in spec["nodes"]}
    gpu_nodes = {
        nid
        for nid, n in by_id.items()
        if isinstance(n.get("config"), dict) and n["config"].get("gpu_required") is True
    }
    assert gpu_nodes == {"generate_multiview", "reconstruct_mesh"}, (
        f"gpu_required set drifted: {gpu_nodes!r}"
    )


def test_stack_doc_exists_alongside_topology() -> None:
    """The companion stack-survey doc lives next to the character data
    so artists + ops can find it without grepping. Lock the path."""
    repo_root = _LG_DIR.parents[2]
    stack_doc = (
        repo_root
        / "60-apps"
        / "etzhayyim-project-mangaka"
        / "data"
        / "ghosthacker"
        / "VRM_AUTHORING_STACK.md"
    )
    assert stack_doc.is_file(), f"missing: {stack_doc}"


def test_no_external_api_hostnames_in_topology() -> None:
    """Production-runtime invariant from `TRAINING_PIPELINE.md`: external
    commercial APIs (Mixamo, OpenAI, Anthropic, Hume, Adobe, …) must
    NOT appear anywhere in this topology YAML — they belong only in
    `b2://etzhayyim-models/*` student-checkpoint or offline-distill paths.

    If a future edit accidentally drops an `api.openai.com` into a
    pod_image hint, an env-var ref, or a config string, this test
    catches it before the topology is seeded into RW."""
    raw = _TOPOLOGY_PATH.read_text(encoding="utf-8").lower()
    forbidden_hosts = (
        "api.openai.com",
        "api.anthropic.com",
        "api.hume.ai",
        "api.adobe.com",
        "mixamo.com",
        "api.runway.com",
        "api.higgsfield.ai",
    )
    for host in forbidden_hosts:
        assert host not in raw, (
            f"production topology references external API host {host!r}. "
            f"External APIs are train-only — move to TRAINING_PIPELINE.md."
        )


def test_training_pipeline_doc_exists() -> None:
    """Self-hosted invariant is documented in a companion file. Lock
    the path so future contributors find the distill workflow."""
    repo_root = _LG_DIR.parents[2]
    train_doc = (
        repo_root
        / "60-apps"
        / "etzhayyim-project-mangaka"
        / "data"
        / "ghosthacker"
        / "TRAINING_PIPELINE.md"
    )
    assert train_doc.is_file(), f"missing: {train_doc}"
    body = train_doc.read_text()
    assert "Production runtime never calls an external commercial API" in body


def test_pending_lexicon_jsons_exist(spec: dict) -> None:
    """P16-b — every NSID in `pending_mcp_tools` MUST have a matching
    lexicon JSON under `00-contracts/lexicons/com/etzhayyim/apps/mangaka/tools/`.
    Without these, `sync-mcp-registry.py` can't reconcile schema hashes
    and the topology can't flip from Phase B (YAML) to Phase C
    (`vertex_langgraph_assistant_node`)."""
    repo_root = _LG_DIR.parents[2]
    lex_dir = (
        repo_root
        / "00-contracts"
        / "lexicons"
        / "ai"
        / "etzhayyim"
        / "apps"
        / "mangaka"
        / "tools"
    )
    import json

    for nsid in spec.get("pending_mcp_tools") or []:
        # NSID = com.etzhayyim.mangaka.tools.<camelCase> ; lexicon filename = <camelCase>.json
        name = nsid.split(".")[-1]
        path = lex_dir / f"{name}.json"
        assert path.is_file(), f"missing lexicon JSON: {path}"
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc.get("id") == nsid, (
            f"lexicon id mismatch in {path.name}: {doc.get('id')!r} != {nsid!r}"
        )
        assert doc.get("defs", {}).get("main", {}).get("type") == "procedure", (
            f"{path.name}: defs.main.type must be 'procedure'"
        )


def test_vrm_bind_retry_dmn_ssot_exists(spec: dict) -> None:
    """P16-e — the conditional edge's `condition_ref` MUST resolve to a
    real DMN XML file under `00-contracts/dmn/` and a seed migration
    under `30-graph/graph-schema/sql_migrations/`. Locking both paths
    so a rename doesn't silently break Phase C activation."""
    repo_root = _LG_DIR.parents[2]
    # The topology declares condition_ref: dmn:com.etzhayyim.policies.mangaka.vrmBindRetry@1.0.0
    ce = {ce["from"]: ce for ce in spec.get("conditional_edges") or []}["validate_vrm"]
    ref = ce["condition_ref"]
    assert ref == "dmn:com.etzhayyim.policies.mangaka.vrmBindRetry@1.0.0", ref

    dmn_path = (
        repo_root
        / "00-contracts"
        / "dmn"
        / "ai"
        / "etzhayyim"
        / "policies"
        / "mangaka"
        / "vrmBindRetry.dmn"
    )
    assert dmn_path.is_file(), f"missing DMN SSoT: {dmn_path}"
    body = dmn_path.read_text(encoding="utf-8")
    # Decision id must match the condition_ref (less the version + scheme).
    assert 'id="com.etzhayyim.policies.mangaka.vrmBindRetry"' in body
    # All three routing paths declared by the topology must appear.
    for verdict in ("accept", "retry", "reject"):
        assert f'"{verdict}"' in body, f"DMN missing route {verdict!r}"

    seed = (
        repo_root
        / "30-graph"
        / "graph-schema"
        / "sql_migrations"
        / "20260514200000_seed_mangaka_vrm_bind_retry_dmn.up.sql"
    )
    assert seed.is_file(), f"missing DMN seed migration: {seed}"
    seed_body = seed.read_text(encoding="utf-8")
    assert "com.etzhayyim.policies.mangaka.vrmBindRetry" in seed_body
    assert "vertex_dmn_model" in seed_body


def test_pending_tools_have_seed_row(spec: dict) -> None:
    """P16-b seed migration must INSERT a `vertex_mcp_tool_def` row for
    every NSID in `pending_mcp_tools`. Locking the path so a future
    migration rename doesn't silently break Phase C dispatch."""
    repo_root = _LG_DIR.parents[2]
    seed = (
        repo_root
        / "30-graph"
        / "graph-schema"
        / "sql_migrations"
        / "20260514190000_seed_mangaka_compose_character_vrm_mcp_tools.up.sql"
    )
    assert seed.is_file(), f"missing seed migration: {seed}"
    body = seed.read_text(encoding="utf-8")
    for nsid in spec.get("pending_mcp_tools") or []:
        assert f"'{nsid}'" in body, (
            f"seed migration does not INSERT NSID {nsid!r}: {seed.name}"
        )


def test_batch_driver_targets_topology_nsid(spec: dict) -> None:
    """P16-d — the batch driver MUST POST to the same NSID the topology
    is registered under. The YAML comment promises the driver wraps the
    Pregel with progress + resume; this test locks that contract."""
    repo_root = _LG_DIR.parents[2]
    driver = (
        repo_root
        / "60-apps"
        / "etzhayyim-project-mangaka"
        / "scripts"
        / "author-ghosthacker-vrms.ts"
    )
    assert driver.is_file(), f"missing batch driver: {driver}"
    body = driver.read_text(encoding="utf-8")
    nsid = spec["assistant_id"]   # com.etzhayyim.mangaka.composeCharacterVrm
    assert nsid in body, f"driver does not reference topology NSID {nsid!r}"
    # Sequential-by-default invariant — the YAML says parallelism saturates
    # the GPU pool; the driver MUST NOT default to Promise.all over the
    # roster. The simplest structural check is "no Promise.all in the
    # main loop." Allow Promise.all inside helpers if a future patch
    # adds one with --concurrency, but in that case the test should be
    # updated deliberately.
    assert "Promise.all" not in body, (
        "batch driver introduced Promise.all — sequential-by-default invariant broken"
    )


def test_mediapipe_face_pod_image_present(spec: dict) -> None:
    """P16-c/1 — the `extract_blendshapes` node declares
    `pod_image: ghcr.io/etzhayyim/mediapipe-face:1`. That image MUST
    have a real build context under `50-infra/k8s/mangaka-mediapipe-face/`
    so the BuildKit remote-build script can push it without surprises."""
    repo_root = _LG_DIR.parents[2]
    pod_dir = repo_root / "50-infra" / "k8s" / "mangaka-mediapipe-face"
    assert pod_dir.is_dir(), f"missing pod image dir: {pod_dir}"
    for name in ("Dockerfile", "requirements.txt", "server.py", "README.md"):
        assert (pod_dir / name).is_file(), f"missing {pod_dir / name}"

    # The Dockerfile MUST reference the same NSID the topology calls.
    nsid = "com.etzhayyim.mangaka.tools.extractFacialBlendshapes"
    server = (pod_dir / "server.py").read_text(encoding="utf-8")
    assert nsid in server, f"server.py does not declare NSID {nsid!r}"

    # The topology's pod_image hint must match the image tag pattern we
    # actually build. We don't pin the version digit here (the topology
    # YAML and Dockerfile can roll forward together).
    by_id = {n["id"]: n for n in spec["nodes"]}
    pod_image = by_id["extract_blendshapes"]["config"]["pod_image"]
    assert pod_image.startswith("ghcr.io/etzhayyim/mediapipe-face:"), pod_image


def test_mediapipe_pod_has_no_runtime_external_api(spec: dict) -> None:
    """P16-c/1 — the Dockerfile MAY pull the FaceLandmarker weights
    from `storage.googleapis.com` at build time (that's how we bake the
    model in), but the runtime CMD MUST NOT invoke any external
    commercial API. Mirrors the topology-level invariant
    (`test_no_external_api_hostnames_in_topology`) — locks the
    self-hosted promise into the image build too."""
    repo_root = _LG_DIR.parents[2]
    pod_dir = repo_root / "50-infra" / "k8s" / "mangaka-mediapipe-face"
    server = (pod_dir / "server.py").read_text(encoding="utf-8").lower()
    forbidden_hosts = (
        "api.openai.com",
        "api.anthropic.com",
        "api.hume.ai",
        "api.adobe.com",
        "mixamo.com",
        "api.runway.com",
        "api.higgsfield.ai",
    )
    for host in forbidden_hosts:
        assert host not in server, (
            f"mediapipe-face server.py references external API host {host!r}. "
            f"External APIs are train-only — move to TRAINING_PIPELINE.md."
        )


def test_character_gen_pod_image_present(spec: dict) -> None:
    """P16-c/2 — the `generate_multiview` node declares
    `pod_image: ghcr.io/etzhayyim/character-gen:0.1`. Build context must
    exist under `50-infra/k8s/mangaka-character-gen/` with the standard
    4-file layout."""
    repo_root = _LG_DIR.parents[2]
    pod_dir = repo_root / "50-infra" / "k8s" / "mangaka-character-gen"
    assert pod_dir.is_dir(), f"missing pod image dir: {pod_dir}"
    for name in ("Dockerfile", "requirements.txt", "server.py", "README.md"):
        assert (pod_dir / name).is_file(), f"missing {pod_dir / name}"

    nsid = "com.etzhayyim.mangaka.tools.generateMultiviewAnime"
    server = (pod_dir / "server.py").read_text(encoding="utf-8")
    assert nsid in server, f"server.py does not declare NSID {nsid!r}"

    by_id = {n["id"]: n for n in spec["nodes"]}
    pod_image = by_id["generate_multiview"]["config"]["pod_image"]
    assert pod_image.startswith("ghcr.io/etzhayyim/character-gen:"), pod_image

    # GPU is required at this step. Lock that into the Dockerfile too —
    # the nvidia/cuda base layer is the structural signal.
    dockerfile = (pod_dir / "Dockerfile").read_text(encoding="utf-8")
    assert "nvidia/cuda" in dockerfile, "character-gen Dockerfile missing CUDA base layer"


def test_character_gen_pod_has_no_runtime_external_api() -> None:
    """P16-c/2 — same self-hosted-only ban as the topology + mediapipe
    pod: the server.py source MUST NOT reference any external commercial
    API. The Dockerfile may pull from `huggingface.co` /
    `github.com` / B2 for weight mirroring, but those are build-time or
    one-shot first-boot calls, not runtime API traffic."""
    repo_root = _LG_DIR.parents[2]
    pod_dir = repo_root / "50-infra" / "k8s" / "mangaka-character-gen"
    server = (pod_dir / "server.py").read_text(encoding="utf-8").lower()
    forbidden_hosts = (
        "api.openai.com",
        "api.anthropic.com",
        "api.hume.ai",
        "api.adobe.com",
        "mixamo.com",
        "api.runway.com",
        "api.higgsfield.ai",
    )
    for host in forbidden_hosts:
        assert host not in server, (
            f"character-gen server.py references external API host {host!r}. "
            f"External APIs are train-only — move to TRAINING_PIPELINE.md."
        )


def test_hunyuan3d_pod_image_present(spec: dict) -> None:
    """P16-c/3 — `reconstruct_mesh` node declares
    `pod_image: ghcr.io/etzhayyim/hunyuan3d-2:0.2`. Build context must
    exist under `50-infra/k8s/mangaka-hunyuan3d-2/`."""
    repo_root = _LG_DIR.parents[2]
    pod_dir = repo_root / "50-infra" / "k8s" / "mangaka-hunyuan3d-2"
    assert pod_dir.is_dir(), f"missing pod image dir: {pod_dir}"
    for name in ("Dockerfile", "requirements.txt", "server.py", "README.md"):
        assert (pod_dir / name).is_file(), f"missing {pod_dir / name}"

    nsid = "com.etzhayyim.mangaka.tools.reconstructMesh"
    server = (pod_dir / "server.py").read_text(encoding="utf-8")
    assert nsid in server, f"server.py does not declare NSID {nsid!r}"

    by_id = {n["id"]: n for n in spec["nodes"]}
    pod_image = by_id["reconstruct_mesh"]["config"]["pod_image"]
    assert pod_image.startswith("ghcr.io/etzhayyim/hunyuan3d-2:"), pod_image

    # GPU is required — CUDA base layer is the structural signal.
    dockerfile = (pod_dir / "Dockerfile").read_text(encoding="utf-8")
    assert "nvidia/cuda" in dockerfile, "hunyuan3d-2 Dockerfile missing CUDA base layer"


def test_hunyuan3d_pod_has_no_runtime_external_api() -> None:
    """P16-c/3 — same self-hosted-only ban for the hunyuan3d-2 server.
    Build-time pulls (HF / GitHub / B2) are allowed, runtime API
    traffic to commercial endpoints is not."""
    repo_root = _LG_DIR.parents[2]
    pod_dir = repo_root / "50-infra" / "k8s" / "mangaka-hunyuan3d-2"
    server = (pod_dir / "server.py").read_text(encoding="utf-8").lower()
    forbidden_hosts = (
        "api.openai.com",
        "api.anthropic.com",
        "api.hume.ai",
        "api.adobe.com",
        "mixamo.com",
        "api.runway.com",
        "api.higgsfield.ai",
    )
    for host in forbidden_hosts:
        assert host not in server, (
            f"hunyuan3d-2 server.py references external API host {host!r}. "
            f"External APIs are train-only — move to TRAINING_PIPELINE.md."
        )


def test_blender_rigify_pod_image_present(spec: dict) -> None:
    """P16-c/4 — `auto_rig` node declares
    `pod_image: ghcr.io/etzhayyim/blender-rigify-rignet:0.1`. Pod dir
    must hold the 6-file layout (Dockerfile, requirements, README,
    server.py, rigify_fit.py, rignet_inference.py)."""
    repo_root = _LG_DIR.parents[2]
    pod_dir = repo_root / "50-infra" / "k8s" / "mangaka-blender-rigify-rignet"
    assert pod_dir.is_dir(), f"missing pod image dir: {pod_dir}"
    for name in (
        "Dockerfile",
        "requirements.txt",
        "server.py",
        "rigify_fit.py",
        "rignet_inference.py",
        "README.md",
    ):
        assert (pod_dir / name).is_file(), f"missing {pod_dir / name}"

    nsid = "com.etzhayyim.mangaka.tools.autoRigHumanoid"
    server = (pod_dir / "server.py").read_text(encoding="utf-8")
    assert nsid in server, f"server.py does not declare NSID {nsid!r}"

    by_id = {n["id"]: n for n in spec["nodes"]}
    pod_image = by_id["auto_rig"]["config"]["pod_image"]
    assert pod_image.startswith("ghcr.io/etzhayyim/blender-rigify-rignet:"), pod_image

    # GPU NOT required here (Rigify is CPU-only, RigNet fallback is
    # CPU-only). Dockerfile MUST NOT use the CUDA base layer — locking
    # it so a future patch can't accidentally pull a GPU-tier node.
    dockerfile = (pod_dir / "Dockerfile").read_text(encoding="utf-8")
    assert "nvidia/cuda" not in dockerfile, (
        "blender-rigify-rignet Dockerfile uses CUDA base — auto_rig must stay on the CPU pool"
    )
    # Must source Blender from the official upstream tarball (signal:
    # reproducible, self-hosted, no apt-mirror-of-the-week drift).
    assert "download.blender.org" in dockerfile, (
        "blender-rigify-rignet Dockerfile missing official Blender tarball"
    )


def test_blender_rigify_pod_has_no_runtime_external_api() -> None:
    """P16-c/4 — same self-hosted-only ban for all 3 source files in
    the blender-rigify-rignet pod (server, rigify_fit, rignet_inference)."""
    repo_root = _LG_DIR.parents[2]
    pod_dir = repo_root / "50-infra" / "k8s" / "mangaka-blender-rigify-rignet"
    forbidden_hosts = (
        "api.openai.com",
        "api.anthropic.com",
        "api.hume.ai",
        "api.adobe.com",
        "mixamo.com",
        "api.runway.com",
        "api.higgsfield.ai",
    )
    for name in ("server.py", "rigify_fit.py", "rignet_inference.py"):
        body = (pod_dir / name).read_text(encoding="utf-8").lower()
        for host in forbidden_hosts:
            assert host not in body, (
                f"blender-rigify-rignet/{name} references external API host {host!r}. "
                f"External APIs are train-only — move to TRAINING_PIPELINE.md."
            )


def test_blender_vrm_pod_image_present(spec: dict) -> None:
    """P16-c/5 — `bind_vrm` node declares
    `pod_image: ghcr.io/etzhayyim/blender-vrm:4.1`. Pod dir must hold
    the 5-file layout (Dockerfile, requirements, README, server.py,
    bind_vrm.py)."""
    repo_root = _LG_DIR.parents[2]
    pod_dir = repo_root / "50-infra" / "k8s" / "mangaka-blender-vrm"
    assert pod_dir.is_dir(), f"missing pod image dir: {pod_dir}"
    for name in ("Dockerfile", "requirements.txt", "server.py", "bind_vrm.py", "README.md"):
        assert (pod_dir / name).is_file(), f"missing {pod_dir / name}"

    nsid = "com.etzhayyim.mangaka.tools.bindVrm"
    server = (pod_dir / "server.py").read_text(encoding="utf-8")
    assert nsid in server, f"server.py does not declare NSID {nsid!r}"

    by_id = {n["id"]: n for n in spec["nodes"]}
    pod_image = by_id["bind_vrm"]["config"]["pod_image"]
    assert pod_image.startswith("ghcr.io/etzhayyim/blender-vrm:"), pod_image

    # CPU-only invariant — must NOT use a CUDA base layer.
    dockerfile = (pod_dir / "Dockerfile").read_text(encoding="utf-8")
    assert "nvidia/cuda" not in dockerfile, (
        "blender-vrm Dockerfile uses CUDA base — bind_vrm must stay on the CPU pool"
    )
    assert "download.blender.org" in dockerfile, (
        "blender-vrm Dockerfile missing official Blender tarball"
    )


def test_blender_vrm_pod_has_no_runtime_external_api() -> None:
    """P16-c/5 — external-API ban across server.py + bind_vrm.py."""
    repo_root = _LG_DIR.parents[2]
    pod_dir = repo_root / "50-infra" / "k8s" / "mangaka-blender-vrm"
    forbidden_hosts = (
        "api.openai.com",
        "api.anthropic.com",
        "api.hume.ai",
        "api.adobe.com",
        "mixamo.com",
        "api.runway.com",
        "api.higgsfield.ai",
    )
    for name in ("server.py", "bind_vrm.py"):
        body = (pod_dir / name).read_text(encoding="utf-8").lower()
        for host in forbidden_hosts:
            assert host not in body, (
                f"blender-vrm/{name} references external API host {host!r}. "
                f"External APIs are train-only — move to TRAINING_PIPELINE.md."
            )


def test_all_p16c_pod_images_scaffolded(spec: dict) -> None:
    """P16-c roll-up — every mcp_tool node in the topology that
    declares a `pod_image` MUST have a real build context under
    `50-infra/k8s/mangaka-*`. attachCharacterVrm (P13) is the only
    mcp_tool without a pod_image (it runs in-process); everything
    else must point at a real image dir."""
    repo_root = _LG_DIR.parents[2]
    missing: list[str] = []
    for n in spec["nodes"]:
        cfg = n.get("config") or {}
        if n.get("kind") != "mcp_tool" or "pod_image" not in cfg:
            continue
        # Image tag format: ghcr.io/etzhayyim/<name>:<ver>
        tag = cfg["pod_image"]
        if not tag.startswith("ghcr.io/etzhayyim/"):
            missing.append(f"{n['id']}: {tag!r} not under ghcr.io/etzhayyim/")
            continue
        slug = tag[len("ghcr.io/etzhayyim/"):].split(":", 1)[0]
        pod_dir = repo_root / "50-infra" / "k8s" / f"mangaka-{slug}"
        if not pod_dir.is_dir():
            missing.append(f"{n['id']}: no build context at {pod_dir}")
    assert not missing, "P16-c pod scaffold gaps:\n  " + "\n  ".join(missing)


def test_character_roster_directory_present() -> None:
    """The driver's default `--characters-dir` points at the ghost-hacker
    roster. Lock the path so a roster rename doesn't silently make every
    Pregel run no-op (empty roster → 0 authored)."""
    repo_root = _LG_DIR.parents[2]
    roster = (
        repo_root
        / "60-apps"
        / "etzhayyim-project-mangaka"
        / "data"
        / "ghosthacker"
        / "resources"
        / "characters"
    )
    assert roster.is_dir(), f"missing roster dir: {roster}"
    # At least one character folder must exist — protects against a future
    # cleanup that empties the directory.
    children = [p for p in roster.iterdir() if p.is_dir() and not p.name.startswith(".")]
    assert len(children) > 0, f"roster dir has no character folders: {roster}"
