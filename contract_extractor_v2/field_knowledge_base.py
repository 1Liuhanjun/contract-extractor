"""
字段知识库
===========
每个目标字段的定义：允许值、映射规则、已知样本。
这是整个系统的"业务规则层"，业务逻辑集中在这里。
当分类或规则变化时，只需要改这个文件。

字段分类：
  - contract: 可从合同正文提取
  - contract → mapping: 需从合同提取原文后再映射为标准值
  - internal: 不在合同中，需从其他系统获取（标红字段）
"""

# ============================================================
# Excel 表头顺序（输出用，保留所有32列）
# ============================================================
EXCEL_HEADERS = [
    "登记日期", "项目名称/编号", "客户分类", "业务类型", "合同类型",
    "省", "市", "客户名称", "合同主体", "合同编码", "合同名称",
    "合同开始时间", "合同结束时间", "是否完成签订", "是否同步财务",
    "合同预警提醒", "账期（天）", "保证金（万元）", "税率",
    "是否有旺季补偿", "旺季补偿时间", "旺季补偿规则", "补偿比例",
    "是否有油价联动", "油价基准(元/升）", "是否有疫情补贴", "补贴标准",
    "联系人", "电话", "地址", "快递单号", "钉钉审批单号"
]

# Excel列名 → 字段key的映射
HEADER_TO_KEY = {h: h for h in EXCEL_HEADERS}
# 特殊映射（Excel列名含特殊字符）
HEADER_TO_KEY["项目名称/编号"] = "项目名称_编号"
HEADER_TO_KEY["账期（天）"] = "账期_天"
HEADER_TO_KEY["保证金（万元）"] = "保证金_万元"
HEADER_TO_KEY["油价基准(元/升）"] = "油价基准_元每升"


# ============================================================
# 标红字段（不在合同中，不需要从合同提取）
# ============================================================
# 这些字段的 stage1_extract = False，AI不会尝试从合同提取
# 输出Excel时这些列留空，由人工或其他系统填写
RED_FIELDS = [
    "登记日期", "项目名称_编号", "合同编码", "合同名称",
    "联系人", "电话", "地址", "快递单号", "钉钉审批单号",
]


# ============================================================
# 字段知识库
# ============================================================
# 每个字段包含：
#   description: 定义
#   source: 信息来源（contract=可从合同提取, internal=不在合同中）
#   allowed_values: 允许的值（None表示自由文本）
#   mapping_rules: 映射规则列表
#   known_examples: 已知的 "事实→标准值" 样本
#   note: 注意事项
#   stage1_extract: 是否需要Stage 1从合同提取

