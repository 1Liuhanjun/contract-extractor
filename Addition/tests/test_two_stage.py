"""两阶段台账抽取（新合同台账）离线测试。

验证点：
- Stage2 system prompt 带"防照抄/口径/输出格式"护栏（合并自同事主表方法 + 本项目防过拟合框架）。
- 少样本文本只含被请求字段、且演示的是"转换方法"而非默认值。
- 两阶段端到端：Stage1 事实 → Stage2 标准化，产出正确的台账 Field（口径对齐本项目）。
- 邮政（线路）抽取未受影响（extract_routes 仍正常）。
不需要任何 API key（全程 Mock）。
"""
import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE_DIR))

from app_config import Config
from providers import mock
from providers.mock import MockLlm
from layers import extract as L2
from layers import calculate as L3
from companies import ledger_kb


def test_stage2_prompt_has_guardrails():
    fields = ["税率", "保证金", "合同结束时间", "合同期限", "补偿比例"]
    sysp = ledger_kb.build_stage2_system(fields)
    # 防照抄铁律
    assert "严禁照抄" in sysp or "防照抄" in sysp
    # 关键口径
    assert "万元" in sysp
    assert "YYYY-MM-DD" in sysp
    assert "小数" in sysp
    # 被请求字段都在
    for f in fields:
        assert f in sysp


def test_fewshot_is_method_only_no_concrete_values():
    """few-shot 去数值化：只示范转换方法/口径，不含任何真实合同的具体业务数值（防过拟合照抄）。"""
    fs = ledger_kb.build_fewshot_text(["保证金", "税率", "补偿比例"])
    assert fs.strip()
    for leaked in ("317.8", "8.16"):           # 不得泄漏真实合同的保证金/油价数值
        assert leaked not in fs
    assert "万元" in fs and "0.09" in fs        # 仍保留方法/口径要点（换算关系是示意，非合同答案）
    # FEW_SHOT 这类"具体合同答案"容器应已移除
    assert not hasattr(ledger_kb, "FEW_SHOT")


def test_two_stage_extract_values():
    cfg = Config()
    llm = MockLlm()
    out = L2.extract_ledger(llm, cfg.skill_text, cfg.field_map, mock.ZHEJIANG_MD)

    assert out["账期"].value == "60"
    assert out["省"].value == "浙江"
    assert out["市"].value == "/"                       # 省级合同 → 市填 "/"
    assert str(out["税率"].value) == "0.09"             # 小数口径
    assert out["保证金"].value in (None, "", "null")    # 无金额 → 留空
    assert out["业务类型"].value == "二干"
    assert out["合同类型"].value == "主选"
    # 两阶段：Stage1 + Stage2 都至少各调一次
    assert llm.call_count >= 2


def test_stage2_non_dict_falls_back():
    """Stage2 返回异常形态（非 dict，如 list）时必须回退单阶段，绝不"成功地空跑"。"""
    cfg = Config()

    class BadStage2(MockLlm):
        def call_json(self, system, user, temperature=None, label=""):
            self.call_count += 1
            if "标准化字段值" in user:       # Stage2 故意返回非 dict
                return ["不是字典"]
            if "提取事实" in user:           # Stage1 正常
                return self._facts()
            if "抽取以下字段" in user:       # 单阶段回退
                return self._ledger()
            return {}

    out = L2.extract_ledger(BadStage2(), cfg.skill_text, cfg.field_map, mock.ZHEJIANG_MD)
    # 回退成功：单阶段填出了省=浙江（而非全空）
    assert out["省"].value == "浙江"
    assert out["账期"].value == "60"


def test_routes_still_work():
    """邮政（线路）抽取不受台账两阶段改造影响。"""
    cfg = Config()
    llm = MockLlm()
    routes, found, billing = L2.extract_routes(
        llm, cfg.skill_text, cfg.field_map, mock.ZHEJIANG_MD,
        L2.tables_to_md(mock.MockOcr().parse_pdf("x")))
    assert found is True
    assert len(routes) == 2
    assert routes[0].线路名称 == "杭州-郑州"
    # 红字不抽取列：即便 mock 返回了 类型/合同编号，RouteRow 也不接收（保持 None）
    assert routes[0].类型 is None
    assert routes[0].合同编号 is None
    assert billing is None


def test_routes_prompt_prefers_price_table_over_mileage_list():
    """江苏南京『价格表(3行) + 第32页比照线路清单(无价)』过抽 17 行的回归：
    线路抽取提示词须明确『存在带价格的明细表时，只抽价格表、跳过无价的发运/比照清单』，
    仅当通篇无任何带价格明细表时才回退抽发运计划表。"""
    cfg = Config()

    class CapturingLlm(MockLlm):
        routes_prompt = None
        def call_json(self, system, user, temperature=None, label=""):
            if "吨位列名必须从下面这组里选" in user:
                CapturingLlm.routes_prompt = user
            return super().call_json(system, user, temperature, label)

    L2.extract_routes(CapturingLlm(), cfg.skill_text, cfg.field_map,
                      mock.ZHEJIANG_MD, L2.tables_to_md(mock.MockOcr().parse_pdf("x")))
    p = CapturingLlm.routes_prompt
    assert p, "未捕获到线路抽取 prompt"
    # 核心护栏：价格表优先、无价清单跳过、无价格表才回退
    assert "价格列" in p
    assert "比照" in p and "发运计划表" in p
    assert "整张跳过" in p or "绝不要把这类无价清单里的线路当明细行" in p
    assert "回退" in p


def test_redfields_aligned_with_colleague():
    """按同事口径：登记日期/合同名称/联系人/电话/地址 划为红字不抽；是否完成签订 改为可抽。"""
    cfg = Config()
    asks = L2._ledger_a_fields(cfg.field_map)
    for red in ["登记日期", "合同名称", "联系人", "电话", "地址"]:
        assert red not in asks, f"{red} 应为红字、不应被抽取"
    assert "是否完成签订" in asks, "是否完成签订 应为可抽字段（对齐同事）"


def test_end_date_excludes_extension_clause():
    """合肥结束时间错算根因回归：'自签订之日起2年，至下一轮采购完成，最多不超过4个月'
    应只按主期限 2 年算到 2024-09-28，而不是把 4 个月延期也加进去算成 2025-01-28。"""
    term = "自签订之日起2年，至下一轮采购完成，最多不超过4个月"
    assert L3._parse_term_months(term) == 24           # 不是 28
    assert L3._compute_end_date("2022-09-29", term) == "2024-09-28"   # 不是 2025-01-28
    # 纯主期限/无延期条款不受影响
    assert L3._parse_term_months("24个月") == 24
    assert L3._parse_term_months("自签订之日起2年") == 24
    # 护栏：固定日期串误填进合同期限时，不当相对期限解析（不算出天文数字）
    assert L3._parse_term_months("自2022年9月29日至2024年9月28日") == 0
    assert L3._compute_end_date("2022-09-29", "自2022年9月29日至2024年9月28日") is None
