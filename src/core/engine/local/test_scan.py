import sqlite3
import sys
import os

# 将 src 目录加入路径，方便导入
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from core.engine.local.mlx_engine import MLXLocalEngine

DB_PATH = '/Users/bytedance/code/LuminaReader/data/knowledge_base.db'
MODEL_PATH = "/Users/bytedance/code/models/Qwen3-4B"

def test_first_chapter_scan():
    # 1. 从数据库读取第一章
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    row = cursor.execute("SELECT title, content FROM chapters WHERE chapter_index = 1").fetchone()
    conn.close()

    if not row:
        print("❌ 数据库中没有找到第一章内容，请先运行 index_parser.py")
        return

    title, content = row
    print(f"📖 载入章节: {title}")
    # print(f"内容预览: {content[:100]}...")

    # 2. 初始化本地模型
    try:
        engine = MLXLocalEngine(MODEL_PATH)
    except Exception as e:
        print(f"❌ 模型加载失败: {e}")
        return

    # 3. 构造指令 (强制关闭 CoT 推理)
    prompt = f"""你是一个高效的文学解构 JSON 转换器。
请直接将以下章节内容转换为指定格式的 JSON，严禁输出任何 <think> 标签或推理过程。

### 章节内容：
标题：{title}
内容：{content[:3000]}

### 待填充 JSON 模板 (直接开始输出 JSON 内容):
{{
  "summary": "50字内概括",
  "entities": [
    {{"name": "名称", "type": "类型", "description": "核心作用"}}
  ]
}}
"""

    print("\n🔍 正在使用本地 Qwen 进行“无思考”精准解构...")
    print("-" * 30)
    
    # 使用新实现的 assistant_prefix 机制
    stream = engine.stream_local_llm(prompt, max_tokens=800, assistant_prefix="{")
    
    full_response = "{" 
    print(full_response, end="", flush=True) 
    
    print("\n✨ 扫描结果 (完全原始流，不带任何过滤)：")
    for response in stream:
        token = response if isinstance(response, str) else getattr(response, 'text', str(response))
        print(token, end="", flush=True)
        full_response += token
    
    print("\n" + "-" * 30)
    print("✅ 扫描结束。")

if __name__ == "__main__":
    test_first_chapter_scan()
