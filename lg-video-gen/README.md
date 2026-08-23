# lg-video-gen

`mangaka` / `ghosthacker` の **prompt → H3 video → manga storyboard** 制作ワークフロー。
制作名 `h3` は公式APIの `MiniMax-H3` とHugging Faceの
`MiniMaxAI/MiniMax-H3` を指す。`MiniMax-Hailuo-2.3` とは別モデルとして扱う。

```text
scene prompt
  → Hume-grounded face/eye rig を具体的な描画指示へ展開
  → MiniMax H3 V2 task submit / resume
  → scene.mp4 download
  → フレーム差分で各場面の最大動作点を検出
  → カット直前・直後を対として採用
  → 右→左、上→下のコマ割り
  → storyboard.json / storyboard.svg / manifest.edn
```

## 最短実行

```bash
npm install
npm run run -- --prompt "蓮が異変に気づき、Neiへ視線を移す" --dry-run

MINIMAX_API_KEY=... npm run run -- \
  --prompt "蓮が異変に気づき、Neiへ視線を移す" \
  --duration 8 --ratio 16:9 --resolution 768P \
  --out out/ren-realization
```

既定は `MiniMax-H3` / 6秒 / 768P / 16:9 / 最大8コマ。
H3は4〜15秒、768Pまたは2Kを指定できる。開始画像を渡す場合、比率は画像から自動決定する。

```bash
MINIMAX_API_KEY=... npm run run -- \
  --scene examples/ren-realization.scene.json \
  --first-frame ren.png \
  --max-panels 8 \
  --out out/ren-realization
```

中断時は `task.json` のtask IDで再開できる。

```bash
MINIMAX_API_KEY=... npm run run -- \
  --scene examples/ren-realization.scene.json \
  --resume TASK_ID \
  --out out/ren-realization
```

旧Hailuo 2.3を明示的に使う場合だけ `--model hailuo-2.3` を指定する。

## 動画から選ぶコマ

等間隔の全フレームは採用しない。8fpsで隣接フレームの差分を測り、次を選ぶ。

1. 導入：人物、場所、位置関係が読める冒頭
2. 動作：検出された各場面で差分スコアが最大の瞬間
3. 転換：カット直前と直後を必ず対にする
4. 着地：動作後の状態が読める終端

全体で最も動きが強い瞬間を最大コマにする。場面転換の対は同じ段に置き、
日本漫画の読み順に合わせて「転換前を右、転換後を左」に配置する。
放射状の曖昧なコマ割りは生成しない。

成果物：

- `scene.mp4` — 元動画
- `panel-*.png` — 採用されたコマ
- `storyboard.json` — 時刻、動作スコア、役割、矩形、採用理由
- `storyboard.svg` — コマ割りプレビュー
- `manifest.edn` — エンジン非依存の制作manifest

## Scene JSONと表情

目、眉、瞳孔、視線、ハイライト、左右差、口、作者指定の表情強度をScene JSONで保持する。
Hume次元のconfidenceは来歴として保持するが、動画promptには内面の断定や強度として入れない。
見た目はface rigの解剖的制御と、作者が指定した`intensity`で決める。

Hugging Face調査と運用上の注意は
[`docs/minimax-h3-huggingface.md`](docs/minimax-h3-huggingface.md)を参照。
