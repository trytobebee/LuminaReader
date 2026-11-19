import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { SpendMeter } from './spend-meter.js';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:5001/api';

class LuminaApp {
    constructor() {
        this.chapters = [];
        this.chapterSearchResults = null;
        this.chapterSearchRequest = null;
        this.currentChapterIndex = null;
        this.chatHistory = [];
        this.chatScope = null;
        this.chatPending = false;
        this.chatTurnCount = 0;
        this.currentDocumentId = 'default';
        this.documents = [];
        this.sessions = [];
        this.currentSession = null;
        this.restoring = false;
        this.spendMeter = new SpendMeter((path, options) => this.request(path, options));
        this.welcomeTemplate = document.querySelector('.chat-empty').cloneNode(true);
        this.init();
    }

    async init() {
        this.restoreWorkspace();
        this.setupEventListeners();
        this.loadModel();
        try {
            await this.loadDocuments();
            const selected = this.documents.find(doc => doc.id === this.currentDocumentId) || this.documents[0];
            if (selected) await this.selectDocument(selected.id, true);
            else this.showEmptyLibrary();
        } catch (error) {
            this.notify('暂时无法连接文档库，请确认 Reader 服务已启动');
            document.querySelector('#processing-status .status-label').textContent = '连接失败';
        }
        window.addEventListener('focus', async () => {
            await this.loadChapters();
            this.renderChapterList();
        });
    }

    showEmptyLibrary() {
        for (const id of ['sidebar-book-title', 'header-document', 'welcome-book-title', 'composer-book-title']) {
            const element = document.getElementById(id);
            if (element) element.textContent = '载入你的第一份文档';
        }
        document.getElementById('sidebar-book-status').textContent = '选择文档后开始';
        document.getElementById('welcome-book-meta').textContent = 'TXT / Markdown';
        document.getElementById('suggestions').hidden = true;
        document.querySelector('#processing-status .status-label').textContent = '待载入';
        document.getElementById('chat-msg-input').placeholder = '先载入一份文档，再开始对话';
    }

    request(path, options = {}) {
        const url = new URL(API_BASE + path);
        url.searchParams.set('document_id', this.currentDocumentId);
        return fetch(url, options);
    }

    restoreWorkspace() {
        try {
            const saved = JSON.parse(localStorage.getItem('lumina-workspace-v2') || 'null');
            if (saved) {
                this.sessions = Array.isArray(saved.sessions) ? saved.sessions : [];
                this.currentDocumentId = saved.documentId || 'default';
                this.savedSessionId = saved.sessionId;
            }
        } catch (error) {
            this.sessions = [];
        }
    }

    persistWorkspace() {
        try {
            localStorage.setItem('lumina-workspace-v2', JSON.stringify({
                documentId: this.currentDocumentId,
                sessionId: this.currentSession?.id,
                sessions: this.sessions.slice(0, 16),
            }));
        } catch (error) {
            this.notify('本地存储空间不足，这段讨论暂未保存');
        }
    }

    saveConversation() {
        if (!this.currentSession) return;
        this.currentSession.history = this.chatHistory;
        this.currentSession.scope = this.chatScope;
        this.currentSession.chapter = this.currentChapterIndex;
        this.currentSession.updatedAt = Date.now();
        this.persistWorkspace();
        this.renderConversations();
    }

    renderConversations() {
        const list = document.getElementById('conversation-list');
        list.replaceChildren();
        const sessions = this.sessions.filter(session => session.documentId === this.currentDocumentId)
            .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
        if (!sessions.length) {
            const empty = document.createElement('p');
            empty.className = 'conversations-empty';
            empty.textContent = '从一个问题开始，讨论会保存在这里。';
            list.appendChild(empty);
        }
        for (const session of sessions) {
            const button = document.createElement('button');
            button.textContent = session.title;
            button.title = this.chatPending ? '当前回答生成中，完成后可切换讨论' : session.title;
            button.disabled = this.chatPending;
            button.className = this.currentSession?.id === session.id ? 'selected' : '';
            button.onclick = () => this.openConversation(session);
            list.appendChild(button);
        }
    }

    closeReference() {
        document.getElementById('reference-panel').hidden = true;
        document.getElementById('app').classList.remove('reference-open');
        document.getElementById('discussion-home').classList.add('active');
        document.getElementById('discussion-home').setAttribute('aria-current', 'page');
        document.getElementById('open-reference').classList.remove('active');
        document.getElementById('open-reference').removeAttribute('aria-current');
    }

