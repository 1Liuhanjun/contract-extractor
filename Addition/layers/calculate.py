"""第3层 派生计算：跨吨位系数换算、省/市派生。

- 换算：基准价 = 直读价 / 该吨位系数；目标吨位价 = 基准价 × 目标系数（写数值）。
- 找不到系数表 → 只保留直读吨位，其余留空并记复核。
- 自洽校验：多个直读价反算基准价应一致（≤0.5%），否则告警。
"""
import re
import calendar
import logging
from datetime import date, timedelta

from schemas import Field

log = logging.getLogger("calculate")


def _num(x):
    """把单元格值解析成数值；'/'、空、非数字 → None。"""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "")
    if s in ("", "/", "-", "—", "无"):
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def _lead_ton(label):
    """从吨位标签里取主吨位数值，如 '12吨/9.6米' → 12.0、'2.75吨/3吨' → 2.75。

    保留小数，不截断（int() 会把 2.75 截成 2 → 与系数表对齐错位）。
    返回 float；与 field_map 的整数 ton 比较时 3.0==3 成立，2.75≠3 区分正确。
    """
    if not label:
        return None
    m = re.search(r"\d+(?:\.\d+)?", str(label))
    return float(m.group()) if m else None


def compute(result, field_map):
    """就地补全 routes 的吨位价格与省/市，返回计算说明列表(供报告)。"""
    notes = []
    tcols = field_map.get("tonnage_columns", [])
    header_to_ton = {t["name"]: t.get("ton") for t in tcols}
    ton_to_header = {t["ton"]: t["name"] for t in tcols if t.get("ton") is not None}

    # 直辖市「市」确定性归一：LLM 常把直辖市的「市」填成'北京市'，据客户名称里的'XX区/县'
    # 归一为区县名（'北京市大兴区分公司' → 大兴），先于后续省级判定与明细派生执行。
    _normalize_municipality_city(result.ledger_fields, notes)

    # 省（取台账省值）
    prov_field = result.ledger_fields.get("省")
    province = prov_field.value if prov_field else None
    # 省级合同判定：台账"市"为"/"或空 → 省级，邮政 sheet 市列填"/"（§4.2/§6.2）
    city_field = result.ledger_fields.get("市")
    city_val = city_field.value if city_field else None
    is_provincial = str(city_val).strip() in ("/", "／", "", "None", "null") or city_val is None

    # 系数表 → {ton: coef}
    coef_by_ton = {}
    base_ton = None
    if result.coefficient.found:
        for label, c in result.coefficient.系数.items():
            t = _lead_ton(label)
            if t is not None:
                coef_by_ton[t] = float(c)
        base_ton = _lead_ton(result.coefficient.基准车型)
        if base_ton is not None:
            coef_by_ton.setdefault(base_ton, 1.0)  # 基准吨位系数=1
        notes.append(f"系数表(第{result.coefficient.page}页)：基准={result.coefficient.基准车型}，"
                     f"系数={result.coefficient.系数}")
    else:
        notes.append("未发现车型系数表：各吨位价格仅保留合同直读值。")

    # 合同结束时间派生：合同无固定结束日（写"自签订之日起N年/月"）时，用 开始时间 + 合同期限 推算。
    # 注意：会同时影响台账「合同结束时间」列与邮政 sheet「有效期结束时间」列（后者 from:ledger）。
    _derive_end_date(result, notes)

    # 邮政 sheet「市」口径（修：明细市应是甲方所在地，而非线路起点）：
    # - 台账「市」是具体市/区（非"/"）→ 明细**直接继承台账市**。修两类错：
    #     ① 南京合同的线路起点是徐州，明细市应填"南京"(甲方)而非"徐州"(起点)；
    #     ② 北京大兴合同线路起点是"北京黄村"，明细市应填"大兴"而非"北京黄村"。
    # - 省级合同（台账市="/"）→ 用线路共同起点兜底：全部线路同一起点→该城市
    #     （如 33 条"成都-X"→成都）；起点跨多市/解析不出→仍填"/"。
    origin_cities = {c for c in (_derive_city(r.线路名称) for r in result.routes) if c}
    common_city = next(iter(origin_cities)) if len(origin_cities) == 1 else None
    if not is_provincial:
        derived_city = city_val
        notes.append(f"【市】继承台账「市」=「{city_val}」（甲方所在地，明细与台账一致；不取线路起点）。")
    else:
        derived_city = common_city or "/"
        if common_city:
            notes.append(f"【市】台账为省级(市='/')，按线路共同起点派生为「{common_city}」（{len(result.routes)} 条线路起点一致）。")
        else:
            notes.append(f"【市】填「/」：台账为省级，且线路起点跨多市（{sorted(origin_cities) or '无法解析'}）或无法解析，无单一归属市。")

    # 注：线路「类型」列已改红字不抽取（业务手动标注），不再派生，留空交人工。

    for idx, r in enumerate(result.routes):
        r.__dict__["省"] = province
        r.__dict__["市"] = derived_city

        # 直读价格（header → number）
        direct = {}
        for h, v in (r.吨位价格 or {}).items():
            n = _num(v)
            if n is not None:
                direct[h] = n

        final = dict(direct)  # 最终价格

        # 换算
        if coef_by_ton and direct:
            # 选一个有系数的直读吨位作基准来源（优先基准吨位）
            src_ton, src_price = None, None
            for h, n in direct.items():
                t = header_to_ton.get(h)
                if t in coef_by_ton:
                    if base_ton is not None and t == base_ton:
                        src_ton, src_price = t, n
                        break
                    if src_ton is None:
                        src_ton, src_price = t, n
            if src_ton is not None and coef_by_ton.get(src_ton):
                base_price = src_price / coef_by_ton[src_ton]
                # 自洽校验：多直读价反算基准价不一致(>0.5%) → 放弃换算，只留直读，避免用 OCR 误读价带偏整行
                consistent = _selfcheck(direct, header_to_ton, coef_by_ton, base_price, idx, notes)
                if not consistent:
                    r.换算说明 = ("多个直读价反算基准价不一致(>0.5%)，疑似 OCR 误读，"
                                "已放弃换算、仅保留直读价，请人工核对。")
                    r.__dict__["最终吨位价格"] = final
                    continue
                # 填充缺失吨位
                filled = []
                for t, coef in coef_by_ton.items():
                    h = ton_to_header.get(t)
                    if h and h not in final:
                        final[h] = round(base_price * coef, 2)
                        filled.append(h)
                if filled:
                    r.换算说明 = (f"以 {ton_to_header.get(src_ton)}={src_price} 反算基准价 "
                                f"{round(base_price,2)}，换算出 {', '.join(filled)}")
            else:
                r.换算说明 = "有系数表但直读吨位不在系数范围内，无法换算，仅保留直读值。"
        elif direct:
            r.换算说明 = "无系数表，仅保留直读吨位价格。"
        else:
            r.换算说明 = "本合同未给该线路任何直读价格，价格列留空（需人工核对来源）。"

        r.__dict__["最终吨位价格"] = final

    return notes


