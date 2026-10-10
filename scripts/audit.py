#!/usr/bin/env python3
"""Local, read-only dashboard reconciliation. Python 3.10+, standard library only."""
from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

VERSION = "0.1.0"
SCOPE_FIELDS = (
    "basis", "date_basis", "period_start", "period_end", "timezone", "currency",
    "filters_id", "metric_definitions_id", "cost_policy", "fx_policy",
    "allocation_policy", "estimate_policy", "tax_policy", "snapshot_at",
)
CANONICAL = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
DECIMAL = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
OWNER = {
    "scope": "业务Owner确认口径，数据团队提供实现证据",
    "data": "数据团队定位处理，业务Owner验收",
    "formula": "业务Owner确认公式，数据团队复算",
    "evidence": "数据提供方补证，业务Owner复核",
}


class ConfigError(ValueError):
    """Messages contain no raw values, input paths, credentials, or identifiers."""


def dec(value: Any, decimal_mark: str = ".", thousands: str = "") -> Decimal:
    """Never infer separators or convert missing values to zero."""
    if isinstance(value, bool) or value is None:
        raise ValueError("invalid number")
    s = str(value).strip()
    if thousands:
        if thousands == " ":
            s = s.replace("\u00a0", " ").replace("\u202f", " ")
        sign = ""
        if s[:1] in ("+", "-"):
            sign, s = s[0], s[1:]
        parts = s.split(decimal_mark)
        if len(parts) > 2 or (len(parts) == 2 and thousands in parts[1]):
            raise ValueError("invalid number")
        integer = parts[0]
        if thousands in integer:
            groups = integer.split(thousands)
            if not (1 <= len(groups[0]) <= 3 and all(g.isdigit() for g in groups)
                    and all(len(g) == 3 for g in groups[1:])):
                raise ValueError("invalid number")
        s = sign + s.replace(thousands, "")
    if decimal_mark != ".":
        if "." in s:
            raise ValueError("invalid number")
        s = s.replace(decimal_mark, ".")
    if len(s) > 60 or not DECIMAL.fullmatch(s):
        raise ValueError("invalid number")
    try:
        n = Decimal(s)
    except InvalidOperation as exc:
        raise ValueError("invalid number") from exc
    if not n.is_finite():
        raise ValueError("invalid number")
    return n


def iso_date(value: Any) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must be YYYY-MM-DD")
    return date.fromisoformat(value)


def number(value: Decimal | None) -> str:
    return "" if value is None else format(value, "f")


def within(expected: Decimal, actual: Decimal, rule: dict) -> bool:
    absolute = dec(rule.get("abs_tolerance", "0"))
    relative = dec(rule.get("rel_tolerance", "0"))
    # Relative tolerance is anchored to the declared reference (left), not the larger side.
    return abs(actual - expected) <= max(absolute, abs(expected) * relative)


def safe_cell(value: Any) -> str:
    s = str(value if value is not None else "")
    # Prevent CSV formula execution after whitespace trimming in common spreadsheet clients.
    return "'" + s if s.lstrip().startswith(("=", "+", "-", "@")) else s


