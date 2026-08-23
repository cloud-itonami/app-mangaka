# lg-video-gen

`mangaka` / `ghosthacker` の **prompt → H3 video → manga storyboard** 制作ワークフロー。
制作名 `h3` はMurakumoの検証済みセルフホストモデル `minimax-h3` と、
その出典であるHugging Faceの `MiniMaxAI/MiniMax-H3` を指す。

ネームとキャラクターを作る前段は
[`docs/name-character-steps.md`](docs/name-character-steps.md)の12 stepsを使う。
「描きたい瞬間」から始め、キャラごとの立場と最善手を衝突させ、粗いネームを作り、
読者視点と助言で前のstepへ戻る。特定作家の画風や既存キャラクターは生成指示に入れない。

```text
scene prompt
  → Hume-grounded face/eye rig を具体的な描画指示へ展開
  → api.murakumo.cloudへH3 generation jobをsubmit / resume
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

MURAKUMO_GENERATION_TOKEN=... npm run run -- \
  --prompt "蓮が異変に気づき、Neiへ視線を移す" \
  --out out/ren-realization
```

既定の `one-minute` プリセットは `minimax-h3` / 約1秒 / 512×512 /
22フレーム / 1 step / 最大3コマ。640×640・175f・20 stepsの実測6038秒から
線形換算したウォーム計算予算は約24.3秒。キュー待ちとモデル読込は含まないため、
「1分以内」はSLAではなくシーン生成の計算予算である。

```bash
# 従来の品質設定
MURAKUMO_GENERATION_TOKEN=... npm run run -- \
  --preset quality --scene examples/ren-realization.scene.json \
  --out out/ren-quality
```

`quality`は5秒 / 640×640 / 124フレーム / 20 steps / 最大8コマ。
H3のフレーム数は24fpsを基準に`17k+5`へ切り上げる。安全範囲は22〜175フレーム、
寸法は32の倍数で指定する。別環境では `MURAKUMO_GENERATION_URL` で接続先を明示できる。

```bash
MURAKUMO_GENERATION_TOKEN=... npm run run -- \
  --scene examples/ren-realization.scene.json \
  --first-frame https://assets.example/ren.png \
  --max-panels 8 \
  --out out/ren-realization
```

中断時は `task.json` のtask IDで再開できる。

```bash
MURAKUMO_GENERATION_TOKEN=... npm run run -- \
  --scene examples/ren-realization.scene.json \
  --resume TASK_ID \
  --out out/ren-realization
```

トークンはgeneration scopeを持つものを環境変数から読むだけで、
`request.json`、`task.json`、manifestには保存しない。成果物は広告された外部URLへ
直接アクセスせず、認証付きのMurakumo artifact endpointから取得する。
H3の参照画像は先頭フレーム固定ではなく、人物同一性などを誘導する参照として扱われる。
現行API契約に合わせ、`--first-frame`にはMurakumoから取得できるHTTP(S) URLを渡す。

## 動画から選ぶコマ

等間隔の全フレームは採用しない。8fpsで隣接フレームの差分を測り、次を選ぶ。
`one-minute`では1動画を1場面に限定し、導入・最大動作点・着地の3コマを作る。
複数の場面切り替えを一動画に詰める場合は`quality`を使う。

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