_MUNICIPALITIES = ("北京", "上海", "天津", "重庆")


def _normalize_municipality_city(ledger_fields, notes):
    """直辖市合同：台账「市」只由【客户名称(甲方实体名)】确定，**绝不看办公地址**。

    口径（与人工台账一致）：
    - 甲方名含'<直辖市>市<区县>区/县'（区县级分公司，如'北京市大兴区分公司'）→ 市=该区县名（大兴）。
    - 甲方名是直辖市**市级**（含直辖市名但**无区县**，如'上海市分公司'/'上海市邮区中心'）→ 市=直辖市名（上海）。
    办公地址里的'XX区'只是办公地点、不代表甲方行政层级，据此定市会出错
    （曾把甲方为'上海市分公司'、地址在'上海市浦东新区龙东大道'的合同误归成'浦东新区'，应为'上海'）。
    非直辖市合同（客户名称里无直辖市名）一律不处理，保持原值（南京→南京）。
    """
    name_f = ledger_fields.get("客户名称")
    name = str(getattr(name_f, "value", "") or "")
    muni = next((m for m in _MUNICIPALITIES if m in name), None)
    if not muni:
        return  # 非直辖市合同：不动（南京/地级市等保持原值）
    # 仅从【客户名称】里找'<直辖市>市<区县>区/县分公司'里的区县；非贪婪取最短区县名。
    # 要求区/县后紧跟'分公司'（可含'邮政'），以排除'邮区中心'这类'区'非行政区的误匹配
    # （'上海市邮区中心'的'邮区'不是区县，须按市级 → 上海）。
    m = re.search(muni + r"市?([一-龥]{2,5}?)(?:区|县)(?:邮政)?分公司", name)
    target = m.group(1) if m else muni        # 有区县级分公司→区县名；否则市级→直辖市名
    detail = f"{m.group(0)}" if m else f"{muni}市级（甲方名无区县分公司）"

    city_f = ledger_fields.get("市")
    city = (str(city_f.value).strip() if city_f and city_f.value is not None else "")
    if city == target:
        return                                # 已正确，无需改
    old = city or "（空）"
    if city_f is None:
        ledger_fields["市"] = Field(value=target, confidence=0.9, evidence_ok=True,
                                    evidence=f"直辖市按甲方名归一：{detail}")
    else:
        city_f.value = target
        city_f.evidence = f"直辖市按甲方名归一：{detail}（原「{old}」）"
        if (city_f.confidence or 0) < 0.9:
            city_f.confidence = 0.9
        city_f.evidence_ok = True
    notes.append(f"【市】直辖市归一：台账市由「{old}」按客户名称校正为「{target}」（依据：{detail}）。")
    return


def _derive_city(line_name):
    """从线路名称取起点城市：取首个分隔符（- — ～ → 至 到 等）之前的起点段。

    例："成都-东莞"→成都、"武汉-京山-钟祥"→武汉、"乌鲁木齐-西安"→乌鲁木齐。
    纯文本切分，不做地名校验、不臆造；无分隔符或起点段过长（>5字，不像市名）→
    返回 None，交由上层兜底（省级填"/"，否则留空）。
    """
    if not line_name:
        return None
    s = str(line_name).strip()
    head = re.split(r"[-—－﹣~～→>]+|至|到", s, maxsplit=1)[0].strip()
    if not head or len(head) > 5:
        return None
    return head


