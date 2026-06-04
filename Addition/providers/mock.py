"""离线用的 Mock provider：无需任何 API key 即可跑通全流程，供自测/回归。

- MockOcr.parse_pdf 返回一份预置的 OcrDoc（含分类关键词、线路表、系数说明）。
- MockLlm.call_json 按 user prompt 里的标志串路由，返回预置 JSON。

真实运行用 providers/ocr.py、providers/llm.py；本文件只用于 tests。
"""
from providers.ocr import OcrDoc, OcrTable
from providers.llm import LLMProvider


# 一份"浙江式"合同的伪 OCR 结果：以 20 吨为直读、配车型系数表。
ZHEJIANG_MD = """<!-- 第1页 -->
中国邮政集团有限公司浙江省分公司 邮件运输服务合同
合同编号：浙邮合审字2023第39号
甲方：中国邮政集团有限公司浙江省分公司
乙方：北京博华物流有限公司
本合同有效期自2023年1月1日起至2025年12月31日止。
结算周期：货到票到后60天内付款。
增值税率9%。
联系人：张三  电话：13800000000  地址：杭州市西湖区某路1号
本合同为二干线路运输，中标位次第一（主选）。

<!-- 第2页 -->
附件1 主要中标价格明细表（计重）
车型转换系数表：基准车型20吨/12.5米，8吨0.63、12吨0.74、25吨1.14、30吨1.27。
"""

ROUTES_TABLE_HTML = """<table>
<tr><td>分包号</td><td>线路名称</td><td>邮路性质</td><td>里程</td><td>20吨/12.5米单价</td><td>备注</td></tr>
<tr><td>1</td><td>杭州-郑州</td><td>双程</td><td>820</td><td>100.00</td><td></td></tr>
<tr><td>2</td><td>宁波-北京</td><td>双程</td><td>1300</td><td>200.00</td><td></td></tr>
</table>"""


class MockOcr:
    def parse_pdf(self, pdf_path: str, on_progress=None) -> OcrDoc:
        doc = OcrDoc(
            full_markdown=ZHEJIANG_MD,
            pages=[p for p in ZHEJIANG_MD.split("<!-- 第") if p.strip()],
            tables=[OcrTable(page=2, html=ROUTES_TABLE_HTML)],
            raw={"mock": True},
        )
        return doc


class MockLlm(LLMProvider):
    """按 prompt 标志串返回预置抽取结果。"""

    def __init__(self):
        self.call_count = 0

    def call_json(self, system: str, user: str, temperature: float = None, label: str = "") -> dict:
        self.call_count += 1
        # 用各 prompt 的唯一指令串路由（这些串只出现在指令里、不会出现在合同正文/全文中）
        if "标准化字段值" in user:                       # 台账 Stage2 标准化映射（两阶段）
            return self._ledger()
        if "提取事实" in user:                           # 台账 Stage1 事实抽取（两阶段）
            return self._facts()
        if "抽取以下字段" in user:                       # 台账单阶段回退路径
            return self._ledger()
        if "吨位列名必须从下面这组里选" in user:         # 线路块
            return self._routes()
        if "请在这份邮政合同中查找" in user:             # 系数块
            return self._coefficients()
        return {}

    def _facts(self) -> dict:
        """Stage1 事实抽取的伪结果（浙江式省级合同）。Stage2 mock 不依赖其内容，仅需非空。"""
        return {
            "合同名称": {"value": "邮件运输服务合同", "evidence": "邮件运输服务合同"},
            "甲方_全称": {"value": "中国邮政集团有限公司浙江省分公司",
                          "evidence": "甲方：中国邮政集团有限公司浙江省分公司"},
            "甲方_省份": {"value": "浙江省", "evidence": "浙江省分公司"},
            "乙方_全称": {"value": "北京博华物流有限公司",
                          "evidence": "乙方：北京博华物流有限公司"},
            "合同期限": {"value": "自2023年1月1日起至2025年12月31日止", "evidence": "本合同有效期自2023年1月1日起至2025年12月31日止"},
            "付款账期": {"value": "货到票到后60天内付款", "evidence": "结算周期：货到票到后60天内付款"},
            "纳税人_税务信息": {"value": "增值税率9%", "evidence": "增值税率9%"},
            "合同性质": {"value": "二干线路运输，中标位次第一（主选）", "evidence": "中标位次第一（主选）"},
            "履约保证金": {"value": "未提及", "evidence": "全文"},
        }

    @staticmethod
    def _f(value, conf=0.95, ev="见合同", page=1):
        return {"value": value, "confidence": conf, "evidence": ev, "page": page}

    def _ledger(self) -> dict:
        return {"fields": {
            "登记日期": self._f("2023-01-01"),
            "业务类型": self._f("二干"),
            "合同类型": self._f("主选"),
            "省": self._f("浙江"),
            "市": self._f("/"),
            "客户名称": self._f("中国邮政集团有限公司浙江省分公司"),
            "合同主体": self._f("北京博华物流有限公司"),   # 抽自乙方（不再是固定默认值）
            "合同名称": self._f("邮件运输服务合同"),
            "合同开始时间": self._f("2023-01-01"),
            "合同结束时间": self._f("2025-12-31"),
            "是否完成签订": self._f("是"),
            "账期": self._f("60"),
            "保证金": self._f(None, conf=0.3, ev="未检索到保证金条款"),
            "税率": self._f("0.09"),
            "是否有旺季补偿": self._f("否", conf=0.6),
            "是否有油价联动": self._f("否", conf=0.6),
            "是否有疫情补贴": self._f("否", conf=0.6),
            "联系人": self._f("张三"),
            "电话": self._f("13800000000"),
            "地址": self._f("杭州市西湖区某路1号"),
        }}

    def _routes(self) -> dict:
        return {"found": True, "routes": [
            {"线路名称": "杭州-郑州", "邮路性质": "双程", "里程": 820, "分包号": "1",
             "合同编号": "浙邮合审字2023第39号", "类型": "主选/计重",
             "吨位价格": {"20吨/12.5": 100.00}, "evidence": "杭州-郑州 100.00", "page": 2},
            {"线路名称": "宁波-北京", "邮路性质": "双程", "里程": 1300, "分包号": "2",
             "合同编号": "浙邮合审字2023第39号", "类型": "主选/计重",
             "吨位价格": {"20吨/12.5": 200.00}, "evidence": "宁波-北京 200.00", "page": 2},
        ]}

    def _coefficients(self) -> dict:
        return {"found": True, "基准车型": "20吨/12.5米",
                "系数": {"8吨": 0.63, "12吨": 0.74, "25吨": 1.14, "30吨": 1.27},
                "evidence": "车型转换系数表", "page": 2}
