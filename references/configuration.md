# 配置与数据契约

## 支持范围

V0.1是本地CSV核对器，不是平台API客户端、数据库采集器或自动ETL。每次比较两个来源；完整链路执行两次。Python 3.10+，核心无第三方依赖。输入必须是已经规范化的可加总金额与数量；同一配置仅一个币种。超大数据先由数据团队在受控环境聚合并保存完整性证据，程序目前会把CSV读入内存。

## 顶层

- `schema_version`: 当前为1。
- `keys`: 共有分组粒度，例如 `store,event_date,sku`。字段名使用英文字母小写、数字、下划线。
- `metrics`: 标准字段到规则的映射。`kind` 仅支持 `quantity` / `amount`。容差用十进制文本，默认为0。
- `datasets.left/right`: 两份待核对文件。left是参考来源，不保证它天然正确。
- `profit_formula`: 可选。`target` 为利润列，`terms` 是各金额字段的系数。只接受数值，不执行字符串表达式。用户必须先确认组成项和符号。

## 各来源

`path` 相对配置文件目录（也支持绝对本地路径）。`encoding` 默认utf-8-sig；`delimiter` 默认逗号；`decimal_mark` 默认句点，`thousands_separator` 默认空字符串即不使用千分位。

`columns` 显式映射“标准名: 原始表头”；禁止同一个源列重复映射多个标准名。额外原始列一律不输出。`unique_key` 是该来源**真实行粒度**，不是为了消除重复而临时拼凑的行号。`date_field` 对应 ISO 日期列（YYYY-MM-DD），须在 columns 中。

例如源数据唯一键可为店铺+订单+SKU+交易类型，数仓日汇总唯一键可为店铺+日期+SKU；两端按店铺+日期+SKU比较。没有订单/交易级证据时，不能声称验证了订单级无重复。

## scope 元数据

两端以下字段必须非空且一致，否则停止数值对比（仍执行单源质量和已声明公式检查）：

| 字段 | 必须写清 |
| --- | --- |
| basis | sales、settlement或hybrid，不得根据文件名猜测 |
| date_basis | 实際业务日期含义 |
| period_start / period_end | 两端闭区间，YYYY-MM-DD |
| timezone | 规范化时采用的时区；程序不转换时间戳 |
| currency | 3位大写代码；程序不换汇 |
| filters_id | 店铺/状态/业务范围过滤规则版本 |
| metric_definitions_id | 指标定义版本，含数量、收入、退款及正负号 |
| cost_policy | 成本来源与版本；不适用时写 not_applicable |
| fx_policy | 汇率方向与日期政策；不适用时写 not_applicable |
| allocation_policy | 分摊方法与版本；不适用时写 not_applicable |
| estimate_policy | 实际/预估组成及版本；不适用时写 not_applicable |
| tax_policy | 含税/未税定义与版本 |
| snapshot_at | 同一业务快照截止时间（不是凭空改成相同的导出时间） |

元数据相等只是**用户声明口径一致**，不是程序已证明SQL实现一致。不允许为了运行而把不同口径字段改成同样文字。

## 判断公式

`差额 = right - left`。

`差异率 = (right - left) / abs(left)`；left为0时留空，不生成无穷大或误报0%。

`通过容差 = abs(right-left) <= max(absolute_tolerance, abs(left)*relative_tolerance)`。

金额使用Decimal，默认不预先四舍五入；报告保留精确十进制文本。CSV中负数前加单引号以防公式注入，Excel汇总使用JSON原始数值生成。需要不同判定方法时应修改经过审批的规则而非扩大容差掩盖差异。

## 不进行的隐式处理

不填零、不去重、不反转费用符号、不转换币种、不解析歧义日期、不平均利润率、不对销售与结算做强行相等检验。空表、无效值、重复粒度等导致该段等式核对暂停，避免对不完整小计发出通过结果。

默认只检查两个CSV中已存在分组。两边同时漏掉的日期/店铺/SKU只有借助期望覆盖清单或控制总表才可发现；V0.1不自动补建该清单。
