#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenAPI/Swagger -> 规范化接口清单 (endpoints.json)

用法:
    python parse_contract.py --spec openapi.yaml --out workspace/endpoints.json
    python parse_contract.py --spec openapi.json --filter "/api/v1/orders" --out ep.json

设计原则: 零第三方依赖。json 直接解析; yaml 需要 pyyaml, 缺失时给出明确提示。
"""

import argparse
import json
import os
import sys

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")


def load_spec(path):
    """加载 spec, 返回 dict。支持 .json 与 .yaml/.yml。"""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if path.lower().endswith((".yaml", ".yml")):
        try:
            import yaml  # type: ignore
        except ImportError:
            sys.exit(
                "[parse_contract] 缺少 pyyaml, 无法解析 YAML。\n"
                "  方案一: pip install pyyaml\n"
                "  方案二: 先把 spec 转成 json (多数工具支持导出 json), 再运行本脚本"
            )
        return yaml.safe_load(text)
    return json.loads(text)


def resolve_ref(spec, ref):
    """解析本地 $ref, 如 '#/components/schemas/Order'。"""
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    node = spec
    for part in ref.lstrip("#/").split("/"):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def deref(spec, obj, depth=0):
    """递归展开 $ref, 防止循环引用导致无限递归。"""
    if depth > 10 or not isinstance(obj, dict):
        return obj
    if "$ref" in obj:
        return deref(spec, resolve_ref(spec, obj["$ref"]) or {}, depth + 1)
    return {k: deref(spec, v, depth + 1) for k, v in obj.items()}


def summarize_schema(spec, schema):
    """把 schema 压成用例生成需要的要点: 必填/类型/枚举/范围/格式/字段表。"""
    if not schema:
        return None
    s = deref(spec, schema)
    out = {
        "type": s.get("type"),
        "format": s.get("format"),
        "enum": s.get("enum"),
        "minimum": s.get("minimum"),
        "maximum": s.get("maximum"),
        "minLength": s.get("minLength"),
        "maxLength": s.get("maxLength"),
        "minItems": s.get("minItems"),
        "maxItems": s.get("maxItems"),
        "pattern": s.get("pattern"),
        "required": s.get("required", []),
        "properties": {},
    }
    for name, prop in (s.get("properties") or {}).items():
        p = deref(spec, prop)
        out["properties"][name] = {
            "type": p.get("type"),
            "format": p.get("format"),
            "enum": p.get("enum"),
            "minimum": p.get("minimum"),
            "maximum": p.get("maximum"),
            "minLength": p.get("minLength"),
            "maxLength": p.get("maxLength"),
        }
    return out


def extract_params(spec, operation, path_item):
    """合并 path 级与 operation 级参数。"""
    params = []
    for p in list(path_item.get("parameters", [])) + list(operation.get("parameters", [])):
        p = deref(spec, p)
        params.append({
            "name": p.get("name"),
            "in": p.get("in"),
            "required": bool(p.get("required", False)),
            "schema": summarize_schema(spec, p.get("schema")),
        })
    return params


def parse(spec, path_filter=None, tag_filter=None):
    endpoints = []
    for path, path_item in (spec.get("paths") or {}).items():
        if path_filter and not path.startswith(path_filter):
            continue
        path_item = deref(spec, path_item) or {}
        shared_params = path_item.get("parameters", [])
        for method in HTTP_METHODS:
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue
            if tag_filter and tag_filter not in (op.get("tags") or []):
                continue

            # 请求体
            body = None
            rb = op.get("requestBody")
            if rb:
                rb = deref(spec, rb)
                for content_type, media in (rb.get("content") or {}).items():
                    body = {
                        "content_type": content_type,
                        "required": bool(rb.get("required", False)),
                        "schema": summarize_schema(spec, media.get("schema")),
                    }
                    break

            # 响应: 每个状态码 + schema
            responses = {}
            for code, resp in (op.get("responses") or {}).items():
                resp = deref(spec, resp) or {}
                schema = None
                for _, media in (resp.get("content") or {}).items():
                    schema = summarize_schema(spec, media.get("schema"))
                    break
                responses[str(code)] = {
                    "description": resp.get("description", ""),
                    "schema": schema,
                }

            endpoints.append({
                "path": path,
                "method": method.upper(),
                "operation_id": op.get("operationId"),
                "tags": op.get("tags") or [],
                "summary": op.get("summary", ""),
                "auth_required": bool(op.get("security") is not None
                                      or spec.get("security") is not None),
                "parameters": extract_params(spec, op, {"parameters": shared_params}),
                "request_body": body,
                "responses": responses,
                "status_codes": sorted(responses.keys()),
            })
    return endpoints


def main():
    ap = argparse.ArgumentParser(description="OpenAPI -> 接口清单")
    ap.add_argument("--spec", required=True, help="openapi.json / openapi.yaml 路径")
    ap.add_argument("--out", required=True, help="输出 endpoints.json 路径")
    ap.add_argument("--filter", dest="path_filter", default=None, help="只保留该 path 前缀")
    ap.add_argument("--tag", dest="tag_filter", default=None, help="只保留该 tag")
    args = ap.parse_args()

    spec = load_spec(args.spec)
    if "paths" not in spec:
        sys.exit("[parse_contract] 这不是 OpenAPI: 缺少 paths 字段。"
                 " Swagger 2.0 请先转成 OpenAPI 3.x。")

    endpoints = parse(spec, args.path_filter, args.tag_filter)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"endpoints": endpoints}, f, ensure_ascii=False, indent=2)

    total_codes = sum(len(e["status_codes"]) for e in endpoints)
    print("[parse_contract] 接口数: %d, 状态码分支数: %d" % (len(endpoints), total_codes))
    print("[parse_contract] 输出: %s" % args.out)
    print("[parse_contract] 提示: 下一步用 references/source-mapping.md 逐接口定位服务端 handler 做交叉验证")


if __name__ == "__main__":
    main()
