"""主表（新合同台账）两阶段抽取 prompt 装配。

**规则源已外置到 `companies/ledger.md`**（台账侧单一规则文档），运行时读取它的分段：
  - stage1_rules：Stage1 事实抽取规则
  - field_kb    ：Stage2 字段知识库（逐字段映射规则）
  - fewshot     ：转换方法演示（去数值化，防跨合同照抄）
  - anti_copy   ：防照抄铁律

本模块只负责「读 md 分段 + 拼装成两阶段 prompt」，不再硬编码任何业务规则文本。
改台账规则 → 只改 `ledger.md`。线路/邮政明细规则在 `companies/youzheng.md`，由 extract.py 处理。

历史对齐（沿用同事「主表提取方法」并本地化）：键名/口径对齐本项目 field_map（税率/补偿比例小数、
保证金万元、日期 YYYY-MM-DD、相对期限→结束日留空交 calculate 推算）；few-shot 去数值化防过拟合。
"""
import re
from pathlib import Path

_LEDGER_MD = Path(__file__).resolve().parent / "ledger.md"


def _load_sections() -> dict:
    """从 ledger.md 解析 <!-- BEGIN:name --> … <!-- END:name --> 各分段。"""
    text = _LEDGER_MD.read_text(encoding="utf-8") if _LEDGER_MD.exists() else ""
    out = {}
    for m in re.finditer(r"<!--\s*BEGIN:(\w+)\s*-->(.*?)<!--\s*END:\1\s*-->", text, re.S):
        out[m.group(1)] = m.group(2).strip()
    return out


_SECTIONS = _load_sections()


def _section(name: str) -> str:
    return _SECTIONS.get(name, "")


# ============================================================
# Stage 1：事实抽取（规则文本来自 ledger.md 的 stage1_rules 分段）
# ============================================================
STAGE1_RULES = _section("stage1_rules")


def build_stage1_user(contract_text: str) -> str:
    """Stage1 用户输入。含路由标志串「提取事实」（供离线 mock 识别，正文不会出现该指令串）。"""
    return f"""请从以下邮政运输合同文本中**提取事实**（按上面的清单，逐条给 value+evidence）。

合同文本：
---
{contract_text}
---"""


# ============================================================
# 事实→字段 证据回填索引（内部管道，非业务规则）：
# Stage2 未回带 evidence 时，用对应 Stage1 事实的 evidence 回填，供证据回查命中。
# 业务规则全部在 ledger.md；这里只是“哪个事实承载该字段证据”的程序索引。
# ============================================================
FACT_KEYS = {
    "业务类型": "业务范围描述",
    "合同类型": "合同性质",
    "省": "甲方_省份",
    "市": "甲方_城市",
    "客户名称": "甲方_全称",
    "合同主体": "乙方_全称",
    "合同开始时间": "合同期限",
    "合同结束时间": "合同期限",
    "是否完成签订": "签署状态",
    "合同期限": "合同期限",
    "账期": "付款账期",
    "保证金": "履约保证金",
    "税率": "纳税人_税务信息",
    "是否有旺季补偿": "旺季补偿",
    "旺季补偿时间": "旺季补偿",
    "旺季补偿规则": "旺季补偿",
    "补偿比例": "旺季补偿",
    "是否有油价联动": "油价联动",
    "油价基准": "油价联动",
    "是否有疫情补贴": "疫情补贴",
    "补贴标准": "疫情补贴",
}


def fact_key_for(field_name: str):
    """字段 → Stage1 事实键名（用于在 Stage2 留空 evidence 时回退取证据）。"""
    return FACT_KEYS.get(field_name)


# ============================================================
# Stage 2：标准化映射 prompt 构件
# ============================================================
STAGE2_ANTI_COPY = _section("anti_copy")


def build_kb_text(ask_fields=None) -> str:
    """字段知识库文本（来自 ledger.md 的 field_kb 分段）。

    ask_fields 仅为兼容调用保留——知识库覆盖全部台账抽取字段，台账每次请求的就是这批字段，
    故整段注入即可（与旧实现按字段渲染结果一致）。
    """
    return _section("field_kb")


def build_fewshot_text(ask_fields=None) -> str:
    """转换方法演示文本（来自 ledger.md 的 fewshot 分段；去数值化、只示范方法/口径）。"""
    return _section("fewshot")


def build_stage2_system(ask_fields) -> str:
    """Stage2 system prompt：知识库规则 + 转换方法演示 + 防抄铁律 + 输出契约。"""
    kb = build_kb_text(ask_fields)
    fs = build_fewshot_text(ask_fields)
    field_list = "、".join(ask_fields)
    return f"""你是合同字段标准化专家。第一阶段已从合同抽好"事实"，本阶段把事实**映射成业务系统的标准字段值**。

核心原则：
1. 忠实于事实，不创造合同里没有的信息。
2. 严格按下面的字段知识库规则映射。
3. 参考转换方法演示学**方法/口径**，但遵守下面的防照抄铁律（绝不照抄演示里的数字）。
4. 不确定/事实里没有 → 填 null（不要瞎填、不要默认值）。

{STAGE2_ANTI_COPY}

========== 字段知识库（业务规则） ==========
{kb}

========== 转换方法演示（只示范方法/口径，严禁照抄其中数字） ==========
{fs}

========== 要输出的字段 ==========
{field_list}

========== 输出格式（务必严格） ==========
只输出 JSON，不要任何解释：
{{"fields": {{ "字段名": {{"value": 标准值或null, "evidence": "支持该值的合同原文片段(从事实的evidence里抄来,必须是原文真实出现的字串)", "confidence": 0~1}}, ... }}}}
- 上面"要输出的字段"逐个都要给（没有就 value=null、给低 confidence）。
- 取值口径严格按知识库：税率/补偿比例写**小数**，保证金按**万元数值**，日期 **YYYY-MM-DD**，是否类只用 **是/否**。
- **相对期限**时「合同结束时间」必须为 null、把时长原文放进「合同期限」。
- evidence 必须能在合同原文里找到（系统会逐条回查，编造的证据会被判幻觉并降置信）。
"""


def build_stage2_user(facts: dict) -> str:
    """Stage2 用户输入。含路由标志串「标准化字段值」（供离线 mock 识别）。"""
    import json
    return f"""请根据以下从合同中抽取的事实，输出**标准化字段值**（只按事实，遵守防照抄铁律）。

提取的事实：
{json.dumps(facts, ensure_ascii=False, indent=2)}"""