    resetReference() {
        this.chapterSearchRequest?.abort();
        this.currentChapterIndex = null;
        this.chapterSearchResults = null;
        document.getElementById('chapter-search-query').value = '';
        document.getElementById('chapter-search-status').hidden = true;
        document.getElementById('clear-chapter-search').hidden = true;
        document.getElementById('reader-title').textContent = '选择一个章节';
        document.getElementById('reader-body').replaceChildren();
        document.getElementById('analysis-chapter-title').textContent = '选择一个章节';
        document.getElementById('analysis-status').replaceChildren();
        document.getElementById('chapter-summary').value = '';
        document.getElementById('plot-points-edit').value = '';
        document.getElementById('entity-list').replaceChildren();
        document.getElementById('ask-chapter').disabled = true;
        this.renderChapterList();
        this.closeReference();
    }

    newConversation() {
        if (this.chatPending) return;
        if (!this.documents.some(doc => doc.id === this.currentDocumentId)) {
            document.getElementById('document-dialog').showModal();
            return;
        }
        this.saveConversation();
        this.resetReference();
        this.currentSession = null;
        this.chatHistory = [];
        this.chatScope = null;
        this.chatTurnCount = 0;
        const messages = document.getElementById('chat-messages');
        messages.replaceChildren(this.welcomeTemplate.cloneNode(true));
        this.updateDocumentLabels();
        this.renderConversations();
        this.persistWorkspace();
        document.getElementById('chat-msg-input').value = '';
        document.getElementById('chat-msg-input').focus();
    }

    openConversation(session) {
        if (this.chatPending) return;
        this.saveConversation();
        this.resetReference();
        this.currentSession = session;
        this.chatHistory = session.history || [];
        this.chatScope = session.scope || null;
        this.chatTurnCount = 0;
        document.getElementById('chat-msg-input').value = '';
        document.getElementById('chat-messages').replaceChildren();
        this.restoring = true;
        for (const message of session.messages) this.appendMessage(message.role, message.text, message.result || {});
        this.restoring = false;
        if (session.chapter) this.selectChapter(session.chapter, false, false);
        this.renderConversations();
        this.persistWorkspace();
        document.getElementById('chat-messages').scrollTop = 0;
    }

    async loadDocuments() {
        const response = await this.request('/documents');
        if (!response.ok) throw new Error('Library unavailable');
        this.documents = await response.json();
        this.renderDocuments();
    }

    async selectDocument(id, initial = false) {
        if (this.chatPending) {
            this.notify('请等待当前回答完成后切换文档');
            return;
        }
        this.saveConversation();
        this.currentSession = null;
        this.chatHistory = [];
        this.chatScope = null;
        this.currentDocumentId = id;
        document.getElementById('chat-msg-input').disabled = true;
        document.getElementById('send-chat').disabled = true;
        this.chapters = [];
        this.renderChapterList();
        this.currentChapterIndex = null;
        this.chapterSearchResults = null;
        document.getElementById('chapter-search-query').value = '';
        document.getElementById('chapter-search-status').hidden = true;
        document.getElementById('clear-chapter-search').hidden = true;
        document.getElementById('reference-panel').hidden = true;
        document.getElementById('app').classList.remove('reference-open');
        document.getElementById('reader-title').textContent = '选择一个章节';
        document.getElementById('reader-body').replaceChildren();
        document.getElementById('analysis-chapter-title').textContent = '选择一个章节';
        document.getElementById('analysis-status').replaceChildren();
        document.getElementById('chapter-summary').value = '';
        document.getElementById('plot-points-edit').value = '';
        document.getElementById('entity-list').replaceChildren();
        document.getElementById('ask-chapter').disabled = true;
        await this.loadChapters();
        if (this.currentDocumentId !== id) return;
        this.renderChapterList();
        const saved = initial && this.sessions.find(session => session.id === this.savedSessionId && session.documentId === id);
        if (saved) this.openConversation(saved);
        else this.newConversation();
        this.updateDocumentLabels();
        this.renderDocuments();
        document.getElementById('chat-msg-input').disabled = false;
        document.getElementById('send-chat').disabled = false;
    }

