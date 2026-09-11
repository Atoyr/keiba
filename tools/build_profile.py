#!/usr/bin/env python3
"""
build_profile.py — 出走馬プロファイルの一括生成（Phase A-1）

netkeiba の「出馬表・5走表示」1ページから全頭の近5走（通過順・上がり・距離・馬場・着順・格・
斤量・騎手・馬体重）と父・母父・今回の騎手・斤量を取り、『脚質認定ルール.md』§2〜§5 と
末脚指数（ロードマップ v2 §5-2）を機械計算して、

  references/脚質認定_<日付>_<レース名>.md   … §6 書式（全頭）＋レース単位サマリー
  cache/profile/<slug>.json                 … mark.py / reflect_prep.py の入力

を書き出す。**当日オッズ・当日人気は取得も出力もしない**（ブラインド評価・v2 §4-3 判断6）。

使い方:
  python3 tools/build_profile.py --netkeiba-id 202609040311 --slug 2026_challenge_cup --name チャレンジカップ
  オプション:
    --no-agari      近5走のレースページを取りに行かず、上がり差（末脚指数）を「取得失敗」にする
    --allow-missing 必須列が欠けても生成する（既定は生成せず取得失敗で停止）
    --force         キャッシュを無視して取り直す

必須列（1頭でも欠けたら停止）: 父・母父・斤量・近5走の各走（距離/馬場/着順/格）。
騎手は枠順確定前なら「未定」を許す。取れない項は '不明' と書き、推測で埋めない。
"""
import argparse
import json
import os
import re
import statistics
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import polite_fetch  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TTL_SHUTUBA = 6 * 3600
TTL_RESULT = 365 * 24 * 3600

JRA = ["札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"]
LOCAL = ["大井", "川崎", "船橋", "浦和", "門別", "盛岡", "水沢", "金沢", "笠松", "名古屋",
         "園田", "姫路", "高知", "佐賀", "帯広", "ばんえい"]
# netkeiba の「大」＝大逃げ（逃と同じ horse_race_type01 アイコン）。紫苑S2026 以降の
# プロファイルと同じく主観=逃げとして数え、表示は（netkeiba表示=大逃げ）で残す。
KYAKU = {"逃": "逃げ", "大": "逃げ", "先": "先行", "差": "差し", "追": "追込", "自": "自在"}


# ---------- 取得・解析 ----------

