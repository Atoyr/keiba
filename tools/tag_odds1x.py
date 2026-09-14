#!/usr/bin/env python3
"""
tag_odds1x.py — predictions.csv の base_breakdown に過去走の単勝1倍台タグを付ける（仮説32・記録専用）

  1倍台=自身n;先着n;未取得n;走数n

自身＝近5走で自身が単勝1倍台（2.0倍未満）だった走数／先着＝1倍台の他馬に先着した走数／
未取得＝結果表に自馬が無かった走数（地方・海外・取得失敗）／走数＝プロファイルの取得走数。
入力は cache/profile/<slug>.json（build_profile.py の生成物）と、その生成時に取得済みの
過去走結果ページ（db.netkeiba.com/race/<id>/・TTL 365日のキャッシュ）。JSON に `odds1x` が無い
（2026-09-15 より前に生成した）プロファイルは、同じキャッシュから数え直す。

**振り返りV6で実行する。** 過去の単勝オッズも市場の評価なので、予想工程（評価前）には出さない
（ブラインド評価）。base・印・妙味の根拠に使うかは仮説32の判定後に #設計 で決める。
JSON が無い／馬番の馬名が predictions.csv と一致しない馬は `1倍台=取得失敗`（推測で埋めない）。

使い方:
  python3 tools/tag_odds1x.py --slug 2026_st_lite_kinen [--dry-run]
"""
import argparse
import csv
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_profile as bp  # noqa: E402

TAG_RE = re.compile(r"\s*/\s*1倍台=\S*")


def norm(s):
    return re.sub(r"\s+", "", s or "")


def counts_for(h, cache):
    if h.get("odds1x"):
        return h["odds1x"]
    for r in h.get("runs", []):
        rid = r.get("race_id")
        if not rid or r.get("place") not in bp.JRA or rid in cache:
            continue
        try:
            cache[rid] = bp.parse_race_odds(bp.fetch_html(f"https://db.netkeiba.com/race/{rid}/", bp.TTL_RESULT))
        except Exception as e:  # 取得失敗はそのまま未取得として数える
            print(f"[取得失敗] race {rid}: {e}", file=sys.stderr)
            cache[rid] = {}
    return bp.odds1x_counts(h, cache)


def main():
    ap = argparse.ArgumentParser(description="predictions.csv に 1倍台= タグを付ける（仮説32・記録専用）")
    ap.add_argument("--slug", required=True, help="log/races.csv の race_id")
    ap.add_argument("--dry-run", action="store_true", help="書き込まずに表示だけ")
    a = ap.parse_args()

    pj = os.path.join(bp.BASE, "cache", "profile", f"{a.slug}.json")
    horses = {}
    if os.path.exists(pj):
        prof = json.load(open(pj, encoding="utf-8"))
        horses = {str(h["no"]): h for h in prof["horses"] if h.get("no") is not None}
    else:
        print(f"[取得失敗] {pj} が無い（全頭 1倍台=取得失敗 で記録）", file=sys.stderr)

    pp = os.path.join(bp.BASE, "log", "predictions.csv")
    raw = open(pp, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(nl)
    header = next(csv.reader([lines[0]]))
    cache, out, n = {}, [], 0
    for ln in lines:
        if not ln.startswith(a.slug + ","):
            out.append(ln)
            continue
        rec = dict(zip(header, next(csv.reader([ln]))))
        h = horses.get(rec["horse_no"])
        why = ""
        if h is None:
            val, why = "取得失敗", "プロファイルに馬番なし"
        elif norm(h.get("name")) != norm(rec["horse_name"]):
            val, why = "取得失敗", f"馬名不一致（プロファイル={h.get('name')}）"
        else:
            val = bp.odds1x_tag(counts_for(h, cache))
        rec["base_breakdown"] = TAG_RE.sub("", rec["base_breakdown"]) + f" / 1倍台={val}"
        buf = io.StringIO()
        csv.writer(buf, lineterminator="").writerow([rec[k] for k in header])
        out.append(buf.getvalue())
        n += 1
        print(f"#{rec['horse_no']:>2} {rec['horse_name']}: 1倍台={val}" + (f"（{why}）" if why else ""))
    if n == 0:
        sys.exit(f"[ERROR] predictions.csv に {a.slug} の行が無い")
    if a.dry_run:
        print("（--dry-run のため書き込みなし）")
        return
    with open(pp, "w", encoding="utf-8", newline="") as f:
        f.write(nl.join(out))
    print(f"→ {pp} の {n} 行に 1倍台= を付与（validate.py で確認）")


if __name__ == "__main__":
    main()
