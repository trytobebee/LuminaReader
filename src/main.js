const API_BASE = 'http://localhost:5001/api';

class LuminaApp {
    constructor() {
        this.chapters = [];
        this.currentChapterIndex = null;
        this.init();
    }

    async init() {
        await this.loadChapters();
        this.renderChapterList();
        this.setupEventListeners();

        // 如果有章节，默认选中第一章
        if (this.chapters.length > 0) {
            this.selectChapter(this.chapters[0].chapter_index);
        }
    }

    // 1. 加载章节列表
    async loadChapters() {
        try {
            const res = await fetch(`${API_BASE}/chapters`);
            this.chapters = await res.json();
            document.getElementById('total-chapters').textContent = this.chapters.length;
        } catch (e) {
            console.error("Failed to load chapters", e);
        }
    }

    // 2. 渲染左侧列表 (增加分卷显示逻辑)
    renderChapterList() {
        const list = document.getElementById('chapter-list');
        list.innerHTML = '';

        let lastVolume = null;

        this.chapters.forEach(chap => {
            // 如果分卷变化，插入分卷头
            if (chap.volume !== lastVolume) {
                const volLi = document.createElement('div');
                volLi.className = 'volume-header';
                volLi.textContent = chap.volume || '默认卷';
                list.appendChild(volLi);
                lastVolume = chap.volume;
            }

            const li = document.createElement('li');
            li.className = `chapter-item ${this.currentChapterIndex === chap.chapter_index ? 'active' : ''}`;
            li.innerHTML = `
                <span class="status-dot status-${chap.status}"></span>
                <span class="chap-title">第${chap.chapter_index}章 ${chap.title}</span>
            `;
            li.onclick = () => this.selectChapter(chap.chapter_index);
            list.appendChild(li);
        });
    }

    // 3. 选择章节并加载详情
    async selectChapter(index) {
        this.currentChapterIndex = index;
        this.renderChapterList();

        const chap = this.chapters.find(c => c.chapter_index === index);
        document.getElementById('reader-title').textContent = `第${chap.chapter_index}章 ${chap.title}`;

        // 加载原文
        try {
            const res = await fetch(`${API_BASE}/chapters/${index}/content`);
            const data = await res.json();
            const contentStr = data.content.trim();

            // 2. 深度断句并转为结构化 HTML
            const readerBody = document.getElementById('reader-body');
            readerBody.innerHTML = ''; // 清空原有内容

            // 使用正则拆分：捕捉标点符号和结尾引号
            const parts = contentStr.split(/(([。！？]|…{2,3}|\.{3,})[”’"'″]?)\s*/);

            for (let i = 0; i < parts.length; i += 3) {
                const text = parts[i];
                const punct = parts[i + 1] || '';
                const sentence = (text + punct).trim();

                if (sentence) {
                    const sDiv = document.createElement('div');
                    sDiv.className = 'sentence';
                    sDiv.textContent = sentence;
                    readerBody.appendChild(sDiv);
                }
            }

            // 3. 更新分析面板状态
            const analysisStatus = document.getElementById('analysis-status');
            if (chap.status === 'completed') {
                analysisStatus.innerHTML = '<span class="badge badge-sedimented">已沉淀 (Sedimented)</span>';
            } else {
                analysisStatus.innerHTML = '<span class="badge badge-pending">待解构 (Pending)</span>';
            }

            // 加载详情 (包括摘要和关键事件)
            document.getElementById('chapter-summary').value = chap.summary || (chap.status === 'pending' ? '【该章节尚未解构，请运行后台引擎进行分析】' : '');
            this.renderPlotPoints(chap.key_events);

            // 加载本章相关的实体
            await this.loadChapterEntities(index);
        } catch (e) {
            console.error("Error loading chapter detail", e);
        }
    }

    renderPlotPoints(eventsJson) {
        const editArea = document.getElementById('plot-points-edit');
        editArea.value = '';
        try {
            const events = typeof eventsJson === 'string' ? JSON.parse(eventsJson) : eventsJson;
            if (Array.isArray(events)) {
                editArea.value = events.join('\n');
            }
        } catch (e) {
            console.warn("Failed to parse plot points", e);
        }
    }

    async loadChapterEntities(index) {
        try {
            const res = await fetch(`${API_BASE}/entities?chapter_index=${index}`);
            const entities = await res.json();
            const list = document.getElementById('entity-list');
            list.innerHTML = '';

            entities.forEach(ent => {
                const isNew = ent.first_chap_id === index;
                const div = document.createElement('div');
                div.className = 'entity-row';

                div.innerHTML = `
                    <div class="entity-info" title="${ent.description || ''}">
                        <span class="entity-name">${ent.name}</span>
                        <span class="type-tag">${ent.type}</span>
                    </div>
                    <div class="entity-badges">
                        <span class="discovery-badge ${isNew ? 'new' : 'known'}">
                            ${isNew ? 'NEW' : 'KNOWN'}
                        </span>
                        <button class="btn-delete-ent" data-name="${ent.name}">×</button>
                    </div>
                `;
                list.appendChild(div);
            });

            // 绑定删除事件
            list.querySelectorAll('.btn-delete-ent').forEach(btn => {
                btn.onclick = (e) => {
                    e.stopPropagation();
                    this.deleteEntity(btn.dataset.name);
                };
            });
        } catch (e) {
            console.error("Failed to load chapter entities", e);
        }
    }

    async deleteEntity(name) {
        if (!confirm(`Are you sure you want to remove '${name}' from Knowledge Base?`)) return;

        try {
            const res = await fetch(`${API_BASE}/entities/${encodeURIComponent(name)}`, {
                method: 'DELETE'
            });
            if (res.ok) {
                if (this.currentChapterIndex) {
                    await this.loadChapterEntities(this.currentChapterIndex);
                }
            }
        } catch (e) {
            console.error("Failed to delete entity", e);
        }
    }

    async submitNewEntity() {
        const nameInput = document.getElementById('new-ent-name');
        const descInput = document.getElementById('new-ent-desc');
        const typeSelect = document.getElementById('new-ent-type');

        const name = nameInput.value.trim();
        const type = typeSelect.value;
        const desc = descInput.value.trim();

        if (!name) {
            alert("Entity name is required!");
            return;
        }

        try {
            const res = await fetch(`${API_BASE}/entities`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, type, description: desc })
            });

            if (res.ok) {
                document.getElementById('entity-modal').classList.add('hidden');
                nameInput.value = '';
                descInput.value = '';
                if (this.currentChapterIndex) {
                    await this.loadChapterEntities(this.currentChapterIndex);
                    // 如果新添加的是本章实体，需要重新加载章节列表以显示新发现标记（可选）
                }
            }
        } catch (e) {
            console.error("Failed to add entity", e);
        }
    }