    updateDocumentLabels() {
        const doc = this.documents.find(item => item.id === this.currentDocumentId);
        if (!doc) return;
        for (const id of ['sidebar-book-title', 'header-document', 'welcome-book-title', 'composer-book-title']) {
            const element = document.getElementById(id);
            if (element) element.textContent = doc.title;
        }
        const ready = doc.analyzed_count === doc.chapter_count;
        document.getElementById('sidebar-book-status').textContent = ready ? '章节分析已就绪' : '已建立原文索引';
        const meta = document.getElementById('welcome-book-meta');
        if (meta) meta.textContent = `${doc.chapter_count.toLocaleString()} 章已就绪`;
        const prompts = this.currentDocumentId === 'default' ? null : [
            ['内容概览', '先了解这份文档的主要内容。', '从核心观点与整体脉络开始', '请概括这份文档的主要内容，并标出依据章节。'],
            ['寻找线索', '把分散的信息，联系起来。', '梳理关键概念与它们的关系', '请梳理这份文档的核心观点，以及它们之间的关系，引用对应章节。'],
            ['回到原文', '找到你想核对的那一段。', '通过章节查看原文和上下文', '这份文档有哪些值得进一步讨论的问题？请附上对应的原文出处。'],
            ['一起讨论', '从阅读走向自己的理解。', '比较观点，展开新的思考', '这份文档有哪些值得商榷的观点？请区分原文事实和你的分析。'],
        ];
        if (prompts) {
            document.querySelectorAll('.suggestion').forEach((button, index) => {
                const [label, title, description, prompt] = prompts[index];
                button.querySelector('.suggestion-label').firstChild.textContent = label + ' ';
                button.querySelector('strong').textContent = title;
                button.querySelector('p').textContent = description;
                button.dataset.prompt = prompt;
            });
        }
    }

    renderDocuments() {
        const list = document.getElementById('document-list');
        list.replaceChildren();
        for (const doc of this.documents) {
            const button = document.createElement('button');
            button.className = 'library-document';
            const cover = document.createElement('span');
            cover.className = 'mini-cover';
            cover.textContent = '书';
            const label = document.createElement('span');
            label.className = 'document-label';
            const title = document.createElement('strong');
            title.textContent = doc.title;
            const status = document.createElement('small');
            status.textContent = `${doc.format} · ${doc.chapter_count.toLocaleString()} 章 · 已建立索引`;
            label.append(title, status);
            const current = document.createElement('span');
            current.className = 'library-current';
            current.textContent = doc.id === this.currentDocumentId ? '当前文档' : '打开';
            button.append(cover, label, current);
            button.onclick = async () => {
                await this.selectDocument(doc.id);
                document.getElementById('document-dialog').close();
            };
            list.appendChild(button);
        }
    }

    async importDocument(event) {
        event.preventDefault();
        const file = document.getElementById('document-file').files[0];
        if (!file) return;
        const button = document.getElementById('import-document');
        const status = document.getElementById('document-upload-status');
        button.disabled = true;
        status.textContent = '正在识别章节并建立原文索引…';
        try {
            const form = new FormData();
            form.append('file', file);
            const response = await this.request('/documents', { method: 'POST', body: form });
            const doc = await response.json();
            if (!response.ok) throw new Error(doc.error || '载入失败');
            await this.loadDocuments();
            await this.selectDocument(doc.id);
            status.textContent = `已完成，${doc.chapter_count} 章可以开始提问。`;
            document.getElementById('document-dialog').close();
            this.notify(`《${doc.title}》已建立索引，可以开始对话`);
        } catch (error) {
            status.textContent = error.message;
        } finally {
            button.disabled = false;
        }
    }

    openReference(tab = 'chapters') {
        document.getElementById('discussion-home').classList.remove('active');
        document.getElementById('discussion-home').removeAttribute('aria-current');
        document.getElementById('open-reference').classList.add('active');
        document.getElementById('open-reference').setAttribute('aria-current', 'page');
        document.getElementById('reference-panel').hidden = false;
        document.getElementById('app').classList.add('reference-open');
        document.querySelectorAll('.reference-tabs button').forEach(button => {
            const selected = button.dataset.tab === tab;
            button.setAttribute('aria-selected', String(selected));
            document.getElementById('reference-' + button.dataset.tab).hidden = !selected;
        });
    }

    notify(message) {
        const toast = document.getElementById('toast');
        toast.textContent = message;
        toast.hidden = false;
        clearTimeout(this.toastTimer);
        this.toastTimer = setTimeout(() => { toast.hidden = true; }, 4500);
    }

    async loadModel() {
        try {
            const response = await this.request(`/model`);
            if (response.ok) {
                const data = await response.json();
                document.getElementById('chat-model').textContent = (data.reader_version === 'v2' ? 'V2 · ' : '') + data.model;
                if (data.reader_version === 'v2') document.title = 'Lumina V2 · 小石寨104章试点';
            }
        } catch (error) {
            console.warn('Model status unavailable');
        }
    }

    // 1. 加载章节列表
    async loadChapters() {
        const documentId = this.currentDocumentId;
        try {
            const res = await this.request(`/chapters`);
            const chapters = await res.json();
            if (this.currentDocumentId !== documentId) return;
            this.chapters = chapters;
            document.getElementById('total-chapters').textContent = this.chapters.length;
            const completed = this.chapters.filter(chapter => chapter.status === 'completed').length;
            const doc = this.documents.find(item => item.id === documentId);
            if (doc) {
                doc.chapter_count = this.chapters.length;
                doc.analyzed_count = completed;
                this.updateDocumentLabels();
            }
            document.querySelector('#processing-status .status-label').textContent =
                completed === this.chapters.length
                    ? `已就绪 · ${this.chapters.length.toLocaleString()} 章`
                    : `已索引 · ${this.chapters.length.toLocaleString()} 章`;
        } catch (e) {
            console.error("Failed to load chapters", e);
        }
    }

