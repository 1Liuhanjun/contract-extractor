"""文字字段准确率增强（对应 v2 §13.1）：

1. 证据回查（反幻觉）：每个非空字段的 evidence 必须能在 OCR 原文里命中，
   不命中 → evidence_ok=False 且压低 confidence（疑似幻觉/迁移）。
2. 枚举软归一：把 是否类/主备选/邮路性质 归一到标准枚举（仅在能明确映射时），
   不能映射的保留原值并不强改（不做硬过滤）。
3. self-consistency 投票：对"低置信或证据未命中"的标量字段，用 temperature>0
   重复抽取 N 次，与首次结果一起取多数票；票数分布记入 candidates 供复核。

注意：价格表数字不走这里（靠 OCR 交叉 + 校验层），见 price_ocr.py / validate.py。
"""
import re
import json
import logging
from collections import Counter

from schemas import Field, ENUM_YESNO, ENUM_CONTRACT_TYPE, ENUM_ROUTE_NATURE

log = logging.getLogger("verify")

_PUNCT = re.compile(r"[\s　，,。.、；;：:！!？?（）()【】\[\]「」“”\"'’‘\-—_/\\|]+")


def _norm(s) -> str:
    """归一化：去空白与常见标点、全角空格，便于证据子串命中。"""
    if s is None:
        return ""
    return _PUNCT.sub("", str(s)).lower()


def evidence_hit(evidence, full_md, min_len=4) -> bool:
    """evidence 是否真实出现在 OCR 文本里（归一化后子串匹配）。

    - 证据过短（<min_len 个有效字符）放宽为 True（避免对 "9%"/"60" 这类短值误杀）。
    - 否则要求归一化证据是归一化全文的子串。
    """
    ne = _norm(evidence)
    if len(ne) < min_len:
        return True
    return ne in _norm(full_md)


def recheck_evidence(fields: dict, full_md: str, penalty=0.4, floor_conf=0.3):
    """对一组 {name: Field} 做证据回查；就地修改并返回命中失败的字段名列表。"""
    failed = []
    for name, f in fields.items():
        if f is None or f.value in (None, "", "null"):
            continue
        ok = evidence_hit(f.evidence, full_md)
        f.evidence_ok = ok
        if not ok:
            old = f.confidence if f.confidence is not None else 0.8
            f.confidence = round(max(floor_conf, old - penalty), 2)
            failed.append(name)
            log.info("证据回查未命中: %s（值=%r），置信降至 %s", name, f.value, f.confidence)
    return failed


# ---- 枚举软归一 ----
def normalize_enums(fields: dict):
    """把可明确映射的枚举字段归一到标准值；不能映射保留原值。"""
    for name, f in fields.items():
        if f is None or f.value in (None, ""):
            continue
        v = str(f.value).strip()
        if name.startswith("是否"):
            f.value = _to_yesno(v) or f.value
        elif name == "合同类型":
            f.value = _pick(v, ENUM_CONTRACT_TYPE) or f.value
        elif name in ("邮路性质",):
            f.value = _pick(v, ENUM_ROUTE_NATURE) or f.value


def _to_yesno(v):
    s = v.lower()
    if v in ENUM_YESNO:
        return v
    if any(k in s for k in ("有", "是", "yes", "true", "√")):
        return "是"
    if any(k in s for k in ("无", "否", "没有", "不适用", "no", "false")):
        return "否"
    return None


def _pick(v, enum):
    for e in enum:
        if e in v:
            return e
    return None


# ---- self-consistency 投票 ----
def _key(v):
    """投票归并键：数值按值、字符串按归一化。"""
    if isinstance(v, (int, float)):
        return ("num", float(v))
    return ("str", _norm(v))


def self_consistency(llm, system, user, base_fields: dict, weak_names,
                     n_votes=3, temperature=0.7):
    """对 weak_names 字段重复抽取投票，更新 base_fields。

    llm.call_json 需支持 temperature 覆盖。每轮把模型返回的对应字段值收集起来，
    与首次值一起多数表决；票数写入 candidates，命中多数则提升 confidence。
    """
    if not weak_names:
        return
    ballots = {name: [] for name in weak_names}  # 每项存 (value, evidence, page) 三元组
    # 首次结果也投一票
    for name in weak_names:
        f = base_fields.get(name)
        if f and f.value not in (None, "", "null"):
            ballots[name].append((f.value, f.evidence, f.page))

    for i in range(n_votes):
        try:
            data = llm.call_json(system, user, temperature=temperature,
                                 label=f"投票{i+1}/{n_votes}")
        except Exception as e:  # noqa
            log.warning("self-consistency 第%d票失败: %s", i + 1, e)
            continue
        raw = data.get("fields", data) if isinstance(data, dict) else {}
        for name in weak_names:
            cand = Field.coerce(raw.get(name))
            if cand.value not in (None, "", "null"):
                ballots[name].append((cand.value, cand.evidence, cand.page))

    for name in weak_names:
        votes = ballots[name]
        if not votes:
            continue
        counter = Counter(_key(v[0]) for v in votes)
        # 取票数最多的归一键，回填一个该键对应的原始值
        best_key, best_n = counter.most_common(1)[0]
        winner = next(v[0] for v in votes if _key(v[0]) == best_key)
        f = base_fields.get(name) or Field()
        f.candidates = _summarize(votes, counter)
        f.value = winner
        # 多数一致 → 提置信；分歧大 → 压低并进复核
        agree = best_n / len(votes)
        f.confidence = round(min(0.95, 0.5 + 0.45 * agree), 2)
        if len(counter) > 1:
            log.info("self-consistency 分歧 %s: %s → 采用 %r(%d/%d)",
                     name, f.candidates, winner, best_n, len(votes))
        base_fields[name] = f


def _summarize(votes, counter):
    """把候选票数整理成可读列表（含代表性证据/页码），供复核报告展示冲突。

    votes：(value, evidence, page) 三元组列表。每个候选取一个有证据的代表位置。
    返回按票数降序的 [{value, votes, evidence, page}]。
    """
    rep = {}
    for value, evidence, page in votes:
        k = _key(value)
        if k not in rep or (not rep[k][1] and evidence):  # 优先留有证据的代表
            rep[k] = (value, evidence, page)
    out = []
    for k, n in counter.most_common():
        value, evidence, page = rep[k]
        out.append({"value": value, "votes": n, "evidence": evidence, "page": page})
    return out
