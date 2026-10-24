#!/usr/bin/env python3
"""
plan_check.py — `#準備` が保存した収録計画 `plan/<週>.json` の検査と、`#収集` の実行順の算出

  検査   : python3 tools/plan_check.py plan/2026-W44.json
  実行順 : python3 tools/plan_check.py plan/2026-W44.json --race アルテミスS [--json]

検査（ERROR＝`#収集` に進まない）:
  - ノード id の重複・書式（ブランチ名に使うので [a-z0-9-] のみ）・必須キー欠落
  - depends_on の参照先が無い・循環
  - 同じファイルに書く2ノードに順序が無い（並列で追記すると番号と末尾追記が衝突する → depends_on を足す）
  - 番号予約（reserve）の重複・未マージのノードが予約した番号が既に見出しとして存在する

実行順（--race）:
  対象レースのノード＋未マージの祖先ノードを集め、依存でつながる塊ごとに1本の鎖へ直列化する。
  鎖の先頭は base=main、2番目以降は直前ノードのブランチを base にする（派生PR）。
  1本の鎖を1エージェントが担当する（ロードマップ §4-2 ガードレール6・同時起動は3まで）。
  PR の状態は gh で取る。MERGED は除外、OPEN は「既存PR」として base にだけ使う。
  gh が使えなければ「gh取得失敗」と出し、全ノードを未着手として扱う（推測で済みにしない）。
"""
import argparse
import json
import os
import re
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
REQUIRED = ("id", "race", "kinds", "files", "depends_on", "prompt")
MAX_CHAINS = 3


def branch_of(week, node_id):
    return f"claude/collect-{week.lower()}-{node_id}"


def pr_state(branch):
    """(state, url)。PR 無しは (None, None)。gh 失敗は例外。"""
    out = subprocess.run(
        ["gh", "pr", "list", "--head", branch, "--state", "all", "--json", "state,url", "--limit", "1"],
        cwd=BASE, capture_output=True, text=True, check=True).stdout
    rows = json.loads(out or "[]")
    return (rows[0]["state"], rows[0]["url"]) if rows else (None, None)


def ancestors(nodes, nid, seen=None):
    seen = set() if seen is None else seen
    for d in nodes[nid]["depends_on"]:
        if d in nodes and d not in seen:
            seen.add(d)
            ancestors(nodes, d, seen)
    return seen


def check(plan, states):
    errors = []
    order = [n.get("id") for n in plan.get("nodes", [])]
    nodes = {}
    for n in plan.get("nodes", []):
        miss = [k for k in REQUIRED if k not in n]
        if miss:
            errors.append(f"{n.get('id', '?')}: 必須キー欠落 {','.join(miss)}")
            continue
        if not ID_RE.match(n["id"]):
            errors.append(f"{n['id']}: id は [a-z0-9-] のみ（ブランチ名に使う）")
        if n["id"] in nodes:
            errors.append(f"{n['id']}: id が重複")
        nodes[n["id"]] = n
    if not plan.get("week"):
        errors.append("week が無い（例 2026-W44）")
    for nid, n in nodes.items():
        for d in n["depends_on"]:
            if d not in nodes:
                errors.append(f"{nid}: depends_on の {d} が計画に無い")
    # 循環
    state = {}

    def visit(nid, path):
        if state.get(nid) == 1:
            errors.append("循環: " + " → ".join(path + [nid]))
            return
        if state.get(nid) == 2:
            return
        state[nid] = 1
        for d in nodes[nid]["depends_on"]:
            if d in nodes:
                visit(d, path + [nid])
        state[nid] = 2

    for nid in nodes:
        visit(nid, [])
    if errors:
        return nodes, order, errors
    # 同一ファイルの順序
    anc = {nid: ancestors(nodes, nid) for nid in nodes}
    ids = list(nodes)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            shared = set(nodes[a]["files"]) & set(nodes[b]["files"])
            if shared and a not in anc[b] and b not in anc[a]:
                errors.append(f"{a} と {b} が同じファイル（{', '.join(sorted(shared))}）に書くのに順序が無い → depends_on を足す")
    # 番号予約
    taken = {}
    for nid, n in nodes.items():
        for f, num in (n.get("reserve") or {}).items():
            key = (f, num)
            if key in taken:
                errors.append(f"{nid} と {taken[key]} が {f} の {num} を二重予約")
            taken[key] = nid
            if states.get(nid, (None,))[0] in ("OPEN", "MERGED"):
                continue
            path = os.path.join(BASE, f)
            if not os.path.exists(path):
                errors.append(f"{nid}: 予約先 {f} が無い")
                continue
            with open(path, encoding="utf-8") as fh:
                if re.search(r"^#{2,3}\s*%s[\s　]" % re.escape(num), fh.read(), re.M):
                    errors.append(f"{nid}: {f} の {num} は既に見出しがある（予約し直す）")
    return nodes, order, errors


