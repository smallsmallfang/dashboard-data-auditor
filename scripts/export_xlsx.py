#!/usr/bin/env python3
"""Optional XLSX presentation adapter. Requires artifact_tool in the host environment.
Core audit.py remains dependency-free; report.json remains the precision source of truth.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def as_number(value):
    return None if value in (None, '') else float(value)


def literal(value):
    text = str(value if value is not None else '')
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def make_workbook(report: dict):
    from artifact_tool import Workbook
    wb = Workbook.create()
    wb.worksheets.add("验收摘要")
    header = {'fill': '#153243', 'font': {'bold': True, 'color': '#FFFFFF'},
              'row_height': 26, 'wrap_text': True}

    def sheet(name, title, subtitle, headers, rows, widths):
        s = wb.worksheets.get_or_add(name)
        n = len(headers)
        s.get_range_by_indexes(0, 0, 1, n).merge()
        s.get_cell(0, 0).values = [[title]]
        s.get_range_by_indexes(0, 0, 1, n).format = {'fill': '#153243', 'font': {'bold': True, 'size': 18, 'color': '#FFFFFF'}, 'row_height': 38}
        s.get_range_by_indexes(1, 0, 1, n).merge()
        s.get_cell(1, 0).values = [[subtitle]]
        s.get_range_by_indexes(1, 0, 1, n).format.wrap_text = True
        s.get_range_by_indexes(1, 0, 1, n).format.row_height = 34
        s.get_range_by_indexes(3, 0, 1, n).values = [headers]
        s.get_range_by_indexes(3, 0, 1, n).format = header
        if rows:
            s.get_range_by_indexes(4, 0, len(rows), n).values = rows
            s.get_range_by_indexes(4, 0, len(rows), n).format = {'font': {'size': 10}, 'wrap_text': True, 'row_height': 32}
        for i, width in enumerate(widths):
            s.get_range_by_indexes(0, i, max(len(rows)+4, 8), 1).format.column_width = width
        s.freeze_panes.freeze_rows(4)
        return s

    rows = [[literal(x['key_token']), literal(x['metric']), as_number(x['left']), as_number(x['right']), None, None, literal(x['status'])] for x in report['comparisons']]
    dif = sheet('底数差异', '底数差异 | 分组与总计', '差额=右侧-左侧；缺失不按零；Excel用于展示，精确十进制以report.json为准。',
                ['脱敏分组', '指标', '参考来源', '待核来源', '差额', '差异率', '检查状态'], rows, [34,18,17,17,17,15,16])
    end = max(5, len(rows)+4)
    if rows:
        dif.get_range(f'E5:F{end}').formulas = [[f'=IF(OR(C{r}="",D{r}=""),"",D{r}-C{r})', f'=IF(OR(C{r}="",D{r}="",C{r}=0),"",(D{r}-C{r})/ABS(C{r}))'] for r in range(5,end+1)]
        dif.get_range(f'C5:E{end}').set_number_format('#,##0.00;[Red](#,##0.00);0.00')
        dif.get_range(f'F5:F{end}').set_number_format('0.00%;[Red](0.00%);0.00%')
        dif.get_range(f'G5:G{end}').conditional_formats.add_custom('=G5="DIFF"', {'fill': '#FCE4D6'})
        dif.get_range(f'G5:G{end}').conditional_formats.add_custom('=G5="MISSING"', {'fill': '#FFF2CC'})
        dif.tables.add(f'A4:G{end}', True, 'DifferenceTable')

    issues = [[literal(x[k]) for k in ['issue_id','code','dataset','severity','row','metric','key_token','detail','suggested_owner','status','due_date']] for x in report['issues']]
    ish = sheet('异常清单', '异常清单 | 证据与处理', '建议责任方并非已认领；FAIL为失败项，BLOCK为阻断项。不得用“时间差”替代未确认根因。',
                ['问题ID','检查规则','所在环节','级别','原始行号','指标','脱敏分组','已确认事实','建议责任方','跟进状态','完成日期'],
                issues,[18,34,12,12,12,18,34,40,35,15,15])
    iend=max(5,len(issues)+4)
    if issues:
        ish.get_range(f'A5:K{iend}').format.row_height=48
        ish.get_range(f'J5:J{iend}').data_validation={'rule':{'type':'list','values':['待确认','待修复','待复核','已关闭']}}
        ish.tables.add(f'A4:K{iend}',True,'IssueTable')

    summary_rows = [
        ['程序检查结论',literal(report['status'])],
        ['跨表数值比较','已执行' if report['comparison_executed'] else '未执行'],
        ['失败项数',None],['阻断项数',None],
        ['币种',literal(report['currency'])],
        ['业务验收','尚未由业务Owner验收'],
        ['适用范围','仅本次输入、显式配置和已执行检查'],
        ['保密边界','标识脱敏不等于可公开；真实金额报告只能内部使用'],
        ['下一步','按异常清单补充证据或修复后复核；责任方与日期待双方确认'],
    ]
    overview = sheet('验收摘要','经营看板底数核对 | V0.1','报告由本地核对结果生成。示例使用纯合成数据，不能据此宣称生产看板验收通过。',
                     ['核对项目','结果与说明'],summary_rows,[24,90])
    overview.get_range('B7').formulas=[[f'=COUNTIF(\'异常清单\'!D5:D{iend},"FAIL")']]
    overview.get_range('B8').formulas=[[f'=COUNTIF(\'异常清单\'!D5:D{iend},"BLOCK")']]
    overview.get_range('B5').format={'font':{'bold':True,'size':14},'fill':'#FFF2CC'}

    notes = [
        ['覆盖边界',literal(report['coverage'])],
        ['判断原则','先查可比性，再查质量，最后按共有粒度逐组比较。总计相等不能证明明细正确。'],
        ['数量规则','整数校验；缺失、空值与无效值不补零。'],
        ['货币规则','程序不换汇；所有输入金额必须由数据提供方规范化成声明币种。'],
        ['利润规则','按配置显式声明的同币种线性公式复算，公式没有被独立认证。'],
        ['数字精度','JSON使用十进制文本；Excel受浮点及有效位数限制，只作展示与常规复核。'],
        ['元数据可信度','scope相同只是使用者声明一致，并未证明源系统和SQL实现正确。'],
        ['来源完整性','双方同时缺失的记录需要额外控制总表，当前程序不能单独发现。'],
        ['版本',literal(report['version'])],
        ['生成时间UTC',literal(report['generated_at_utc'])],
        ['配置SHA-256',literal(report['config_sha256'])],
    ]
    for s in report['sources']:
        notes.append([f"来源 {s['dataset']}",f"行数={s['row_count']}；SHA-256={s['sha256']}"])
    sheet('核对说明','核对说明 | 范围与限制','保留本地配置和原始输入以便复跑；本表不写入原路径、原始SKU、产品名或链接。',
          ['说明项目','核对记录'],notes,[24,100])
    return wb


def main():
    parser=argparse.ArgumentParser(description='Optional XLSX report using host artifact_tool')
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.out.exists():
        parser.error('Output file already exists; choose a new file.')
    try:
        from artifact_tool import SpreadsheetFile
    except ImportError:
        parser.exit(3,'宿主未提供artifact_tool。核心CSV/JSON报告仍可用；未生成Excel。\n')
    report=json.loads(args.report.read_text(encoding='utf-8'))
    wb=make_workbook(report)
    SpreadsheetFile.export_xlsx(wb).save(str(args.out))
    print('Excel报告已生成。')

if __name__=='__main__':
    main()
