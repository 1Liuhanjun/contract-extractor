"""
LLM API 客户端
支持 Anthropic Claude、DeepSeek、OpenAI 兼容接口
"""

import json
import os
import re
import sys


class LLMClient:
    """统一的LLM调用接口"""

    def __init__(self, provider="anthropic", api_key=None, model=None):
        self.provider = provider

        if provider == "anthropic":
            self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            self.model = model or "claude-sonnet-4-20250514"
            self.api_url = "https://api.anthropic.com/v1/messages"
        elif provider == "deepseek":
            self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
            self.model = model or "deepseek-chat"
            self.api_url = "https://api.deepseek.com/v1/chat/completions"
        elif provider == "openai":
            self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
            self.model = model or "gpt-4o"
            base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
            self.api_url = f"{base_url}/chat/completions"
        else:
            raise ValueError(f"不支持的provider: {provider}")

    def _ensure_httpx(self):
        try:
            import httpx
            return httpx
        except ImportError:
            print("需要安装httpx: pip install httpx")
            sys.exit(1)

    def call(self, system_prompt, user_prompt, temperature=0.1, max_tokens=4096):
        """调用LLM"""
        if not self.api_key:
            print(f"\n[配置] 请设置 {self.provider.upper()}_API_KEY 环境变量")
            print(f"[配置] 或者直接在 contract_extractor_v2/config.py 中配置")
            return None

        httpx = self._ensure_httpx()
        headers = {
            "Content-Type": "application/json",
        }

        if self.provider == "anthropic":
            headers["x-api-key"] = self.api_key
            headers["anthropic-version"] = "2023-06-01"
            data = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}]
            }
        else:
            headers["Authorization"] = f"Bearer {self.api_key}"
            data = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "response_format": {"type": "json_object"}
            }

        try:
            with httpx.Client(timeout=300.0) as client:
                response = client.post(self.api_url, headers=headers, json=data)
                response.raise_for_status()
                result = response.json()

                if self.provider == "anthropic":
                    content = result["content"][0]["text"]
                else:
                    content = result["choices"][0]["message"]["content"]

                return self._parse_json(content)
        except Exception as e:
            print(f"  [错误] API调用失败: {e}")
            return None

    def _parse_json(self, content):
        """从LLM回复中解析JSON"""
        content = content.strip()

        # 尝试提取 ```json ... ``` 代码块
        json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
        if json_match:
            content = json_match.group(1).strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # 尝试找第一个 { 到最后一个 }
            start = content.find('{')
            end = content.rfind('}')
            if start != -1 and end != -1:
                try:
                    return json.loads(content[start:end + 1])
                except json.JSONDecodeError:
                    pass
            print(f"  [警告] 无法解析JSON响应")
            print(f"  原始响应前300字符: {content[:300]}")
            return None
