#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REST 用例执行器: 读 cases.json, 发真实 HTTP 请求, 产出 execution_results.json

用法:
    python run_cases.py --health-check --base-url https://dev-api.example.com
    python run_cases.py --cases workspace/cases.json --out workspace/execution_results.json
    python run_cases.py --cases cases.json --only-failed prev_results.json --out r2.json

零第三方依赖(仅标准库), 便于直接放进公司内部环境。
token 一律从环境变量读, 不落盘。
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

MISSING = object()
TOKEN_ENV = "API_TEST_TOKEN"
REDACTED = "***REDACTED***"


# ---------------------------------------------------------------- 变量与工具

def new_run_id():
    return datetime.now().strftime("%Y%m%d%H%M%S")


def substitute(obj, variables):
    """递归替换字符串里的 {{var}} 占位符。"""
    if isinstance(obj, str):
        def repl(m):
            key = m.group(1)
            return str(variables.get(key, m.group(0)))
        return re.sub(r"\{\{\s*([A-Za-z0-9_\-]+)\s*\}\}", repl, obj)
    if isinstance(obj, dict):
        return {k: substitute(v, variables) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute(v, variables) for v in obj]
    return obj


def resolve_path(data, expr):
    """极简 json_path: 支持 $.a.b[0].c。找不到返回 MISSING。"""
    expr = (expr or "").strip()
    if expr.startswith("$"):
        expr = expr[1:]
    if not expr:
        return data
    cur = data
    for name, idx in re.findall(r"([A-Za-z0-9_\-]+)|\[(\d+)\]", expr):
        if name:
            if not isinstance(cur, dict) or name not in cur:
                return MISSING
            cur = cur[name]
        else:
            i = int(idx)
            if not isinstance(cur, list) or i >= len(cur):
                return MISSING
            cur = cur[i]
    return cur


def op_check(op, actual, expected):
    """返回 (ok, 说明)。"""
    try:
        if op == "eq":
            return actual == expected, None
        if op == "ne":
            return actual != expected, None
        if op == "in":
            return actual in (expected or []), None
        if op == "lt":
            return actual < expected, None
        if op == "lte":
            return actual <= expected, None
        if op == "gt":
            return actual > expected, None
        if op == "gte":
            return actual >= expected, None
        if op == "contains":
            return expected in (actual if isinstance(actual, (str, list, dict)) else str(actual)), None
        if op == "type":
            tmap = {"array": list, "object": dict, "string": str,
                    "integer": int, "number": (int, float), "boolean": bool, "null": type(None)}
            py = tmap.get(expected)
            if py is None:
                return False, "unknown type %s" % expected
            return isinstance(actual, py), None
        if op == "exists":
            return actual is not MISSING, None
        if op == "not_empty":
            if actual is MISSING:
                return False, None
            return (len(actual) > 0) if hasattr(actual, "__len__") else bool(actual), None
        if op == "matches":
            return bool(re.search(expected, str(actual))), None
    except Exception as e:
        return False, "op error: %s" % e
    return False, "unsupported op: %s" % op


def schema_check(data, schema):
    """简化 JSON Schema 校验: 覆盖 required / type / enum / 范围。"""
    if not schema:
        return True, None
    errs = []
    for field in schema.get("required", []):
        if not isinstance(data, dict) or field not in data:
            errs.append("missing required: %s" % field)
    for field, rule in (schema.get("properties") or {}).items():
        if not isinstance(data, dict) or field not in data:
            continue
        val = data[field]
        if rule.get("enum") and val not in rule["enum"]:
            errs.append("%s not in enum" % field)
        if rule.get("type"):
            ok, _ = op_check("type", val, rule["type"])
            if not ok:
                errs.append("%s type mismatch (want %s)" % (field, rule["type"]))
        if isinstance(val, (int, float)):
            if rule.get("minimum") is not None and val < rule["minimum"]:
                errs.append("%s < minimum" % field)
            if rule.get("maximum") is not None and val > rule["maximum"]:
                errs.append("%s > maximum" % field)
    return (len(errs) == 0), "; ".join(errs) if errs else None


# ---------------------------------------------------------------- HTTP