    // 2. 渲染左侧列表 (增加分卷显示逻辑)
    renderChapterList() {
        const list = document.getElementById('chapter-list');
        list.innerHTML = '';

        let lastVolume = null;

        const chaptersByIndex = new Map(this.chapters.map(chapter => [chapter.chapter_index, chapter]));
        const entries = this.chapterSearchResults ?? this.chapters;
        entries.forEach(entry => {
            const chap = chaptersByIndex.get(entry.chapter_index);
            if (!chap) return;
            // 如果分卷变化，插入分卷头
            if (!this.chapterSearchResults && chap.volume !== lastVolume) {
                const volLi = document.createElement('div');
                volLi.className = 'volume-header';
                volLi.textContent = chap.volume || '默认卷';
                list.appendChild(volLi);
                lastVolume = chap.volume;
            }

            const li = document.createElement('li');
            li.dataset.chapterIndex = chap.chapter_index;
            li.className = `chapter-item ${this.currentChapterIndex === chap.chapter_index ? 'active' : ''}`;
            const link = document.createElement('button');
            link.type = 'button';
            link.className = 'chapter-link';
            const dot = document.createElement('span');
            dot.className = `status-dot status-${chap.status}`;
            const label = document.createElement('span');
            label.className = 'chapter-label';
            label.textContent = this.chapterTitle(chap);
            if (entry.snippet) {
                const snippet = document.createElement('small');
                snippet.className = 'chapter-snippet';
                snippet.textContent = entry.snippet;
                label.appendChild(snippet);
            }
            link.append(dot, label);
            link.onclick = () => this.selectChapter(chap.chapter_index, true);
            li.appendChild(link);
            list.appendChild(li);
        });
    }

    async searchChapters() {
        const query = document.getElementById('chapter-search-query').value.trim();
        const mode = document.getElementById('chapter-search-mode').value;
        this.chapterSearchRequest?.abort();
        const status = document.getElementById('chapter-search-status');
        document.getElementById('clear-chapter-search').hidden = !query;
        status.hidden = !query;

        if (!query) {
            this.chapterSearchResults = null;
            this.renderChapterList();
            return;
        }

        const request = new AbortController();
        this.chapterSearchRequest = request;
        status.textContent = '搜索中…';
        try {
            const params = new URLSearchParams({ q: query, mode });
            const response = await this.request(`/chapters/search?${params}`, { signal: request.signal });
            if (!response.ok) throw new Error('Chapter search failed');
            const rows = await response.json();
            if (request.signal.aborted) return;
            this.chapterSearchResults = rows;
            status.textContent = rows.length ? `${rows.length} 章匹配` : '没有找到匹配章节';
        } catch (error) {
            if (request.signal.aborted) return;
            this.chapterSearchResults = null;
            status.textContent = '搜索失败，请重试';
        }
        this.renderChapterList();
        document.getElementById('chapter-list').scrollTop = 0;
    }

    chapterTitle(chapter) {
        return /^第.+?章/.test(chapter.title)
            ? chapter.title
            : `第${chapter.chapter_index}章 ${chapter.title}`;
    }

