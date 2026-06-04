"""第2层 字段抽取：用 DeepSeek（纯文本）对 OCR markdown 分块抽取。

- 系统提示 = companies/youzheng.md（Skill）+ 输出契约。
- 分三块：元信息/条款、线路明细表、车型系数表。
- 每个字段返回 {value, confidence, evidence, page}；证据须来自原文。
"""
import json
import logging
from schemas import (Field, RouteRow, Coefficient,
                     ENUM_YESNO, ENUM_CONTRACT_TYPE, ENUM_ROUTE_NATURE)
from layers import verify
from companies import ledger_kb

log = logging.getLogger("extract")

MAX_MD = 120_000  # 传给 LLM 的 markdown 上限（防上下文溢出）

OUTPUT_RULE = f"""
你是合同字段抽取器。只输出 JSON，不要任何解释文字。
铁律：
- 每个值必须来自下面提供的合同文本/表格，并给出原文证据(evidence)；证据必须是文本里真实出现的片段（系统会逐条回查，编造的证据会被丢弃并扣分）。
- 找不到/看不清/无法确定 → value 设为 null，confidence 给低值。绝不臆造、不默认、不迁移其它合同的值。
- 输入是 OCR 结果，可能有错字；可疑内容标低置信并照实记录，不要"修正"成看似合理的值。
- 枚举约束：是否类字段只用 {ENUM_YESNO}；合同类型只用 {ENUM_CONTRACT_TYPE}；邮路性质只用 {ENUM_ROUTE_NATURE}（无法判定则 null）。
"""

# 增强默认参数（被 cfg.enhance 覆盖）
DEFAULT_ENHANCE = {
    "evidence_recheck": True,
    "self_consistency": True,
    "low_conf_threshold": 0.7,
    "n_votes": 3,
    "vote_temperature": 0.7,
}


def _clip(md: str, keep_tail: bool = False) -> str:
    """截断超长 markdown。

    keep_tail=True：保留首尾各半（价格明细表/系数表常在附件、位于文档尾部，
    纯头部截断会切掉它们 → 线路/系数抽取丢行）。
    """
    if len(md) <= MAX_MD:
        return md
    log.warning("markdown 超长(%d)，截断到 %d 字符（keep_tail=%s）", len(md), MAX_MD, keep_tail)
    if not keep_tail:
        return md[:MAX_MD]
    head = MAX_MD * 2 // 3
    tail = MAX_MD - head
    return md[:head] + "\n…（中间略）…\n" + md[-tail:]


def _safe_call(llm, system, user, what, temperature=None):
    """字段级降级：单块 LLM 调用失败不抛出，返回 None，让其它块与写表继续。

    what 同时作为 token 用量日志的 label，便于看清每个抽取块的消耗。
    """
    try:
        return llm.call_json(system, user, temperature=temperature, label=what)
    except Exception as e:  # noqa
        log.error("LLM 抽取「%s」失败，跳过该块并标复核：%s", what, e)
        return None


def _ledger_a_fields(field_map):
    """台账里需要 LLM 抽取的字段名（source=A 且未禁抽、非 from:ledger 派生、非 no_llm 注入）。

    no_llm=True 的列（如「客户分类」由分类层结果注入）虽是 source A，但值由代码给，不问 LLM。
    """
    names = []
    for c in field_map["ledger"]:
        if (c.get("source") == "A" and c.get("extract", True)
                and not c.get("from") and not c.get("no_llm")):
            names.append(c.get("field", c["name"]))
    return names