def send(base_url, req, variables, token, timeout, verify_tls=True):
    method = (req.get("method") or "GET").upper()
    path = substitute(req.get("path", "/"), variables)
    url = base_url.rstrip("/") + ("/" + path.lstrip("/"))

    query = substitute(req.get("query") or {}, variables)
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)

    headers = {}
    for k, v in (substitute(req.get("headers") or {}, variables)).items():
        val = str(v)
        if "{{token}}" in val or "Bearer {{" in val:
            val = re.sub(r"\{\{token\}\}", token or "", val)
        headers[k] = val

    data = None
    if "raw" in req and req["raw"] is not None:
        data = substitute(req["raw"], variables).encode("utf-8")
    elif "json" in req and req["json"] is not None:
        data = json.dumps(substitute(req["json"], variables), ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    elif "form" in req and req["form"] is not None:
        data = urllib.parse.urlencode(substitute(req["form"], variables), doseq=True).encode("utf-8")
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

    if token and not any(k.lower() == "authorization" for k in headers):
        headers["Authorization"] = "Bearer " + token

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    ctx = None if verify_tls else __import__("ssl")._create_unverified_context()

    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ctx) as resp:
            body = resp.read()
            status = resp.status
            resp_headers = {k.lower(): v for k, v in resp.getheaders()}
    except urllib.error.HTTPError as e:
        body = e.read()
        status = e.code
        resp_headers = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, e), "status": None,
                "elapsed_ms": int((time.time() - started) * 1000),
                "headers": {}, "text": "", "json": None}

    elapsed = int((time.time() - started) * 1000)
    text = body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text) if text.strip() else None
    except Exception:
        parsed = None
    return {"status": status, "headers": resp_headers, "text": text,
            "json": parsed, "elapsed_ms": elapsed}


def sanitize(req):
    """脱敏: 任何含 token/Authorization 的内容都不落盘。"""
    out = substitute(req, {})
    headers = dict(out.get("headers") or {})
    for k in list(headers):
        if k.lower() in ("authorization", "x-token", "cookie"):
            headers[k] = REDACTED
        elif "{{token}}" in str(headers[k]):
            headers[k] = REDACTED
    if headers:
        out["headers"] = headers
    return out


# ---------------------------------------------------------------- 执行

def run_assertions(case, resp):
    results = []
    for a in case.get("assertions", []):
        atype = a.get("type")
        if atype == "status":
            actual, expected, op = resp.get("status"), a.get("value"), a.get("op", "eq")
        elif atype == "json_path":
            actual, expected, op = resolve_path(resp.get("json"), a.get("path")), a.get("value"), a.get("op", "eq")
        elif atype == "json_schema":
            ok, err = schema_check(resp.get("json"), a.get("schema"))
            results.append({"type": atype, "op": "schema", "ok": ok,
                            "expected": a.get("schema_ref") or "inline", "actual": err,
                            "strength": "strong"})
            continue
        elif atype == "header":
            actual = (resp.get("headers") or {}).get((a.get("name") or "").lower())
            expected, op = a.get("value"), a.get("op", "eq")
        elif atype == "response_time_ms":
            actual, expected, op = resp.get("elapsed_ms"), a.get("value"), a.get("op", "lt")
        elif atype == "body_contains":
            actual, expected, op = resp.get("text"), a.get("value"), a.get("op", "contains")
        else:
            results.append({"type": atype, "op": "-", "ok": False,
                            "expected": None, "actual": "unsupported assertion type",
                            "strength": "unknown"})
            continue

        ok, err = op_check(op, actual, expected)
        strength = "strong" if (atype in ("status", "json_schema") or
                                (atype == "json_path" and op == "eq")) else "weak"
        results.append({
            "type": atype, "op": op, "ok": bool(ok),
            "expected": expected,
            "actual": (None if actual is MISSING else actual),
            "detail": err,
            "strength": strength,
        })
    return results


