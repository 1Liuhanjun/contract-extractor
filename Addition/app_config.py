"""配置加载：读取 config.yaml、field_map.yaml、.env，解析相对路径。"""
import os
import yaml
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent


def _load_dotenv(path: Path):
    """极简 .env 加载（不依赖 python-dotenv），仅设置尚未存在的环境变量。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def _resolve(p: str) -> Path:
    """相对路径相对本代码目录解析。"""
    pp = Path(p)
    return pp if pp.is_absolute() else (CODE_DIR / pp).resolve()


class Config:
    def __init__(self, config_path: str = None):
        _load_dotenv(CODE_DIR / ".env")
        cfg_path = _resolve(config_path) if config_path else (CODE_DIR / "config.yaml")
        self.raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

        self.ocr = self.raw["ocr"]
        self.llm = self.raw["llm"]
        self.classify = self.raw.get("classify", {})
        self.defaults = self.raw.get("defaults", {})
        self.enhance = self.raw.get("enhance", {})
        self.supported_companies = self.raw.get("supported_companies", ["邮政"])

        paths = self.raw["paths"]
        self.template_xlsx = _resolve(paths["template_xlsx"])
        self.field_map_path = _resolve(paths["field_map"])
        self.skill_path = _resolve(paths["skill_youzheng"])
        self.output_dir = _resolve(paths["output_dir"])
        self.input_dir = _resolve(paths.get("input_dir", "./input"))

        self.field_map = yaml.safe_load(self.field_map_path.read_text(encoding="utf-8"))
        self.skill_text = self.skill_path.read_text(encoding="utf-8") if self.skill_path.exists() else ""

    # —— 密钥（只从环境变量取，绝不写日志）——
    @property
    def ocr_token(self) -> str:
        return os.environ.get(self.ocr["token_env"], "")

    @property
    def llm_key(self) -> str:
        return os.environ.get(self.llm["token_env"], "")
