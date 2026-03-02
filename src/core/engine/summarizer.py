import json
import os
import sqlite3
import logging
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Lumina.Summarizer")

class ChapterSummarizer:
    def __init__(self, db_conn, use_local=True, local_model_path=None):
        self.conn = db_conn
        self.use_local = use_local
        
        if use_local:
            from core.engine.local.mlx_engine import MLXLocalEngine
            path = local_model_path or "/Users/bytedance/code/models/Qwen3-4B"
            self.local_engine = MLXLocalEngine(path)
            logger.info(f"🚀 Summarizer initialized with LOCAL MLX model: {path}")
        else:
            api_key = os.getenv("OPENAI_API_KEY")
            base_url = os.getenv("OPENAI_BASE_URL")
            self.client = OpenAI(api_key=api_key, base_url=base_url)
            self.model = os.getenv("OPENAI_MODEL")
            logger.info(f"🌐 Summarizer initialized with API model: {self.model}")

    def get_cognitive_context(self, current_index, raw_content):
        """
        核心认知步：在阅读前，检索已有的知识体系。
        """
        cursor = self.conn.cursor()
        
        # 1. 线性记忆：最近 3 章的深度概括（保持叙事流）
        recent = cursor.execute('''
            SELECT title, summary FROM chapters 
            WHERE chapter_index < ? AND status = 'completed'
            ORDER BY chapter_index DESC LIMIT 3
        ''', (current_index,)).fetchall()
        recent_desc = "\n".join([f"【{r[0]}】: {r[1]}" for r in reversed(recent)]) or "序幕开启。"

        # 2. 体系记忆：全局世界观设定（底稿架构）
        settings = cursor.execute('''
            SELECT category, content FROM knowledge_points 
            WHERE category IN ('world_setting', 'power_system')
            ORDER BY id ASC
        ''').fetchall()
        global_knowledge = "\n".join([f"· [{s[0]}] {s[1]}" for s in settings])

        # 3. 关联记忆：扫描本章关键词，召回相关实体的“陈年旧案”
        # 哪怕是 2000 章没出场的老角色，只要被提及，立刻召回其特征描述
        # C. 唤醒关联实体 (解决痛点 2: 关联检索)
        # 扫描正文提及的角色，并还原其在这一刻之前的“历史状态”
        all_known_entities = cursor.execute('SELECT name, type, description FROM entities').fetchall()
        recalled_entities = []
        for name, etype, base_desc in all_known_entities:
            # 这里的匹配可以后续优化为别名匹配
            if name in raw_content:
                # 关键：从日志中重构该时间点的状态
                evos = cursor.execute('''
                    SELECT content FROM entity_evolutions 
                    WHERE entity_name = ? AND chapter_index < ? 
                    ORDER BY chapter_index ASC
                ''', (name, current_index)).fetchall()
                
                evolution_history = " -> ".join([e[0] for e in evos]) if evos else "无演进记录"
                recalled_entities.append(f"· {name}({etype}): {base_desc} [历程: {evolution_history}]")
        
        entity_context = "\n".join(recalled_entities) if recalled_entities else "本章未发现已知关联实体。"

        return {
            "linear": recent_desc,
            "systemic": global_knowledge,
            "associative": entity_context
        }

    def generate_agentic_prompt(self, title, content, context):
        """
        构造“阅读者”意图 Prompt
        """
        return f"""你是一位拥有‘绝对记忆’的高阶文学评论员，正在深度解构《超品相师》。
请结合你已掌握的知识体系，阅读并消化当前章节。

---
### 🧠 你已获取的知识库（携带的认知）：
【线性剧情线】:
{context['linear']}

【核心体系设定】:
{context['systemic']}

【关联历史档案】:
{context['associative']}

### 📖 待消化章节：{title}
原文：
{content}

---
### ⚙️ 请输出你的解构成果 (严格 JSON 格式):
1. summary: 对本章的精炼解构（150字内）。
2. cognitive_updates:
   - new_entities: 发现的新人物/地点/关键组织（即便本章是首章，主角秦宇也需作为首个实体记录）。格式: [{"name": "...", "type": "...", "description": "身份初识/背景"}]
   - entity_evolutions: 老角色在这一章的具体状态变化/关系位移。请精准捕捉“从什么变成了什么”。格式: [{"name": "...", "evolution": "..."}]
   - world_insights: 揭露的新世界规则、功法细节或因果律设定（字符串）。
3. key_events: 推动主线的 2-3 个核心事件（字符串列表）。
4. pending_questions: 你认为本章留下的重要伏笔或疑点（字符串列表）。
"""

    def analyze_chapter(self, index, title, content):
        """
        执行解构并完成知识沉淀。
        """
        # A. 阅读前：唤醒记忆
        context = self.get_cognitive_context(index, content)
        
        if self.use_local:
            # 本地模式：使用前缀注入强制 JSON
            local_prompt = self.generate_agentic_prompt(title, content, context)
            # 在 prompt 结尾处进行微导，防止 MLX 模型输出废话
            # 这里的 prompt 结构已经在 generate_agentic_prompt 中定义好，
            # 我们可以简单包装一下
            wrapped_prompt = f"{local_prompt}\n\nStrict JSON output starting with {{:"
            
            try:
                ai_raw = self.local_engine.call_local_llm(wrapped_prompt, max_tokens=1000, assistant_prefix="{")
                # 确保以 { 开头
                if not ai_raw.strip().startswith("{"):
                    ai_raw = "{" + ai_raw
            except Exception as e:
                logger.error(f"Local MLX Analysis failed: {e}")
                return {"error": str(e)}
        else:
            # API 模式
            prompt = self.generate_agentic_prompt(title, content, context)
            logger.info(f"Sending API request for Chapter {index}: {title}")
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "你是一个只输出 JSON 的高阶文学解构助手。你的回答必须极其严谨。"},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=800,
                    response_format={ "type": "json_object" }
                )
                ai_raw = response.choices[0].message.content
            except Exception as e:
                logger.error(f"AI API Analysis failed: {str(e)}")
                return {"error": str(e)}
        
        # C. 阅读后：知识沉淀
        try:
            self.digest_to_database(index, ai_raw)
            logger.info(f"Chapter {index} digested and sedimented successfully.")
            return {"status": "success", "data": json.loads(ai_raw)}
        except json.JSONDecodeError as je:
            logger.error(f"JSON Decode Error for Chapter {index}: {str(je)}")
            logger.error(f"Raw AI output was: {ai_raw}")
            return {"error": f"JSON parsing failed: {str(je)}"}

    def digest_to_database(self, chapter_index, ai_json_str):
        """
        【认知沉淀逻辑】: 将 AI 的理解转化为数据库的持久状态。
        """
        data = json.loads(ai_json_str)
        cursor = self.conn.cursor()
        
        # 1. 沉淀章节概要
        cursor.execute('''
            UPDATE chapters SET summary = ?, key_events = ?, status = 'completed' 
            WHERE chapter_index = ?
        ''', (data.get('summary'), json.dumps(data.get('key_events', []), ensure_ascii=False), chapter_index))

        # 2. 沉淀实体演进 (这是完善知识体系的关键)
        updates = data.get('cognitive_updates', {})
        
        # 处理新实体
        new_entities = updates.get('new_entities', [])
        if new_entities:
            logger.info(f"Found {len(new_entities)} new entities in Chapter {chapter_index}")
        for ent in new_entities:
            # 防御性编程：确保 ent 是字典且包含必要键
            if not isinstance(ent, dict):
                logger.warning(f"AI returned invalid entity format: {ent}")
                continue
            
            name = ent.get('name')
            if not name: continue
                
            cursor.execute('''
                INSERT INTO entities (name, type, description, first_chap_id, last_updated_chap_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET 
                    last_updated_chap_id = excluded.last_updated_chap_id,
                    first_chap_id = CASE 
                        WHEN first_chap_id = 0 OR first_chap_id > excluded.first_chap_id THEN excluded.first_chap_id 
                        ELSE first_chap_id 
                    END
            ''', (name, ent.get('type', 'unknown'), ent.get('description', ''), chapter_index, chapter_index))

        # 处理老实体演进 (入库到 entity_evolutions 表，实现时间线溯源)
        evolutions = updates.get('entity_evolutions', [])
        if evolutions and isinstance(evolutions, list):
            logger.info(f"Tracking {len(evolutions)} entity evolutions in Chapter {chapter_index}")
            for evo in evolutions:
                if not isinstance(evo, dict): continue
                name = evo.get('name')
                change = evo.get('evolution')
                if not name or not change: continue
                
                # 记录到演进日志表，不再直接修改实体表的主描述
                cursor.execute('''
                    INSERT INTO entity_evolutions (entity_name, chapter_index, evolution_type, content)
                    VALUES (?, ?, ?, ?)
                ''', (name, chapter_index, 'status_change', change))
                
                # 更新实体表的“最后活跃章节”
                cursor.execute('UPDATE entities SET last_updated_chap_id = ? WHERE name = ?', 
                             (chapter_index, name))
        elif evolutions:
             logger.warning(f"Invalid evolutions format: {evolutions}")

        # 3. 沉淀系统认知 (认知沉淀)
        insight = updates.get('world_insights')
        if insight and len(str(insight)) > 10:
            logger.info(f"New world insight sedimented from Chapter {chapter_index}")
            cursor.execute('INSERT INTO knowledge_points (category, content, source_chap_id) VALUES (?, ?, ?)',
                         ('world_setting', str(insight), chapter_index))

        self.conn.commit()
