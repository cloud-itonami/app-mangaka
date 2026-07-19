#!/usr/bin/env python3
"""Install a mangaka.etzhayyim.com cine workflow into ComfyUI's user library.

Emits a 13-node multi-pass workflow that exposes the kami-cine pipeline as
ComfyUI nodes the artist can edit visually:

  ┌─ CheckpointLoaderSimple (1) ────────────────────────────────────────┐
  │                                                                     │
  │ EmptyLatentImage (2) ──┐                                            │
  │                        │                                            │
  │ CLIPTextEncode pos (3) ┤    ┌── VAEDecode (6) ── SaveImage (7)      │
  │ CLIPTextEncode neg (4) ┤    │   "composition"                       │
  │                        │    │                                       │
  │                        ▼    │                                       │
  │                     KSampler (5) ── LATENT_base                     │
  │                     20 steps, euler, denoise=1.0                    │
  │                                                                     │
  │                                LATENT_base                          │
  │                                ▼                                    │
  │ CLIPTextEncode ink (9) ┐                                            │
  │ CLIPTextEncode anti(10) ┤   ┌── VAEDecode (12) ── SaveImage (13)    │
  │                         │   │   "inked"                             │
  │                         ▼   │                                       │
  │                     KSampler (11) ── LATENT_inked                   │
  │                     14 steps, dpmpp_2m, denoise=0.4                 │
  └─────────────────────────────────────────────────────────────────────┘

The output saves TWO images per run: `mangaka-composition_NNNNN.png` and
`mangaka-inked_NNNNN.png`. Stage 1 maps to worldModel composition; stage 2
maps to diffusionPass inking with reduced denoise so structure carries
through from the first pass.

Usage:
  python3 lg/scripts/install-mangaka-comfy-workflow.py
  python3 lg/scripts/install-mangaka-comfy-workflow.py --comfy http://192.168.1.70:8188
  python3 lg/scripts/install-mangaka-comfy-workflow.py --emit-api-format api.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
import uuid
from typing import Any

DEFAULT_COMFY = os.environ.get("COMFY_POD_URL") or "http://192.168.1.70:8188"
DEFAULT_CKPT = os.environ.get("COMFY_DEFAULT_CKPT", "animagine-xl-4.0.safetensors")

POS_COMPOSITION = (
    "establishing shot composition, dramatic chiaroscuro lighting, dynamic "
    "perspective, cinematic framing, lone figure silhouette"
)
NEG_COMPOSITION = "blurry, low quality, color photograph, soft focus, deformed"

POS_INK = (
    "manga inked panel, sharp black ink lines, screentone, hatching, "
    "high contrast monochrome, dynamic composition, shonen-jump style"
)
NEG_INK = "color photograph, blurry, low quality, watermark, soft focus, gradient"


def _node(
    nid: int,
    type_: str,
    pos: tuple[int, int],
    size: tuple[int, int],
    *,
    outputs: list[dict[str, Any]] | None = None,
    widgets: list[Any] | None = None,
    order: int = 0,
) -> dict[str, Any]:
    return {
        "id": nid,
        "type": type_,
        "pos": list(pos),
        "size": list(size),
        "flags": {},
        "order": order,
        "mode": 0,
        "outputs": outputs or [],
        "properties": {"Node name for S&R": type_},
        "widgets_values": widgets or [],
    }


def _build_workflow() -> dict[str, Any]:
    """GUI-format workflow ComfyUI's frontend can open + edit."""
    # Link list entries are [link_id, src_node, src_slot, dst_node, dst_slot, "TYPE"]
    links: list[list[Any]] = []
    def link(src: int, src_slot: int, dst: int, dst_slot: int, t: str) -> int:
        lid = len(links) + 1
        links.append([lid, src, src_slot, dst, dst_slot, t])
        return lid

    # --- Stage 1: shared loader ---
    n1_out_model: list[int] = []
    n1_out_clip:  list[int] = []
    n1_out_vae:   list[int] = []
    n1 = _node(
        1, "CheckpointLoaderSimple", (24, 40), (320, 100),
        outputs=[
            {"name": "MODEL", "type": "MODEL", "links": n1_out_model, "slot_index": 0},
            {"name": "CLIP",  "type": "CLIP",  "links": n1_out_clip,  "slot_index": 1},
            {"name": "VAE",   "type": "VAE",   "links": n1_out_vae,   "slot_index": 2},
        ],
        widgets=[DEFAULT_CKPT],
        order=0,
    )

    # --- Stage 2: empty latent (composition canvas) ---
    n2_out_latent: list[int] = []
    n2 = _node(
        2, "EmptyLatentImage", (24, 200), (320, 110),
        outputs=[{"name": "LATENT", "type": "LATENT", "links": n2_out_latent, "slot_index": 0}],
        widgets=[832, 1216, 1],
        order=1,
    )

    # --- Stage 3-4: composition prompts ---
    n3_out: list[int] = []
    n3 = _node(
        3, "CLIPTextEncode", (380, 40), (380, 150),
        outputs=[{"name": "CONDITIONING", "type": "CONDITIONING", "links": n3_out, "slot_index": 0}],
        widgets=[POS_COMPOSITION], order=2,
    )
    n4_out: list[int] = []
    n4 = _node(
        4, "CLIPTextEncode", (380, 220), (380, 130),
        outputs=[{"name": "CONDITIONING", "type": "CONDITIONING", "links": n4_out, "slot_index": 0}],
        widgets=[NEG_COMPOSITION], order=3,
    )

    # --- Stage 5: KSampler (base composition) ---
    n5_out: list[int] = []
    n5 = _node(
        5, "KSampler", (820, 40), (300, 270),
        outputs=[{"name": "LATENT", "type": "LATENT", "links": n5_out, "slot_index": 0}],
        widgets=[0, "randomize", 20, 7.0, "euler", "normal", 1.0],
        order=4,
    )

    # --- Stage 6+7: composition preview + save ---
    n6_out: list[int] = []
    n6 = _node(
        6, "VAEDecode", (1160, 40), (220, 50),
        outputs=[{"name": "IMAGE", "type": "IMAGE", "links": n6_out, "slot_index": 0}],
        order=5,
    )
    n7 = _node(7, "SaveImage", (1400, 40), (320, 270),
               widgets=["mangaka-composition"], order=6)

    # --- Stage 9-10: ink prompts (reuse CLIP from node 1) ---
    n9_out: list[int] = []
    n9 = _node(
        9, "CLIPTextEncode", (380, 400), (380, 150),
        outputs=[{"name": "CONDITIONING", "type": "CONDITIONING", "links": n9_out, "slot_index": 0}],
        widgets=[POS_INK], order=7,
    )
    n10_out: list[int] = []
    n10 = _node(
        10, "CLIPTextEncode", (380, 580), (380, 130),
        outputs=[{"name": "CONDITIONING", "type": "CONDITIONING", "links": n10_out, "slot_index": 0}],
        widgets=[NEG_INK], order=8,
    )

    # --- Stage 11: KSampler (refine to ink, low denoise) ---
    n11_out: list[int] = []
    n11 = _node(
        11, "KSampler", (820, 400), (300, 270),
        outputs=[{"name": "LATENT", "type": "LATENT", "links": n11_out, "slot_index": 0}],
        widgets=[0, "randomize", 14, 6.5, "dpmpp_2m", "karras", 0.4],
        order=9,
    )

    # --- Stage 12+13: inked decode + save ---
    n12_out: list[int] = []
    n12 = _node(
        12, "VAEDecode", (1160, 400), (220, 50),
        outputs=[{"name": "IMAGE", "type": "IMAGE", "links": n12_out, "slot_index": 0}],
        order=10,
    )
    n13 = _node(13, "SaveImage", (1400, 400), (320, 270),
                widgets=["mangaka-inked"], order=11)

    # --- Wire it ---
    n1_out_model.append(link(1, 0, 5, 0, "MODEL"))      # to base sampler
    n1_out_model.append(link(1, 0, 11, 0, "MODEL"))     # to refine sampler
    n1_out_clip.append(link(1, 1, 3, 0, "CLIP"))
    n1_out_clip.append(link(1, 1, 4, 0, "CLIP"))
    n1_out_clip.append(link(1, 1, 9, 0, "CLIP"))
    n1_out_clip.append(link(1, 1, 10, 0, "CLIP"))
    n1_out_vae.append(link(1, 2, 6, 1, "VAE"))          # composition decode
    n1_out_vae.append(link(1, 2, 12, 1, "VAE"))         # inked decode

    n2_out_latent.append(link(2, 0, 5, 3, "LATENT"))
    n3_out.append(link(3, 0, 5, 1, "CONDITIONING"))
    n4_out.append(link(4, 0, 5, 2, "CONDITIONING"))

    n5_out.append(link(5, 0, 6, 0, "LATENT"))           # base decode
    n5_out.append(link(5, 0, 11, 3, "LATENT"))          # base → refine input

    n6_out.append(link(6, 0, 7, 0, "IMAGE"))

    n9_out.append(link(9, 0, 11, 1, "CONDITIONING"))
    n10_out.append(link(10, 0, 11, 2, "CONDITIONING"))

    n11_out.append(link(11, 0, 12, 0, "LATENT"))
    n12_out.append(link(12, 0, 13, 0, "IMAGE"))

    nodes = [n1, n2, n3, n4, n5, n6, n7, n9, n10, n11, n12, n13]

    return {
        "id": str(uuid.uuid4()),
        "revision": 0,
        "last_node_id": 13,
        "last_link_id": len(links),
        "nodes": nodes,
        "links": links,
        "groups": [
            {
                "id": 1, "title": "Stage 5 · neuralRender (composition)",
                "bounding": [10, 10, 1740, 320],
                "color": "#3f5159", "font_size": 24,
                "flags": {},
            },
            {
                "id": 2, "title": "Stage 6 · diffusionPass (inked refine)",
                "bounding": [10, 370, 1740, 320],
                "color": "#594a3f", "font_size": 24,
                "flags": {},
            },
        ],
        "config": {},
        "extra": {
            "ds": {"scale": 0.7, "offset": [0, 0]},
            "info": {
                "name": "mangaka cine pipeline",
                "author": "studio.etzhayyim.com",
                "description": (
                    "2-pass mangaka panel generator. Pass 1 (top row) builds "
                    "the composition latent; pass 2 (bottom row) refines into "
                    "the inked final using denoise=0.4 so the structure carries "
                    "through. Edit the CLIPTextEncode nodes to taste."
                ),
            },
        },
        "version": 0.4,
    }


