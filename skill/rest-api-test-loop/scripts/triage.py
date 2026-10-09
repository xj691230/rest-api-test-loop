#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flaky 判别与归因辅助

用法:
    python triage.py --results workspace/execution_results.json --cases workspace/cases.json --mode flaky
    python triage.py --results workspace/execution_results.json --cases workspace/cases.json --out workspace/triage.json

核心: 对每个失败用例独立重跑 3 次, 按通过次数分流。
  0/3   -> deterministic 确定性缺陷, 进入归因
  1-2/3 -> flaky        隔离标记 non-blocking, 禁止 agent 修
  3/3   -> transient    瞬时抖动, 记为环境噪声

只做判别与信号提取, 不做自动修复 —— 归因与修复由 agent 按 SKILL.md Step 6/7 执行。
"""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_cases import execute_case, new_run_id  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

RERUNS = 3

# 环境噪声的典型信号: 命中即提示灰灯
NOISE_STATUS = {401, 403, 429, 502, 503, 504}


def rerun(case, base_url, token, timeout, insecure):
    """独立重跑 RERUNS 次, 返回通过次数与最后一次记录。"""
    passed = 0
    last = None
    for _ in range(RERUNS):
        record, _ = execute_case(case, base_url, token, timeout,
                                 new_run_id(), verify_tls=not insecure)
        last = record
        if record.get("status") == "passed":
            passed += 1
    return passed, last


def extract_signals(record):
    """提取供归因使用的信号。"""
    resp = record.get("response") or {}
    status = resp.get("status")
    failed_asserts = [a for a in record.get("assertions", []) if not a.get("ok")]
    expected = None
    for a in failed_asserts:
        if a.get("type") == "status":
            expected = a.get("expected")
            break
    return {
        "response_status": status,
        "expected_status": expected,
        "transport_error": resp.get("error"),
        "noise_signal": status in NOISE_STATUS,
        "failed_assertions": [
            {"type": a.get("type"), "op": a.get("op"),
             "expected": a.get("expected"), "actual": a.get("actual")}
            for a in failed_asserts
        ],
        "body_preview": (resp.get("body_preview") or "")[:500],
    }


def batch_noise_hint(items):
    """批量失败 -> 大概率环境问题。返回一个提示串。"""
    if not items:
        return None
    errs = [i["signals"].get("transport_error") for i in items if i["signals"].get("transport_error")]
    if len(errs) > len(items) / 2:
        return "超过半数用例出现传输层错误, 优先怀疑环境(灰灯), 先做 health-check"
    codes = Counter(i["signals"].get("response_status") for i in items)
    for code, cnt in codes.items():
        if code in NOISE_STATUS and cnt > len(items) / 2:
            return "超过半数用例返回 %s, 优先怀疑鉴权/限流/网关(灰灯)" % code
    return None


def main():
    ap = argparse.ArgumentParser(description="flaky 判别与归因辅助")
    ap.add_argument("--results", required=True, help="execution_results.json")
    ap.add_argument("--cases", required=True, help="cases.json (重跑需要原始用例)")
    ap.add_argument("--out", default="workspace/triage.json")
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--reruns", type=int, default=RERUNS)
    ap.add_argument("--mode", default="flaky", choices=["flaky", "signals"],
                    help="signals: 跳过重跑, 只提取信号")
    ap.add_argument("--insecure", action="store_true")
    args = ap.parse_args()

    with open(args.results, "r", encoding="utf-8") as f:
        results = json.load(f)
    with open(args.cases, "r", encoding="utf-8") as f:
        cases = {c.get("id"): c for c in json.load(f)}

    base_url = args.base_url or results.get("base_url") or ""
    token = os.environ.get("API_TEST_TOKEN", "")

    bad = [r for r in results.get("results", []) if r.get("status") in ("failed", "error")]
    items = []
    for r in bad:
        case = cases.get(r.get("id"))
        if not case:
            items.append({"id": r.get("id"), "verdict": "unknown",
                          "blocking": False,
                          "note": "cases.json 中找不到该用例, 无法重跑"})
            continue

        if args.mode == "signals":
            passed_of_3, record = None, r
            verdict = "unknown"
        else:
            if not base_url:
                sys.exit("[triage] 缺少 --base-url (也可由 results 里的 base_url 提供)")
            passed_of_3, record = rerun(case, base_url, token, args.timeout, args.insecure)
            if passed_of_3 == 0:
                verdict = "deterministic"
            elif passed_of_3 == args.reruns:
                verdict = "transient"
            else:
                verdict = "flaky"

        blocking = verdict in ("deterministic", "unknown")
        items.append({
            "id": r.get("id"),
            "endpoint": r.get("endpoint"),
            "class": r.get("class"),
            "priority": r.get("priority"),
            "rerun_passed_of_%d" % args.reruns: passed_of_3,
            "verdict": verdict,
            "blocking": blocking,
            "action": ("进入归因 (Step 6)" if verdict == "deterministic"
                       else "隔离标记 non-blocking, 禁止自动修" if verdict == "flaky"
                       else "记为环境噪声, 不计入" if verdict == "transient"
                       else "需人工确认"),
            "signals": extract_signals(record),
            "residual": r.get("residual", []),
        })

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_run": results.get("run_id"),
        "mode": args.mode,
        "summary": {
            "failed_total": len(bad),
            "deterministic": sum(1 for i in items if i["verdict"] == "deterministic"),
            "flaky": sum(1 for i in items if i["verdict"] == "flaky"),
            "transient": sum(1 for i in items if i["verdict"] == "transient"),
        },
        "batch_noise_hint": batch_noise_hint([i for i in items if "signals" in i]),
        "items": items,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    s = payload["summary"]
    print("[triage] failed=%d deterministic=%d flaky=%d transient=%d"
          % (s["failed_total"], s["deterministic"], s["flaky"], s["transient"]))
    if payload["batch_noise_hint"]:
        print("[triage] 提示: %s" % payload["batch_noise_hint"])
    print("[triage] 输出: %s" % args.out)
    print("[triage] 下一步: 对 deterministic 的用例按 references/failure-taxonomy.md 归因")


if __name__ == "__main__":
    main()