    async saveAnalysis() {
        if (!this.currentChapterIndex) return;

        const btn = document.getElementById('save-analysis');
        const originalText = btn.textContent;
        btn.textContent = "🚀 Syncing Knowledge...";
        btn.disabled = true;

        const summary = document.getElementById('chapter-summary').value;
        // 获取编辑过的情节点，过滤空行并转回 JSON 数组
        const plotPointsRaw = document.getElementById('plot-points-edit').value;
        const plotPointsArr = plotPointsRaw.split('\n').map(l => l.trim()).filter(l => l !== '');

        try {
            const res = await fetch(`${API_BASE}/chapters/${this.currentChapterIndex}/summary`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    summary,
                    key_events: JSON.stringify(plotPointsArr)
                })
            });

            if (res.ok) {
                btn.textContent = "✅ Knowledge Sedimented!";
                btn.style.background = "#10b981";

                setTimeout(async () => {
                    btn.textContent = originalText;
                    btn.style.background = "";
                    btn.disabled = false;
                    await this.loadChapters();
                    this.renderChapterList();
                }, 2000);
            }
        } catch (e) {
            btn.textContent = "❌ Sync Failed";
            btn.disabled = false;
        }
    }

    async sendChat() {
        const input = document.getElementById('chat-msg-input');
        const text = input.value.trim();
        if (!text) return;

        this.appendMessage('user', text);
        input.value = '';

        const res = await fetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text })
        });
        const data = await res.json();
        this.appendMessage('bot', data.response);
    }

    appendMessage(role, text) {
        const chatBox = document.getElementById('chat-messages');
        const div = document.createElement('div');
        div.className = `msg ${role}`;
        div.textContent = text;
        chatBox.appendChild(div);
        chatBox.scrollTop = chatBox.scrollHeight;
    }

    setupEventListeners() {
        document.getElementById('save-analysis').onclick = () => this.saveAnalysis();
        document.getElementById('toggle-chat').onclick = () => {
            document.getElementById('chat-widget').classList.toggle('hidden');
        };
        document.getElementById('close-chat').onclick = () => {
            document.getElementById('chat-widget').classList.add('hidden');
        };
        document.getElementById('send-chat').onclick = () => this.sendChat();
        document.getElementById('chat-msg-input').onkeypress = (e) => {
            if (e.key === 'Enter') this.sendChat();
        };

        // Modal Logic
        document.getElementById('add-entity').onclick = () => {
            document.getElementById('entity-modal').classList.remove('hidden');
        };
        document.getElementById('close-entity-modal').onclick = () => {
            document.getElementById('entity-modal').classList.add('hidden');
        };
        document.getElementById('submit-entity').onclick = () => this.submitNewEntity();
    }
}

new LuminaApp();
