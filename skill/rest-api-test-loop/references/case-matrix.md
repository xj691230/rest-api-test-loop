# 用例设计矩阵

每个接口的每个**状态码分支**至少 1 条用例。优先级：P0 阻断发布，P1 主流程，P2 健壮性。

## 一、类别与优先级

| # | 类别 | 覆盖点 | 优先级 |
|---|---|---|---|
| 1 | 正常流 | 合法最小请求 → 预期成功码 + 完整 schema 校验 | P0 |
| 2 | 必填校验 | 逐个缺失必填字段（每个字段单独一条） | P0 |
| 3 | 类型校验 | 字段传错类型（string 传 int、int 传 string、传 null、传数组） | P1 |
| 4 | 边界值 | min / max / min-1 / max+1 / 0 / 负数 / 空串 | P1 |
| 5 | 长度与格式 | 超长字符串、超规格数组、email/uuid/date 格式非法 | P1 |
| 6 | 枚举 | 传枚举外的值、传空、传大小写变体 | P1 |
| 7 | 鉴权 | 无 token → 401；错误/过期 token → 401；权限不足 → 403 | P0 |
| 8 | 资源存在性 | 不存在的 ID → 404；越权访问他人资源 → 403 或 404 | P0 |
| 9 | 冲突 | 重复创建唯一资源 → 409；状态不允许的操作 → 409 | P1 |
| 10 | 幂等 | 同一 PUT 发两次结果一致；DELETE 两次第二次应为 404 或 204 | P1 |
| 11 | 分页 | 首页 / 末页 / 空结果 / page 超限 / page=0 / pageSize 超大 | P1 |
| 12 | 安全 | SQL 注入串、XSS 串、路径穿越 `../`、超长 payload → 413 或优雅拒绝 | P1 |
| 13 | 契约 | 响应 schema、Content-Type、必返回字段、错误体结构一致性 | P0 |
| 14 | 性能 | 响应时间基线（P0 接口必带） | P2 |
| 15 | 限流 | 快速连发 → 429（**共享环境谨慎，默认关闭**） | P2 |

## 二、状态码分支矩阵

对每个接口，spec 里声明的每个 response code 都要有用例：

`200` `201` `204` `400` `401` `403` `404` `409` `413` `415` `422` `429` `500`

覆盖不到的必须在报告里显式列出并说明原因（如"429 需压测工具，本轮跳过"）。**不许假装测过**。

## 三、断言写法（强度从高到低）

断言强度是铁律 2 的度量对象，只能往高走。

```json
"assertions": [
  {"type": "status", "op": "eq", "value": 400},
  {"type": "json_schema", "schema_ref": "#/components/schemas/Error"},
  {"type": "json_path", "path": "$.code", "op": "eq", "value": "INVALID_PARAM"},
  {"type": "json_path", "path": "$.data.items", "op": "type", "value": "array"},
  {"type": "header", "name": "Content-Type", "op": "contains", "value": "application/json"},
  {"type": "response_time_ms", "op": "lt", "value": 2000}
]
```

| 强度 | 断言类型 | 说明 |
|---|---|---|
| 强 | `status eq` 精确码 | 不许写成 `in [400, 200]` |
| 强 | `json_schema` | 全量结构校验 |
| 强 | `json_path eq` 精确值 | 业务码、关键字段值 |
| 中 | `json_path type` / `contains` | 类型或子串 |
| 弱 | `json_path exists` / `not_empty` | **单独使用视为弱断言**，需补强 |

**规则**：一条用例至少包含 1 条强断言。全是弱断言的用例不算覆盖。

## 四、写操作用例的强制性要求

任何会写数据的用例必须带：

```json
"setup":    [{"action": "create", "ref": "order_id", "endpoint": "POST /api/orders", "body": {...}}],
"teardown": [{"action": "delete", "endpoint": "DELETE /api/orders/{order_id}"}],
"unique_prefix": "autotest_{{run_id}}_"
```

- 资源名一律带 `autotest_{{run_id}}_` 前缀，避免撞别人的数据和留下无法清理的残留；
- `teardown` 必须存在，即使执行失败也要尝试清理并在报告里登记残留；
- 用例之间**默认串行**，禁止依赖执行顺序。

## 五、生成顺序（避免一次生成过多导致失控）

1. 先生成全部 P0 —— 跑通再继续
2. 再生成 P1 —— 跑通再继续
3. 最后 P2

**不要一次生成上百条再跑**。分批跑能快速发现环境/契约的共性问题，避免在同一类错误上浪费整轮。
