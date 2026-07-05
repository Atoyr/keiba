# tools/patch_radj.py — 一度だけ実行して削除してよい
import csv
rows = list(csv.DictReader(open('log/predictions.csv', encoding='utf-8')))
applied = {1:2, 2:2, 3:4, 4:0, 5:2, 6:4, 7:0, 8:0, 9:0, 10:2, 11:0, 12:-2, 13:4}  # 適用時点のR補正
diff_note = {2:'+4', 10:'+4', 1:'+4', 11:'+2', 8:'+2'}  # 確定オッズ帯との差分
cols = ['race_id','horse_no','horse_name','mark','base_score','base_breakdown',
        'composite_coef','additive_total','r_adj','final_score','myomi_score',
        'popularity','win_odds','place_odds_max','r_value','finish_pos','in_place','notes']
for r in rows:
    r.setdefault('base_breakdown',''); r.setdefault('r_adj','')
    if r['race_id']=='2026_kitakyushu_kinen':
        no=int(r['horse_no']); r['r_adj']=str(applied[no])
        if no in diff_note:
            r['notes'] += f"／R補正は締切前オッズで適用({applied[no]:+d})・確定帯は{diff_note[no]}（適用値を記録）"
w = csv.DictWriter(open('log/predictions.csv','w',encoding='utf-8',newline=''),
                   fieldnames=cols, lineterminator='\n')
w.writeheader(); w.writerows(rows)