def chains_for(plan, nodes, order, states, race):
    week = plan["week"]
    target = {nid for nid, n in nodes.items() if race in n["race"]}
    if not target:
        return None
    scope = set()
    for nid in target:
        for x in {nid} | ancestors(nodes, nid):
            if states.get(x, (None,))[0] != "MERGED":
                scope.add(x)
    # 依存でつながる塊（無向）
    parent = {x: x for x in scope}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for x in scope:
        for d in nodes[x]["depends_on"]:
            if d in scope:
                parent[find(x)] = find(d)
    groups = {}
    for x in order:
        if x in scope:
            groups.setdefault(find(x), []).append(x)
    chains = []
    for members in groups.values():
        # 計画順を保った位相整列
        done, seq = set(), []
        while len(seq) < len(members):
            for x in members:
                if x not in done and all(d in done or d not in scope for d in nodes[x]["depends_on"]):
                    done.add(x)
                    seq.append(x)
                    break
        steps, prev = [], None
        for x in seq:
            st, url = states.get(x, (None, None))
            br = branch_of(week, x)
            steps.append({
                "id": x, "branch": br, "base": prev or "main",
                "action": "既存PR" if st == "OPEN" else "作成",
                "pr": url if st == "OPEN" else None,
                "gate": nodes[x].get("gate"), "files": nodes[x]["files"],
                "reserve": nodes[x].get("reserve") or {}, "prompt": nodes[x]["prompt"],
                "in_target": x in target,
            })
            prev = br
        if any(s["action"] == "作成" for s in steps):
            chains.append(steps)
    return chains


def main():
    ap = argparse.ArgumentParser(description="収録計画 plan/<週>.json の検査と #収集 の実行順")
    ap.add_argument("plan")
    ap.add_argument("--race", help="#収集 の対象レース名（計画の race と完全一致）")
    ap.add_argument("--json", action="store_true", help="実行順を JSON で出す")
    a = ap.parse_args()

    with open(a.plan, encoding="utf-8") as fh:
        plan = json.load(fh)
    states = {}
    gh_ok = True
    for n in plan.get("nodes", []):
        if "id" not in n:
            continue
        try:
            states[n["id"]] = pr_state(branch_of(plan.get("week", ""), n["id"]))
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
            gh_ok = False
            states = {}
            break
    if not gh_ok:
        print("[WARN] gh取得失敗：PR の状態が分からないので全ノードを未着手として扱う")

    nodes, order, errors = check(plan, states)
    for e in errors:
        print(f"[ERROR] {e}")
    if errors:
        sys.exit(1)
    if not a.race:
        print(f"OK: {len(nodes)} ノード・ERROR 0")
        return

    chains = chains_for(plan, nodes, order, states, a.race)
    if chains is None:
        sys.exit(f"[ERROR] {a.race} を race に持つノードが計画に無い（#準備 の計画を確認）")
    if a.json:
        print(json.dumps({"week": plan["week"], "chains": chains}, ensure_ascii=False, indent=2))
        return
    if not chains:
        print(f"{a.race}: 作成するPRなし（全ノード MERGED か既存PR）")
        return
    for i, steps in enumerate(chains, 1):
        print(f"## 鎖{i}" + ("（同時起動上限3を超える → 前の鎖の完了後に起動）" if i > MAX_CHAINS else ""))
        for s in steps:
            mark = "" if s["in_target"] else "（祖先・他レース分）"
            gate = f" gate={s['gate']}" if s["gate"] else ""
            print(f"- {s['id']}{mark} [{s['action']}] {s['branch']} ← base {s['base']}{gate}")


if __name__ == "__main__":
    main()
