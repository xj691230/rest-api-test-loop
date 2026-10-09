# rest-api-test-loop

给开发人员自测**新开发的 REST 接口**用的测试 skill：接口刚写完、提测或合码之前，跑一轮「**生成用例 → 实跑验证 → 归因 → 修复 → 复跑**」闭环，把该修的在自己手里修完再交出去。

黑盒 HTTP 驱动：被测服务零改动、零编译，一轮反馈是秒级而不是编译级——写完接口当场就能跑，不等编译排队的空档。

## 它解决什么问题

- 新写的接口懒得配测试，提测后被打回，一来一回半天就没了
- 新接口的 OpenAPI 是顺手写的，跟实现早就不一致，自己也不知道差在哪
- 红灯分不清是**接口 bug**、**用例自己写错**，还是**环境在抖**
- 为了跑测试去改被测代码，改完还要重新编译部署

## 核心机制

| 机制 | 说明 |
|---|---|
| 黑盒 HTTP 用例 | 声明式 JSON 用例 + Python 执行器，不写进被测代码库 |
| 契约 × 源码交叉验证 | spec 是**意图**、源码是**事实**；冲突登记为契约漂移交人工裁决，不自行选边 |
| 失败三分类 | 红灯（产品缺陷）· 黄灯（用例缺陷）· 灰灯（环境噪声），另设 flaky 判别（0/3、1-2/3、3/3） |
| 分类先于修复 | 未归因的红灯不许动任何东西；断言强度只增不减 |
| 3 轮上限 | 防闭环空转，原地打转或引入回归就停下升级人工 |

## 目录结构

```
├── docs/
│   └── v0.3-design.md          # 下一版设计稿（数据供给子系统），尚未实现
└── skill/
    └── rest-api-test-loop/     # skill 本体，可直接复制到 skills 目录
        ├── SKILL.md
        ├── references/         # 源码定位、用例矩阵、归因手册、修复手册、反模式
        ├── scripts/            # 契约解析 / 执行器 / 归因 / 报告
        └── assets/             # 用例 schema、示例 OpenAPI
```

## 安装

把 `skill/rest-api-test-loop` 整个目录复制到你的 agent skills 目录即可：

```bash
cp -r skill/rest-api-test-loop ~/.workbuddy/skills/
```

## 使用

前置输入：OpenAPI 文件、源码仓路径、目标环境 base URL、鉴权 token。

```
token     → 环境变量 API_TEST_TOKEN（禁止写进用例文件或报告）
base URL  → 环境变量 API_TEST_BASE_URL 或 --base-url
```

直接跑脚本：

```bash
python scripts/parse_contract.py --spec openapi.yaml --out workspace/endpoints.json
python scripts/run_cases.py --health-check --base-url "$BASE_URL"
python scripts/run_cases.py --cases workspace/cases.json --out workspace/execution_results.json
python scripts/triage.py --results workspace/execution_results.json --mode flaky
python scripts/report.py --results workspace/execution_results.json --out workspace/report.html
```

也可以在支持 skill 的 agent 里直接说「测一下 XX 接口」，由 agent 按 SKILL.md 的 9 步流程跑完整闭环。

## 已知边界

- 脚本零第三方依赖（仅标准库），Python 3.7+；YAML 格式的 spec 需要 `pyyaml`
- `run_cases.py` 与 `triage.py` 的真实 HTTP 路径尚未在真实服务上验证过，首次使用建议先 `--health-check`
- 当前版本 **v0.2.0**；`docs/v0.3-design.md` 是「测试数据供给子系统」的设计稿，尚未实现

## 设计思路来源

设计稿 `docs/v0.3-design.md` 里有完整的业界调研映射：Schemathesis（契约 examples 优先取值、属性用例）、RESTler（生产者-消费者依赖链）、EvoMaster（用户值字典）、Karate（数据与用例分离），以及刻意不采纳的方案与理由。

## License

尚未附带 LICENSE，按需自行添加（如 MIT）。
