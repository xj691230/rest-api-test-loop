# 源码定位：从 OpenAPI path 找到服务端 handler

归因时只说"接口返回不对"不算完成，必须定位到 `file:line`。本文件给出按框架分类的探测手法。

## 通用三步法

### 第 1 步：提取路径特征片段

OpenAPI 的 `/api/v1/orders/{orderId}/items` 在源码里几乎不会原样出现。去掉参数段，取**最独特的静态片段**去搜：

```
/api/v1/orders/{orderId}/items
        └─ 特征片段："orders"（优先）或 "v1/orders"
```

参数段的常见写法：`{id}` / `:id` / `<id>` / `%s` / 正则 `(\d+)` / 框架占位符。**搜的时候先把参数段删掉**。

### 第 2 步：按框架特征 Grep

用 Grep 工具，按下面表格逐个试，命中即用：

| 框架 | 路由注册特征 | Grep 模式 |
|---|---|---|
| **Drogon** | 控制器宏 | `METHOD_ADD\|METHOD_LIST_BEGIN\|addMethod` |
| Drogon（裸注册） | `app().registerHandler(` | `registerHandler` |
| **cpp-httplib** | `svr.Get("/path", handler)` | `svr\.(Get\|Post\|Put\|Delete\|Patch)` |
| **Oat++** | `ENDPOINT("GET", "/path", name)` | `ENDPOINT\(` 或 `ENDPOINT_INFO` |
| **Crow** | `CROW_ROUTE(app, "/path")` | `CROW_ROUTE` |
| **Poco** | 工厂类 + 路由映射表 | `HTTPRequestHandlerFactory`、`createRequestHandler` |
| **Sogou workflow** | `WFHttpServer` + 手写分发 | `WFHttpServer`、`server_uri`、路径字符串比较 |
| **brpc** | HTTP 协议 + 手写分发 | `uri()`、`path ==` |
| **自研** | 路由表数组 / map | 直接 Grep 路径片段字符串 |

**自研框架最可靠的一招**：直接 Grep 路径片段本身。

```
Grep pattern: "orders"  (限定 .cpp/.h/.cc)
```

路由表通常集中在一个文件里（`route.cpp`、`router.cc`、`api_routes.h`），一眼能看到全貌。

### 第 3 步：从 handler 往下挖校验与错误码

找到 handler 函数后，按这个顺序读，归因要的信息都在里面：

1. **参数解析**：`getParameter` / `parseJson` / 手动取值 —— 看有没有判空、有没有类型检查
2. **校验逻辑**：`validate` / `check` / `if (field.empty())` —— 必填校验在哪、返回什么错误码
3. **错误码定义**：Grep `400` / `INVALID_PARAM` / `BAD_REQUEST`，通常在公共头文件里集中定义
4. **响应结构体**：Grep 响应字段的英文名，找到 `struct` 定义 —— 对照 OpenAPI 的响应 schema

## 交叉验证时的三个高频发现

| 现象 | 说明什么 | 记录为 |
|---|---|---|
| handler 里判空了，spec 没标 required | spec 过期 | 契约漂移（spec 缺 required） |
| spec 标了 required，handler 直接取值没校验 | 实现缺陷或靠下游兜底 | 红灯候选，交人工裁决 |
| handler 返回的错误码与 spec 的 response 不一致 | 二者必有一错 | 契约漂移，交人工裁决 |

## 坑

- **宏展开的路由搜不到**：Drogon 的 `METHOD_ADD(OrderCtrl::getOrder, "/api/v1/orders/{id}", Get)` 里路径是宏参数，Grep `METHOD_ADD` 才能看到，Grep 路径片段会漏。**先 Grep 框架特征，再看宏内容**。
- **路径前缀在网关层**：`/api/v1` 可能是 nginx 或网关加的，源码里只有 `/orders`。搜不到时**逐段截断再试**：先试 `/orders`，再试 `orders`。
- **多个 handler 匹配**：一个路径可能在不同版本目录下各有一份（`v1/`、`v2/`）。用 spec 里的版本号对齐，别挑错。
- **校验在中间件**：签名、token、限流常在拦截器里，不在 handler。搜不到校验时去 Grep 中间件/拦截器目录。
