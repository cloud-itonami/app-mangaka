"""Emit a Mermaid + ASCII view of compose_scene_3d.topology.yaml.

The Mermaid string is valid Markdown — paste into any GitHub issue, IDE
preview, or `https://mermaid.live` to see the live graph. The ASCII
view is a quick terminal sketch.

    python3 scripts/topology_diagram.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_LG_DIR = _HERE.parent
_TOPO_YAML = _LG_DIR / "lg_mangaka" / "graphs" / "compose_scene_3d.topology.yaml"
_OUT_DIR = _HERE / "demo_outputs"


def _load() -> dict:
    import yaml

    return yaml.safe_load(_TOPO_YAML.read_text(encoding="utf-8"))


_KIND_STYLE = {
    "mcp_tool":   ("[/", "/]"),
    "llm":        ("([", "])"),
    "llm_vision": ("[(", ")]"),
    "foreach":    ("{{", "}}"),
    "py_primitive": ("[", "]"),
}


def _mermaid(spec: dict) -> str:
    lines = ["```mermaid", "flowchart TD"]
    lines.append(f'    START([__start__]):::start')
    by_id = {n["id"]: n for n in spec["nodes"]}
    for nid, n in by_id.items():
        open_, close_ = _KIND_STYLE.get(n["kind"], ("[", "]"))
        label = f"{nid}<br/>kind={n['kind']}"
        if n.get("ref"):
            ref = n["ref"]
            if len(ref) > 40:
                ref = ref[:37] + "..."
            label += f"<br/>{ref}"
        lines.append(f'    {nid}{open_}"{label}"{close_}')
    lines.append('    END([__end__]):::start')
    lines.append(f'    START --> {spec["entry"]}')
    for e in spec.get("edges") or []:
        src = e["from"]
        dst = "END" if e["to"] == "END" else e["to"]
        lines.append(f'    {src} --> {dst}')
    for ce in spec.get("conditional_edges") or []:
        src = ce["from"]
        if "router" in ce and ce["router"] == "send_fanout":
            fanout = ce.get("fanout") or {}
            target = fanout.get("to_node")
            if target:
                lines.append(f'    {src} -- "Send fan-out<br/>(per pose_plan key)" --> {target}')
        elif "condition_ref" in ce:
            ref = ce["condition_ref"]
            short = ref.split("@")[0] if "@" in ref else ref
            for label, tgt in (ce.get("paths") or {}).items():
                dst = "END" if tgt == "END" else tgt
                lines.append(f'    {src} -- "{short}:{label}" --> {dst}')
    lines.append('    classDef start fill:#222,stroke:#888,color:#fff')
    lines.append('```')
    return "\n".join(lines)


def _ascii(spec: dict) -> str:
    out: list[str] = []
    out.append(f"assistant : {spec['assistant_id']} v{spec['version']} ({spec['kind']})")
    out.append(f"entry     : {spec['entry']}")
    out.append(f"state keys: {len(spec.get('state_keys') or [])}")
    out.append(f"nodes     : {len(spec.get('nodes') or [])}")
    out.append("")
    by_id = {n["id"]: n for n in spec["nodes"]}
    out.append("nodes:")
    for nid, n in by_id.items():
        ref = n.get("ref", "")
        if len(ref) > 50:
            ref = ref[:47] + "..."
        out.append(f"  • {nid:24s}  kind={n['kind']:11s}  ref={ref}")
    out.append("")
    out.append("backbone:")
    out.append(f"  START → {spec['entry']}")
    for e in spec.get("edges") or []:
        out.append(f"  {e['from']:24s} → {e['to']}")
    out.append("")
    out.append("conditional:")
    for ce in spec.get("conditional_edges") or []:
        if ce.get("router") == "send_fanout":
            fan = ce.get("fanout") or {}
            out.append(
                f"  {ce['from']:24s} ──Send(per {fan.get('from_state','?')} key)→ {fan.get('to_node','?')}"
            )
        elif "condition_ref" in ce:
            paths = " | ".join(f"{k}→{v}" for k, v in (ce.get("paths") or {}).items())
            out.append(f"  {ce['from']:24s} ──DMN({ce['condition_ref'].split('@')[0]})→ [{paths}]")
    return "\n".join(out)


def main() -> None:
    spec = _load()
    mermaid = _mermaid(spec)
    ascii_view = _ascii(spec)

    _OUT_DIR.mkdir(exist_ok=True)
    (_OUT_DIR / "topology.mermaid.md").write_text(mermaid)
    (_OUT_DIR / "topology.ascii.txt").write_text(ascii_view)

    print(ascii_view)
    print()
    print(f"→ scripts/demo_outputs/topology.mermaid.md  (paste into mermaid.live)")
    print(f"→ scripts/demo_outputs/topology.ascii.txt")


if __name__ == "__main__":
    main()
