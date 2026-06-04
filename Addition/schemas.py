"""结构化输出 schema（兼容 pydantic v1）。

抽取的每个字段是一个 Field：value / confidence / evidence / page。
路由行 RouteRow 含动态吨位价格字典。系数表 Coefficient。
"""
from typing import Optional, Any, List, Dict
from pydantic import BaseModel

# —— 枚举字典（§13.1 Schema 枚举约束；用于软归一与校验，不做硬过滤）——
ENUM_YESNO = ["是", "否"]
ENUM_CONTRACT_TYPE = ["主选", "备选"]
ENUM_ROUTE_NATURE = ["单程", "双程", "往返"]


class Field(BaseModel):
    value: Optional[Any] = None
    confidence: Optional[float] = None
    evidence: Optional[str] = None
    page: Optional[int] = None
    evidence_ok: Optional[bool] = None   # 证据回查：evidence 是否在 OCR 原文命中
    candidates: Optional[List[Any]] = None  # self-consistency 投票时的候选分布

    @classmethod
    def coerce(cls, obj) -> "Field":
        """把 LLM 返回的任意形态规整成 Field。"""
        if obj is None:
            return cls()
        if isinstance(obj, dict):
            return cls(
                value=obj.get("value"),
                confidence=_to_float(obj.get("confidence")),
                evidence=obj.get("evidence"),
                page=_to_int(obj.get("page")),
            )
        # 标量直接当 value
        return cls(value=obj)


class RouteRow(BaseModel):
    线路名称: Optional[str] = None
    邮路性质: Optional[str] = None
    里程: Optional[Any] = None
    分包号: Optional[Any] = None
    备注: Optional[str] = None
    类型: Optional[str] = None
    合同编号: Optional[str] = None
    # 吨位价格：键=吨位列表头（如 "20吨/12.5"），值=直读价格；只含表里真有数字的吨位
    吨位价格: Dict[str, Any] = {}
    confidence: Optional[float] = None
    evidence: Optional[str] = None
    page: Optional[int] = None
    # 计算层回填：换算来源说明
    换算说明: Optional[str] = None


class Coefficient(BaseModel):
    found: bool = False
    基准车型: Optional[str] = None
    系数: Dict[str, float] = {}     # 键=吨位标签(如 "8吨")，值=系数
    evidence: Optional[str] = None
    page: Optional[int] = None


class ContractResult(BaseModel):
    """一份合同的完整抽取+计算结果。"""
    company: str = "未知"
    company_confidence: float = 0.0
    ledger_fields: Dict[str, Field] = {}      # 台账字段
    routes: List[RouteRow] = []
    coefficient: Coefficient = Coefficient()
    routes_found: bool = False


def _to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _to_int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None
