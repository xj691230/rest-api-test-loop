---
name: rest-api-test-loop
description: 对后台服务的 REST 接口执行「生成用例 → 实跑验证 → 归因 → 修复 → 复跑」闭环。基于 OpenAPI/Swagger 契约生成黑盒 HTTP 用例，用源码仓交叉校验契约并定位根因，在开发环境实跑；把失败分类为产品缺陷 / 用例缺陷 / 环境噪声 / flaky，用例缺陷自动修复，产品缺陷定位到 file:line 后交人工确认。当用户要求测接口、生成接口测试用例、验证 API 契约、排查失败用例、跑接口回归时使用。
agent_created: true
version: 0.2.0
allowed-tools: Read, Write, Edit, Grep, Glob, Bash(python:*)
---

# REST 接口测试闭环（黑盒 HTTP）

## 定位与关键约束

被测服务是有 **OpenAPI/Swagger 契约**、有**源码仓**和**开发环境**的后台服务（语言不限，典型是编译型服务）。四条硬约束，整个 skill 的设计都建立在它们之上：

1. **测试不写进被测代码。** 编译型服务任何改动都要重新编译、部署，反馈是分钟到十分钟级。若测试也用被测语言写单测（例如 C++ 的 GoogleTest + libcurl），闭环每一轮都要等编译，3 轮就是半小时起步，且对被测侧零收益。因此测试一律做成**黑盒 HTTP 用例**：声明式 JSON 用例数据 + Python 执行器。被测侧零改动、零编译，单轮秒级反馈。
2. **契约有两份事实来源，冲突时以源码为准。** OpenAPI 描述的是**意图**，服务端源码是**事实**。老项目的 OpenAPI 经常与实现对不上（字段改名没同步、新增了必填校验没写进 spec）。因此闭环里必须做交叉验证，把契约漂移当成一类正式产出报出来——这本身就是商业级价值。
3. **修用例是秒级，修代码是人工级。** 改被测服务源码后必须人工确认、编译、部署，复跑前**必须确认部署已完成**。闭环因此有两种节奏，不许混为一谈。
4. **开发环境可控，测试环境不干净。** 开发环境能起服务、能重启、能自己部署，优先用它跑闭环。测试环境是共享的：别人也在用、有脏数据、可能被重启、有限流。在共享环境上跑时，所有"环境看着有问题"的信号一律先归灰灯，不许往被测代码上猜。

## 铁律

违反任何一条，本轮产出作废重来。

### 铁律 1：`NO FIX WITHOUT CLASSIFICATION FIRST`
未归因的红灯，不许动任何东西——既不许改用例，也不许改被测代码。先跑完 Step 5（flaky 判别）和 Step 6（归因分类），拿到分类标签才允许进入 Step 7。

### 铁律 2：`NEVER TRADE ASSERTION STRENGTH FOR GREEN`
放宽断言换绿灯等同于删除用例。禁止：把 `eq 400` 改成 `in [400, 200]`、删掉 schema 断言、把精确值改成"非空即可"、加 `skip`、加无脑 `sleep`、加 blanket retry。**断言强度只增不减**，每轮结束要能拿出断言强度的 diff 证明没有降级。

### 铁律 3：`THE LOOP HAS A CEILING`
最多 **3 轮**。出现下列任一情况立即停止并升级人工：
- 轮次达到 3；
- 连续两轮没有任何新增修复（说明在原地打转）；
- 同一处修复失败 3 次（抄 `systematic-debugging`：这不是 bug 是设计问题，停下来质疑接口设计本身）；
- 出现任何回归（修复引入了新的失败）。

### 铁律 4：`NEVER TRUST SELF-GRADING`
"我觉得这个用例应该过了"不算过。一切结论以执行器输出的 `execution_results.json` 为准，该文件由独立的非 LLM 程序生成。

### 铁律 5：`SPEC IS INTENT, SOURCE IS TRUTH`
用例断言的**期望值**以 OpenAPI 为准（那是产品承诺），但判定**谁是错的**时以源码为准。二者冲突时不许自行选边，按 Step 2 登记为契约漂移交给人工裁决。

## 输入契约

| 项 | 必填 | 说明 |
|---|---|---|
| OpenAPI 文件 | 是 | `openapi.json` / `openapi.yaml`；Swagger 2.0 需先转 3.x |
| 源码仓路径 | 是 | 用于交叉验证契约与定位根因 |
| 目标环境 base URL | 是 | 优先开发环境；共享测试环境需明确标注 |
| 鉴权 token | 是 | 走环境变量传入，**禁止写进任何用例文件或报告** |
| 目标范围 | 否 | 限定 path 前缀或 tag；不指定则全覆盖 |
| 轮次上限 | 否 | 默认 3 |