class Masker:
    def __init__(self, key: bytes | None = None):
        self.key = key or secrets.token_bytes(32)
        if len(self.key) < 32:
            raise ConfigError("脱敏密钥至少为32字节。")

    def token(self, namespace: str, value: Any) -> str:
        payload = json.dumps([namespace, value], ensure_ascii=False, separators=(",", ":"))
        digest = hmac.new(self.key, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
        return f"{namespace}_{digest}"


class Auditor:
    def __init__(self, config: dict, base_dir: Path, key: bytes | None = None):
        self.cfg = config
        self.base = base_dir.resolve()
        self.mask = Masker(key)
        self.issues: list[dict] = []
        self.comparisons: list[dict] = []
        self.sources: list[dict] = []
        self._validate()

    def _validate(self) -> None:
        c = self.cfg
        if c.get("schema_version") != 1:
            raise ConfigError("配置需要 schema_version=1。")
        if not isinstance(c.get("keys"), list) or not c["keys"] or len(set(c["keys"])) != len(c["keys"]):
            raise ConfigError("keys 必须为不重复的标准字段列表。")
        if not isinstance(c.get("metrics"), dict) or not c["metrics"]:
            raise ConfigError("必须显式配置 metrics。")
        if set(c["keys"]) & set(c["metrics"]):
            raise ConfigError("维度字段与指标字段不可重名。")
        if any(not CANONICAL.fullmatch(str(n)) for n in [*c["keys"], *c["metrics"]]):
            raise ConfigError("标准字段名只能使用小写英文、数字和下划线。")
        for rule in c["metrics"].values():
            if not isinstance(rule, dict) or rule.get("kind") not in ("quantity", "amount"):
                raise ConfigError("仅支持可加总的 quantity/amount；比率需由分子分母复算。")
            for k in ("abs_tolerance", "rel_tolerance"):
                try:
                    if dec(rule.get(k, "0")) < 0:
                        raise ValueError()
                except ValueError as exc:
                    raise ConfigError("容差必须为有限非负数。") from exc
        datasets = c.get("datasets")
        if not isinstance(datasets, dict) or set(datasets) != {"left", "right"}:
            raise ConfigError("必须配置 left 和 right 两个数据集。")
        for label, spec in datasets.items():
            if not isinstance(spec, dict) or not isinstance(spec.get("path"), str):
                raise ConfigError("数据集必须提供 CSV path。")
            if spec.get("scope", {}).get("basis") not in ("sales", "settlement", "hybrid"):
                raise ConfigError("basis 需显式设置 sales/settlement/hybrid。")
            if not re.fullmatch(r"[A-Z]{3}", str(spec["scope"].get("currency", ""))):
                raise ConfigError("currency 必须为3位大写币种代码；不得多币种混加。")
            mapping = spec.get("columns", {})
            unique = spec.get("unique_key")
            date_field = spec.get("date_field")
            if not isinstance(unique, list) or not unique or len(set(unique)) != len(unique):
                raise ConfigError("每个来源必须声明真实行粒度 unique_key。")
            required = set(c["keys"]) | set(c["metrics"]) | set(unique) | {date_field}
            if not date_field or not required <= set(mapping):
                raise ConfigError("columns 缺少分组键、指标、日期或唯一键映射。")
            if any(not CANONICAL.fullmatch(str(n)) for n in mapping):
                raise ConfigError("映射中的标准字段名无效。")
            if any(not isinstance(v, str) or not v.strip() for v in mapping.values()):
                raise ConfigError("原始列名必须为非空文本。")
            if len(set(mapping.values())) != len(mapping):
                raise ConfigError("同一个原始列不能被重复映射成多个标准字段。")
            dm, ts = spec.get("decimal_mark", "."), spec.get("thousands_separator", "")
            if dm not in (".", ",") or ts not in ("", ".", ",", " ") or dm == ts:
                raise ConfigError("数字小数符/千分位配置无效。")
            if not isinstance(spec.get("delimiter", ","), str) or len(spec.get("delimiter", ",")) != 1:
                raise ConfigError("CSV 分隔符必须是单字符。")
            try:
                start = iso_date(spec["scope"]["period_start"])
                end = iso_date(spec["scope"]["period_end"])
                if start > end:
                    raise ValueError()
            except (KeyError, ValueError, TypeError) as exc:
                raise ConfigError("每个来源必须配置有效起止日期，格式 YYYY-MM-DD。") from exc
        formula = c.get("profit_formula")
        if formula is not None:
            if not isinstance(formula, dict) or formula.get("target") not in c["metrics"]:
                raise ConfigError("利润公式 target 必须为已配置指标。")
            terms = formula.get("terms")
            if not isinstance(terms, dict) or not terms or not set(terms) <= set(c["metrics"]):
                raise ConfigError("利润公式 terms 必须为已配置指标与数值系数。")
            if formula["target"] in terms:
                raise ConfigError("利润公式不得引用目标本身。")
            try:
                for factor in terms.values():
                    dec(factor)
            except ValueError as exc:
                raise ConfigError("利润系数必须为数值，禁止执行表达式。") from exc
            if any(c["metrics"][m]["kind"] != "amount" for m in [formula["target"], *terms]):
                raise ConfigError("利润公式只能使用同币种金额指标。")

    def issue(self, code: str, category: str, severity: str, dataset: str,
              row: int | str = "", metric: str = "", key: Any = None,
              detail: str = "", expected: Decimal | None = None,
              actual: Decimal | None = None) -> None:
        self.issues.append({
            "issue_id": f"ISSUE_{len(self.issues)+1:04d}", "code": code,
            "category": category, "severity": severity, "dataset": dataset,
            "row": row, "metric": metric,
            "key_token": self.mask.token("key", key) if key is not None else "",
            "expected": number(expected), "actual": number(actual),
            "difference": number(actual-expected) if actual is not None and expected is not None else "",
            "detail": detail, "suggested_owner": OWNER[category],
            "status": "待确认", "due_date": "待约定",
        })

    def scope_gate(self) -> bool:
        left, right = (self.cfg["datasets"][s]["scope"] for s in ("left", "right"))
        ok = True
        for field in SCOPE_FIELDS:
            values = [left.get(field), right.get(field)]
            if any(not isinstance(v, str) or not v.strip() for v in values):
                ok = False
                self.issue("SCOPE_MISSING", "scope", "BLOCK", "both", metric=field,
                           detail="缺少核对口径证据；不使用默认口径补齐。")
            elif values[0] != values[1]:
                ok = False
                self.issue("SCOPE_MISMATCH", "scope", "BLOCK", "both", metric=field,
                           detail="口径元数据不同，停止等式对账；先做有证据的差异桥接。")
        return ok

    def load(self, label: str) -> tuple[list[dict], bool]:
        spec = self.cfg["datasets"][label]
        path = (self.base / spec["path"]).resolve()
        records: list[dict] = []
        ok = True
        try:
            raw_bytes = path.read_bytes()
        except OSError:
            self.issue("INPUT_UNAVAILABLE", "evidence", "BLOCK", label, detail="无法读取输入文件；未记录原路径。")
            return [], False
        self.sources.append({"dataset": label, "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                             "source_token": self.mask.token("source", str(path)),
                             "byte_count": len(raw_bytes), "row_count": 0})
        try:
            text = raw_bytes.decode(spec.get("encoding", "utf-8-sig"))
            # newline='' is important for quoted CSV fields with embedded newlines.
            import io
            reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=spec.get("delimiter", ","), strict=True)
            headers = reader.fieldnames
            if not headers or any(not x for x in headers) or len(headers) != len(set(headers)):
                raise ConfigError("CSV表头为空、缺失或重复。")
            if not set(spec["columns"].values()) <= set(headers):
                raise ConfigError("CSV缺少映射指定列。")
            seen: set[tuple] = set()
            first = iso_date(spec["scope"]["period_start"])
            last = iso_date(spec["scope"]["period_end"])
            count = 0
            for count, raw in enumerate(reader, 1):
                row_num = reader.line_num
                if None in raw or any(v is None for v in raw.values()):
                    self.issue("CSV_ROW_WIDTH", "data", "FAIL", label, row_num, detail="行字段数与表头不一致。")
                    ok = False
                    continue
                row = {k: raw[v].strip() for k, v in spec["columns"].items()}
                required_text = set(self.cfg["keys"]) | set(spec["unique_key"]) | {spec["date_field"]}
                missing = [k for k in required_text if not row[k]]
                if missing:
                    ok = False
                    for k in sorted(missing):
                        self.issue("NULL_KEY", "data", "FAIL", label, row_num, k, detail="关键维度/唯一键/日期为空。")
                    continue
                unique = tuple(row[k] for k in spec["unique_key"])
                group = tuple(row[k] for k in self.cfg["keys"])
                if unique in seen:
                    ok = False
                    self.issue("DUPLICATE_KEY", "data", "FAIL", label, row_num, key=group,
                               detail="声明的真实行粒度重复；不自行去重或选第一条。")
                seen.add(unique)
                try:
                    d = iso_date(row[spec["date_field"]])
                    if not first <= d <= last:
                        raise ValueError()
                except ValueError:
                    ok = False
                    self.issue("DATE_INVALID_OR_OUT_OF_SCOPE", "data", "FAIL", label, row_num,
                               metric=spec["date_field"], key=group, detail="日期无效或不在声明范围；不静默删除。")
                for metric, rule in self.cfg["metrics"].items():
                    try:
                        value = dec(row[metric], spec.get("decimal_mark", "."), spec.get("thousands_separator", ""))
                        if rule["kind"] == "quantity" and value != value.to_integral_value():
                            raise ValueError()
                        row[metric] = value
                    except ValueError:
                        ok = False
                        self.issue("INVALID_NUMBER", "data", "FAIL", label, row_num, metric, group,
                                   detail="空值/非数值/歧义分隔符/非整数数量；不按零处理。")
                        row[metric] = None
                row["_group"] = group
                row["_row"] = row_num
                records.append(row)
            self.sources[-1]["row_count"] = count
            if count == 0:
                self.issue("EMPTY_INPUT", "evidence", "BLOCK", label, detail="输入没有数据行；两个空文件不构成通过证据。")
                ok = False
        except (UnicodeError, LookupError, csv.Error, ConfigError):
            self.issue("INPUT_SCHEMA_INVALID", "evidence", "BLOCK", label,
                       detail="CSV编码、结构或映射不匹配；请在受控环境检查输入。")
            ok = False
        return records, ok

    def check_profit(self, label: str, records: list[dict]) -> None:
        formula = self.cfg.get("profit_formula")
        if not formula:
            return
        target, terms = formula["target"], formula["terms"]
        for row in records:
            if any(row[m] is None for m in [target, *terms]):
                continue
            expected = sum((row[m]*dec(f) for m, f in terms.items()), Decimal(0))
            if not within(expected, row[target], self.cfg["metrics"][target]):
                self.issue("PROFIT_FORMULA_MISMATCH", "formula", "FAIL", label,
                           row["_row"], target, row["_group"],
                           "按用户声明的同币种线性公式复算不符；原因尚未确认。", expected, row[target])

    def aggregate(self, records: list[dict]) -> dict:
        result: dict = defaultdict(lambda: {m: Decimal(0) for m in self.cfg["metrics"]})
        for row in records:
            for m in self.cfg["metrics"]:
                result[row["_group"]][m] += row[m]
        return dict(result)

    def compare(self, left: dict, right: dict) -> None:
        metrics = self.cfg["metrics"]
        for key in sorted(set(left) | set(right)):
            key_token = self.mask.token("key", key)
            if key not in left or key not in right:
                side = "left" if key not in left else "right"
                self.issue("MISSING_GROUP", "data", "FAIL", side, key=key,
                           detail="对侧存在该分组，本侧缺失；缺失不等于业务零值。")
            for metric, rule in metrics.items():
                a = left.get(key, {}).get(metric)
                b = right.get(key, {}).get(metric)
                status = "MISSING" if a is None or b is None else ("MATCH" if within(a, b, rule) else "DIFF")
                self.comparisons.append({
                    "key_token": key_token,
                    "dimensions": {name: self.mask.token(name, value) for name, value in zip(self.cfg["keys"], key)},
                    "metric": metric, "left": number(a), "right": number(b),
                    "difference": number(b-a) if a is not None and b is not None else "",
                    "relative_difference": number((b-a)/abs(a)) if a not in (None, Decimal(0)) and b is not None else "",
                    "status": status,
                })
                if status == "DIFF":
                    self.issue("VALUE_MISMATCH", "data", "FAIL", "both", metric=metric, key=key,
                               detail="同一声明口径下分组金额/数量超出配置容差；未断言根因。", expected=a, actual=b)
        for metric, rule in metrics.items():
            a = sum((x[metric] for x in left.values()), Decimal(0))
            b = sum((x[metric] for x in right.values()), Decimal(0))
            self.comparisons.append({"key_token": "TOTAL", "metric": metric,
                                     "left": number(a), "right": number(b), "difference": number(b-a),
                                     "relative_difference": number((b-a)/abs(a)) if a else "",
                                     "status": "MATCH" if within(a, b, rule) else "DIFF"})
            if not within(a, b, rule):
                self.issue("TOTAL_MISMATCH", "data", "FAIL", "both", metric=metric,
                           detail="来源总计差异。总计不能替代逐分组核对。", expected=a, actual=b)

    def run(self) -> dict:
        with localcontext() as context:
            context.prec = 80
            scope_ok = self.scope_gate()
            left, left_ok = self.load("left")
            right, right_ok = self.load("right")
            self.check_profit("left", left)
            self.check_profit("right", right)
            executed = scope_ok and left_ok and right_ok
            if executed:
                self.compare(self.aggregate(left), self.aggregate(right))
        counts = Counter(i["severity"] for i in self.issues)
        status = "不通过" if counts["FAIL"] else ("无法验收" if counts["BLOCK"] else "已配置检查通过")
        return {
            "version": VERSION, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": status, "comparison_executed": executed,
            "scope_gate_passed": scope_ok, "fail_count": counts["FAIL"], "blocked_count": counts["BLOCK"],
            "coverage": "仅本次输入及显式配置；未代表完整生产链路验收",
            "currency": self.cfg["datasets"]["left"]["scope"].get("currency", ""),
            "config_sha256": hashlib.sha256(json.dumps(self.cfg, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            "sources": self.sources, "comparisons": self.comparisons, "issues": self.issues,
        }


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: safe_cell(json.dumps(row[k], ensure_ascii=False) if isinstance(row.get(k), dict) else row.get(k, "")) for k in fields} for row in rows)