def _decode(b):
    for enc in ("euc-jp", "utf-8", "cp932"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("euc-jp", "replace")


def _text(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", s).strip()


def fetch_html(url, ttl, force=False):
    return _decode(polite_fetch.fetch(url, ttl, force=force))


def parse_header(html):
    h = {}
    t = re.search(r"<title>(.*?)</title>", html, re.S)
    title = _text(t.group(1)) if t else ""
    h["title"] = title
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", title)
    h["date"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None
    m = re.search(r"日\s*(\S+?)(\d{1,2})R", title)
    h["course"] = m.group(1) if m else None
    h["race_no"] = int(m.group(2)) if m else None
    m = re.search(r"^(.*?)\s*5走表示", title)
    h["race_name"] = m.group(1).strip() if m else title
    d1 = re.search(r'RaceData01[^>]*>(.*?)</div>', html, re.S)
    d1 = _text(d1.group(1)) if d1 else ""
    m = re.search(r"(芝|ダ|障)\s*(\d{3,4})m", d1)
    h["surface"] = m.group(1) if m else None
    h["distance"] = int(m.group(2)) if m else None
    m = re.search(r"\((右|左|直)", d1)
    h["turn"] = m.group(1) if m else None
    d2 = re.search(r'RaceData02[^>]*>(.*?)</div>', html, re.S)
    h["conditions"] = _text(d2.group(1)) if d2 else ""
    m = re.search(r"(\d+)頭", h["conditions"])
    h["field_size"] = int(m.group(1)) if m else None
    h["handicap"] = "ハンデ" in h["conditions"]
    return h


def grade_of(name_html, place):
    name = _text(name_html)
    if re.search(r"Icon_GradeType1\b", name_html) or "GI" in name and "GII" not in name and "GIII" not in name:
        g = "G1"
    elif re.search(r"Icon_GradeType2\b", name_html) or "GII" in name and "GIII" not in name:
        g = "G2"
    elif re.search(r"Icon_GradeType3\b", name_html) or "GIII" in name:
        g = "G3"
    elif "(L)" in name or "リステッド" in name or "Icon_GradeType15" in name_html or "Icon_GradeType5" in name_html and "L" in name:
        g = "L"
    elif "新馬" in name:
        g = "新馬"
    elif "未勝利" in name:
        g = "未勝利"
    elif "1勝" in name:
        g = "1勝"
    elif "2勝" in name:
        g = "2勝"
    elif "3勝" in name:
        g = "3勝"
    elif place in JRA:
        g = "OP"
    else:
        g = "地方" if place in LOCAL else "海外"
    if place not in JRA and g in ("G1", "G2", "G3", "L", "OP"):
        g = "地方" if place in LOCAL else "海外"
    return g, re.sub(r"\s*(GI+|GIII|\(L\))\s*$", "", name).strip()


def parse_past(cell):
    """5走表示の1セル → dict。空セルは None。"""
    if "Data_Item" not in cell:
        return None
    p = {}
    m = re.search(r'Data01[^>]*>(.*?)</div>', cell, re.S)
    d01 = m.group(1) if m else ""
    m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})\s*([^\s<]+)", _text(d01))
    p["date"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
    p["place"] = m.group(4) if m else "不明"
    m = re.search(r'class="Num"[^>]*>(.*?)</span>', d01, re.S)
    fin = _text(m.group(1)) if m else ""
    p["finish"] = fin
    m = re.search(r'Data02[^>]*>(.*?)</div>', cell, re.S)
    d02 = m.group(1) if m else ""
    m = re.search(r'db\.netkeiba\.com/race/(\d{12})', d02)
    p["race_id"] = m.group(1) if m else None
    p["grade"], p["race_name"] = grade_of(d02, p["place"])
    m = re.search(r'Data05[^>]*>(.*?)</div>', cell, re.S)
    d05 = _text(m.group(1)) if m else ""
    m = re.search(r"(芝|ダ|障)\s*(\d{3,4})", d05)
    p["surface"] = m.group(1) if m else "不明"
    p["distance"] = int(m.group(2)) if m else None
    m = re.search(r"(\d:\d\d\.\d)", d05)
    p["time"] = m.group(1) if m else None
    m = re.search(r"(稍重|不良|良|稍|重|不)$", d05)
    p["going"] = {"稍": "稍重", "不": "不良"}.get(m.group(1), m.group(1)) if m else "不明"
    m = re.search(r'Data03[^>]*>(.*?)</div>', cell, re.S)
    d03 = _text(m.group(1)) if m else ""
    m = re.search(r"(\d+)頭", d03)
    p["field"] = int(m.group(1)) if m else None
    m = re.search(r"(\d+)番", d03)
    p["no"] = int(m.group(1)) if m else None
    m = re.search(r"(\d+)人", d03)
    p["pop"] = int(m.group(1)) if m else None
    m = re.search(r"人\s*(\S+)\s*([\d.]+)\s*$", d03)
    p["jockey"] = m.group(1) if m else None
    p["weight_carried"] = float(m.group(2)) if m else None
    m = re.search(r'Data06[^>]*>(.*?)</div>', cell, re.S)
    d06 = _text(m.group(1)) if m else ""
    m = re.search(r"^([\d\-]+)", d06)
    p["passing"] = m.group(1) if m and "-" in m.group(1) or (m and m.group(1).isdigit()) else None
    m = re.search(r"\(([\d.]+)\)", d06)
    p["agari"] = float(m.group(1)) if m else None
    m = re.search(r"(\d{3})\(([+\-]?\d+)\)", d06)
    p["body_weight"] = int(m.group(1)) if m else None
    p["body_diff"] = int(m.group(2)) if m else None
    m = re.search(r'Data07[^>]*>(.*?)</div>', cell, re.S)
    p["margin_ref"] = _text(m.group(1)) if m else ""
    return p


def parse_rows(html):
    horses = []
    rows = re.findall(r'<tr class="HorseList"[^>]*>(.*?)</tr>', html, re.S)
    for idx, row in enumerate(rows, 1):
        h = {"row": idx}
        tds = re.findall(r'<td class="Waku[^"]*"[^>]*>(.*?)</td>', row, re.S)
        h["waku"] = int(_text(tds[0])) if len(tds) > 0 and _text(tds[0]).isdigit() else None
        h["no"] = int(_text(tds[1])) if len(tds) > 1 and _text(tds[1]).isdigit() else None
        m = re.search(r'Horse01[^>]*>(.*?)</div>', row, re.S)
        h["sire"] = _text(m.group(1)) if m else ""
        m = re.search(r'db\.netkeiba\.com/horse/(\d+)[^>]*>\s*([^<]+?)\s*</a>', row)
        h["horse_id"] = m.group(1) if m else None
        h["name"] = m.group(2).strip() if m else ""
        if not h["horse_id"]:
            continue  # 馬名リンクの無い行（除外・取消・空行）は対象外
        m = re.search(r'Horse03[^>]*>(.*?)</div>', row, re.S)
        h["dam"] = _text(m.group(1)) if m else ""
        m = re.search(r'Horse04[^>]*>(.*?)</div>', row, re.S)
        h["bms"] = _text(m.group(1)).strip("()（）") if m else ""
        m = re.search(r'Horse05[^>]*>(.*?)</div>', row, re.S)
        h["trainer"] = _text(m.group(1)) if m else ""
        m = re.search(r'class="kyakusitu">(.*?)</span>\s*([^<]*)', row, re.S)
        h["subj_raw"] = _text(m.group(1)) if m else ""
        h["subj_style"] = KYAKU.get(h["subj_raw"], h["subj_raw"]) if m else "未設定"
        h["interval"] = _text(m.group(2)) if m else ""
        m = re.search(r'class="Barei">(.*?)</span>', row, re.S)
        h["sex_age"] = _text(m.group(1)) if m else ""
        jk = re.search(r'<td class="Jockey">(.*?)</td>', row, re.S)
        jk = jk.group(1) if jk else ""
        m = re.search(r'jockey/result/recent/\d+[^>]*>\s*([^<]+?)\s*</a>', jk)
        h["jockey"] = m.group(1).strip() if m else "未定"
        m = re.search(r'<span>\s*([\d.]+)\s*</span>', jk)
        h["weight_carried"] = float(m.group(1)) if m else None
        m = re.search(r'class="Weight[^"]*">\s*(\d{3})kg', row)
        h["last_body_weight"] = int(m.group(1)) if m else None
        cells = re.findall(r'<td class="Past[^"]*"[^>]*>(.*?)</td>', row, re.S)
        h["runs"] = [p for p in (parse_past(c) for c in cells) if p]
        h["rest_cells"] = len(re.findall(r'<td class="Rest', row))  # 休養セルは5走表示の1枠を占める
        horses.append(h)
    return horses


def parse_race_agari(html):
    """db.netkeiba.com/race/<id> の結果表 → {horse_id: 上がり}"""
    i = html.find("race_table_01")
    if i < 0:
        return {}
    seg = html[i:]
    seg = seg[:seg.find("</table>")]
    ths = [_text(x) for x in re.findall(r"<th[^>]*>(.*?)</th>", seg, re.S)]
    try:
        agari_idx = ths.index("上り")
    except ValueError:
        return {}
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", seg, re.S)[1:]:
        tr = re.sub(r"</?diary_snap_cut>", "", tr)
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) <= agari_idx:
            continue
        m = re.search(r"/horse/(\d+)", tr)
        v = _text(tds[agari_idx])
        if m and re.match(r"^\d\d\.\d$", v):
            out[m.group(1)] = float(v)
    return out


# ---------- 脚質認定 §2〜§5 ----------

def rel_pos(passing, field):
    if not passing or not field or field < 2:
        return None, None
    nums = [int(x) for x in passing.split("-") if x.isdigit()]
    if not nums:
        return None, None
    return (nums[-1] - 1) / (field - 1), nums[0]


def quartiles(v):
    s = sorted(v)
    n = len(s)
    if n < 2:
        return s[0], s[0]
    def q(p):
        k = (n - 1) * p
        f = int(k)
        c = min(f + 1, n - 1)
        return s[f] + (s[c] - s[f]) * (k - f)
    return q(0.25), q(0.75)


def classify(h, today):
    rs, hana, recent3_hana = [], 0, 0
    for i, r in enumerate(h["runs"]):
        rp, first = rel_pos(r.get("passing"), r.get("field"))
        r["r"] = round(rp, 3) if rp is not None else None
        r["hana"] = (first == 1) if first is not None else None
        if rp is not None:
            rs.append(rp)
            if first == 1:
                hana += 1
                if i < 3:
                    recent3_hana += 1
    h["r_list"] = rs
    h["hana_count"] = hana
    h["n_runs_r"] = len(rs)
    if len(rs) < 3:
        h["style"] = "未確定"
        h["r_med"] = round(statistics.median(rs), 3) if rs else None
        h["iqr"] = None
        h["jizai"] = False
    else:
        med = statistics.median(rs)
        q1, q3 = quartiles(rs)
        h["r_med"] = round(med, 3)
        h["iqr"] = round(q3 - q1, 3)
        if (hana >= 2 or recent3_hana >= 1) and med <= 0.15:
            st = "逃げ"
        elif med <= 0.35:
            st = "先行"
        elif med <= 0.70:
            st = "差し"
        else:
            st = "追込"
        h["style"] = st
        h["jizai"] = h["iqr"] >= 0.30
    h["hana_rate"] = round(hana / 5, 2)
    n = today.get("field_size") or 0
    h["sim_pos"] = round(h["r_med"] * (n - 1) + 1, 1) if h["r_med"] is not None and n else None
    # 注記（§4：除外しない・注記のみ）
    notes = []
    d0 = today.get("distance")
    for r in h["runs"]:
        tag = []
        if d0 and r.get("distance") and abs(r["distance"] - d0) >= 400:
            tag.append("距離差±400以上")
        if r.get("going") == "不良":
            tag.append("不良")
        if r.get("surface") and today.get("surface") and r["surface"] != today["surface"]:
            tag.append(f"{r['surface']}→{today['surface']}")
        if r.get("place") not in JRA:
            tag.append("地方/海外")
        if r.get("passing") is None:
            tag.append("通過順不明")
        r["notes"] = tag
        if tag:
            notes.append(f"{r.get('date','?')[5:] if r.get('date') else '?'}:{'/'.join(tag)}")
    # 休養
    try:
        ds = [datetime.strptime(r["date"], "%Y-%m-%d") for r in h["runs"] if r.get("date")]
        if ds and today.get("date"):
            gap = (datetime.strptime(today["date"], "%Y-%m-%d") - ds[0]).days
            if gap >= 180:
                notes.append(f"休養{gap}日")
    except ValueError:
        pass
    if len(h["runs"]) < 5:
        notes.append(f"取得{len(h['runs'])}走（それ以前は未取得"
                     + (f"・netkeiba休養セル{h['rest_cells']}枠" if h.get("rest_cells") else "") + "）")
    h["notes"] = notes
    # 馬場別・距離帯経験
    agg = {"良": [0, 0, 0, 0], "稍重": [0, 0, 0, 0], "重不": [0, 0, 0, 0]}
    for r in h["runs"]:
        g = r.get("going")
        key = "良" if g == "良" else ("稍重" if g == "稍重" else ("重不" if g in ("重", "不良") else None))
        if key and r.get("finish", "").isdigit():
            f = int(r["finish"])
            agg[key][min(f, 4) - 1] += 1
    h["going_agg"] = agg
    h["dist_exp"] = any(r.get("distance") and d0 and abs(r["distance"] - d0) <= 200 for r in h["runs"])
    # 末脚指数
    diffs, ranks = [], []
    for r in h["runs"]:
        if r.get("agari") is None or r.get("best_agari") is None:
            continue
        if r.get("surface") == "芝" and (r.get("field") or 99) <= 10 and r["best_agari"] > 34.0:
            r["agari_excluded"] = True
            continue
        diffs.append(round(r["agari"] - r["best_agari"], 1))
        if r.get("agari_rank"):
            ranks.append(r["agari_rank"])
    h["agari_diff_med"] = round(statistics.median(diffs), 2) if diffs else None
    h["agari_top3"] = sum(1 for k in ranks if k <= 3)
    h["agari_n"] = len(diffs)
    h["agari_backed"] = (h["agari_diff_med"] is not None and h["agari_diff_med"] <= 0.3) or h["agari_top3"] >= 2
    # 継続/乗替
    prev = h["runs"][0].get("jockey") if h["runs"] else None
    same = bool(prev) and (prev.startswith(h["jockey"]) or h["jockey"].startswith(prev))  # 出馬表側は省略表記
    h["jockey_change"] = "未定" if h["jockey"] == "未定" else ("継続" if same else "乗替")
    if h["last_body_weight"] is None and h["runs"] and h["runs"][0].get("body_weight"):
        h["last_body_weight"] = h["runs"][0]["body_weight"]
    return h


# ---------- 出力 ----------

def fmt_runs_tag(h):
    parts = []
    for r in h["runs"]:
        fin = r.get("finish") or "不明"
        fin = f"{fin}着" if fin.isdigit() else fin
        parts.append(f"{r.get('distance') or '不明'}:{r.get('going') or '不明'}:{fin}:{r.get('grade') or '不明'}")
    return ";".join(parts) if parts else "なし"


def render_md(today, horses, slug, agari_mode):
    L = []
    L.append(f"# 出走馬プロファイル {today['date']} {today['race_name']}（{today.get('course')} {today.get('surface')}{today.get('distance')}m・{'ハンデ' if today.get('handicap') else '別定/馬齢'}）")
    L.append("")
    L.append(f"**作成日：** {datetime.now().strftime('%Y-%m-%d')}（`tools/build_profile.py`・netkeiba 5走表示 race_id={today['netkeiba_id']}）")
    L.append("**正本：** 『脚質認定ルール.md』v2 §1・§1-b・§2〜§6。末脚指数はロードマップ v2 §5-2")
    L.append(f"**レース条件：** {today.get('conditions') or '取得失敗'}／発走・馬場は出馬表を参照")
    L.append("**当日オッズ・当日人気は本ファイルに載せない（ブラインド評価）。R値・妙味スコアは mark.py がオッズを別途読んで算出する。**")
    if any(h["no"] is None for h in horses):
        L.append("**枠順未確定：** 馬番は netkeiba の並び順の仮番。枠順確定後に再実行して差し替える。")
    L.append("")
    L.append("---")
    L.append("")
    # サマリー
    unk = [h for h in horses if h["style"] == "未確定"]
    known = [h for h in horses if h["style"] != "未確定"]
    mism = [h for h in known if h["subj_style"] not in ("未設定", "自在") and h["subj_style"] != h["style"]]
    nige = [h for h in known if h["style"] == "逃げ"]
    senko = [h for h in known if h["style"] == "先行"]
    subj = {k: sum(1 for h in horses if h["subj_style"] == k) for k in ("逃げ", "先行", "差し", "追込", "自在", "未設定")}
    ps_m = 2 * len(nige) + len(senko)
    ps_s = 2 * subj["逃げ"] + subj["先行"]
    L.append("## 0. レース単位サマリー（機械計算のみ・判断は工程側）")
    L.append("")
    L.append(f"- 出走 {len(horses)} 頭／機械認定 {len(known)} 頭／未確定 {len(unk)} 頭"
             + ("：" + "、".join(f"#{h['no'] or h['row']} {h['name']}（取得{h['n_runs_r']}走）" for h in unk) if unk else ""))
    short = [h for h in horses if len(h["runs"]) < 5]
    if short:
        L.append("- 近5走の取得が5走未満（未取得の走は推測で埋めない）：" + "、".join(
            f"#{h['no'] or h['row']} {h['name']}（{len(h['runs'])}走{'・休養セル' + str(h['rest_cells']) if h.get('rest_cells') else ''}）" for h in short))
    L.append(f"- `脚質不一致={len(mism)}/{len(known)}`（主観＝netkeiba 表示。未確定・自在・未設定は分母に含めない）"
             + ("：" + "、".join(f"#{h['no'] or h['row']} 機械{h['style']}/主観{h['subj_style']}" for h in mism) if mism else ""))
    L.append(f"- 逃げ認定 {len(nige)} 頭"
             + ("：" + "、".join(f"#{h['no'] or h['row']} {h['name']}（単独ハナ成功率 {h['hana_rate']:.2f}）" for h in nige) if nige else "（不在）")
             + f"／先行認定 {len(senko)} 頭")
    hana_others = [h for h in known if h["style"] != "逃げ" and h["hana_count"] >= 1]
    if hana_others:
        L.append("- 逃げ認定以外でハナ実績あり：" + "、".join(f"#{h['no'] or h['row']} {h['name']}（{h['hana_count']}/5・{h['style']}）" for h in hana_others))
    L.append(f"- ペーススコア：機械＝{ps_m}（逃げ{len(nige)}×2＋先行{len(senko)}）／主観＝{ps_s}（逃げ{subj['逃げ']}×2＋先行{subj['先行']}）／差＝{ps_s - ps_m:+d}")
    if today.get("field_size"):
        L.append(f"- ペース密度＝{ps_m / today['field_size']:.2f}（機械スコア÷頭数・仮説6）")
    jz = [h for h in known if h["jizai"]]
    if jz:
        L.append("- 自在フラグ：" + "、".join(f"#{h['no'] or h['row']} {h['name']}" for h in jz))
    nodist = [h for h in horses if not h["dist_exp"]]
    if nodist:
        L.append("- `当該距離帯経験=なし`（R38 対象）：" + "、".join(f"#{h['no'] or h['row']} {h['name']}" for h in nodist))
    backed = [h for h in horses if h["agari_backed"]]
    L.append(f"- 末脚指数「裏付けあり」 {len(backed)} 頭" + ("：" + "、".join(f"#{h['no'] or h['row']} {h['name']}" for h in backed) if backed else "")
             + ("" if agari_mode else "（--no-agari のため上がり差は取得失敗・上がり順位のみ）"))
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 1. 全頭（§6 書式）")
    L.append("")
    for h in horses:
        no = h["no"] if h["no"] is not None else f"仮{h['row']}"
        rl = "[" + ", ".join(f"{x:.2f}" for x in h["r_list"]) + "]"
        L.append(f"#{no} {h['name']}｜4角r {rl} r_med={h['r_med'] if h['r_med'] is not None else '不明'} IQR={h['iqr'] if h['iqr'] is not None else '不明'}")
        mk = "機械=未確定（取得%d走）" % h["n_runs_r"] if h["style"] == "未確定" else f"機械={h['style']}{'（自在）' if h['jizai'] else ''}"
        agree = "" if h["style"] == "未確定" or h["subj_style"] in ("未設定", "自在") else ("／ 一致" if h["subj_style"] == h["style"] else "／ ★不一致")
        raw = "（netkeiba表示=大逃げ）" if h.get("subj_raw") == "大" else ""
        L.append(f"　　単独ハナ {h['hana_count']}/5 → {mk} ／ 主観={h['subj_style']} {agree}{raw}")
        L.append(f"　　5走距離帯={fmt_runs_tag(h)}")
        L.append(f"　　父={h['sire'] or '取得失敗'} ／ 母父={h['bms'] or '取得失敗'} ／ 斤量={h['weight_carried'] if h['weight_carried'] is not None else '取得失敗'} ／ 騎手={h['jockey']}（{h['jockey_change']}）")
        a = h["going_agg"]
        L.append(f"　　馬場別=良[{'-'.join(map(str, a['良']))}]・稍重[{'-'.join(map(str, a['稍重']))}]・重不[{'-'.join(map(str, a['重不']))}] ／ 当該距離帯経験={'あり' if h['dist_exp'] else 'なし'}")
        ad = f"{h['agari_diff_med']:.2f}秒（n={h['agari_n']}）" if h["agari_diff_med"] is not None else "取得失敗"
        L.append(f"　　末脚=上がり差中央値 {ad} ／ 上がり3位以内 {h['agari_top3']}/{len(h['runs'])} ／ {'裏付けあり' if h['agari_backed'] else '裏付けなし'} ／ 想定4角={h['sim_pos'] if h['sim_pos'] is not None else '不明'}")
        L.append(f"　　性齢={h['sex_age']} ／ 前走馬体重={h['last_body_weight'] or '不明'} ／ 間隔={h['interval'] or '不明'}"
                 + (f" ／ 注記={'; '.join(h['notes'])}" if h["notes"] else ""))
        L.append("")
    L.append("---")
    L.append("")
    L.append("## 2. 下流への申し送り（機械）")
    L.append("")
    L.append("- 工程4 ペーススコアは機械認定側の値を使う。主観との差は notes に残す")
    if unk:
        L.append("- 未確定馬は逃げ・先行カウントに入れていない。「未確定馬が逃げた場合」を展開2パターンのどちらかに必ず含める（§5）")
    if nige and len(nige) == 1 and nige[0]["hana_rate"] >= 0.4:
        L.append(f"- 逃げ認定1頭×成功率≧0.4 → R04 の「単騎」宣言条件を形式的に満たす（良馬場要件は工程3で確認）")
    if len(nige) + len(hana_others) >= 2:
        L.append("- ハナ実績馬が2頭以上 → R39 の4項目（内外関係・必逃性・主張実績・先手後の競り込み）を工程5で記録する")
    L.append(f"- JSON：`cache/profile/{slug}.json`（mark.py・reflect_prep.py の入力）")
    L.append("")
    return "\n".join(L) + "\n"


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description="出走馬プロファイルの一括生成")
    ap.add_argument("--netkeiba-id", required=True, help="netkeiba race_id（12桁）")
    ap.add_argument("--slug", required=True, help="log/races.csv の race_id（例 2026_challenge_cup）")
    ap.add_argument("--name", required=True, help="ファイル名に使うレース名（例 チャレンジカップ）")
    ap.add_argument("--no-agari", action="store_true")
    ap.add_argument("--allow-missing", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--out-dir", default=os.path.join(BASE, "references"))
    a = ap.parse_args()

    url = f"https://race.netkeiba.com/race/shutuba_past.html?race_id={a.netkeiba_id}"
    html = fetch_html(url, TTL_SHUTUBA, a.force)
    today = parse_header(html)
    today["netkeiba_id"] = a.netkeiba_id
    horses = parse_rows(html)
    if not horses:
        print("[取得失敗] 出走馬の行が読めない（出馬表未公開か書式変更）", file=sys.stderr)
        sys.exit(2)
    if today.get("field_size") is None:
        today["field_size"] = len(horses)

    # 近5走のレースページ → 最速上がり・上がり順位
    if not a.no_agari:
        seen = {}
        for h in horses:
            for r in h["runs"]:
                rid = r.get("race_id")
                if not rid or r.get("place") not in JRA:
                    continue
                if rid not in seen:
                    try:
                        seen[rid] = parse_race_agari(fetch_html(f"https://db.netkeiba.com/race/{rid}/", TTL_RESULT, a.force))
                    except Exception as e:  # 取得失敗はそのまま記録
                        print(f"[取得失敗] race {rid}: {e}", file=sys.stderr)
                        seen[rid] = {}
                table = seen[rid]
                if table:
                    vals = sorted(table.values())
                    r["best_agari"] = vals[0]
                    mine = table.get(h["horse_id"])
                    r["agari_rank"] = (vals.index(mine) + 1) if mine is not None else None

    for h in horses:
        classify(h, today)

    # 必須列チェック
    missing = []
    confirmed = all(h["no"] is not None for h in horses)
    for h in horses:
        lab = f"#{h['no'] or '仮' + str(h['row'])} {h['name']}"
        if not h["sire"] or not h["bms"]:
            missing.append(f"{lab}: 父/母父")
        if h["weight_carried"] is None:
            missing.append(f"{lab}: 斤量")
        if h["jockey"] == "未定" and confirmed:
            missing.append(f"{lab}: 騎手")
        for r in h["runs"]:
            if not (r.get("finish") or "").isdigit():
                continue  # 取消・除外・中止は着順欄にそのまま残す（§6）
            if r.get("distance") is None or r.get("going") == "不明" or not r.get("grade"):
                missing.append(f"{lab}: 近走 {r.get('date')} の距離/馬場/着順/格")
    if missing and not a.allow_missing:
        print("[取得失敗] 必須列が欠けているため生成しない（--allow-missing で強制可）:", file=sys.stderr)
        for m in missing:
            print("  " + m, file=sys.stderr)
        sys.exit(3)

    out_md = os.path.join(a.out_dir, f"脚質認定_{today['date']}_{a.name}.md")
    os.makedirs(os.path.join(BASE, "cache", "profile"), exist_ok=True)
    out_json = os.path.join(BASE, "cache", "profile", f"{a.slug}.json")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(render_md(today, horses, a.slug, not a.no_agari))
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"race": today, "slug": a.slug, "generated": datetime.now().isoformat(timespec="seconds"),
                   "horses": horses, "missing": missing}, f, ensure_ascii=False, indent=1)
    print(f"→ {out_md}")
    print(f"→ {out_json}")
    print(f"頭数 {len(horses)}／未確定 {sum(1 for h in horses if h['style']=='未確定')}／欠損 {len(missing)}{'（強制生成）' if missing else ''}")


if __name__ == "__main__":
    main()