## 工作流程

### Step 0：环境前置检查（必做，不许跳）

```bash
python scripts/run_cases.py --health-check --base-url "$BASE_URL"
```

检查三项：服务可达、token 有效（用一个只读接口验证）、响应时延基线。
**任一不过就停下报障**，不要带着坏环境往下跑——否则后面所有红灯都无法归因。

### Step 1：解析契约

```bash
python scripts/parse_contract.py --spec openapi.yaml --out workspace/endpoints.json
```

产出规范化清单：path、method、鉴权要求、必填字段、字段类型与约束（枚举 / 最大最小 / 长度 / 格式）、各状态码对应的响应 schema。

### Step 2：契约 × 源码交叉验证（本 skill 的核心增值点）

用 `references/source-mapping.md` 定位每个接口对应的服务端 handler，逐项比对：

| 比对项 | 漂移类型 | 处置 |
|---|---|---|
| spec 有、源码无路由 | 死文档 | 登记，不生成用例 |
| 源码有路由、spec 无 | 未文档化接口 | 登记并**补生成冒烟用例** |
| 必填字段不一致 | 契约漂移 | 登记，用例按 spec 写，红灯时优先怀疑 spec 过期 |
| 枚举 / 取值范围不一致 | 契约漂移 | 登记，边界用例取两者**并集** |
| 响应字段缺失或类型不符 | 实现缺陷或文档过期 | 登记，交人工裁决后再定断言 |

输出 `workspace/contract_drift.json`。**裁决权在人手里，agent 只负责把冲突摆出来**。

### Step 3：生成用例

按 `references/case-matrix.md` 的矩阵逐接口生成，输出 `workspace/cases.json`，格式见 `assets/case-schema.json`。
每条用例必须带：`id`、`class`、`priority`、完整 `request`、至少一条 `assertions`。
**先做覆盖度自检**：每个接口的每个状态码分支至少 1 条用例；没有用例覆盖的分支要在报告里显式列出，不许假装测过。

### Step 4：执行

```bash
python scripts/run_cases.py --cases workspace/cases.json --out workspace/execution_results.json
```

产出 `execution_results.json`（下游唯一事实来源）。注意：
- 用例之间**默认串行**，避免相互干扰；
- 有写操作的用例必须自带 `setup` / `teardown`，资源名带**唯一前缀**（如 `autotest_<runid>_`），防止撞别人的数据和残留；
- 开发环境可放心造数据，共享测试环境要格外保守；
- 全程不落 token 到文件。

### Step 5：flaky 判别（红灯先过这一关）

```bash
python scripts/triage.py --results workspace/execution_results.json --mode flaky
```

对每个失败用例**独立重跑 3 次**，按通过次数分流：

| 通过次数 | 判定 | 处置 |
|---|---|---|
| 0/3 | 确定性缺陷 | 进入 Step 6 归因 |
| 1~2/3 | **flaky** | 隔离标记 `non-blocking`，**禁止 agent 修**（多半是共享环境或被测代码竞态） |
| 3/3 | 瞬时抖动 | 不计入，记为环境噪声 |

这一步是防止"把环境抖动当 bug 修"的唯一屏障，不许跳过直奔归因。

### Step 6：归因分类

对 0/3 的确定性失败，按 `references/failure-taxonomy.md` 判定，打上**且仅打上**一个标签：

- **红灯 · 产品缺陷**：契约明确要求但实现不符（应 400 却 200、校验缺失、schema 字段缺失或类型错、500）
- **黄灯 · 用例缺陷**：契约没规定但用例自己假设了（断言写错、前置数据没建、依赖脏数据、用了契约外的字段）
- **灰灯 · 环境噪声**：连接失败、401 批量、429 限流、超时、服务重启中

**有代码仓的优势在这里兑现**：红灯产品缺陷要继续往下挖，用 `references/source-mapping.md` 定位到 handler 的 `file:line`，说明"契约要求 X，第 N 行只做了 Y 校验"——给出这样的结论才算归因完成，只说"接口返回不对"不算。

判定不清时**默认归黄灯**（先怀疑自己写的用例），并标 `needs_review`。

### Step 7：修复