def extract_ledger(llm, skill, field_map, full_md, enhance=None) -> dict:
    """台账（新合同台账）字段抽取——**两阶段**（来自同事「主表提取方法」的强项）：

    Stage 1 事实抽取：只问"合同写了什么"，逐条给 value+evidence（不做业务映射）。
    Stage 2 标准化映射：据知识库规则 + 少样本（防照抄框架）把事实映射成标准字段值。

    返回 dict[字段名 -> Field]，与旧实现一致，故 calculate/validate/excel/report 全部不变。
    任一阶段失败 → 回退旧单阶段抽取（_extract_ledger_single），保证不空跑。
    线路/系数抽取（邮政明细）由 extract_routes/extract_coefficients 处理，未改动。
    """
    enhance = {**DEFAULT_ENHANCE, **(enhance or {})}
    fields = _ledger_a_fields(field_map)
    # 辅助抽取字段（field_map.aux_extract）：本身不占 Excel 列，只供派生计算用，
    # 如「合同期限」→ 在合同无固定结束日时由代码推算合同结束时间。
    aux = field_map.get("aux_extract", []) or []
    ask_fields = fields + [a["field"] for a in aux]

    # —— Stage 1：事实抽取 ——（skill 一并注入，保留反幻觉/反迁移铁律）
    facts = _extract_facts(llm, skill, full_md)
    if not facts:
        log.warning("台账两阶段：Stage1 事实抽取失败/为空，回退单阶段抽取。")
        return _extract_ledger_single(llm, skill, field_map, full_md, enhance, ask_fields, aux)

    # —— Stage 2：知识库标准化映射 ——
    system = ledger_kb.build_stage2_system(ask_fields)
    user = ledger_kb.build_stage2_user(facts)
    data = _safe_call(llm, system, user, "台账标准化")
    if not isinstance(data, dict):
        # 失败或返回形态异常（None/list/标量）→ 回退单阶段，避免"成功地空跑"出全空字段
        log.warning("台账两阶段：Stage2 标准化失败或非 JSON 对象，回退单阶段抽取。")
        return _extract_ledger_single(llm, skill, field_map, full_md, enhance, ask_fields, aux)

    raw_fields = data.get("fields", data)
    if not isinstance(raw_fields, dict):
        raw_fields = {}
    out = {}
    for name in ask_fields:
        f = Field.coerce(raw_fields.get(name))
        # Stage2 没回带 evidence 时，用对应事实的证据回填（供证据回查命中）
        if (not f.evidence) and f.value not in (None, "", "null"):
            ev = _fact_evidence(facts, ledger_kb.fact_key_for(name))
            if ev:
                f.evidence = ev
        out[name] = f

    # —— §13.1 文字字段增强（与单阶段同一套：枚举归一 + 证据回查 + 低置信投票）——
    #   self-consistency 在 Stage2 上重采样映射（同一 system/user，temperature>0），与旧实现接口一致。
    _finalize(out, llm, system, user, full_md, enhance)
    return out


def _extract_facts(llm, skill, full_md) -> dict:
    """Stage 1：事实抽取。返回 {事实名: {value, evidence}} 或 None（失败/空）。"""
    system = skill + "\n\n" + ledger_kb.STAGE1_RULES
    user = ledger_kb.build_stage1_user(_clip(full_md))
    data = _safe_call(llm, system, user, "事实抽取")
    if not isinstance(data, dict):
        return None
    facts = data.get("facts", data)  # 容错：模型可能包一层 {"facts": {...}}
    return facts if isinstance(facts, dict) and facts else None


def _fact_evidence(facts: dict, fact_key):
    """从事实集合里取某事实的证据片段（evidence 优先，回退 value）。找不到返回 None。"""
    if not fact_key or fact_key not in facts:
        return None
    v = facts[fact_key]
    if isinstance(v, dict):
        return v.get("evidence") or (str(v.get("value")) if v.get("value") else None)
    return str(v) if v else None


def _finalize(out, llm, system, user, full_md, enhance):
    """证据回查 + 枚举软归一 + 低置信 self-consistency 投票（就地更新 out）。"""
    verify.normalize_enums(out)
    failed = verify.recheck_evidence(out, full_md) if enhance["evidence_recheck"] else []
    if enhance["self_consistency"]:
        thr = enhance["low_conf_threshold"]
        weak = sorted({n for n, f in out.items()
                       if f.value not in (None, "", "null")
                       and ((f.confidence or 1) < thr or n in failed)})
        verify.self_consistency(llm, system, user, out, weak,
                                n_votes=enhance["n_votes"],
                                temperature=enhance["vote_temperature"])
        verify.normalize_enums(out)
        verify.recheck_evidence(out, full_md)  # 投票后再核一次证据


