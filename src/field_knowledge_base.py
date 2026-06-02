"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

字段知识库 — 2026-06-02 业务确认版
=====================================
按业务人员确认的规则重写。改动要点：
  - 账期 = 结算周期 + 交付周期 (如 15+30=45)
  - 合同开始/结束时间：中间段落优先 > 末尾签章日期
  - 合同类型：第一中标人→主选, 第二→备选, 默认→主选
  - 合同名称：第一条线路名 + "一干运输合同"
  - 合同编码：BHWL-YZ邮政-YYYYMMDDNNN
  - 合同预警提醒：结束时间 - 今天 (剩余天数)
  - 保证金单位：万元
  - 不再提取：是否完成签订、是否同步财务
"""

EXCEL_HEADERS = [
    "登记日期", "项目名称/编号", "客户分类", "业务类型", "合同类型",
    "省", "市", "客户名称", "合同主体", "合同编码", "合同名称",
    "合同开始时间", "合同结束时间", "是否完成签订", "是否同步财务",
    "合同预警提醒", "账期（天）", "保证金（万元）", "税率",
    "是否有旺季补偿", "旺季补偿时间", "旺季补偿规则", "补偿比例",
    "是否有油价联动", "油价基准(元/升）", "是否有疫情补贴", "补贴标准",
    "联系人", "电话", "地址", "快递单号", "钉钉审批单号"
]

HEADER_TO_KEY = {h: h for h in EXCEL_HEADERS}
HEADER_TO_KEY["项目名称/编号"] = "项目名称_编号"
HEADER_TO_KEY["账期（天）"] = "账期_天"
HEADER_TO_KEY["保证金（万元）"] = "保证金_万元"
HEADER_TO_KEY["油价基准(元/升）"] = "油价基准_元每升"

# ============================================================
# 不提取、不参与验证的字段
# ============================================================
RED_FIELDS = [
    "登记日期", "项目名称_编号", "合同编码", "合同名称",
    "是否完成签订", "是否同步财务",
    "联系人", "电话", "地址", "快递单号", "钉钉审批单号",
    "合同预警提醒",
]

# ============================================================
# 字段定义
# ============================================================
FIELD_KNOWLEDGE_BASE = {

    # ---- 内部 / 不提取 ----
    "登记日期": {"description": "系统时间", "source": "internal", "stage1_extract": False},
    "项目名称_编号": {"description": "不用填写", "source": "internal", "stage1_extract": False},
    "合同编码": {"description": "自动生成", "source": "internal", "stage1_extract": False},
    "合同名称": {"description": "自动生成（第一条线路+一干运输合同）", "source": "internal", "stage1_extract": False},
    "是否完成签订": {"description": "不用写", "source": "internal", "stage1_extract": False},
    "是否同步财务": {"description": "不用写", "source": "internal", "stage1_extract": False},
    "合同预警提醒": {"description": "结束时间-今天=剩余天数", "source": "internal", "stage1_extract": False},
    "联系人": {"description": "不用填", "source": "internal", "stage1_extract": False},
    "电话": {"description": "不用填", "source": "internal", "stage1_extract": False},
    "地址": {"description": "不用填", "source": "internal", "stage1_extract": False},
    "快递单号": {"description": "不用填", "source": "internal", "stage1_extract": False},
    "钉钉审批单号": {"description": "不用填", "source": "internal", "stage1_extract": False},

    # ============================================================
    # ✅ 需提取字段（19个 Stage 1 → 合同中找）
    # ============================================================

    "省": {"description": "甲方所在省份", "source": "contract",
           "stage1_fact_key": "甲方_省份", "stage1_extract": True,
           "note": "⚠️ 只填名称。如'安徽'不要填'安徽省'。直辖市(北京/上海/天津/重庆)：省填城市名（如'北京'），市填区名（如'大兴'）。"},

    "市": {"description": "甲方所在城市，直辖市填区名", "source": "contract",
           "stage1_fact_key": "甲方_城市", "stage1_extract": True,
           "note": "⚠️ 只填名称。普通城市如'合肥'不要填'合肥市'。直辖市(北京/上海/天津/重庆)：市填下级区名（如'大兴'、'浦东'）。"},

    "直辖市": {
        "description": "内部规则：直辖市省份=城市名，城市=区名。从甲方地址中提取区名作为'市'的值。",
        "source": "internal",
        "stage1_extract": False,
    },
    "客户名称": {
        "description": "甲方全称", "source": "contract", "stage1_fact_key": "甲方_全称", "stage1_extract": True,
    },
    "合同主体": {
        "description": "我方公司（承包方/乙方全称）", "source": "contract",
        "stage1_fact_key": "乙方_全称", "stage1_extract": True,
        "note": "可能是北京博华物流有限公司或天津智猪网网络科技有限公司等，从合同'承包方（乙方）：'提取。",
    },

    # ---- 映射类 ----
    "客户分类": {
        "description": "按甲方名称映射", "source": "contract → mapping",
        "allowed_values": ["邮政", "京东", "其他"],
        "stage1_fact_key": "甲方_全称", "stage1_extract": True,
        "mapping_rules": [
            {"condition": "含'中国邮政'/'邮政'", "rule": "→ '邮政'"},
            {"condition": "含'京东'", "rule": "→ '京东'"},
        ],
    },
    "业务类型": {
        "description": "一干=一级干线运输", "source": "contract → mapping",
        "allowed_values": ["一干", "省干", "市内", "其他"],
        "stage1_fact_key": "业务范围描述", "stage1_extract": True,
        "mapping_rules": [{"condition": "标题含'一级干线'/'干线'/'一干'", "rule": "→ '一干'"}],
    },
    "合同类型": {
        "description": "主选/备选（两个维度综合判断）", "source": "contract → mapping",
        "allowed_values": ["主选", "备选", "主选/备选"],
        "stage1_fact_key": "中标人信息", "stage1_extract": True,
        "mapping_rules": [
            {"condition": "⚠️ 验证：提取到的中标人描述必须包含乙方公司全称。不含乙方公司名的一律丢弃。",
             "rule": "只用作含乙方公司名的中标人信息"},
            {"condition": "维度1—中标人(乙方是)：乙方公司名附近的描述含'第一中标人/第一成交人(主供应商)'→有主选身份",
             "rule": "主选侧 = 是"},
            {"condition": "维度1—中标人(乙方是)：乙方被列为'第二中标人/第二成交人'→有备选身份",
             "rule": "备选侧 = 是"},
            {"condition": "维度2—线路表：合同开头线路表格中，'线路性质'列有'备用'/'备选'字样",
             "rule": "备选侧 = 是"},
            {"condition": "维度2—线路表：有'正式'/'主选'等字样",
             "rule": "主选侧 = 是"},
            {"condition": "最终判定：主选侧=是 且 备选侧=是 → '主选/备选'",
             "rule": "两者都有就都写"},
            {"condition": "只有主选侧=是 → '主选'",
             "rule": "→ '主选'"},
            {"condition": "只有备选侧=是 → '备选'",
             "rule": "→ '备选'"},
        ],
        "note": "⚠️ 验证规则：提取到的中标人描述必须包含乙方公司全称（如'北京博华物流有限公司'），否则该描述不是关于乙方的，应丢弃。例如：'江苏省分公司...第一成交人'不含乙方公司名→丢弃。如维度1无有效信息→回退到线路表格维度或默认主选。",
    },
    "合同开始时间": {
        "description": "中间段落优先 > 末尾签章日期", "source": "contract",
        "stage1_fact_key": "合同期限", "stage1_extract": True, "format": "YYYY-MM-DD",
        "mapping_rules": [
            {"condition": "中间有明确起止日期 → 以中间为准", "rule": "取中间段落的日期"},
            {"condition": "中间无 → 去末尾找签章日期", "rule": "取末尾最后一个日期"},
        ],
    },
    "合同结束时间": {
        "description": "中间段落优先", "source": "contract",
        "stage1_fact_key": "合同期限", "stage1_extract": True, "format": "YYYY-MM-DD",
        "mapping_rules": [
            {"condition": "中间有明确起止日期 → 以中间为准", "rule": "取中间段落的日期"},
            {"condition": "中间只有'有效期X年'无具体日期 → 计算", "rule": "开始时间+X年-1天"},
        ],
    },
    "账期_天": {
        "description": "结算周期+交付周期（如15+30=45）", "source": "contract",
        "stage1_fact_key": "付款账期", "stage1_extract": True,
        "mapping_rules": [
            {"condition": "找两个数字相加：A=结算/对账周期天数, B=收到发票后X日付", "rule": "A+B"},
        ],
        "note": "一般A=15, B=30, →45。如果没有找到就不写，不要编造信息",
    },
    "保证金_万元": {
        "description": "履约保证金(万元)", "source": "contract",
        "stage1_fact_key": "履约保证金", "stage1_extract": True,
        "note": "合同写元 → ÷10000转万元。无具体金额留空。",
    },
    "税率": {
        "description": "增值税税率", "source": "contract",
        "stage1_fact_key": "纳税人_税务信息", "stage1_extract": True,
        "allowed_values": ["9%", "6%", "13%", "3%"],
        "mapping_rules": [{"condition": "一般纳税人+运输", "rule": "→ 9%"}],
    },

    # ---- 旺季（4字段，第一个=否则后面都不用填） ----
    "是否有旺季补偿": {
        "description": "是否有旺季条款且已生效", "source": "contract",
        "stage1_fact_key": "旺季补偿", "stage1_extract": True,
        "allowed_values": ["是", "否"],
        "mapping_rules": [
            {"condition": "有旺季条款且生效(打勾/未划)", "rule": "→ '是'"},
            {"condition": "无 / 未打勾", "rule": "→ '否'"},
        ],
    },
    "旺季补偿时间": {"source": "contract", "stage1_fact_key": "旺季补偿", "stage1_extract": True},
    "旺季补偿规则": {"source": "contract", "stage1_fact_key": "旺季补偿", "stage1_extract": True},
    "补偿比例": {"source": "contract", "stage1_fact_key": "旺季补偿", "stage1_extract": True},

    # ---- 油价联动（2字段，成组） ----
    "是否有油价联动": {
        "description": "是否有油价-运费联动", "source": "contract",
        "stage1_fact_key": "油价联动", "stage1_extract": True,
        "allowed_values": ["是", "否"],
    },
    "油价基准_元每升": {
        "description": "基准油价数字(不管单位)", "source": "contract",
        "stage1_fact_key": "油价联动", "stage1_extract": True,
        "note": "只填数字，不管元/升还是元/吨。如8.16。",
    },

    # ---- 疫情补贴（2字段，成组） ----
    "是否有疫情补贴": {
        "description": "是否有疫情相关条款（含价格调整/补贴/特殊措施）", "source": "contract",
        "stage1_fact_key": "疫情补贴", "stage1_extract": True,
        "allowed_values": ["是", "否"],
        "note": "不只找'补贴'二字。疫情导致的价格调整条款(如'疫情等不受控风险可启用旺季价')也算'是'。",
    },
    "补贴标准": {
        "description": "疫情补贴/调整的具体标准（明确数字或公式才填）", "source": "contract",
        "stage1_fact_key": "疫情补贴", "stage1_extract": True,
        "note": "⚠️ 如果没有明确的金额/比例/公式（如只说'单独申请'、'经批准后'），则留空。不要编造。",
    },
}


def get_fields_by_source(source):
    return {k: v for k, v in FIELD_KNOWLEDGE_BASE.items() if v.get("source") == source}

def get_extractable_fields():
    return {k: v for k, v in FIELD_KNOWLEDGE_BASE.items() if v.get("stage1_extract", False)}

def get_internal_fields():
    return {k: v for k, v in FIELD_KNOWLEDGE_BASE.items() if v.get("source") == "internal"}

def get_red_fields():
    return RED_FIELDS

def is_red_field(key):
    return key in RED_FIELDS

def get_excel_header_for_key(key):
    reverse_map = {
        "项目名称_编号": "项目名称/编号",
        "账期_天": "账期（天）",
        "保证金_万元": "保证金（万元）",
        "油价基准_元每升": "油价基准(元/升）",
    }
    if key in reverse_map:
        return reverse_map[key]
    return key
