import os
import requests
import json
import time
from dotenv import load_dotenv

load_dotenv()

class OfflineCloudService:
    """
    【模式 2-B：云端离线服务】
    与火山引擎 (Ark) 的 Batch API 交互，负责文件上传和任务调度。
    """
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
        self.endpoint_id = os.getenv("OPENAI_MODEL")
        self.headers = {"Authorization": f"Bearer {self.api_key}"}

    def upload_and_run(self, file_path):
        """核心流程：上传并立刻启动任务"""
        print(f"☁️ 正在上传 {file_path}...")
        
        # 1. 上传文件
        with open(file_path, 'rb') as f:
            res = requests.post(f"{self.base_url}/files", headers=self.headers, files={
                'file': (os.path.basename(file_path), f),
                'purpose': (None, 'batch')
            })
        
        if res.status_code != 200:
            print(f"❌ 上传失败: {res.text}")
            return None
            
        file_id = res.json().get("id")
        print(f"✅ 文件已就绪 ID: {file_id}")

        # 2. 创建批量推理任务
        print(f"🚀 正在火山引擎创建 5 折优惠推理任务...")
        batch_res = requests.post(f"{self.base_url}/batches", headers=self.headers, json={
            "input_file_id": file_id,
            "endpoint_id": self.endpoint_id,
            "completion_window": "24h"
        })
        
        if batch_res.status_code == 200:
            batch_id = batch_res.json().get("id")
            print(f"✨ 任务启动成功! Batch ID: {batch_id}")
            print("请登录火山引擎控制台查看进度，处理完成后运行 result_ingest 导入数据。")
            return batch_id
        else:
            print(f"❌ 创建失败: {batch_res.text}")
            return None

if __name__ == "__main__":
    service = OfflineCloudService()
    task_file = "/Users/bytedance/code/LuminaReader/data/doubao_offline_tasks.jsonl"
    if os.path.exists(task_file):
        service.upload_and_run(task_file)
    else:
        print(f"❌ 找不到打包文件: {task_file}, 请先运行 offline_task_packer.py")