def _build_api_format(workflow: dict[str, Any]) -> dict[str, Any]:
    """Translate the GUI workflow above into the flat {node_id: {class_type, inputs}}
    shape comfy_run / lg_mangaka.comfy expects to POST to /prompt."""
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": DEFAULT_CKPT}},
        "2": {"class_type": "EmptyLatentImage", "inputs": {"width": 832, "height": 1216, "batch_size": 1}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": POS_COMPOSITION, "clip": ["1", 1]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": NEG_COMPOSITION, "clip": ["1", 1]}},
        "5": {"class_type": "KSampler", "inputs": {
            "seed": 0, "steps": 20, "cfg": 7.0, "sampler_name": "euler",
            "scheduler": "normal", "denoise": 1.0,
            "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0],
            "latent_image": ["2", 0],
        }},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": "mangaka-composition", "images": ["6", 0]}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"text": POS_INK, "clip": ["1", 1]}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {"text": NEG_INK, "clip": ["1", 1]}},
        "11": {"class_type": "KSampler", "inputs": {
            "seed": 0, "steps": 14, "cfg": 6.5, "sampler_name": "dpmpp_2m",
            "scheduler": "karras", "denoise": 0.4,
            "model": ["1", 0], "positive": ["9", 0], "negative": ["10", 0],
            "latent_image": ["5", 0],
        }},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["1", 2]}},
        "13": {"class_type": "SaveImage", "inputs": {"filename_prefix": "mangaka-inked", "images": ["12", 0]}},
    }


