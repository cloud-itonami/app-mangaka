# MiniMax H3 — Hugging Face調査メモ

調査日: 2026-08-23

## 同定

- 公式リポジトリ: `MiniMaxAI/MiniMax-H3`
- Murakumoモデル名: `minimax-h3`
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

`lg-video-gen`はKotoba Labsのセルフホスト実行基盤 `api.murakumo.cloud` を既定経路にする。
2026-08-23に公開generation catalogで、動画の既定が`minimax-h3`、状態が`verified`、
text/image/reference-to-videoと音声が有効であることを確認した。リクエストは
`POST /api/v1/generation`、状態確認は`GET /api/v1/generation/jobs/{jobId}`、
成果物取得は認証付き`GET /api/v1/generation/jobs/{jobId}/artifact`を使う。

Hugging Faceはモデルの同定・能力・ライセンスの一次資料であり、制作時の実行先ではない。
MiniMax公式V2 APIにも対応能力はあるが、このワークフローの既定経路にはしない。

## 1場面を1分予算に収める設定

Murakumoの実測点は640×640・175フレーム・20 stepsで6038秒、
640×640・124フレーム・20 stepsで2284秒。`one-minute`プリセットは
512×512・22フレーム・1 stepへ縮小する。前者からフレーム数・steps・画素数を
線形換算するとウォーム計算は約24.3秒になる。

これは実測済み品質や60秒SLAではない。キュー、モデルのコールドロード、成果物転送を
含まず、1 stepの画質も未認定である。したがって1動画を1場面・3コマに限定し、
品質が必要な場面は`--preset quality`へ戻す。

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
- https://api.murakumo.cloud/api/v1/generation/catalog
- https://platform.minimax.io/docs/api-reference/video-generation-v2-create
- https://platform.minimax.io/docs/api-reference/video-generation-v2-query
