"""第6层 复核报告：每份合同一份 markdown。

为方便人工复核，报告按以下结构组织（对应用户 2026-06-01 要求）：
1. 顶部「优先复核」——校验告警 + 不确定字段 + 需核对口径的计算字段，先看这里。
2. 「计算/派生字段的计算逻辑」——指名每个计算字段怎么算出来的（市/结束时间/类型/吨位换算/系数）。
3. 两张输出表分开列：表一 新合同台账、表二 邮政明细表，各自的字段提取位置/逻辑/确定性。
4. 不确定字段列出全部候选值、各自位置与优先排名（票数）。
"""
import re
from pathlib import Path

LOW_CONF = 0.7
_PAGE_RE = re.compile(r"<!--\s*第(\d+)页")


def generate(report_path: Path, *, contract_id, pdf_path, page_count, company,
             company_conf, ledger_fields, field_map, routes, routes_found,
             calc_notes, warnings, llm_calls, elapsed_sec, cache_dir, tokens_used=None,
             full_md=None):
    L = []
    L.append(f"# 合同复核报告 - {contract_id}\n")
    L.append("## 输入信息")
    L.append(f"- PDF 文件: {pdf_path}")
    L.append(f"- 总页数: {page_count}")
    L.append(f"- 公司分类: {company}（置信度 {company_conf}）")
    L.append(f"- 处理时长: {elapsed_sec:.0f} 秒")
    L.append(f"- LLM 调用次数: {llm_calls}")
    if tokens_used is not None:
        L.append(f"- LLM token 用量: {tokens_used}")
    L.append("")

    # 台账里「需 LLM 抽取」的字段（source=A、未禁抽、非 from:ledger 派生、非 no_llm 注入）
    a_cols = [c for c in field_map["ledger"]
              if c.get("source") == "A" and c.get("extract", True)
              and not c.get("from") and not c.get("no_llm")]
    uncertain = [(c["name"], c.get("field", c["name"]), ledger_fields.get(c.get("field", c["name"])))
                 for c in a_cols]
    uncertain = [(n, fld, f) for n, fld, f in uncertain if _is_uncertain(f)]
    blank = [(c["name"], ledger_fields.get(c.get("field", c["name"]))) for c in a_cols
             if _is_blank(ledger_fields.get(c.get("field", c["name"])))]

    # ===================== 1. 优先复核 =====================
    L.append("## ⚠️ 优先复核（请先看这里）")
    if warnings:
        L.append("**校验告警（最高优先）：**")
        for w in warnings:
            L.append(f"- {w}")
    else:
        L.append("- 校验告警：无")
    if uncertain:
        names = "、".join(n for n, _, _ in uncertain)
        L.append(f"**不确定字段（{len(uncertain)} 个，候选与排名见『表一·不确定字段』）：** {names}")
    if blank:
        L.append(f"**留空字段（合同中未找到，需人工确认是否漏抽）：** {'、'.join(n for n, _ in blank)}")
    L.append("**计算/派生字段（请核对计算口径，逻辑见下一节）：** 市、合同结束时间(如为派生)、各吨位换算价。")
    L.append("> 📌 生成的 Excel 里**黄色高亮**单元格即「不确定/需核对」数据，鼠标悬停单元格可见原因批注。")
    if not (warnings or uncertain or blank):
        L.append("- 未发现需优先复核的风险项（仍建议抽查关键金额）。")
    L.append("")

    # ===================== 2. 计算/派生字段的计算逻辑 =====================
    L.append("## 计算/派生字段的计算逻辑（核对口径）")
    if calc_notes:
        for n in calc_notes:
            L.append(f"- {n}")
    else:
        L.append("- （本合同无派生计算）")
    L.append("")

    # ===================== 3. 表一：新合同台账 =====================
    L.append("---")
    L.append("# 表一：新合同台账（每份合同 1 行）")
    L.append("")
    L.append("## 字段提取明细（值 / 提取逻辑 / 位置 / 确定性）")
    L.append("| 字段 | 值 | 来源与提取逻辑 | 位置（页｜原文证据） | 确定性·优先级 |")
    L.append("| --- | --- | --- | --- | --- |")
    for c in field_map["ledger"]:
        name = c["name"]
        fld = c.get("field", name)
        f = ledger_fields.get(fld) if c.get("source") == "A" else None
        L.append(f"| {name} | {_value_cell(c, f)} | {_col_logic(c)} | {_loc(c, f)} | {_certainty(c, f)} |")
    L.append("")

    L.append("## 不确定字段：全部候选值（按优先排名）")
    if uncertain:
        for name, fld, f in uncertain:
            L.append(f"- **{name}**（已采用：`{f.value}`）{_uncertain_reason(f)}")
            cands = f.candidates if f.candidates else [
                {"value": f.value, "votes": None, "evidence": f.evidence, "page": f.page}]
            for rank, c in enumerate(cands, 1):
                vote = f"，票数 {c.get('votes')}" if c.get("votes") is not None else ""
                page = f"第{c.get('page')}页" if c.get("page") else "页码未标注"
                ev = _short(c.get("evidence"), 70)
                L.append(f"    {_circle(rank)} `{c.get('value')}`{vote}　位置：{page}｜{ev}")
    else:
        L.append("（无：台账抽取字段均证据命中且置信达标）")
    L.append("")

    # ===================== 表二：邮政明细表 =====================
    L.append("---")
    L.append("# 表二：邮政明细表（每条线路 1 行）")
    L.append("")
    L.append(f"- 写入 {len(routes)} 行（线路数 {len(routes)}，found={routes_found}）")
    L.append("")
    L.append("## 各列来源与提取逻辑")
    L.append("| 列 | 字段 | 来源与提取逻辑 |")
    L.append("| --- | --- | --- |")
    for c in field_map["youzheng"]:
        L.append(f"| {c['col']} | {c['name']} | {_col_logic(c)} |")
    L.append("")

    if routes:
        L.append("## 线路逐条（每条：值 + 提取位置 + 换算逻辑）")
        L.append("> 同一条线路的 性质/里程/各吨位价 取自价格明细表的**同一行**；下方「位置」即该行所在页，"
                 "据此可逐字段核查。换算得到的价见「换算逻辑」。")
        for i, r in enumerate(routes):
            prices = r.__dict__.get("最终吨位价格", {})
            page = _page_of(r.线路名称, full_md)
            loc = (f"第{page}页 价格明细表（该行）" if page
                   else "⚠ 线路名未在 OCR 原文命中，位置存疑，请人工核对来源")
            L.append(f"- [{i+1}] {r.线路名称}　性质={r.邮路性质 or '—'}　里程={r.里程 or '—'}")
            L.append(f"    位置：{loc}　|　分包号={r.分包号 or '—'}")
            if r.换算说明:
                L.append(f"    换算逻辑：{r.换算说明}")
            if prices:
                L.append(f"    价格: {prices}")
        L.append("")

    L.append("---")
    L.append("## 中间产物")
    L.append(f"- OCR 与模型返回缓存: {cache_dir}")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(L), encoding="utf-8")
    return report_path