def _extract_ledger_single(llm, skill, field_map, full_md, enhance, ask_fields, aux) -> dict:
    """旧的单阶段台账抽取（保留作两阶段失败时的回退路径，行为与重构前一致）。"""
    aux_hints = "\n".join(f"- 「{a['field']}」：{a.get('hint', '')}" for a in aux)
    system = skill + "\n\n" + OUTPUT_RULE
    user = f"""请从下面这份邮政合同中抽取以下字段，按 JSON 返回：
{{"fields": {{ "字段名": {{"value": ..., "confidence": 0~1, "evidence": "原文片段", "page": 页码或null}}, ... }}}}

要抽取的字段（务必逐个给出，没有的填 value=null）：
{json.dumps(ask_fields, ensure_ascii=False)}

字段含义与定位规则见上文 Skill。注意（请逐字段核对单位与口径，这是高频出错点）：
- "是否有旺季补偿/油价联动/疫情补贴" 用 是/否；有则同时抽对应的时间/规则/比例/基准/标准字段。
- 税率、补偿比例等百分比写成小数（9% → 0.09）；区间/多值保留原文字符串。
- 日期写成 YYYY-MM-DD。
- **保证金按「万元」为单位**：原文"50万元"→填 `50`；原文"500000元"/"50万元"→都填 `50`（绝不要填 500000）。省级合同常无保证金→null。
- 账期填**天数整数**（"60天"→60）。
- **省级合同**（甲方是"XX省分公司"、通篇无具体地级市）：省填省名（如"浙江"），**市填 "/"**；市级合同才填具体市。
- **合同期限/结束日**：若合同写的是"自签订之日起N年/N个月"之类的**相对期限**而非固定结束日期，
  请把"合同开始时间"填为签订日、把该时长原文（如"自签订之日起2年"）填到"合同期限"字段，
  "合同结束时间"此时填 null（由系统据期限推算，禁止你自己算日期）。
{aux_hints}
- 每个数值都要回看原文，确认单位与字段名一致；金额不带逗号、不带单位符号。

合同内容（OCR markdown）：
---
{_clip(full_md)}
---"""
    data = _safe_call(llm, system, user, "元信息/条款")
    if data is None:
        # 整块失败：全部留空 + 标复核，不影响线路/系数块与写表
        return {name: Field(evidence="（LLM 调用失败，未抽取，请人工核对）") for name in ask_fields}
    raw_fields = data.get("fields", data) if isinstance(data, dict) else {}
    out = {}
    for name in ask_fields:
        out[name] = Field.coerce(raw_fields.get(name))
    _finalize(out, llm, system, user, full_md, enhance)
    return out


