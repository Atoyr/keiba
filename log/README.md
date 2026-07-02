# 予測ログ（検証基盤）

予想の精度・回収率を定量検証するためのログ。**レースごとに必ず記録し、10〜20レース単位で集計→ルール・閾値の調整に使う。**

## ファイル構成

| ファイル | 粒度 | 記録タイミング |
|---|---|---|
| `races.csv` | 1レース = 1行 | 予想確定時に前半、結果確定後に後半を記入 |
| `predictions.csv` | 1頭 = 1行 | 予想確定時（最終版の印・点数）＋結果確定後に着順 |
| `bets.csv` | 1券種 = 1行 | 購入時＋結果確定後に払戻 |
| `rules_master.csv` | ルール台帳 | ルール追加時 |
| `rule_fires.csv` | 1レース×1ルール = 1行 | 振り返り時 |
| `analyze.py` | 集計スクリプト | 任意（python3 analyze.py で実行） |

## 記録の原則

1. **予想は「最終版」のみ記録する。** 途中の揺れは記録しない（軸の揺れ自体は rule_fires.csv の R06 で追跡）。
2. **予想確定時に predictions.csv を埋めてからレースを迎える。** 結果を見てから印を書くと検証にならない。
3. 不明な値は空欄のままにする。推測で埋めない。
4. 印は最終版の1頭1印。無印は `-`、消しは `x`。
5. **predictions.csv は原則、出走全頭を記録する。** 「無印で好走した馬」だけ後から書き足すと、無印・消しの成績が実際より悪く見えるバイアスが生じる（既走3レースのバックフィル分はこのバイアスを含むため、無印/消しの集計値は参考外とする）。

## 各CSVのカラム定義

### races.csv
- `race_id` : 一意ID（例 `2026_hakodate_kinen`）
- `date` / `race_name` / `grade` / `course`（例 `函館芝2000`）/ `field_size`
- `going` : 良/稍重/重/不良
- `cushion` : クッション値
- `pace_score_pre` : 枠順確定時のペーススコア（逃げ×2＋先行×1）
- `pace_flag_pre` : 事前フラグ（前残り/中立/前崩れ）
- `pace_actual` : 実際のペース（スロー/平均/ハイ）
- `pace_match` : 事前フラグと実際の一致（1/0）
- `bias_actual` : 当日実バイアス（内前/フラット/外差し）
- `result_1st` `result_2nd` `result_3rd` : 馬番
- `payout_sanrentan` / `payout_sanrenpuku` : 確定配当（円）
- `notes`

### predictions.csv
- `race_id` / `horse_no` / `horse_name`
- `mark` : ◎○▲△☆ / `-`（無印）/ `x`（消し）
- `base_score` : ベース点
- `composite_coef` : 合成係数（クリップ後）
- `additive_total` : 加算層合計
- `final_score` : 最終評価点
- `myomi_score` : 妙味スコア
- `popularity` : 人気
- `win_odds` / `place_odds_max` : 単勝／複勝上限
- `r_value` : R値
- `finish_pos` : 着順（結果後）
- `in_place` : 複勝圏内 1/0（結果後）
- `notes`

### bets.csv
- `race_id` / `bet_type`（三連複/三連単/馬連/ワイド等）
- `structure` : 買い目の構造（例 `軸1頭 5→12,13,6,8,2,9`）
- `points` : 点数 / `unit` : 単価 / `cost` : 購入額
- `hit` : 的中 1/0 / `return` : 払戻額
- `notes`

### rules_master.csv
- `rule_id` / `rule_name` / `origin_race`（このルールを生んだレース）/ `added_date`

### rule_fires.csv
- `race_id` / `rule_id`
- `fired` : 該当場面があったか 1/0
- `followed` : ルールに従ったか 1/0
- `outcome` : `効いた` / `逆効果` / `中立` / `違反して失敗` / `違反したが結果OK`
- `notes`

## 集計で見る指標（analyze.py が出力）

- 印別の複勝率・平均着順・単勝/複勝回収率（◎○▲△☆別）
- 券種別の回収率
- ペーススコア事前フラグの的中率
- R値帯別（R≧4.0 / 3.0-4.0 / <1.5）の複勝率
- ルール別の遵守率と成績

## 運用フロー

```
【予想確定時】races.csv 前半＋predictions.csv＋bets.csv（購入分）を追記
【結果確定後】着順・配当・払戻を記入 → rule_fires.csv を振り返りで記入
【コミット】UCHIYAMA が commit → Sync now
【10レース毎】python3 log/analyze.py で集計 → 閾値・係数の見直しをチャットで実施
```
