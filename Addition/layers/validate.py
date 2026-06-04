"""第4层 校验不变量（含 v2 §5 + §13.3 增强）：不阻断写入，但把告警汇总进复核报告顶部。

校验项：
- 线路行数为 0（武汉式漏表防线）
- 价格区间 / 同吨位离群（§13.3）
- 列类型一致：吨位列应全为数字或全为空/"/"，混入异常符 → 疑似 OCR 错（§13.3）
- 系数自洽：多直读价反算基准价须 ≤0.5% 一致（在 calculate 内已逐行查，这里汇总缺价告警）
- 日期合理：结束 > 开始
- 省市一致：台账省 与 线路名前缀派生市 用省市词典做弱校验（§13.4）
- 低置信价格格强制标注人工核对（§13.2 无 VLM 兜底下的最后防线）
"""
import re
import logging
import statistics
from datetime import datetime

from data.geo import province_of_city, PROVINCE_ALIASES
from schemas import ENUM_ROUTE_NATURE

log = logging.getLogger("validate")

# 『比照 / 延申(延伸) / 新增邮路』定价机制的特征词
_EXTENSION_KW = ("比照", "延申", "延伸线路", "新增邮路")


def check_extension_routes(full_md, n_priced):
    """检测合同是否含『比照 / 延申线路 / 新增邮路』定价机制。

    背景：这类「大标线路合同」价格表只**直读**部分线路；发往『比照地』城市的"延申线路"
    由业务按比照规则**派生**（如：协议价 ÷ 原里程 × 新里程），且新里程多来自合同
    **未印出**的外部距离表。因此系统直读到的条数可能少于台账最终条数——
    系统只直读、绝不臆造派生（缺新里程会编造数据），命中机制时给出提示让人工补充。

    返回提示字符串；未命中返回 None。
    """
    text = full_md or ""
    if not any(k in text for k in _EXTENSION_KW):
        return None
    m = re.search(r"计算公式[为：:\s]*([^\n。]{0,140}。?)", text)
    formula = ("　折算公式：" + m.group(1).strip()) if m else ""
    return (f"⚠️ 本合同含『比照/延申线路』定价机制：价格表仅直读 {n_priced} 条线路。"
            f"发往『比照地』城市的延申线路通常需按比照规则**人工派生**"
            f"（新里程多来自合同未印出的外部距离表，系统不臆造、只直读），"
            f"台账最终行数可能多于直读条数，请人工核对是否需补充延申线路。{formula}")


def validate(result, field_map, price_range=(1, 1_000_000), enhance=None):
    warnings = []
    routes = result.routes
    tcols = {t["name"]: t for t in field_map.get("tonnage_columns", [])}

    # 1) 线路行数 == 0
    if not routes:
        warnings.append("线路行数为 0：未抽到任何线路明细（可能漏表或 OCR 失败），请人工核对。")

    # 2) 价格区间 + 低置信价格 + 列类型一致
    lo, hi = price_range
    by_col = {}  # 列 → [价格]，用于离群检测
    for i, r in enumerate(routes):
        prices = r.__dict__.get("最终吨位价格", {}) or {}
        for h, v in prices.items():
            if isinstance(v, (int, float)):
                if not (lo <= v <= hi):
                    warnings.append(f"第{i+1}条线路 {h}={v} 超出合理价格区间[{lo},{hi}]，请核对。")
                by_col.setdefault(h, []).append((i, v))

        # 武汉式：有线路但全无价格 → 强告警（§4.1）
        if r.__dict__.get("最终吨位价格") == {} or not any(
                isinstance(v, (int, float)) for v in prices.values()):
            warnings.append(f"⚠️ 第{i+1}条线路「{r.线路名称}」无任何价格：本合同内可能未给单价，"
                            f"价格列留空，禁止臆造，请人工确认价格来源。")

        # 低置信线路（证据未命中 / OCR 可疑）
        if (r.confidence or 1) < 0.7:
            warnings.append(f"第{i+1}条线路「{r.线路名称}」置信偏低({r.confidence})，请人工核对。")

        # 邮路性质枚举外（如"单边/双边"）：按口径保留原文但标低置信、进复核（不自动归一）
        nat = r.邮路性质
        if nat and str(nat).strip() and str(nat).strip() not in ENUM_ROUTE_NATURE:
            warnings.append(f"第{i+1}条线路「{r.线路名称}」邮路性质「{nat}」非标准枚举"
                            f"（{'/'.join(ENUM_ROUTE_NATURE)}），已保留原文，请人工确认。")

    # 2b) 同列价格离群（§13.3）：偏离中位数 > 3 倍 MAD
    for h, items in by_col.items():
        vals = [v for _, v in items]
        if len(vals) >= 4:
            med = statistics.median(vals)
            mad = statistics.median([abs(v - med) for v in vals]) or 1e-9
            for idx, v in items:
                if abs(v - med) > 3 * mad and abs(v - med) / med > 0.5:
                    warnings.append(f"第{idx+1}条线路 {h}={v} 在同列中明显离群"
                                    f"（中位数 {med}），疑似 OCR 错，请核对。")

    # 3) 列类型一致：吨位列若混入非数字残留 → 提示
    for r in routes:
        for h, raw in (r.吨位价格 or {}).items():
            if raw in (None, "", "/", "-"):
                continue
            if not _is_number_like(raw):
                warnings.append(f"线路「{r.线路名称}」吨位列 {h} 值 {raw!r} 非纯数字，疑似 OCR 串字符，请核对。")

    # 4) 系数自洽（calculate 已逐行查；这里汇总）— 见 calc_notes 中的 ⚠️

    # 5) 日期合理
    s = _date(result.ledger_fields.get("合同开始时间"))
    e = _date(result.ledger_fields.get("合同结束时间"))
    if s and e and e <= s:
        warnings.append(f"合同结束时间({e.date()}) 不晚于开始时间({s.date()})，请核对。")

    # 6) 省市一致（弱校验，§13.4 省市词典）
    prov_f = result.ledger_fields.get("省")
    ledger_prov = _canon_prov(prov_f.value if prov_f else None)
    if ledger_prov:
        for i, r in enumerate(routes):
            city = r.__dict__.get("市")
            p = province_of_city(city)
            if p and p != ledger_prov:
                warnings.append(f"第{i+1}条线路起点「{city}」属 {p}，与台账省「{ledger_prov}」不一致，请核对。")

    return warnings


def _is_number_like(x):
    if isinstance(x, (int, float)):
        return True
    s = str(x).strip().replace(",", "")
    try:
        float(s)
        return True
    except ValueError:
        return False


def _canon_prov(v):
    if not v:
        return None
    s = str(v).strip()
    for full, aliases in PROVINCE_ALIASES.items():
        if s == full or s in aliases or s in full:
            return full
    return s or None


def _date(field):
    if not field or not field.value:
        return None
    v = str(field.value)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None