def extract_routes(llm, skill, field_map, full_md, tables_md, enhance=None) -> tuple:
    enhance = {**DEFAULT_ENHANCE, **(enhance or {})}
    tonnage_headers = [t["name"] for t in field_map.get("tonnage_columns", [])]
    system = skill + "\n\n" + OUTPUT_RULE
    user = f"""请抽取这份邮政合同里**带价格的线路明细**，按 JSON 返回：
{{"found": true/false,
  "routes": [
    {{"线路名称": "...", "邮路性质": "单程/双程/往返 或 null", "里程": 数值或null,
      "分包号": "...或null",
      "吨位价格": {{ "吨位列名": 数值 }} }}
  ]}}

吨位列名必须从下面这组里选（把价格填到语义对应的吨位列；表里没有的吨位不要写进来）：
{json.dumps(tonnage_headers, ensure_ascii=False)}

【抽哪张表——务必先判断，这是最关键的一步】
合同里可能有两类长得很像的「线路表」，**只有第一类才是明细行**：
1. **价格明细表**（如『主要中标价格明细表/线路价格表』）：有**价格列**（整车价/车公里单价/各吨位价等），每条线路对应一个金额。**只抽这张表里的线路。**
2. **发运计划表/比照线路清单/线路明细表**：列头是『组开地市、始发地市、终到地市、核定里程、邮路属性』之类，**只有里程没有任何价格列**。这类是「比照/延申线路」，价格需人工按比照规则派生——**当合同里存在上面第1类价格表时，绝不要把这类无价清单里的线路当明细行抽出来**（哪怕它列了几十条），否则会凭空多出十几行无价行。
判定口径：
- 合同里**存在带价格列的明细表** → 只输出该价格表里的线路；那张无价的发运/比照清单**整张跳过**。
- 合同里**通篇找不到任何带价格的明细表**（线路只出现在发运计划表里、本就没单价）→ 这时才回退抽发运计划表里的线路（里程照抽、价格留空）。
- 一条线路若同时出现在价格表和无价清单里，以价格表那行为准、只输出一次。

要点（务必遵守，否则会因输出过长被截断而丢数据）：
- **保持紧凑**：每条线路只输出上面那几个键，**不要**写 类型、备注、合同编号、evidence、客户名称、page 等额外字段
  （类型/备注/合同编号 业务已定为不抽取、由人工填或系统去重，抽了也会被丢弃）。
- 一条线路一行；选定的那张表里**全部线路都要输出（哪怕几十条）**，不要省略、不要用"..."省略号。如果整份合同找不到逐条线路的价格/计划表，found=false 且 routes=[]。
- 只抽表里"直读"的价格；缺的吨位不要替它换算（换算由后续程序完成）。
- 「4.2米」列车型基本用不到，识别不确定时该格直接留空（不要硬凑数字）。
- "/" 或空白表示不服务，不要填成 0。
- 金额只填数字，不带单位/逗号；字符串里不要出现换行。

合同表格（OCR 还原的表格，优先看这里）：
---
{_clip(tables_md) if tables_md.strip() else "(未单独提取到表格，请在下方全文里找)"}
---
合同全文（markdown）：
---
{_clip(full_md, keep_tail=True)}
---"""
    data = _safe_call(llm, system, user, "线路明细")
    if data is None:
        return [], False, None
    found = bool(data.get("found", False)) if isinstance(data, dict) else False
    routes = []
    for r in (data.get("routes", []) if isinstance(data, dict) else []):
        if not isinstance(r, dict):
            continue
        prices = r.get("吨位价格", {}) or {}
        # 只保留合法吨位列名（40吨B 等不抽取列已不在 tonnage_headers 内，自动滤除）
        prices = {k: v for k, v in prices.items() if k in tonnage_headers}
        # 类型/备注/合同编号 为红字不抽取列：即便模型返回也不接收（保持 RouteRow 上这些字段为 None）。
        routes.append(RouteRow(
            线路名称=r.get("线路名称"),
            邮路性质=verify._pick(str(r.get("邮路性质") or ""), ENUM_ROUTE_NATURE) or r.get("邮路性质"),
            里程=r.get("里程"),
            分包号=r.get("分包号"),
            吨位价格=prices,
            evidence=r.get("evidence"),
            page=_int(r.get("page")),
        ))

    # 证据回查：紧凑输出无 per-route evidence，改用线路名是否在原文/表格命中来判置信
    if enhance["evidence_recheck"]:
        src = (tables_md or "") + "\n" + (full_md or "")
        for r in routes:
            hit = verify.evidence_hit(r.线路名称, src) or verify.evidence_hit(r.evidence, src)
            r.confidence = 0.95 if hit else 0.5
            if not hit:
                log.info("线路名未在原文命中: %r", r.线路名称)
    # 第三返回值历史上是「计费方式(计趟/计重)」，曾用于派生线路「类型」列；
    # 类型列已改红字不抽取 → 不再判定计费方式，恒返回 None（保持三元组签名不变）。
    return routes, (found or len(routes) > 0), None


def extract_coefficients(llm, skill, full_md, tables_md) -> Coefficient:
    system = skill + "\n\n" + OUTPUT_RULE
    user = f"""请在这份邮政合同中查找「车型转换系数表 / 车型距离基准量表 / 折算系数表」，按 JSON 返回：
{{"found": true/false, "基准车型": "系数表声明的基准车型（如『20吨/12.5米』这种车型规格）或 null",
  "系数": {{ "吨位标签": 该吨位档的系数数值 }},
  "evidence": "原文片段", "page": 页码或null}}

「系数」里把系数表中**写明的每个吨位档及其系数**原样列出（键=吨位标签如"8吨"，值=该档系数数值）；上面只是键值占位、不是参考值，绝不要照抄或臆造任何数字。

要点：
- 只抽表里写明的系数，不要自己计算或补全缺失吨位。
- 如果合同对每个吨位都直接列了价格、没有系数表，found=false（这是正常情况，不要编造系数）。

合同表格：
---
{_clip(tables_md) if tables_md.strip() else "(无单独表格)"}
---
合同全文：
---
{_clip(full_md, keep_tail=True)}
---"""
    data = _safe_call(llm, system, user, "车型系数表")
    if not isinstance(data, dict):
        return Coefficient(found=False)
    coef = {}
    for k, v in (data.get("系数", {}) or {}).items():
        try:
            coef[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return Coefficient(
        found=bool(data.get("found", False)),
        基准车型=data.get("基准车型"),
        系数=coef,
        evidence=data.get("evidence"),
        page=_int(data.get("page")),
    )


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def tables_to_md(doc) -> str:
    """把 OCR 提取到的表格 HTML 拼成一段（供 routes/coefficient 优先阅读）。"""
    parts = []
    for t in doc.tables:
        parts.append(f"<!-- 第{t.page}页 表格 -->\n{t.html or t.markdown}")
    return "\n\n".join(parts)