    // 3. 选择章节并加载详情
    async selectChapter(index, fromList = false, open = true, quote = '') {
        const documentId = this.currentDocumentId;
        this.currentChapterIndex = index;
        if (fromList) {
            const list = document.getElementById('chapter-list');
            list.querySelector('.chapter-item.active')?.classList.remove('active');
            list.querySelector(`[data-chapter-index="${index}"]`)?.classList.add('active');
        } else {
            this.renderChapterList();
        }

        const chap = this.chapters.find(c => c.chapter_index === index);
        if (!chap) return;
        if (open) this.openReference('original');
        document.getElementById('ask-chapter').disabled = false;
        this.saveConversation();
        document.getElementById('analysis-chapter-title').textContent = this.chapterTitle(chap);
        document.getElementById('reader-title').textContent = this.chapterTitle(chap);

        // 加载原文
        try {
            const res = await this.request(`/chapters/${index}/content`);
            const data = await res.json();
            if (this.currentChapterIndex !== index || this.currentDocumentId !== documentId) return;
            const contentStr = data.content.trim();

            const readerBody = document.getElementById('reader-body');
            readerBody.replaceChildren();
            const reference = quote.replace(/^原稿顺序[^\n]*\n/, '').trim();
            let highlighted = false;
            for (const paragraph of contentStr.split(/\n+/)) {
                if (!paragraph.trim()) continue;
                const block = document.createElement('p');
                block.textContent = paragraph.trim();
                const position = reference ? block.textContent.indexOf(reference) : -1;
                if (position >= 0 && !highlighted) {
                    const original = block.textContent;
                    const mark = document.createElement('mark');
                    mark.textContent = reference;
                    block.replaceChildren(
                        document.createTextNode(original.slice(0, position)),
                        mark,
                        document.createTextNode(original.slice(position + reference.length)),
                    );
                    highlighted = true;
                }
                readerBody.appendChild(block);
            }
            if (highlighted) readerBody.querySelector('mark').scrollIntoView({ block: 'center' });
            else document.getElementById('reference-original').scrollTop = 0;

            // 3. 更新分析面板状态
            const analysisStatus = document.getElementById('analysis-status');
            if (chap.status === 'completed') {
                analysisStatus.innerHTML = '<span class="badge badge-sedimented">已分析</span>';
            } else {
                analysisStatus.innerHTML = '<span class="badge badge-pending">原文已索引</span>';
            }

            // 加载详情 (包括摘要和关键事件)
            document.getElementById('chapter-summary').value = chap.summary || (chap.status !== 'completed' ? '已建立原文索引，提问时按需分析。' : '');
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
        const documentId = this.currentDocumentId;
        try {
            const res = await this.request(`/entities?chapter_index=${index}`);
            const entities = await res.json();
            if (this.currentChapterIndex !== index || this.currentDocumentId !== documentId) return;
            const list = document.getElementById('entity-list');
            list.innerHTML = '';

            entities.forEach(ent => {
                if (ent.extracted) {
                    const row = document.createElement('div');
                    row.className = 'entity-row chapter-person';
                    const heading = document.createElement('div');
                    heading.className = 'chapter-person-heading';
                    const name = document.createElement('button');
                    name.type = 'button';
                    name.className = 'entity-name person-search-link';
                    name.title = `搜索 ${ent.name} 出现的章节`;
                    name.onclick = () => {
                        document.getElementById('chapter-search-mode').value = 'people';
                        const query = document.getElementById('chapter-search-query');
                        query.placeholder = '姓名或别称';
                        query.value = ent.name;
                        this.openReference('chapters');
                        this.searchChapters();
                    };
                    name.textContent = ent.name;
                    heading.appendChild(name);
                    for (const alias of ent.aliases) {
                        const label = document.createElement('span');
                        label.className = 'chapter-person-alias';
                        label.textContent = alias;
                        heading.appendChild(label);
                    }
                    const role = document.createElement('p');
                    role.className = 'chapter-person-role';
                    role.textContent = ent.description;
                    row.append(heading, role);
                    list.appendChild(row);
                    return;
                }

                const div = document.createElement('div');
                div.className = 'entity-row chapter-person';
                const heading = document.createElement('div');
                heading.className = 'chapter-person-heading';
                const name = document.createElement('span');
                name.className = 'entity-name';
                name.textContent = ent.name;
                const remove = document.createElement('button');
                remove.className = 'btn-delete-ent';
                remove.dataset.name = ent.name;
                remove.setAttribute('aria-label', `删除 ${ent.name}`);
                remove.textContent = '×';
                heading.append(name, remove);
                const description = document.createElement('p');
                description.className = 'chapter-person-role';
                description.textContent = ent.description || '';
                div.append(heading, description);
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
        if (!confirm(`确定删除“${name}”这条人物记录吗？`)) return;

        try {
            const res = await this.request(`/entities/${encodeURIComponent(name)}`, {
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
            this.notify("请输入人物姓名");
            return;
        }

        try {
            const res = await this.request(`/entities`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, type, description: desc, chapter_index: this.currentChapterIndex })
            });

            if (res.ok) {
                document.getElementById('entity-modal').close();
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
        btn.textContent = "正在保存…";
        btn.disabled = true;

        const summary = document.getElementById('chapter-summary').value;
        // 获取编辑过的情节点，过滤空行并转回 JSON 数组
        const plotPointsRaw = document.getElementById('plot-points-edit').value;
        const plotPointsArr = plotPointsRaw.split('\n').map(l => l.trim()).filter(l => l !== '');

        try {
            const res = await this.request(`/chapters/${this.currentChapterIndex}/summary`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    summary,
                    key_events: JSON.stringify(plotPointsArr)
                })
            });

            if (res.ok) {
                btn.textContent = "已保存";


                setTimeout(async () => {
                    btn.textContent = originalText;
                    btn.style.background = "";
                    btn.disabled = false;
                    await this.loadChapters();
                    this.renderChapterList();
                }, 2000);
            }
            if (!res.ok) throw new Error('Save failed');
        } catch (e) {
            btn.textContent = "保存失败，请重试";
        } finally {
            btn.disabled = false;
        }
    }

    async sendChat() {
        const input = document.getElementById('chat-msg-input');
        const text = input.value.trim();
        if (!text || this.chatPending) return;

        this.appendMessage('user', text);
        input.value = '';
        this.chatPending = true;
        this.spendMeter.setActive(true);
        this.renderConversations();
        const button = document.getElementById('send-chat');
        button.disabled = true;
        document.getElementById('new-conversation').disabled = true;
        document.getElementById('choose-document').disabled = true;
        document.getElementById('load-document').disabled = true;
        const pending = this.appendMessage('bot', '正在理解问题并检查已有证据…', { pending: true });
        try {
            const requestBody = JSON.stringify({
                message: text, history: this.chatHistory, chapter_index: this.currentChapterIndex, scope: this.chatScope,
            });
            const res = await this.request(`/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: requestBody,
            });
            let data = await res.json();
            this.spendMeter.observe(data);
            if (data.job_id) {
                let recovered = false;
                while (data.status === 'running') {
                    const phases = {
                        planning: '正在根据问题和已有证据安排下一步…',
                        workflow: '正在复用已验证的处理步骤…',
                        catalog: '正在确定需要阅读的章节范围…',
                        search: '正在检索相关线索…',
                        read: '正在阅读原文并记录出处…',
                        scan: '正在逐章检索指定范围，核对覆盖情况…',
                        extract: '正在核对原文，整理有出处的记录…',
                        aggregate: '正在合并记录并计算…',
                        composing: '正在依据原文整理回答…',
                    };
                    pending.textContent = phases[data.phase] || data.step_label || '正在处理问题…';
                    await new Promise(resolve => setTimeout(resolve, 2000));
                    const progress = await this.request(`/chat/jobs/${data.job_id}`);
                    if (progress.status === 404 && !recovered) {
                        recovered = true;
                        const resumed = await this.request(`/chat`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: requestBody,
                        });
                        data = await resumed.json();
                        if (!resumed.ok) throw new Error(data.error || '恢复任务失败');
                    } else {
                        data = await progress.json();
                        if (!progress.ok) throw new Error(data.error || '任务查询失败');
                    }
                    this.spendMeter.observe(data);
                }
                if (data.status === 'failed') {
                    pending.closest('.msg').remove();
                    this.appendMessage('bot', data.user_message || '本次分析未完成，可重发问题继续。已核验的资料会保留。', data);
                    return;
                }
            }
            pending.closest('.msg').remove();
            const answer = this.appendMessage('bot', data.response, data);
            answer.closest('.qa-turn').scrollIntoView({ block: 'start', behavior: 'auto' });
            if (res.ok) {
                this.chatScope = data.scope || null;
                this.chatHistory.push({ role: 'user', content: text }, { role: 'assistant', content: data.response });
                this.chatHistory = this.chatHistory.slice(-8);
                this.saveConversation();
            }
        } catch (error) {
            pending.closest('.msg').remove();
            this.appendMessage('bot', '暂时无法连接 Reader，请稍后重试。');
        } finally {
            this.chatPending = false;
            this.spendMeter.setActive(false);
            this.renderConversations();
            button.disabled = false;
            document.getElementById('new-conversation').disabled = false;
            document.getElementById('choose-document').disabled = false;
            document.getElementById('load-document').disabled = false;
        }
    }

    renderAnswerText(container, text) {
        container.classList.add('markdown-body');
        container.innerHTML = DOMPurify.sanitize(marked.parse(text, { gfm: true, breaks: true }), {
            USE_PROFILES: { html: true },
        });
        for (const table of container.querySelectorAll('table')) {
            const wrapper = document.createElement('div');
            wrapper.className = 'markdown-table-scroll';
            wrapper.tabIndex = 0;
            wrapper.setAttribute('role', 'region');
            wrapper.setAttribute('aria-label', '回答表格，可横向滚动');
            table.replaceWith(wrapper);
            wrapper.appendChild(table);
        }
        for (const link of container.querySelectorAll('a')) {
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
        }
    }

    appendUsage(container, result) {
        if (!result.usage) return;
        const usage = result.usage;
        const footer = document.createElement('div');
        footer.className = 'message-usage';
        const count = value => (value || 0).toLocaleString();
        const tokens = document.createElement('p');
        tokens.textContent = `Token ${count(usage.total_tokens)} · 输入 ${count(usage.input_tokens)} / 输出 ${count(usage.output_tokens)} · API ${count(usage.api_requests)} 次`;
        const cost = document.createElement('p');
        const amount = usage.estimated_cost_usd;
        cost.textContent = amount == null ? '预估费用暂无单价' : `预估费用 $${amount.toFixed(6)} USD`;
        if (result.cache_hit) cost.textContent += ' · 本地答案缓存';
        if (usage.resumed) cost.textContent += ' · 含恢复前已完成步骤';
        if (!usage.usage_complete) cost.textContent += ' · 部分调用未返回用量，统计可能不完整';
        footer.append(tokens, cost);
        const detail = [];
        if (usage.cached_input_tokens) detail.push(`缓存读取 ${count(usage.cached_input_tokens)}`);
        if (usage.cache_write_input_tokens) detail.push(`缓存写入 ${count(usage.cache_write_input_tokens)}`);
        if (usage.reasoning_tokens) detail.push(`推理 ${count(usage.reasoning_tokens)}（已含在输出中）`);
        if (detail.length) {
            const line = document.createElement('p');
            line.textContent = detail.join(' · ');
            footer.appendChild(line);
        }
        footer.title = '汇总本轮资料压缩、原文核验、生成和重试的已返回用量。费用按当前模型公开API单价估算，以账单为准。';
        container.appendChild(footer);
    }

    appendExecution(container, result) {
        const execution = result.harness;
        if (!execution) return;
        const details = document.createElement('details');
        details.className = 'qa-execution';
        const summary = document.createElement('summary');
        const modes = { dynamic: '按问题安排步骤', workflow: '复用已有流程', workflow_fallback: '复用流程后补充核对' };
        summary.textContent = `${result.cache_hit ? '原处理过程 · 已复用保存的回答' : `处理过程 · ${modes[execution.mode] || '原文核对'}`} · ${execution.steps?.length || 0} 步`;
        details.appendChild(summary);
        if (execution.goal) {
            const goal = document.createElement('p');
            goal.textContent = execution.goal;
            details.appendChild(goal);
        }
        const list = document.createElement('ol');
        for (const step of execution.steps || []) {
            const item = document.createElement('li');
            item.textContent = `${step.label}${step.cache_hit ? ' · 复用已有结果' : ''}${step.passages ? ` · ${step.passages} 段原文` : ''}`;
            list.appendChild(item);
        }
        details.appendChild(list);
        container.appendChild(details);
        if (!result.job_id || execution.outcome !== 'answered') return;
        const feedback = document.createElement('div');
        feedback.className = 'qa-feedback';
        for (const [text, helpful] of [['有帮助', true], ['有错误', false]]) {
            const button = document.createElement('button');
            button.type = 'button';
            button.textContent = text;
            button.onclick = async () => {
                const buttons = [...feedback.querySelectorAll('button')];
                buttons.forEach(item => { item.disabled = true; });
                try {
                    const response = await this.request('/chat/feedback', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ job_id: result.job_id, helpful }),
                    });
                    if (!response.ok) throw new Error('反馈未保存');
                    feedback.textContent = helpful ? '已记录反馈' : '已记录错误，不再复用这次答案及关联流程';
                } catch {
                    buttons.forEach(item => { item.disabled = false; });
                    this.notify('反馈暂时未保存，请重试');
                }
            };
            feedback.appendChild(button);
        }
        container.appendChild(feedback);
    }

    appendMessage(role, text, result = {}) {
        const chatBox = document.getElementById('chat-messages');
        if (!this.restoring && !result.pending) {
            if (role === 'user' && !this.currentSession) {
                this.currentSession = {
                    id: crypto.randomUUID(), documentId: this.currentDocumentId,
                    title: text.slice(0, 38), messages: [], history: [], scope: null,
                };
                this.sessions.unshift(this.currentSession);
                this.sessions = this.sessions.slice(0, 16);
            }
            if (this.currentSession) {
                this.currentSession.messages.push({ role, text, result });
                if (role === 'bot') this.currentSession.messages = this.currentSession.messages.slice(-40);
                this.saveConversation();
            }
        }
        chatBox.querySelector('.chat-empty')?.remove();
        let turn = chatBox.lastElementChild;
        if (role === 'user' || !turn?.classList.contains('qa-turn')) {
            turn = document.createElement('article');
            turn.className = 'qa-turn';
            chatBox.appendChild(turn);
        }
        const message = document.createElement('div');
        message.className = `msg ${role}${result.pending ? ' is-pending' : ''}`;
        const label = document.createElement('div');
        label.className = 'message-label';
        label.textContent = role === 'user' ? '你 · 提问' : `${result.model || 'Lumina'} · 回答`;
        const div = document.createElement('div');
        div.className = 'message-content';
        if (role === 'user') {
            const question = document.createElement('h3');
            question.className = 'message-question';
            question.id = `chat-question-${++this.chatTurnCount}`;
            question.textContent = text;
            turn.setAttribute('aria-labelledby', question.id);
            div.appendChild(question);
        } else if (result.pending) {
            div.setAttribute('role', 'status');
            div.textContent = text;
        } else {
            this.renderAnswerText(div, text || '暂时没有获得回答，请重试。');
        }
        message.append(label, div);
        turn.appendChild(message);
        if (result.coverage) {
            const coverage = document.createElement('p');
            coverage.className = 'qa-coverage';
            coverage.textContent = result.coverage;
            div.appendChild(coverage);
        }
        if (result.timeline?.length) {
            const timeline = document.createElement('details');
            timeline.className = 'qa-timeline';
            const heading = document.createElement('summary');
            heading.className = 'qa-timeline-heading';
            const chapters = new Set(result.timeline.map(chapter => chapter.chapter_index));
            heading.textContent = `章节与原文依据 · ${chapters.size} 章`;
            timeline.appendChild(heading);
            for (const chapter of result.timeline) {
                const item = document.createElement('section');
                const link = document.createElement('button');
                link.type = 'button';
                link.className = 'qa-chapter-link';
                link.textContent = chapter.title;
                link.onclick = () => {
                    this.selectChapter(chapter.chapter_index, false, true, chapter.quotes?.[0] || '');
                };
                const event = document.createElement('p');
                event.textContent = chapter.event;
                const evidence = document.createElement('details');
                const label = document.createElement('summary');
                label.textContent = '查看原文依据';
                evidence.appendChild(label);
                for (const quote of chapter.quotes) {
                    const text = document.createElement('blockquote');
                    text.textContent = quote;
                    evidence.appendChild(text);
                }
                item.append(link, event, evidence);
                timeline.appendChild(item);
            }
            div.appendChild(timeline);
        }
        if (role === 'bot') {
            this.appendExecution(div, result);
            this.appendUsage(div, result);
        }
        if (role === 'user') {
            turn.scrollIntoView({ block: 'start', behavior: 'auto' });
        }
        return div;
    }

    setupEventListeners() {
        const query = document.getElementById('chapter-search-query');
        const searchMode = document.getElementById('chapter-search-mode');
        document.getElementById('chapter-search').onsubmit = event => {
            event.preventDefault();
            this.searchChapters();
        };
        searchMode.onchange = () => {
            query.placeholder = searchMode.value === 'people' ? '姓名或别称' : '情节或摘要关键词';
            if (query.value.trim()) this.searchChapters();
        };
        query.oninput = () => {
            if (!query.value.trim()) this.searchChapters();
        };
        document.getElementById('clear-chapter-search').onclick = () => {
            query.value = '';
            this.searchChapters();
            query.focus();
        };
        document.getElementById('save-analysis').onclick = () => this.saveAnalysis();
        document.getElementById('new-conversation').onclick = () => this.newConversation();
        document.getElementById('discussion-home').onclick = () => {
            this.closeReference();
            document.getElementById('chat-msg-input').focus();
        };
        for (const id of ['open-reference', 'reference-toggle']) {
            document.getElementById(id).onclick = () => this.openReference('chapters');
        }
        document.getElementById('close-reference').onclick = () => this.closeReference();
        document.querySelectorAll('.reference-tabs button').forEach(button => {
            button.onclick = () => this.openReference(button.dataset.tab);
        });
        for (const id of ['choose-document', 'load-document', 'header-document']) {
            document.getElementById(id).onclick = () => {
                if (this.chatPending) return;
                this.renderDocuments();
                document.getElementById('document-dialog').showModal();
            };
        }
        document.getElementById('close-documents').onclick = () => document.getElementById('document-dialog').close();
        document.getElementById('document-upload').onsubmit = event => this.importDocument(event);
        document.getElementById('chat-messages').onclick = event => {
            const suggestion = event.target.closest('.suggestion');
            if (suggestion) {
                const input = document.getElementById('chat-msg-input');
                input.value = suggestion.dataset.prompt;
                input.focus();
            }
        };
        document.getElementById('ask-chapter').onclick = () => {
            document.getElementById('chat-msg-input').value = '请概括这一章的关键情节，并结合原文聊聊人物的选择。';
            document.getElementById('chat-msg-input').focus();
        };
        document.getElementById('send-chat').onclick = () => this.sendChat();
        document.getElementById('chat-msg-input').onkeydown = event => {
            if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
                event.preventDefault();
                this.sendChat();
            }
        };

        // Modal Logic
        document.getElementById('add-entity').onclick = () => {
            document.getElementById('entity-modal').showModal();
        };
        document.getElementById('close-entity-modal').onclick = () => {
            document.getElementById('entity-modal').close();
        };
        document.getElementById('submit-entity').onclick = () => this.submitNewEntity();
    }
}

new LuminaApp();