def execute_case(case, base_url, token, timeout, run_id, verify_tls=True):
    variables = {"run_id": run_id}
    record = {
        "id": case.get("id"), "endpoint": case.get("endpoint"),
        "class": case.get("class"), "priority": case.get("priority"),
        "request": sanitize(case.get("request", {})),
        "setup": [], "teardown": [], "residual": [],
    }

    # setup
    for step in case.get("setup", []):
        resp = send(base_url, step.get("request", {}), variables, token, timeout, verify_tls)
        for var, path in (step.get("capture") or {}).items():
            val = resolve_path(resp.get("json"), path)
            variables[var] = "" if val is MISSING else val
        record["setup"].append({
            "request": sanitize(step.get("request", {})),
            "status": resp.get("status"),
            "error": resp.get("error"),
        })
        if resp.get("error"):
            record["status"] = "error"
            record["error"] = "setup failed: %s" % resp["error"]
            record["assertions"] = []
            return record, variables

    # 主请求
    resp = send(base_url, case.get("request", {}), variables, token, timeout, verify_tls)
    record["response"] = {
        "status": resp.get("status"),
        "elapsed_ms": resp.get("elapsed_ms"),
        "error": resp.get("error"),
        "body_preview": (resp.get("text") or "")[:2000],
    }
    assertions = run_assertions(case, resp)
    record["assertions"] = assertions
    record["status"] = "passed" if (resp.get("status") is not None
                                    and all(a["ok"] for a in assertions)) else "failed"

    # teardown
    for step in case.get("teardown", []):
        tresp = send(base_url, step, variables, token, timeout, verify_tls)
        failed = bool(tresp.get("error")) or not (200 <= (tresp.get("status") or 0) < 300)
        record["teardown"].append({
            "request": sanitize(step), "status": tresp.get("status"), "ok": not failed,
        })
        if failed:
            record["residual"].append(sanitize(step))
    return record, variables


# ---------------------------------------------------------------- 入口

def health_check(base_url, token, timeout, path="/health", probe=None):
    print("[health-check] base_url=%s" % base_url)
    resp = send(base_url, {"method": "GET", "path": path}, {}, None, timeout)
    print("  %-22s %s" % ("service", "OK (%sms)" % resp["elapsed_ms"]
                          if resp.get("status") else "FAIL: %s" % resp.get("error")))
    if probe:
        r2 = send(base_url, {"method": "GET", "path": probe}, {}, token, timeout)
        auth_ok = r2.get("status") == 200
        print("  %-22s %s" % ("token", "OK" if auth_ok else
                              "FAIL: status=%s %s" % (r2.get("status"), r2.get("error") or "")))
        return 0 if auth_ok else 2
    return 0 if resp.get("status") else 2


def main():
    ap = argparse.ArgumentParser(description="REST 用例执行器")
    ap.add_argument("--cases", help="cases.json 路径")
    ap.add_argument("--out", default="workspace/execution_results.json", help="结果输出路径")
    ap.add_argument("--base-url", default=os.environ.get("API_TEST_BASE_URL", ""), help="测试环境地址")
    ap.add_argument("--timeout", type=float, default=10.0, help="单请求超时秒数")
    ap.add_argument("--health-check", action="store_true", help="只做环境前置检查")
    ap.add_argument("--health-path", default="/health")
    ap.add_argument("--auth-probe", default=None, help="用于校验 token 的只读接口路径")
    ap.add_argument("--only-failed", default=None, help="只重跑上一轮结果里失败的用例")
    ap.add_argument("--insecure", action="store_true", help="跳过 TLS 校验(自签证书环境)")
    args = ap.parse_args()

    token = os.environ.get(TOKEN_ENV, "")
    if not args.base_url:
        sys.exit("[run_cases] 缺少 --base-url 或环境变量 API_TEST_BASE_URL")

    if args.health_check:
        sys.exit(health_check(args.base_url, token, args.timeout, args.health_path, args.auth_probe))

    if not args.cases:
        sys.exit("[run_cases] 需要 --cases")

    with open(args.cases, "r", encoding="utf-8") as f:
        cases = json.load(f)

    if args.only_failed:
        with open(args.only_failed, "r", encoding="utf-8") as f:
            prev = json.load(f)
        failed_ids = {r["id"] for r in prev.get("results", []) if r.get("status") == "failed"}
        cases = [c for c in cases if c.get("id") in failed_ids]
        print("[run_cases] 重跑失败用例: %d 条" % len(cases))

    run_id = new_run_id()
    results = []
    for case in cases:
        record, _ = execute_case(case, args.base_url, token, args.timeout, run_id,
                                 verify_tls=not args.insecure)
        results.append(record)
        flag = "PASS" if record["status"] == "passed" else ("ERR " if record["status"] == "error" else "FAIL")
        print("  [%s] %s" % (flag, record.get("id")))

    passed = sum(1 for r in results if r["status"] == "passed")
    failed = sum(1 for r in results if r["status"] == "failed")
    errors = sum(1 for r in results if r["status"] == "error")

    payload = {
        "run_id": run_id,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "base_url": args.base_url,
        "summary": {"total": len(results), "passed": passed, "failed": failed, "error": errors},
        "results": results,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("[run_cases] total=%d passed=%d failed=%d error=%d" % (len(results), passed, failed, errors))
    print("[run_cases] 结果: %s" % args.out)


if __name__ == "__main__":
    main()