# ---------------- 单元格/逻辑/确定性 渲染 ----------------
def _is_blank(f):
    return (not f) or (f.value in (None, "", "null"))


def _is_uncertain(f):
    """字段是否需进"不确定"清单：有多候选 / 证据未命中 / 低置信。"""
    if _is_blank(f):
        return False
    if f.candidates and len(f.candidates) > 1:
        return True
    if f.evidence_ok is False:
        return True
    return (f.confidence or 1) < LOW_CONF


def _col_logic(col):
    """该列「从哪来、怎么得到值」的人话说明。"""
    if col.get("formula"):
        return f"Excel 公式（写入时按行号替换）：`{col['formula']}`"
    if col.get("extract") is False:
        return "系统外/红字列 → **不抽取、留空**（由其它系统或人工填写）"
    if col.get("tonnage"):
        return "吨位价格：合同价格表直读值；表里没有的吨位由车型系数表反算换算（见计算逻辑节）"
    if col.get("no_llm"):
        return "由分类层结果自动填写（=已识别公司，非合同抽取、非固定默认值）"
    if col.get("from") == "ledger":
        return f"取自台账字段「{col.get('field', col['name'])}」，与台账保持一致"
    src = col.get("source")
    if src == "D":
        return f"固定默认值（取自 config.defaults「{col.get('default', col['name'])}」）"
    if src == "B":
        if col["name"] == "市":
            return "派生：台账「市」具体→明细继承台账市；台账市='/'(省级)→取线路共同起点，跨多市/解析不出→「/」"
        return "派生计算（见计算逻辑节）"
    if src == "A":
        return "合同抽取：按字段语义在原文/表格中定位（位置见证据与页码）"
    return "—"


