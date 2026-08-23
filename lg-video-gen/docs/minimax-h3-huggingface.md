# MiniMax H3 — Hugging Face調査メモ

調査日: 2026-08-23

## 同定

- 公式リポジトリ: `MiniMaxAI/MiniMax-H3`
- APIモデル名: `MiniMax-H3`
- Hailuo 2.3とは別モデル。`h3` を `MiniMax-Hailuo-2.3` に置き換えてはならない
- 33Bパラメータのdense omni-modal Transformer
- 映像と32kHzステレオ音声を共同生成し、24fps、4〜15秒に対応

## 公開チェックポイント

- `H3-Base-FL2VA`: テキストのみ、開始フレーム、終了フレーム、開始＋終了フレーム
- `H3-Base-Ref2VA`: 最大9画像、最大3動画、最大3音声の参照入力
- 公開Baseは768p。2KはH3-Regenerate-2Kを含む公式V2 API経路
- 公式のローカル例はSGLangでFL2VAを4 GPU構成でserveする
- Hugging Face Inference Providersでは、調査時点でfal-aiとWaveSpeedのimage-text-to-video対応が表示された

## 本ワークフローの選択

`lg-video-gen`は再現しやすい公式MiniMax V2 APIを既定経路にする。
リクエストは`POST /v2/video_generation`、状態確認は
`GET /v2/query/video_generation/{task_id}`。テキストのみでも開始画像付きでもH3を使用する。

公開ウェイトをローカル配備する場合は、モデルカードが推奨するSGLang、vLLM、
Diffusers、ComfyUIのいずれかを別の実行基盤として用意する。モデルの巨大なウェイトを
この小さな制作パッケージの依存物として自動取得しない。

## ライセンス境界

公開ウェイトはApache-2.0ではなくMiniMax H3 Community License Agreement。
地域制限、配布時のNOTICE、商用UI表示、年間売上条件、利用制限を含む。
モデルをダウンロード・配備する前に、運用地域と用途について現行ライセンスを再確認する。

## 一次資料

- https://huggingface.co/MiniMaxAI/MiniMax-H3
- https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE
- https://platform.minimax.io/docs/api-reference/video-generation-v2-create
- https://platform.minimax.io/docs/api-reference/video-generation-v2-query
