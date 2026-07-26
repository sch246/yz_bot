'''用于处理供应商管理'''

import os
from typing import Dict, List, Optional
import requests
from dotenv import load_dotenv

class Vendor:
    """AI供应商的基类"""
    def __init__(self, name: str, base_url: str, api_key: str):
        self.name = name
        self.base_url = base_url
        self.api_key = api_key
        
    def list_models(self) -> List[Dict]:
        """获取此供应商可用的模型"""
        raise NotImplementedError("子类必须实现 list_models()")
    
    def get_balance(self) -> Dict:
        """获取账户余额信息"""
        raise NotImplementedError("子类必须实现 get_balance()")

class OpenAIVendor(Vendor):
    """OpenAI特定的实现"""
    def list_models(self) -> List[Dict]:
        url = f"{self.base_url}/v1/models"
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            models = response.json().get("data", [])
            return [{"id": model["id"], "vendor": self.name, "created": model.get("created")} 
                   for model in models]
        else:
            return []
    
    def get_balance(self) -> Dict:
        # OpenAI没有直接的余额API
        return {"error": "OpenAI不提供直接的余额API接口"}

class DeepseekVendor(Vendor):
    """Deepseek特定的实现"""
    def list_models(self) -> List[Dict]:
        url = f"{self.base_url}/v1/models"  # 假设API结构相似
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            models = response.json().get("data", [])
            return [{"id": model["id"], "vendor": self.name, "created": model.get("created")} 
                   for model in models]
        else:
            # 如果API不可用，返回硬编码的模型
            return [
                {"id": "deepseek-reasoner", "vendor": self.name},
                {"id": "deepseek-chat", "vendor": self.name},
                {"id": "deepseek-ai/DeepSeek-R1", "vendor": self.name},
                {"id": "deepseek-ai/DeepSeek-V3", "vendor": self.name},
                {"id": "Pro/deepseek-ai/DeepSeek-R1", "vendor": self.name},
                {"id": "Pro/deepseek-ai/DeepSeek-V3", "vendor": self.name},
                # 根据需要添加其他Deepseek模型
            ]
    
    def get_balance(self) -> Dict:
        url = f"{self.base_url}/user/balance"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        else:
            return {"error": f"错误: {response.status_code}, {response.text}"}

class AnthropicVendor(Vendor):
    """Anthropic特定的实现"""
    def list_models(self) -> List[Dict]:
        # 可能调用API或返回硬编码模型
        return [
            {"id": "claude-3-5-sonnet-20240620", "vendor": self.name},
            {"id": "claude-3-5-sonnet-20241022", "vendor": self.name},
            {"id": "claude-3-5-haiku-20241022", "vendor": self.name},
        ]
    
    def get_balance(self) -> Dict:
        return {"error": "Anthropic不提供直接的余额API接口"}

class VendorManager:
    """管理多个AI供应商"""
    def __init__(self):
        load_dotenv()
        self.vendors = {}
        self.init_vendors()
        
    def init_vendors(self):
        """从环境变量初始化可用的供应商"""
        # OpenAI
        openai_api_key = os.getenv("OPENAI_API_KEY")
        openai_base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
        if openai_api_key:
            self.vendors["openai"] = OpenAIVendor("openai", openai_base_url, openai_api_key)
            
        # Deepseek
        deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
        deepseek_base_url = os.getenv("DEEPSEEK_BASE_URL")
        if deepseek_api_key and deepseek_base_url:
            self.vendors["deepseek"] = DeepseekVendor("deepseek", deepseek_base_url, deepseek_api_key)
            
        # Anthropic
        anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        anthropic_base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        if anthropic_api_key:
            self.vendors["anthropic"] = AnthropicVendor("anthropic", anthropic_base_url, anthropic_api_key)
    
    def get_vendor(self, vendor_name: str) -> Optional[Vendor]:
        """根据名称获取供应商"""
        return self.vendors.get(vendor_name.lower())
    
    def list_vendors(self) -> List[str]:
        """列出所有可用的供应商"""
        return list(self.vendors.keys())
    
    def list_all_models(self) -> List[Dict]:
        """列出所有供应商的模型"""
        all_models = []
        for vendor_name, vendor in self.vendors.items():
            try:
                models = vendor.list_models()
                all_models.extend(models)
            except Exception as e:
                print(f"列出{vendor_name}模型时出错: {e}")
        return all_models