def _value_cell(col, f):
    if col.get("formula"):
        return "（写入公式）"
    if col.get("extract") is False:
        return "（留空·不抽取）"
    if col.get("no_llm"):
        return f"`{f.value}`" if (f and not _is_blank(f)) else "（待分类结果）"
    src = col.get("source")
    if src == "D":
        return "（固定默认值）"
    if src == "A":
        return f"`{f.value}`" if (f and not _is_blank(f)) else "（未取到·留空）"
    return "（派生/见明细表）"


def _loc(col, f):
    """每个字段的「位置/来源」：抽取字段给页码+原文证据；非抽取字段说明其来源，绝不留空。"""
    if col.get("formula"):
        return "公式生成（非合同抽取）"
    if col.get("extract") is False:
        return "系统外·不在合同抽取（红字列）"
    if col.get("no_llm"):
        return "分类层结果（非合同抽取）"
    src = col.get("source")
    if src == "D":
        return "配置默认值（非合同抽取）"
    if col.get("from") == "ledger":
        return f"同台账「{col.get('field', col['name'])}」字段"
    if src == "B":
        return "派生计算（见『计算逻辑』节）"
    # source A：合同抽取
    if _is_blank(f):
        return "未在合同中找到（已全文检索，留空）"
    page = f"第{f.page}页" if f.page else "页码未标注"
    ev = _short(f.evidence, 60)
    return f"{page}｜{ev}" if ev else page


def _page_of(needle, full_md):
    """在 OCR 全文里定位文本所在页（按 <!-- 第N页 --> 标记）。找不到返回 None。"""
    if not needle or not full_md:
        return None
    idx = full_md.find(str(needle).strip())
    if idx < 0:
        return None
    page = None
    for m in _PAGE_RE.finditer(full_md):
        if m.start() <= idx:
            page = int(m.group(1))
        else:
            break
    return page


def _certainty(col, f):
    """确定性·优先级标签（越不确定，复核优先级越高）。"""
    if col.get("formula"):
        return "公式（复核公式本身）"
    if col.get("extract") is False:
        return "—（不抽取）"
    if col.get("no_llm"):
        return "确定（分类结果）"
    src = col.get("source")
    if src == "D":
        return "确定（固定值）"
    if src == "B" or col.get("from") == "ledger" or col.get("tonnage"):
        return "派生（核对计算口径）"
    if src == "A":
        if _is_blank(f):
            return "留空（未找到，建议人工核对）"
        if f.evidence_ok is False:
            return f"⚠ 低优先级最高（证据未命中原文，置信{f.confidence}）"
        c = f.confidence if f.confidence is not None else 1
        if c < LOW_CONF:
            return f"中低（置信{c}，建议复核）"
        return f"高（证据命中，置信{c}）"
    return "—"


def _uncertain_reason(f):
    rs = []
    if f.candidates and len(f.candidates) > 1:
        rs.append("多次抽取出现分歧")
    if f.evidence_ok is False:
        rs.append("证据未在原文命中")
    if (f.confidence or 1) < LOW_CONF:
        rs.append(f"置信偏低({f.confidence})")
    return f"——{ '、'.join(rs) }" if rs else ""


def _circle(n):
    return "①②③④⑤⑥⑦⑧⑨"[n - 1] if 1 <= n <= 9 else f"{n}."


def _short(s, n=120):
    if not s:
        return ""
    s = str(s).replace("\n", " ").replace("|", "/")
    return s[:n] + ("…" if len(s) > n else "")