- **黄灯**：按 `references/repair-playbook.md` 自动修用例。**只改用例文件，不改被测代码**，断言强度不得降低。
- **红灯**：**不自动改**。产出最小 diff + `file:line` + 失败用例 + 期望行为描述，交人工确认，并明确写出"改完需要重新编译部署"。
- **灰灯**：不修。重试 2 次仍失败则挂起，输出报障信息（哪个接口、什么错误、建议找谁）。

### Step 8：复跑与收敛

- 修用例后直接复跑（秒级）。
- 修被测代码后，**先确认部署完成再复跑**：比对服务返回的构建号 / 版本头与预期一致，或用 `--health-check` 确认服务已重启。开发环境可以自己拉起服务，这一步相对可控；共享测试环境必须找人确认。**没确认就复跑等于白跑一轮**。
- 复跑后回到 Step 4，并检查是否引入回归。

### Step 9：出报告

```bash
python scripts/report.py --results workspace/execution_results.json --out workspace/report.html
```

报告必须包含：结论摘要、覆盖率矩阵（接口 × 状态码）、**契约漂移清单**、失败清单与分类、每轮修复的 diff、断言强度变化、未覆盖分支、终止原因。

## 自检清单

- [ ] Step 0 环境前置检查已通过
- [ ] Step 2 契约 × 源码交叉验证已执行，漂移已登记
- [ ] 用例全部来自 Step 1 的清单，无凭空字段
- [ ] 每个状态码分支有用例覆盖，未覆盖的已显式列出
- [ ] 红灯全部过过 Step 5 的 flaky 判别
- [ ] 每个失败仅一个分类标签
- [ ] 产品缺陷已定位到 `file:line`，不是"接口返回不对"
- [ ] 断言强度无降级（有 diff 可证）
- [ ] 产品缺陷已交人工确认，未自动改被测代码
- [ ] 有写操作的用例带了唯一前缀和 teardown
- [ ] token 未出现在任何产出文件中
- [ ] 未超过 3 轮，或已明确记录停止原因

## 禁止事项

- 禁止为了让测试变绿而修改断言（见铁律 2）
- 禁止自动修改被测服务源码
- 禁止在契约漂移上自行选边站（见铁律 5）
- 禁止 skip / 注解掉失败用例
- 禁止用 blanket retry 或无脑 sleep 掩盖失败
- 禁止整文件重写用例——只接受局部 diff
- 禁止把 token、密码写入用例文件或报告
- 禁止在没有 teardown 的情况下造数据
- 禁止超过 3 轮还继续跑

## 环境与验证记录

- 脚本零第三方依赖（仅标准库），Python 3.7+ 可直接跑。YAML 格式的 spec 需要 `pyyaml`，缺失时脚本会给出明确提示。
- token 走环境变量 `API_TEST_TOKEN`，base URL 走 `API_TEST_BASE_URL`，也可用 `--base-url` 显式传。
- Windows 控制台若中文输出乱码，设置 `PYTHONIOENCODING=utf-8`。
- 自签证书环境加 `--insecure`。

**已完成的验证（v0.2.0）**：
- 4 个脚本 `ast.parse` 语法检查通过
- 17 项纯逻辑冒烟全部通过：json_path 取值与缺失、7 种断言算子、schema 校验（required/type/enum）、变量替换、凭据脱敏
- `parse_contract.py` 对 `assets/example_openapi.json` 解析出 3 接口 / 10 个状态码分支
- `report.py` 生成的 HTML 报告结构完整

**尚未验证**：`run_cases.py` 与 `triage.py` 的真实 HTTP 路径（需要可用的 base URL 与 token 才能跑）。首次使用时建议先跑 `--health-check`，再挑 1~2 条只读用例试跑。

## 资源文件

- `references/source-mapping.md`：从 OpenAPI path 定位服务端 handler（按框架分类的探测手法）
- `references/case-matrix.md`：用例设计矩阵（类别、优先级、断言写法）
- `references/failure-taxonomy.md`：归因决策树与典型信号
- `references/repair-playbook.md`：黄灯修复手册
- `references/anti-patterns.md`：反模式与常见自我合理化
- `assets/case-schema.json`：用例文件格式定义
- `scripts/parse_contract.py`：OpenAPI → 接口清单
- `scripts/run_cases.py`：执行器（含 health-check）
- `scripts/triage.py`：flaky 判别与归因辅助
- `scripts/report.py`：报告生成
