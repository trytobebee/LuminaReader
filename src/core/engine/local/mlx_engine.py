import os
import json
import logging
from mlx_lm import load, generate, stream_generate

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Lumina.MLX")

class MLXLocalEngine:
    def __init__(self, model_path_or_id="mlx-community/Llama-3.2-3B-Instruct-4bit"):
        """
        初始化 MLX 本地引擎
        默认推荐一个小而强的模型作为测试
        """
        logger.info(f"💾 正在加载本地 MLX 模型: {model_path_or_id}...")
        try:
            self.model, self.tokenizer = load(model_path_or_id)
            logger.info("✅ 模型加载成功。")
        except Exception as e:
            logger.error(f"❌ 模型加载失败: {e}")
            raise e

    def stream_local_llm(self, prompt, max_tokens=1000, assistant_prefix="{"):
        """
        流式调用：支持前缀注入，强迫模型跳过思考
        """
        messages = [{"role": "user", "content": prompt}]
        formatted_prompt = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        
        # 强制拼接前缀，让模型以为它已经开始输出结果了
        if assistant_prefix:
            formatted_prompt += assistant_prefix

        return stream_generate(
            self.model, 
            self.tokenizer, 
            prompt=formatted_prompt, 
            max_tokens=max_tokens
        )

    def call_local_llm(self, prompt, max_tokens=1000, assistant_prefix="{"):
        """
        同步调用：支持前缀注入
        """
        messages = [{"role": "user", "content": prompt}]
        formatted_prompt = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        
        if assistant_prefix:
            formatted_prompt += assistant_prefix

        response = generate(
            self.model, 
            self.tokenizer, 
            prompt=formatted_prompt, 
            max_tokens=max_tokens
        )
        
        # 如果有注入前缀，结果也要补全
        if assistant_prefix and not response.startswith(assistant_prefix):
            response = assistant_prefix + response

        return response

# 测试脚本
if __name__ == "__main__":
    # 使用示例 (请确保您已经下载了模型，或者网络环境支持自动下载)
    # python3 -m mlx_lm.fuse --model <path> 也可以手动处理
    try:
        engine = MLXLocalEngine("/Users/bytedance/code/models/Qwen3-4B")
        test_prompt = "请简要分析一下：秦宇是一个拥有玄学传承的青年，他在第一章获得了一本诸葛内经。"
        print("--- Testing Local MLX ---")
        result = engine.call_local_llm(test_prompt)
        print(f"Output: \n{result}")
    except ImportError:
        logger.error("🚫 请先安装 mlx-lm: pip install mlx-lm")
    except Exception as e:
        logger.error(f"Runtime error: {e}")
