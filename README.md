# 经营看板底数核对助手

`dashboard-data-auditor` · V0.1.0 · 中文 · 本地只读

面向财务BP和经营分析业务Owner，将“这个看板的数据到底能不能用”变成可复跑的口径确认、质量检查、差异证据和验收建议。

**这是可运行的skill包，不只有提示词。** 核心脚本使用Python标准库；需要先提供规范化CSV和明确配置。没有内置数据库或Ozon API连接，没有使用真实商家数据验证，也未获得平台官方认证。

## 实际能力

| 模块 | 已实现 |
| --- | --- |
| 可比性门禁 | 销售/结算/混合、期间、时区、过滤、快照、币种及成本/费用等版本不一致时停止强行对账 |
| 单源质量 | 缺列、空关键字段、重复真实粒度、无效/缺失数字、非整数数量、日期异常、空文件 |
| 底数差异 | 参考来源→待核来源，逐分组和总计比较；识别单侧缺组及总额相等但明细对冲 |
| 利润复算 | 按显式配置的同币种线性公式复算，不猜测经济含义、不使用固定平台费率 |
| 脱敏输出 | SKU、店铺等维度用HMAC别名关联，原始维度值、额外产品链接与输入路径不输出 |
| 可复核报告 | Markdown摘要、CSV差异和异常、JSON精确数值与来源指纹；可选宿主Excel导出 |

未实现：自动采集数据库/API、自动业务根因判断、自动换汇、自动费用分摊、不同口径自动桥接、源系统完整性认证。规则与调查模板已放入references，**不能把模板当成已自动化的功能**。

## 运行合成示例

在解压后的 `dashboard-data-auditor` 目录执行：

```bash
python3 scripts/audit.py --config examples/demo_config.json --out local-output/demo
```

样例**故意**设置了漏掉一个SKU分组、收入差异和利润错误，因此退出码2且发现异常是正确结果。再次运行要换一个新的输出目录，防止覆盖。

```text
local-output/demo/
├── report.md
├── differences.csv
├── issues.csv
└── report.json
```

测试：

```bash
python3 -m unittest discover -s tests -v
```

样例只是合成数据，不包含真实公司配置或历史经营数字。

## 使用真实底数

把配置副本和数据放在被忽略的 `local-data/` 或仓库外，编辑字段映射、真实唯一键、共有核对粒度和全部scope字段。每个来源需声明行粒度，不能用“店铺+SKU”代替真实订单SKU行来检查重复。

原始报表→数仓底表运行一次；数仓底表→看板导出再运行一次。核对期间与快照必须有依据。不要为了让程序运行而把不一致的口径文字改成相同。

```bash
python3 scripts/audit.py --config local-data/raw_to_warehouse.local.json --out local-output/raw_to_warehouse_v1
python3 scripts/audit.py --config local-data/warehouse_to_dashboard.local.json --out local-output/warehouse_to_dashboard_v1
```

同一运行里脱敏标识稳定。跨运行/跨链路需要保持别名时，使用同一受控会话中的秘密密钥：

```bash
export AUDIT_MASK_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
```

不要把该变量值写到对话、配置或仓库。每次重新执行上面的命令都会生成新密钥，从而改变别名。真实金额仍然机密，报告不能因为脱敏就公开。

详细配置见 [references/configuration.md](references/configuration.md)。

## 作为Skill使用

本包按Agent Skills目录规范提供 `SKILL.md`、`scripts/`、`references/` 和 `assets/`。将整个 `dashboard-data-auditor` 文件夹交给支持该规范的宿主并按其当前说明安装；不要只复制SKILL.md而遗漏脚本与模板。

任务示例：

> 使用 dashboard-data-auditor 核对我授权的原始报表、数仓底表和看板导出。先列出可比性与字段映射，再分两段核对。不得自动更改生产表。输出脱敏证据、差异表、问题清单和业务验收建议；无法验证的项目明确标记。

## Excel报告（可选）

核心运行不依赖第三方库。**仅当宿主已经提供 `artifact_tool` 时**，运行：

```bash
python3 scripts/export_xlsx.py --report local-output/demo/report.json --out local-output/demo/report.xlsx
```

包含“验收摘要、底数差异、异常清单、核对说明”四页；差额和差异率使用Excel公式。JSON是精确十进制证据，Excel受有效位数与浮点限制，只用于展示。没有该宿主能力时交付CSV/JSON，不需要为使用核心功能安装它。

Excel输入需先在经授权环境转成保留文本ID的CSV；核心脚本不读取xlsx。

## 发布到GitHub

默认只创建**当前认证用户下的新私有仓库**，不覆盖已有仓库、不改变既有权限，不上传本地数据或旧Git历史。项目未指定开源许可证。

先确保本机有Python 3.10+、git和GitHub CLI；Mac且已安装Homebrew时可用 `brew install gh` 安装CLI。然后在项目目录运行：

```bash
python3 scripts/publish_github.py
```

未登录时会进入GitHub CLI官方浏览器授权流程；不要向对话粘贴Token。已认证后脚本运行测试、检查交付文件SHA-256、只复制发布清单中的文件至全新临时目录，再提交并推送，最后校验远端提交与私有状态。

仓库默认名 `dashboard-data-auditor`。重名时会停止；指定另一个新名字：

```bash
python3 scripts/publish_github.py --repo dashboard-data-auditor-v01
```

离线检查，不创建仓库：

```bash
python3 scripts/publish_github.py --prepare-only local-output/publication-check
```

当前包的存在不表示已经远端发布成功。必须以脚本输出的GitHub URL和匹配的commit为准。第一次发布后，日常维护应克隆远端仓库建立正常代码审查流程；本脚本不是覆盖更新工具。

## 验收边界

“已配置检查通过”不等于业务Owner验收通过。相同元数据不证明SQL实现正确；双方都漏了的记录也不可能只靠两份相同导出发现。来源完整性、关键口径和业务风险需要独立证据。

业务Owner负责需求、指标定义和验收；数据团队负责底表、加工和修复。报告只给出建议责任方，不捏造责任认领或排期。

## 资料与安全

- [SKILL.md](SKILL.md)：AI执行流程。
- [SECURITY.md](SECURITY.md)：数据与发布边界。
- [references/reconciliation-rules.md](references/reconciliation-rules.md)：核对矩阵和桥接方法。
- [references/ozon-checklist.md](references/ozon-checklist.md)：Ozon调查项，不预设官方规则。
- [references/sources.md](references/sources.md)：格式与发布命令的官方资料。