def _http(method: str, url: str, body: bytes | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _build_character_workflow() -> dict[str, Any]:
    """Single-pass character design sheet — batch_size 2 for 2 view variants."""
    links: list[list[Any]] = []
    def link(src, ss, dst, ds, t):
        lid = len(links) + 1
        links.append([lid, src, ss, dst, ds, t]); return lid

    out_model: list[int] = []
    out_clip:  list[int] = []
    out_vae:   list[int] = []
    out_lat:   list[int] = []
    out_p:     list[int] = []
    out_n:     list[int] = []
    out_s:     list[int] = []
    out_i:     list[int] = []

    n1 = _node(1, "CheckpointLoaderSimple", (24, 40), (320, 100),
               outputs=[{"name":"MODEL","type":"MODEL","links":out_model,"slot_index":0},
                        {"name":"CLIP","type":"CLIP","links":out_clip,"slot_index":1},
                        {"name":"VAE","type":"VAE","links":out_vae,"slot_index":2}],
               widgets=[DEFAULT_CKPT], order=0)
    n2 = _node(2, "EmptyLatentImage", (24, 200), (320, 110),
               outputs=[{"name":"LATENT","type":"LATENT","links":out_lat,"slot_index":0}],
               widgets=[832, 1216, 2], order=1)
    n3 = _node(3, "CLIPTextEncode", (380, 40), (380, 150),
               outputs=[{"name":"CONDITIONING","type":"CONDITIONING","links":out_p,"slot_index":0}],
               widgets=[("manga character design, <name>, <description>, character reference sheet, "
                        "multiple views, front three-quarter view, cleanly lined, anime, manga, ink, screentone, "
                        "detailed face, expressive eyes, dynamic pose")], order=2)
    n4 = _node(4, "CLIPTextEncode", (380, 220), (380, 130),
               outputs=[{"name":"CONDITIONING","type":"CONDITIONING","links":out_n,"slot_index":0}],
               widgets=["blurry, low quality, watermark, deformed, extra limbs, color photograph, soft focus"],
               order=3)
    n5 = _node(5, "KSampler", (820, 40), (300, 270),
               outputs=[{"name":"LATENT","type":"LATENT","links":out_s,"slot_index":0}],
               widgets=[0, "randomize", 22, 7.0, "euler", "normal", 1.0], order=4)
    n6 = _node(6, "VAEDecode", (1160, 40), (220, 50),
               outputs=[{"name":"IMAGE","type":"IMAGE","links":out_i,"slot_index":0}],
               order=5)
    n7 = _node(7, "SaveImage", (1400, 40), (320, 270),
               widgets=["mangaka-character"], order=6)

    out_model.append(link(1, 0, 5, 0, "MODEL"))
    out_clip.extend([link(1, 1, 3, 0, "CLIP"), link(1, 1, 4, 0, "CLIP")])
    out_vae.append(link(1, 2, 6, 1, "VAE"))
    out_lat.append(link(2, 0, 5, 3, "LATENT"))
    out_p.append(link(3, 0, 5, 1, "CONDITIONING"))
    out_n.append(link(4, 0, 5, 2, "CONDITIONING"))
    out_s.append(link(5, 0, 6, 0, "LATENT"))
    out_i.append(link(6, 0, 7, 0, "IMAGE"))

    return {
        "id": str(uuid.uuid4()), "revision": 0,
        "last_node_id": 7, "last_link_id": len(links),
        "nodes": [n1, n2, n3, n4, n5, n6, n7],
        "links": links,
        "groups": [{"id": 1, "title": "Character design sheet (batch=2)",
                    "bounding": [10, 10, 1740, 320], "color": "#3f4459",
                    "font_size": 24, "flags": {}}],
        "config": {}, "extra": {"ds": {"scale": 0.7, "offset": [0, 0]},
                                "info": {"name": "mangaka character sheet",
                                         "description": "Replace <name>/<description> in node 3 prompt before queue."}},
        "version": 0.4,
    }


def _build_scene_workflow() -> dict[str, Any]:
    """Single-pass establishing-shot scene generator (landscape 1216x832)."""
    links: list[list[Any]] = []
    def link(src, ss, dst, ds, t):
        lid = len(links) + 1
        links.append([lid, src, ss, dst, ds, t]); return lid

    out_model: list[int] = []
    out_clip:  list[int] = []
    out_vae:   list[int] = []
    out_lat:   list[int] = []
    out_p:     list[int] = []
    out_n:     list[int] = []
    out_s:     list[int] = []
    out_i:     list[int] = []

    n1 = _node(1, "CheckpointLoaderSimple", (24, 40), (320, 100),
               outputs=[{"name":"MODEL","type":"MODEL","links":out_model,"slot_index":0},
                        {"name":"CLIP","type":"CLIP","links":out_clip,"slot_index":1},
                        {"name":"VAE","type":"VAE","links":out_vae,"slot_index":2}],
               widgets=[DEFAULT_CKPT], order=0)
    n2 = _node(2, "EmptyLatentImage", (24, 200), (320, 110),
               outputs=[{"name":"LATENT","type":"LATENT","links":out_lat,"slot_index":0}],
               widgets=[1216, 832, 1], order=1)
    n3 = _node(3, "CLIPTextEncode", (380, 40), (380, 150),
               outputs=[{"name":"CONDITIONING","type":"CONDITIONING","links":out_p,"slot_index":0}],
               widgets=[("manga establishing shot, <description>, no characters, "
                        "environment design, atmospheric perspective, wide cinematic framing, "
                        "detailed background, anime, manga, ink, screentone, detailed")], order=2)
    n4 = _node(4, "CLIPTextEncode", (380, 220), (380, 130),
               outputs=[{"name":"CONDITIONING","type":"CONDITIONING","links":out_n,"slot_index":0}],
               widgets=["blurry, low quality, watermark, deformed, color photograph"],
               order=3)
    n5 = _node(5, "KSampler", (820, 40), (300, 270),
               outputs=[{"name":"LATENT","type":"LATENT","links":out_s,"slot_index":0}],
               widgets=[0, "randomize", 22, 7.0, "euler", "normal", 1.0], order=4)
    n6 = _node(6, "VAEDecode", (1160, 40), (220, 50),
               outputs=[{"name":"IMAGE","type":"IMAGE","links":out_i,"slot_index":0}],
               order=5)
    n7 = _node(7, "SaveImage", (1400, 40), (320, 270),
               widgets=["mangaka-scene"], order=6)

    out_model.append(link(1, 0, 5, 0, "MODEL"))
    out_clip.extend([link(1, 1, 3, 0, "CLIP"), link(1, 1, 4, 0, "CLIP")])
    out_vae.append(link(1, 2, 6, 1, "VAE"))
    out_lat.append(link(2, 0, 5, 3, "LATENT"))
    out_p.append(link(3, 0, 5, 1, "CONDITIONING"))
    out_n.append(link(4, 0, 5, 2, "CONDITIONING"))
    out_s.append(link(5, 0, 6, 0, "LATENT"))
    out_i.append(link(6, 0, 7, 0, "IMAGE"))

    return {
        "id": str(uuid.uuid4()), "revision": 0,
        "last_node_id": 7, "last_link_id": len(links),
        "nodes": [n1, n2, n3, n4, n5, n6, n7],
        "links": links,
        "groups": [{"id": 1, "title": "Scene / environment establishing shot",
                    "bounding": [10, 10, 1740, 320], "color": "#3f594a",
                    "font_size": 24, "flags": {}}],
        "config": {}, "extra": {"ds": {"scale": 0.7, "offset": [0, 0]},
                                "info": {"name": "mangaka scene", "description": "Edit node 3 prompt to taste."}},
        "version": 0.4,
    }


_INSTALLERS = {
    "mangaka-cine.json":      _build_workflow,         # per-panel 2-pass (composition+inked)
    "mangaka-panel.json":     _build_workflow,         # alias — same workflow
    "mangaka-character.json": _build_character_workflow,
    "mangaka-scene.json":     _build_scene_workflow,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--comfy", default=DEFAULT_COMFY,
                    help=f"ComfyUI base URL (default {DEFAULT_COMFY})")
    ap.add_argument("--name", default=None,
                    help="Install only this workflow filename "
                         f"(default: install all — {', '.join(_INSTALLERS)})")
    ap.add_argument("--emit-api-format", metavar="PATH",
                    help="Also write the /prompt-style API workflow for the panel/cine workflow to PATH")
    ap.add_argument("--emit-gui-format", metavar="PATH",
                    help="Also write the panel/cine GUI workflow to PATH (other workflows are install-only)")
    args = ap.parse_args()

    targets = [args.name] if args.name else list(_INSTALLERS.keys())
    rc = 0
    for name in targets:
        builder = _INSTALLERS.get(name)
        if not builder:
            print(f"unknown workflow {name!r} (known: {', '.join(_INSTALLERS)})", file=sys.stderr)
            rc = 1; continue
        wf = builder()
        body = json.dumps(wf, indent=2).encode("utf-8")

        if name == "mangaka-cine.json" and args.emit_gui_format:
            with open(args.emit_gui_format, "wb") as f:
                f.write(body)
            print(f"wrote GUI workflow → {args.emit_gui_format} ({len(body)} bytes)")
        if name == "mangaka-cine.json" and args.emit_api_format:
            api = _build_api_format(wf)
            with open(args.emit_api_format, "w", encoding="utf-8") as f:
                json.dump(api, f, indent=2)
            print(f"wrote API workflow → {args.emit_api_format}")

        target = args.comfy.rstrip("/") + "/userdata/" + urllib.parse.quote("workflows/" + name, safe="")
        status, resp = _http("POST", target, body)
        if status != 200:
            print(f"POST {target} → HTTP {status}: {resp[:300].decode('utf-8', 'replace')}", file=sys.stderr)
            rc = 1; continue
        print(f"installed → {target}")
    print(f"\nOpen ComfyUI: {args.comfy}/  → Workflows panel → pick any installed file")
    return rc


if __name__ == "__main__":
    sys.exit(main())
