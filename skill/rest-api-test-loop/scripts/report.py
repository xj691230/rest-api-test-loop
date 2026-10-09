#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试报告生成 (浅色卡片式 HTML)

用法:
    python report.py --results workspace/execution_results.json --out workspace/report.html
    python report.py --results r.json --triage t.json --endpoints ep.json --cases c.json --drift d.json --out r.html

报告必含: 结论摘要 / 覆盖率矩阵 / 契约漂移 / 失败分类 / 断言强度 / 残留 / 未覆盖分支。
"""

import argparse
import html
import json
import os
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CSS = """
body{margin:0;padding:32px;background:#f7f7f5;color:#2c2c2a;
font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.6;font-size:14px}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:22px;font-weight:500;margin:0 0 4px}
h2{font-size:16px;font-weight:500;margin:28px 0 12px;padding-bottom:8px;border-bottom:1px solid #e3e2dd}
.sub{color:#6b6a66;font-size:13px;margin-bottom:20px}
.card{background:#fff;border:1px solid #e8e7e2;border-radius:12px;padding:18px 20px;margin-bottom:14px}
.grid{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px}
.kpi{flex:1;min-width:120px;background:#fff;border:1px solid #e8e7e2;border-radius:12px;padding:14px 16px}
.kpi .n{font-size:24px;font-weight:500}
.kpi .l{font-size:12px;color:#6b6a66}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #efeeea;vertical-align:top}
th{font-weight:500;color:#5f5e5a;background:#faf9f6}
code{font-family:"Cascadia Mono",Consolas,monospace;font-size:12px;background:#f2f1ed;padding:1px 5px;border-radius:4px}
.ok{color:#3b6d11}.bad{color:#a32d2d}.warn{color:#854f0b}.muted{color:#6b6a66}
.tag{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;border:1px solid}
.t-red{color:#a32d2d;border-color:#f09595;background:#fcebeb}
.t-amber{color:#854f0b;border-color:#efa027;background:#faeeda}
.t-gray{color:#5f5e5a;border-color:#c9c8c2;background:#f1efe8}
.t-green{color:#3b6d11;border-color:#97c459;background:#eaf3de}
.foot{margin-top:28px;padding-top:14px;border-top:1px solid #e3e2dd;font-size:12px;color:#6b6a66}
.empty{color:#6b6a66;font-size:13px;padding:8px 0}
"""


def esc(x):
    return html.escape("" if x is None else str(x))


def load(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def coverage_matrix(endpoints, cases):
    """接口 x 状态码 覆盖率。返回 (rows, uncovered)。rows: endpoint, covered[], missing[]"""
    if not endpoints:
        return [], []
    by_ep = {}
    for c in cases or []:
        key = (c.get("endpoint") or "").upper().replace("  ", " ")
        st = c.get("expect_status")
        if st is None:
            for a in c.get("assertions", []):
                if a.get("type") == "status" and a.get("op") == "eq":
                    st = a.get("value")
                    break
        by_ep.setdefault(key, set()).add(st)

    rows, uncovered = [], []
    for e in endpoints.get("endpoints", []):
        key = ("%s %s" % (e["method"], e["path"])).upper()
        codes = [int(c) for c in e.get("status_codes", []) if str(c).isdigit()]
        hit = by_ep.get(key, set())
        covered = [c for c in codes if c in hit]
        missing = [c for c in codes if c not in hit]
        rows.append({"endpoint": "%s %s" % (e["method"], e["path"]),
                     "covered": covered, "missing": missing})
        for c in missing:
            uncovered.append("%s -> %d" % (("%s %s" % (e["method"], e["path"])), c))
    return rows, uncovered


def assertion_strength(results):
    strong = weak = 0
    for r in results or []:
        for a in r.get("assertions", []):
            if a.get("strength") == "strong":
                strong += 1
            elif a.get("strength") == "weak":
                weak += 1
    return strong, weak


def main():
    ap = argparse.ArgumentParser(description="生成测试报告")
    ap.add_argument("--results", required=True)
    ap.add_argument("--triage", default=None)
    ap.add_argument("--endpoints", default=None)
    ap.add_argument("--cases", default=None)
    ap.add_argument("--drift", default=None)
    ap.add_argument("--round", default="1", help="当前轮次")
    ap.add_argument("--stop-reason", default="", help="终止原因")
    ap.add_argument("--out", default="workspace/report.html")
    args = ap.parse_args()

    results = load(args.results) or {}
    triage = load(args.triage)
    endpoints = load(args.endpoints)
    cases = load(args.cases)
    drift = load(args.drift)

    s = results.get("summary", {})
    total = s.get("total", 0)
    passed = s.get("passed", 0)
    failed = s.get("failed", 0) + s.get("error", 0)
    rows, uncovered = coverage_matrix(endpoints, cases)
    strong, weak = assertion_strength(results.get("results", []))
    residuals = [(r.get("id"), r.get("residual")) for r in results.get("results", [])
                 if r.get("residual")]

    p = []
    p.append("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>")
    p.append("<title>REST 接口测试报告</title><style>%s</style></head><body><div class='wrap'>" % CSS)
    p.append("<h1>REST 接口测试报告</h1>")
    p.append("<div class='sub'>run %s &middot; %s &middot; %s</div>"
             % (esc(results.get("run_id", "-")), esc(results.get("base_url", "-")),
                datetime.now().strftime("%Y-%m-%d %H:%M")))

    # 结论摘要
    p.append("<h2>结论摘要</h2><div class='grid'>")
    for n, l, cls in ((total, "用例总数", ""), (passed, "通过", "ok"),
                      (failed, "失败", "bad"),
                      (len(uncovered), "未覆盖分支", "warn" if uncovered else "")):
        p.append("<div class='kpi'><div class='n %s'>%s</div><div class='l'>%s</div></div>"
                 % (cls, n, l))
    p.append("</div>")
    p.append("<div class='card'>强断言 %d 条 &middot; 弱断言 %d 条 &middot; 断言强度比 %s<br>"
             "轮次 %s &middot; 终止原因: %s</div>"
             % (strong, weak,
                ("%.0f%%" % (strong * 100.0 / (strong + weak))) if (strong + weak) else "-",
                esc(args.round), esc(args.stop_reason) or "未终止/正常收敛"))

    # 覆盖率矩阵
    p.append("<h2>覆盖率矩阵（接口 &times; 状态码）</h2>")
    if rows:
        p.append("<div class='card'><table><tr><th>接口</th><th>已覆盖</th><th>未覆盖</th></tr>")
        for r in rows:
            cov = " ".join("<code>%d</code>" % c for c in r["covered"]) or "<span class='muted'>-</span>"
            mis = " ".join("<code class='bad'>%d</code>" % c for c in r["missing"]) or "<span class='ok'>全覆盖</span>"
            p.append("<tr><td><code>%s</code></td><td>%s</td><td>%s</td></tr>"
                     % (esc(r["endpoint"]), cov, mis))
        p.append("</table></div>")
    else:
        p.append("<div class='card empty'>未提供 endpoints.json / cases.json，跳过覆盖率矩阵。</div>")

    if uncovered:
        p.append("<div class='card'><b>未覆盖分支（不许假装测过）</b><br>"
                 + "<br>".join("<code>%s</code>" % esc(u) for u in uncovered[:60]) + "</div>")

    # 契约漂移
    p.append("<h2>契约漂移（OpenAPI &times; 源码）</h2>")
    if drift:
        items = drift if isinstance(drift, list) else drift.get("items", [])
        if items:
            p.append("<div class='card'><table><tr><th>接口</th><th>类型</th><th>说明</th><th>处置</th></tr>")
            for d in items:
                p.append("<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td class='muted'>%s</td></tr>"
                         % (esc(d.get("endpoint")), esc(d.get("type")),
                            esc(d.get("detail")), esc(d.get("action", "交人工裁决"))))
            p.append("</table></div>")
        else:
            p.append("<div class='card empty'>未发现契约漂移。</div>")
    else:
        p.append("<div class='card empty'>未提供 contract_drift.json，本轮未做交叉验证。</div>")

    # 失败分类
    p.append("<h2>失败清单与分类</h2>")
    if triage and triage.get("items"):
        tagmap = {"deterministic": "t-red", "flaky": "t-amber",
                  "transient": "t-gray", "unknown": "t-gray"}
        p.append("<div class='card'><table><tr><th>用例</th><th>判定</th><th>响应</th><th>期望</th><th>处置</th></tr>")
        for i in triage["items"]:
            sg = i.get("signals", {})
            p.append("<tr><td><code>%s</code></td>"
                     "<td><span class='tag %s'>%s</span></td>"
                     "<td>%s</td><td>%s</td><td class='muted'>%s</td></tr>"
                     % (esc(i.get("id")), tagmap.get(i.get("verdict"), "t-gray"),
                        esc(i.get("verdict")), esc(sg.get("response_status")),
                        esc(sg.get("expected_status")), esc(i.get("action"))))
        p.append("</table></div>")
        if triage.get("batch_noise_hint"):
            p.append("<div class='card warn'>批量信号提示：%s</div>" % esc(triage["batch_noise_hint"]))
    else:
        bad = [r for r in results.get("results", []) if r.get("status") != "passed"]
        if bad:
            p.append("<div class='card'><table><tr><th>用例</th><th>状态</th><th>响应</th></tr>")
            for r in bad:
                p.append("<tr><td><code>%s</code></td><td class='bad'>%s</td><td>%s</td></tr>"
                         % (esc(r.get("id")), esc(r.get("status")),
                            esc((r.get("response") or {}).get("status"))))
            p.append("</table></div>")
            p.append("<div class='card muted'>未跑 flaky 判别（triage.json 缺失），"
                     "以上失败尚未确定是否为 flaky，不能直接归因。</div>")
        else:
            p.append("<div class='card'><span class='ok'>全部通过。</span></div>")

    # 残留
    p.append("<h2>数据残留</h2>")
    if residuals:
        p.append("<div class='card warn'>以下 teardown 未成功，需人工清理：<br>")
        for cid, res in residuals:
            p.append("<code>%s</code> &rarr; %s<br>" % (esc(cid), esc(json.dumps(res, ensure_ascii=False))))
        p.append("</div>")
    else:
        p.append("<div class='card empty'>无残留。</div>")

    p.append("<div class='foot'>本报告由 rest-api-test-loop 生成。本轮未修改任何被测服务代码；"
             "用例修改以 diff 形式记录。token 等凭据不落盘。</div>")
    p.append("</div></body></html>")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(p))
    print("[report] 输出: %s" % args.out)


if __name__ == "__main__":
    main()