# ---- 合同结束时间派生（无固定结束日时，按"自签订之日起N年/月"推算）----
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}

# 延期/上限条款特征词：这些词之后的月数是"后续可能延长"而非基础合同期，
# 不计入基础结束日的推算（修复合肥『自签订之日起2年，至下一轮采购完成，最多不超过4个月』
# 被误算成 24+4=28 个月 → 结束日 2025-01-28 的 bug；正确应只按主期限 2 年算到 2024-09-28）。
_TERM_EXTEND_KW = ("至下一轮", "下一轮采购", "下一次采购", "最多不超过", "最长不超过",
                   "不超过", "延长", "顺延", "续签", "续约")


def _parse_term_months(term) -> int:
    """把期限表述解析成总月数。'2年'→24、'24个月'→24、'一年半'→18、'18个月'→18。解析不出→0。

    只解析**主期限**：若含『至下一轮采购完成/最多不超过N个月』等延期/上限条款，
    在最早的延期词处截断，其后的月数不计入（延期是后续可能性，由人工确认，见 _derive_end_date 的提示）。
    """
    s = str(term)
    for kw in _TERM_EXTEND_KW:
        i = s.find(kw)
        if i != -1:
            s = s[:i]          # 截到延期词之前，丢弃延期/上限部分
    # 护栏：截断后若仍含"YYYY年"四位年份，说明这是"自X年X月至Y年Y月"的**固定日期串**误填进了
    # 合同期限，而非"N年/N个月"时长 → 不当相对期限解析（否则会把 2022 当 2022 年期限算出天文数字）。
    # 此时结束日应由 LLM 直读或人工补，不在此推算。
    if re.search(r"\d{4}\s*年", s):
        return 0
    months = 0
    ym = re.search(r"(\d+(?:\.\d+)?)\s*年", s)
    if ym:
        months += int(round(float(ym.group(1)) * 12))
    else:
        for cn, v in _CN_NUM.items():
            if cn + "年" in s:
                months += v * 12
                break
    mm = re.search(r"(\d+)\s*个?月", s)
    if mm:
        months += int(mm.group(1))
    elif "半年" in s or ("年半" in s):
        months += 6
    return months


def _add_months(d0: date, n: int) -> date:
    m = d0.month - 1 + n
    y = d0.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d0.day, calendar.monthrange(y, m)[1]))


def _compute_end_date(start_str, term):
    """开始日 + 期限 → 结束日（YYYY-MM-DD）。

    口径："自签订之日起N年/月" 覆盖区间末日 = 开始日 + N - 1天
    （如 2022-10-08 起2年 → 2024-10-07，与业务命名一致）。
    """
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", str(start_str))
    if not m:
        return None
    months = _parse_term_months(term)
    if months <= 0:
        return None
    try:
        start = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return (_add_months(start, months) - timedelta(days=1)).strftime("%Y-%m-%d")


def _derive_end_date(result, notes):
    """合同结束时间为空、但有开始时间 + 合同期限 → 推算并回填 ledger_fields。"""
    ledger = result.ledger_fields
    end_f = ledger.get("合同结束时间")
    end_empty = (end_f is None) or (str(getattr(end_f, "value", None)).strip()
                                    in ("", "/", "／", "None", "null"))
    start_f = ledger.get("合同开始时间")
    term_f = ledger.get("合同期限")
    if not (end_empty and start_f and start_f.value and term_f and term_f.value):
        return
    computed = _compute_end_date(start_f.value, term_f.value)
    if not computed:
        return
    ledger["合同结束时间"] = Field(
        value=computed, confidence=0.6,
        evidence=f"派生：开始 {start_f.value} +「{term_f.value}」按『自起算日N年/月、末日减1天』推算",
    )
    notes.append(
        f"⚠️ 合同结束时间为派生计算：{start_f.value} +「{term_f.value}」→ {computed}"
        f"（合同未写固定结束日，按「自签订之日起N年」口径推算并减1天；"
        f"若另有「至下一轮采购完成/最多不超过N个月」等延期条款，请人工确认）")


def _selfcheck(direct, header_to_ton, coef_by_ton, base_price, idx, notes):
    """用每个直读价反算基准价，互相偏差 >0.5% 则告警。返回是否自洽(bool)。"""
    bases = []
    for h, n in direct.items():
        t = header_to_ton.get(h)
        if t in coef_by_ton and coef_by_ton[t]:
            bases.append(n / coef_by_ton[t])
    if len(bases) >= 2:
        lo, hi = min(bases), max(bases)
        if lo > 0 and (hi - lo) / lo > 0.005:
            notes.append(f"⚠️ 第{idx+1}条线路：多个直读价反算的基准价不一致"
                         f"（{round(lo,2)}~{round(hi,2)}，差>0.5%），疑似 OCR 错或系数不符，请人工核对。")
            return False
    return True
