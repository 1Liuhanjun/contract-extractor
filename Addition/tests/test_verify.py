"""单元测试：文字字段增强（证据回查 / 枚举归一 / self-consistency 投票）。"""
import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE_DIR))

from schemas import Field
from layers import verify


FULL = "本合同有效期自2023年1月1日起至2025年12月31日止。增值税率9%。联系人：张三。"


def test_evidence_hit():
    assert verify.evidence_hit("增值税率9%", FULL) is True
    assert verify.evidence_hit("联系人：张三", FULL) is True
    assert verify.evidence_hit("联系人李四王五", FULL) is False     # 长证据未命中
    assert verify.evidence_hit("9%", FULL) is True                  # 过短放宽


def test_recheck_evidence_lowers_conf():
    fields = {
        "联系人": Field(value="李四", confidence=0.9, evidence="甲方联系人为李四先生"),  # 不在原文
        "税率": Field(value="0.09", confidence=0.9, evidence="增值税率9%"),             # 命中
    }
    failed = verify.recheck_evidence(fields, FULL)
    assert "联系人" in failed
    assert fields["联系人"].evidence_ok is False
    assert fields["联系人"].confidence < 0.9
    assert fields["税率"].evidence_ok is True


def test_normalize_enums():
    fields = {
        "是否有旺季补偿": Field(value="有，双11加价"),
        "是否有油价联动": Field(value="无"),
        "合同类型": Field(value="第一中标人，主选线路"),
        "邮路性质": Field(value="双程运输"),
    }
    verify.normalize_enums(fields)
    assert fields["是否有旺季补偿"].value == "是"
    assert fields["是否有油价联动"].value == "否"
    assert fields["合同类型"].value == "主选"
    assert fields["邮路性质"].value == "双程"


class FakeVoteLlm:
    """投票时返回不同值，制造冲突。"""
    def __init__(self, seq):
        self.seq = list(seq)
        self.i = 0

    def call_json(self, system, user, temperature=None, label=""):
        v = self.seq[self.i % len(self.seq)]
        self.i += 1
        return {"fields": {"账期": {"value": v, "confidence": 0.6, "evidence": "结算账期", "page": 1}}}


def test_self_consistency_majority():
    base = {"账期": Field(value="60", confidence=0.6, evidence="账期60天")}
    llm = FakeVoteLlm(["60", "60", "90"])   # 3 票：60,60,90；加首次 60 → 多数 60
    verify.self_consistency(llm, "sys", "user", base, ["账期"], n_votes=3, temperature=0.7)
    assert str(base["账期"].value) == "60"
    assert base["账期"].candidates is not None
    # 候选里应出现 60 和 90 两种
    vals = {str(c["value"]) for c in base["账期"].candidates}
    assert "60" in vals and "90" in vals