def export_report(result: dict, target: Path) -> None:
    # New directory only: prevent accidental overwrites and prohibit replacing source data.
    target.mkdir(parents=True, exist_ok=False)
    (target / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(target / "differences.csv", ["key_token", "dimensions", "metric", "left", "right", "difference", "relative_difference", "status"], result["comparisons"])
    write_csv(target / "issues.csv", ["issue_id", "code", "category", "severity", "dataset", "row", "metric", "key_token", "expected", "actual", "difference", "detail", "suggested_owner", "status", "due_date"], result["issues"])
    summary = (
        f"# 经营看板底数核对报告\n\n"
        f"**程序检查结论：{result['status']}**\n\n"
        f"已发现失败项：{result['fail_count']}；阻断项：{result['blocked_count']}。\n\n"
        f"跨表数值比较：{'已执行' if result['comparison_executed'] else '未执行，先处理阻断/输入质量问题'}。\n\n"
        "本结论仅覆盖本次输入和配置，不等于业务Owner已经验收。相同口径下的汇总一致不证明明细正确。\n\n"
        "## 交付与下一步\n\n"
        "逐组差异见 differences.csv，异常与建议责任方见 issues.csv，运行证据见 report.json。\n"
        "已确认事实和待确认原因分开；处理人/完成日期仍需双方认领。\n\n"
        "## 保密边界\n\n"
        "输出不包含原始维度值、产品名、链接及输入路径；仍包含真实金额和输入文件指纹，属于内部材料。\n"
        "默认每次运行使用临时HMAC密钥；跨运行关联须在环境变量 AUDIT_MASK_KEY 中提供同一32字节以上密钥，禁止上传密钥。\n"
    )
    (target / "report.md").write_text(summary, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only, local dashboard data reconciliation")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="A NEW output directory")
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ConfigError("配置根节点必须是对象。")
        key_value = os.environ.get("AUDIT_MASK_KEY")
        key = key_value.encode("utf-8") if key_value else None
        result = Auditor(config, args.config.parent, key).run()
        export_report(result, args.out)
        print(json.dumps({"status": result["status"], "fail_count": result["fail_count"],
                          "blocked_count": result["blocked_count"], "comparison_executed": result["comparison_executed"]}, ensure_ascii=False))
        return 0 if result["status"] == "已配置检查通过" else 2
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 3
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print("输入、配置或输出目录无效；未输出原始值/路径。请在本地检查文件及配置。", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
