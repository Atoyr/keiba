# GitHub Pages 公開ルール v1

## 目的

予想ログの正本を `log/*.csv` に保ったまま、確定済み予想ボードを GitHub Pages で共有する。
公開HTMLは派生成果物であり、予想判断・集計・振り返りの入力には使わない。

## 正本と派生物

- 正本：`log/races.csv`、`log/predictions.csv`、`log/bets.csv`、`log/rule_fires.csv`
- 表示物：`docs/predictions/<race_id>/index.html`
- 一覧：`docs/index.html`
- HTMLとCSVが食い違う場合はCSVが正しい。
- HTMLからCSVへ値を戻してはいけない。

## 公開タイミング

1. `log/*.csv` の追記行を確定する。
2. `予想ボード_出力ルール.md` に従い、同じ `race_id`・同じ数値でHTMLを完成させる。
3. HTMLヘッダーに `race_id` を必ず表示する。
4. `tools/publish_board.py` を実行する。
5. `log/*.csv` と `docs/` の差分を同じコミットに含める。
6. PRをレビューして main へマージする。
7. GitHub Pages は main `/docs` を公開元とする。

CSV確定前にHTMLだけを先行公開しない。

## コマンド

```bash
python3 tools/publish_board.py \
  --race-id <race_id> \
  --source <完成済みrace-prediction.html>
```

## publish_board.py のゲート

- `race_id` が `log/races.csv` に存在する。
- 同じ `race_id` の行が `log/predictions.csv` に1件以上存在する。
- 入力HTMLが完全なHTML文書である。
- 入力HTML本文に同じ `race_id` が含まれる。

成功時だけ以下を更新する。

- `docs/predictions/<race_id>/index.html`
- `docs/index.html`
- `docs/.nojekyll`

## URL

- 一覧：`https://atoyr.github.io/keiba/`
- レース：`https://atoyr.github.io/keiba/predictions/<race_id>/`

## GitHub Pages 初回設定

`Settings` → `Pages` → `Build and deployment` → `Deploy from a branch`

- Branch: `main`
- Folder: `/docs`

## 禁止

- `docs/` を正本扱いする。
- HTMLだけを別コミットで更新してCSVと世代をずらす。
- `race_id` を表示用に別名へ変換する。
- `reflection/` やチャット記憶から公開数値を埋める。