FIELD_KNOWLEDGE_BASE = {

    # ===========================================================
    # 🔴 标红字段（不在合同中，不提取）
    # ===========================================================
    "登记日期": {
        "description": "合同信息录入系统的日期",
        "source": "internal",
        "stage1_extract": False,
        "note": "标红字段，不在合同中，需从OA/ERP系统获取",
    },
    "项目名称_编号": {
        "description": "内部项目编号",
        "source": "internal",
        "stage1_extract": False,
        "note": "标红字段，不在合同中，需从ERP/投标文件获取",
    },
    "合同编码": {
        "description": "合同内部编码",
        "source": "internal",
        "stage1_extract": False,
        "note": "标红字段，不在合同中，需从系统获取",
    },
    "合同名称": {
        "description": "内部项目名称",
        "source": "internal",
        "stage1_extract": False,
        "note": "标红字段，取内部项目名（如'2022年安徽合肥邮局11省大标线路合同'），非合同正式标题",
    },
    "联系人": {
        "description": "乙方派驻现场的业务对接人姓名",
        "source": "internal",
        "stage1_extract": False,
        "note": "标红字段，非合同中的通知联系人。现场对接人信息需从线下获取",
    },
    "电话": {
        "description": "乙方派驻现场对接人的电话",
        "source": "internal",
        "stage1_extract": False,
        "note": "标红字段，不在合同中",
    },
    "地址": {
        "description": "乙方服务/现场地址",
        "source": "internal",
        "stage1_extract": False,
        "note": "标红字段，非合同注册地址或通知地址。服务地址需从线下获取",
    },
    "快递单号": {
        "description": "合同寄送的快递单号",
        "source": "internal",
        "stage1_extract": False,
        "note": "标红字段，不在合同中，需从快递记录获取",
    },
    "钉钉审批单号": {
        "description": "钉钉/OA审批流程单号",
        "source": "internal",
        "stage1_extract": False,
        "note": "标红字段，不在合同中，需从钉钉/OA获取",
    },

    # ===========================================================
    # ✅ 需提取的字段（23个）
    # ===========================================================

    # ---- 从合同直接提取 ----
    "省": {
        "description": "客户（甲方）所在的省份",
        "source": "contract",
        "allowed_values": None,
        "stage1_fact_key": "甲方_省份",
        "stage1_extract": True,
        "mapping_rules": [
            {"condition": "从甲方全称的省名提取", "rule": "如'安徽省'→'安徽'"}
        ],
        "known_examples": [
            {"fact": "发包方（甲方）：中国邮政集团有限公司安徽省合肥邮区中心", "value": "安徽"}
        ]
    },
    "市": {
        "description": "客户（甲方）所在的城市",
        "source": "contract",
        "allowed_values": None,
        "stage1_fact_key": "甲方_城市",
        "stage1_extract": True,
        "mapping_rules": [
            {"condition": "从甲方全称提取城市名", "rule": "如'合肥'→'合肥'"}
        ],
        "known_examples": [
            {"fact": "发包方（甲方）：中国邮政集团有限公司安徽省合肥邮区中心", "value": "合肥"}
        ]
    },
    "客户名称": {
        "description": "客户（甲方/发包方）的企业全称",
        "source": "contract",
        "allowed_values": None,
        "stage1_fact_key": "甲方_全称",
        "stage1_extract": True,
        "note": "直接从合同'发包方（甲方）：'后提取原文。合同写什么就是什么，不加'局'字",
        "mapping_rules": [
            {"condition": "从合同找'发包方（甲方）：'后的企业全称", "rule": "原文提取，不修改"}
        ],
        "known_examples": [
            {"fact": "发包方（甲方）：中国邮政集团有限公司安徽省合肥邮区中心",
             "value": "中国邮政集团有限公司安徽省合肥邮区中心"}
        ]
    },
    "合同主体": {
        "description": "与我方签订合同的法人主体全称（即乙方/承包方）",
        "source": "contract",
        "allowed_values": None,
        "stage1_fact_key": "乙方_全称",
        "stage1_extract": True,
        "note": "注意！合同主体是乙方（我方），不是甲方！从'承包方（乙方）：'处提取",
        "mapping_rules": [
            {"condition": "从合同找'承包方（乙方）：'后的企业全称", "rule": "原文提取"}
        ],
        "known_examples": [
            {"fact": "承包方（乙方）：北京博华物流有限公司",
             "value": "北京博华物流有限公司"}
        ]
    },
    "合同开始时间": {
        "description": "合同生效日期",
        "source": "contract",
        "allowed_values": None,
        "format": "YYYY-MM-DD",
        "stage1_fact_key": "合同期限",
        "stage1_extract": True,
        "note": "取最后一个签署/盖章日期。如合同说'自签订之日起X年'，开始时间=签署日期",
        "mapping_rules": [
            {"condition": "合同写'自签订之日起X年'", "rule": "开始时间=签署日期（以最后一个日期为准）"},
        ],
        "known_examples": [
            {"fact": "本合同有效期自签订之日起2年，签字页日期：2022年9月29日（甲方）、2022年7月29日（乙方）",
             "value": "2022-09-29"}
        ]
    },
    "合同结束时间": {
        "description": "合同到期日期",
        "source": "contract",
        "allowed_values": None,
        "format": "YYYY-MM-DD",
        "stage1_fact_key": "合同期限",
        "stage1_extract": True,
        "note": "基础结束时间 = 开始时间 + 有效期年数。延长条款（'至下一轮采购完成最多不超过4个月'）暂不体现，等确认后加入",
        "mapping_rules": [
            {"condition": "合同写'有效期X年'", "rule": "结束时间=开始时间+X年-1天"},
        ],
        "known_examples": [
            {"fact": "本合同有效期自签订之日起2年", "value": "2024-09-28"}
        ]
    },
    "是否完成签订": {
        "description": "双方是否已完成盖章签字",
        "source": "contract",
        "allowed_values": ["是", "否"],
        "stage1_fact_key": "签署状态",
        "stage1_extract": True,
        "mapping_rules": [
            {"condition": "有双方盖章签字页", "rule": "'是'"},
            {"condition": "缺少一方签章", "rule": "'否'"},
        ],
        "known_examples": [
            {"fact": "有甲方盖章和乙方盖章，双方签字日期完整", "value": "是"}
        ]
    },
    "是否同步财务": {
        "description": "合同是否已同步到财务系统",
        "source": "internal",
        "allowed_values": ["是", "否"],
        "stage1_extract": False,
        "note": "不在合同中，需从OA审批状态获取。当前为绿色字段但AI无法提取",
    },
    "合同预警提醒": {
        "description": "合同到期预警",
        "source": "internal",
        "allowed_values": None,
        "stage1_extract": False,
        "note": "不在合同中，通常由Excel公式（DATEDIF）计算。当前为绿色字段但AI无法提取",
    },

    # ---- 从合同提取后按规则映射 ----
    "客户分类": {
        "description": "客户的内部业务分类标签",
        "source": "contract → mapping",
        "allowed_values": ["邮政", "京东", "石油", "烟草", "政府", "民营", "其他"],
        "stage1_fact_key": "甲方_全称",
        "stage1_extract": True,
        "mapping_rules": [
            {"condition": "甲方名称含'中国邮政'或'邮政'", "rule": "→ '邮政'"},
            {"condition": "甲方名称含'京东'", "rule": "→ '京东'"},
            {"condition": "甲方名称含'中国石油'或'中石油'或'石化'", "rule": "→ '石油'"},
            {"condition": "甲方名称含'烟草'", "rule": "→ '烟草'"},
            {"condition": "其他情况", "rule": "→ '其他'（需人工确认）"},
        ],
        "known_examples": [
            {"fact": "中国邮政集团有限公司安徽省合肥邮区中心", "value": "邮政"},
        ]
    },
    "业务类型": {
        "description": "业务类型（内部缩写）",
        "source": "contract → mapping",
        "allowed_values": ["一干", "省干", "市内", "其他"],
        "stage1_fact_key": "业务范围描述",
        "stage1_extract": True,
        "note": "'一干'=一级干线运输，从合同标题或业务条款判断",
        "mapping_rules": [
            {"condition": "合同标题含'一级干线'或'干线'", "rule": "→ '一干'"},
            {"condition": "合同描述省内运输", "rule": "→ '省干'"},
            {"condition": "合同描述市内配送", "rule": "→ '市内'"},
        ],
        "known_examples": [
            {"fact": "# 一级干线运输外包服务协议", "value": "一干"},
        ]
    },
    "合同类型": {
        "description": "合同业务分类（主选/备选/其他）",
        "source": "contract → mapping",
        "allowed_values": ["主选", "备选", "临时", "其他"],
        "stage1_fact_key": "合同性质",
        "stage1_extract": True,
        "note": "'主选'是供应商分级。规则：中标项目+框架协议→主选",
        "mapping_rules": [
            {"condition": "合同是中标项目+框架协议", "rule": "→ '主选'"},
            {"condition": "合同是备选中标", "rule": "→ '备选'"},
            {"condition": "临时或单项合同", "rule": "→ '临时'"},
        ],
        "known_examples": [
            {"fact": "本合同为框架协议，第一中标人，一级干线运输外包", "value": "主选"}
        ]
    },
    "税率": {
        "description": "增值税税率（需推断）",
        "source": "contract → mapping",
        "allowed_values": ["9%", "6%", "13%", "3%"],
        "stage1_fact_key": "纳税人_税务信息",
        "stage1_extract": True,
        "note": "合同写'一般纳税人'和'增值税专用发票'，不直接写具体税率。9%=货物运输服务法定税率（一般纳税人+运输服务）",
        "mapping_rules": [
            {"condition": "一般纳税人 + 运输服务", "rule": "→ '9%'"},
            {"condition": "一般纳税人 + 仓储/快递服务", "rule": "→ '6%'"},
            {"condition": "小规模纳税人", "rule": "→ '3%'"},
        ],
        "known_examples": [
            {"fact": "纳税人类型：一般纳税人；业务：邮件干线运输；开具增值税专用发票", "value": "9%"},
        ]
    },
    "账期_天": {
        "description": "甲方收到发票后的付款账期天数",
        "source": "contract",
        "allowed_values": None,
        "stage1_fact_key": "付款账期",
        "stage1_extract": True,
        "note": "⚠️ 待确认：合同写'收到发票之日起30日内'=30天，但参考答案填45天。暂按合同原文提取30天，不写few-shot示例",
        "mapping_rules": [
            {"condition": "事实包含'收到发票之日起X日内'或'X日'付清", "rule": "提取X"},
        ],
        "known_examples": []  # 账期有问题，暂时不加少样本
    },
    "保证金_万元": {
        "description": "履约保证金金额（万元）",
        "source": "contract",
        "allowed_values": None,
        "stage1_fact_key": "履约保证金",
        "stage1_extract": True,
        "format": "保留1位小数",
        "note": "合同单位是元，需÷10000转为万元。保留1位小数",
        "mapping_rules": [
            {"condition": "合同写'保证金XXX元'", "rule": "XXX ÷ 10000，保留1位小数"},
        ],
        "known_examples": [
            {"fact": "自本合同订立之日起3日内，乙方须向甲方缴纳保证金3178420元（按照邮路全年运费的10%收取）",
             "value": 317.8}
        ]
    },

    # ---- 旺季补偿 ----
    "是否有旺季补偿": {
        "description": "合同是否有旺季价格上浮机制",
        "source": "contract",
        "allowed_values": ["是", "否"],
        "stage1_fact_key": "旺季补偿",
        "stage1_extract": True,
        "mapping_rules": [
            {"condition": "有旺季上浮条款", "rule": "→ '是'"},
            {"condition": "无相关条款", "rule": "→ '否'"},
        ],
        "known_examples": [
            {"fact": "旺季期间根据市场实际供需情况，为保证旺季用车调配，旺季期间的邮路价格（正班车辆除外）在合同结算价基础上上浮10%",
             "value": "是"}
        ]
    },
    "旺季补偿时间": {
        "description": "旺季的具体时间段",
        "source": "contract",
        "allowed_values": None,
        "stage1_fact_key": "旺季补偿",
        "stage1_extract": True,
        "note": "保留原文完整描述",
    },
    "旺季补偿规则": {
        "description": "旺季价格调整的具体规则",
        "source": "contract",
        "allowed_values": None,
        "stage1_fact_key": "旺季补偿",
        "stage1_extract": True,
        "note": "保留原文完整描述",
    },
    "补偿比例": {
        "description": "旺季价格上浮比例",
        "source": "contract",
        "allowed_values": None,
        "format": "保留两位小数",
        "stage1_fact_key": "旺季补偿",
        "stage1_extract": True,
    },

    # ---- 油价联动 ----
    "是否有油价联动": {
        "description": "合同是否有油价-运费联动机制",
        "source": "contract",
        "allowed_values": ["是", "否"],
        "stage1_fact_key": "油价联动",
        "stage1_extract": True,
        "mapping_rules": [
            {"condition": "有油价联动条款", "rule": "→ '是'"},
            {"condition": "无相关条款", "rule": "→ '否'"},
        ],
        "known_examples": [
            {"fact": "油价-运费联动机制：以承运商报价当日发改委执行的0号柴油价格8.16元/升为基准油价",
             "value": "是"}
        ]
    },
    "油价基准_元每升": {
        "description": "油价联动机制中的基准油价（元/升）",
        "source": "contract",
        "allowed_values": None,
        "stage1_fact_key": "油价联动",
        "stage1_extract": True,
        "known_examples": [
            {"fact": "以承运商报价当日发改委执行的0号柴油价格8.16元/升为基准油价",
             "value": 8.16}
        ]
    },

    # ---- 疫情补贴 ----
    "是否有疫情补贴": {
        "description": "合同是否有疫情相关补贴条款",
        "source": "contract",
        "allowed_values": ["是", "否"],
        "stage1_fact_key": "疫情补贴",
        "stage1_extract": True,
        "mapping_rules": [
            {"condition": "有疫情或公共卫生补贴条款", "rule": "→ '是'"},
            {"condition": "无相关条款", "rule": "→ '否'"},
        ],
        "known_examples": [
            {"fact": "(合同未提及疫情补贴)", "value": "否"}
        ]
    },
    "补贴标准": {
        "description": "疫情补贴的具体标准",
        "source": "contract",
        "allowed_values": None,
        "stage1_fact_key": "疫情补贴",
        "stage1_extract": True,
    },
}


# ============================================================
# 辅助函数
# ============================================================

def get_fields_by_source(source):
    """按来源类型获取字段列表"""
    return {k: v for k, v in FIELD_KNOWLEDGE_BASE.items()
            if v.get("source") == source}


def get_extractable_fields():
    """获取需要Stage 1提取的字段（stage1_extract=True）"""
    return {k: v for k, v in FIELD_KNOWLEDGE_BASE.items()
            if v.get("stage1_extract", False)}


def get_internal_fields():
    """获取内部系统字段（不在合同中，包括标红字段）"""
    return {k: v for k, v in FIELD_KNOWLEDGE_BASE.items()
            if v.get("source") == "internal"}


def get_red_fields():
    """获取标红字段（不提取的字段）"""
    return RED_FIELDS


def is_red_field(key):
    """判断是否为标红字段"""
    return key in RED_FIELDS


def get_excel_header_for_key(key):
    """将内部key转为Excel列名"""
    reverse_map = {
        "项目名称_编号": "项目名称/编号",
        "账期_天": "账期（天）",
        "保证金_万元": "保证金（万元）",
        "油价基准_元每升": "油价基准(元/升）",
    }
    if key in reverse_map:
        return reverse_map[key]
    return key
