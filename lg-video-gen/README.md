# lg-video-gen

`mangaka` / `ghosthacker` の **prompt → scene video** 制作ワークフロー。
制作上の短縮名 `h3` は、MiniMax APIへ送る際に公式モデル名
`MiniMax-Hailuo-2.3` へ解決する。

```text
scene prompt
  → Hume-grounded face/eye rig を具体的な描画指示へ展開
  → MiniMax Hailuo 2.3 task submit
  → task-id を即時保存
  → poll / resume
  → scene.mp4 download
  → 0.35秒グリッドの t*.png を抽出
  → engine-independent manifest.edn
```

## 最短実行

```bash
npm install
npm run run -- --prompt "蓮が隠れたプロセスに気づき、Neiへ静かに視線を移す" --dry-run

MINIMAX_API_KEY=... npm run run -- \
  --prompt "蓮が隠れたプロセスに気づき、Neiへ静かに視線を移す" \
  --out out/ren-realization
```

既定は `h3` / 6秒 / 768P / prompt optimizer無効。10秒は768Pのみ、1080Pは6秒のみ。
生成が中断した場合は、`out/.../task.json` のtask IDを使う。

```bash
MINIMAX_API_KEY=... npm run run -- \
  --scene examples/ren-realization.scene.json \
  --resume TASK_ID \
  --out out/ren-realization
```

## Scene JSON

```json
{
  "prompt": "Ren notices a hidden process and slowly looks toward Nei.",
  "setting": "A dark incident response room before dawn.",
  "atmosphere": "Monitor light, dry still air, dust in the beam.",
  "camera": ["Push in", "Pan right"],
  "characters": [{
    "name": "Ren",
    "identity": "17-year-old Japanese boy, raven hair, charcoal hoodie",
    "action": "His hand stops above the keyboard, then his gaze shifts.",
    "face": {
      "rig": "wide-reveal",
      "intensity": 0.7,
      "eyes": {"open": 0.96, "brow": "inner-raised", "pupil": 0.82, "gaze": "side", "highlight": "double"},
      "mouth": "small-open",
      "dimensions": [{"label": "Realization", "confidence": 0.72}]
    }
  }]
}
```

Hume次元のconfidenceは来歴として保持するが、動画promptには内面の断定や強度として入れない。
見た目はface rigの解剖的制御と、作者が指定した`intensity`で決める。
