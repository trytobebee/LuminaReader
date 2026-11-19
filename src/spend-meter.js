// A lifetime counter driven by returned API usage, never simulated spending.
export class SpendMeter {
    constructor(request) {
        this.request = request;
        this.root = document.getElementById('spend-meter');
        this.price = document.getElementById('spend-cost');
        this.tokens = document.getElementById('spend-tokens');
        this.status = document.getElementById('spend-status');
        this.display = { cost: 0, tokens: 0 };
        this.initialized = false;
        this.lastEvent = 0;
        this.active = false;
        this.reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
        try {
            const saved = JSON.parse(localStorage.getItem('lumina-spend-display-v1') || 'null');
            if (saved && Number.isFinite(saved.cost) && Number.isFinite(saved.tokens)) {
                this.hasSaved = true;
                this.renderNumbers(saved.cost, saved.tokens);
                this.status.textContent = '上次记录 · 正在同步';
            }
        } catch { /* The server ledger remains authoritative. */ }
        this.refresh = this.refresh.bind(this);
        this.refresh();
        this.timer = setInterval(() => {
            if (!document.hidden) this.refresh();
        }, 2500);
        window.addEventListener('focus', this.refresh);
        document.addEventListener('visibilitychange', () => { if (!document.hidden) this.refresh(); });
    }

    observe(result) {
        const labels = {
            planning: '正在安排阅读', workflow: '正在复用阅读步骤',
            catalog: '正在查阅目录', search: '正在寻找线索', read: '正在阅读原文',
            scan: '正在核对章节', extract: '正在整理证据', aggregate: '正在合并记录',
            composing: '正在整理回答',
        };
        if (result?.cache_hit) this.notice = '复用已有回答 · 本次未新增用量';
        else if (result?.status === 'failed') this.notice = '本轮未完成 · 已返回用量仍计入';
        else this.notice = null;
        this.phase = labels[result?.phase] || '正在处理问题';
        this.refresh();
        this.renderStatus();
    }

    setActive(active) {
        this.active = active;
        if (active) {
            this.notice = null;
            this.phase = '正在理解问题';
        }
        this.renderStatus();
        this.refresh();
    }

    async refresh() {
        if (this.loading) return;
        this.loading = true;
        try {
            const response = await this.request('/usage');
            if (!response.ok) throw new Error('Usage unavailable');
            const data = await response.json();
            this.offline = false;
            this.snapshot = data;
            const events = data.events || [];
            const fresh = this.initialized ? events.filter(event => event.id > this.lastEvent) : [];
            this.lastEvent = data.last_event_id || 0;
            this.animateTo(data.known_cost_usd || 0, data.total_tokens || 0, this.initialized);
            try {
                localStorage.setItem('lumina-spend-display-v1', JSON.stringify({cost:data.known_cost_usd || 0,tokens:data.total_tokens || 0}));
            } catch { /* Local display caching is optional. */ }
            this.initialized = true;
            if (fresh.length) this.settle(fresh);
            this.renderDetails(data);
            this.renderSparkline(events);
            this.renderStatus();
        } catch {
            this.offline = true;
            this.renderStatus();
        } finally {
            this.loading = false;
        }
    }

    animateTo(cost, tokens, animate) {
        cancelAnimationFrame(this.frame);
        const start = { ...this.display };
        if (!animate || this.reducedMotion || (start.cost === cost && start.tokens === tokens)) {
            this.renderNumbers(cost, tokens);
            return;
        }
        const began = performance.now();
        this.root.classList.add('is-counting');
        const tick = now => {
            const progress = Math.min(1, (now - began) / 1000);
            const eased = 1 - (1 - progress) ** 3;
            this.renderNumbers(start.cost + (cost - start.cost) * eased, start.tokens + (tokens - start.tokens) * eased);
            if (progress < 1) this.frame = requestAnimationFrame(tick);
            else this.root.classList.remove('is-counting');
        };
        this.frame = requestAnimationFrame(tick);
    }

    renderNumbers(cost, tokens) {
        this.display = { cost, tokens };
        this.price.textContent = cost.toFixed(cost >= 100 ? 4 : 6);
        this.tokens.textContent = Math.round(tokens).toLocaleString('en-US');
        document.getElementById('spend-cost-short').textContent = cost > 0 && cost < 0.01 ? '<1¢' : `$${cost.toFixed(2)}`;
        document.getElementById('spend-tokens-short').textContent = new Intl.NumberFormat('en-US', {
            notation: 'compact', maximumFractionDigits: 1,
        }).format(Math.round(tokens));
        this.root.setAttribute('aria-label', `阅读问答累计预估费用 ${cost.toFixed(6)} 美元，${Math.round(tokens).toLocaleString('zh-CN')} Token`);
    }

    settle(events) {
        const cost = events.reduce((total, event) => total + event.cost, 0);
        const tokens = events.reduce((total, event) => total + event.tokens, 0);
        const delta = document.getElementById('spend-delta');
        delta.textContent = `${cost > 0 ? `+$${cost.toFixed(6)} · ` : ''}+${tokens.toLocaleString('en-US')} Token`;
        this.notice = '用量已回传 · 已计入累计';
        this.root.classList.remove('just-settled');
        // Re-trigger only for an actual new ledger event, not every poll.
        void this.root.offsetWidth;
        this.root.classList.add('just-settled');
        clearTimeout(this.settleTimer);
        this.settleTimer = setTimeout(() => this.root.classList.remove('just-settled'), 2200);
    }

    renderStatus() {
        const data = this.snapshot;
        const running = this.active || data?.active_runs > 0;
        this.root.dataset.state = this.offline ? 'offline' : running ? 'active' : 'idle';
        let text;
        if (this.offline) text = this.initialized || this.hasSaved ? '连接中断 · 保留上次累计' : '暂时无法读取用量';
        else if (!data) text = this.hasSaved ? '上次记录 · 正在同步' : '正在读取累计用量';
        else if (running) text = data.active_requests > 0 ? '模型处理中 · 等待本次用量' : this.phase || '正在处理问题';
        else if (this.notice) text = this.notice;
        else if (data.missing_usage_requests > 0) text = '部分调用未返回用量';
        else if (data.incomplete_history_records > 0) text = '部分历史用量记录不完整';
        else if (data.unpriced_requests > 0) text = '部分 Token 尚无计费单价';
        else text = '已记录问答 · 全部文档';
        if (this.status.textContent !== text) this.status.textContent = text;
    }

    renderDetails(data) {
        const number = value => (value || 0).toLocaleString('en-US');
        document.getElementById('spend-detail-cost').textContent = `$${data.known_cost_usd.toFixed(6)} USD`;
        for (const field of ['input_tokens', 'output_tokens', 'reasoning_tokens', 'api_requests', 'cached_input_tokens', 'cache_write_input_tokens']) {
            document.getElementById(`spend-detail-${field}`).textContent = number(data[field]);
        }
        const warnings = [];
        if (data.active_requests) warnings.push(`${number(data.active_requests)} 次模型请求等待用量，尚未结算`);
        if (data.missing_usage_requests) warnings.push(`${number(data.missing_usage_requests)} 次请求未取得完整用量`);
        if (data.incomplete_history_records) warnings.push(`${number(data.incomplete_history_records)} 条历史记录不完整，缺失请求数无法确定`);
        if (data.unpriced_requests) warnings.push(`${number(data.unpriced_requests)} 次调用尚无单价，Token 已计入`);
        document.getElementById('spend-detail-warning').textContent = warnings.join('；');
        document.getElementById('spend-detail-warning').hidden = !warnings.length;
        const recent = document.getElementById('spend-recent');
        recent.replaceChildren();
        for (const event of [...(data.events || [])].slice(-3).reverse()) {
            const row = document.createElement('li');
            row.textContent = `${event.source === 'history' ? '历史记录' : '用量回传'} · +${number(event.tokens)} Token${event.cost > 0 ? ` · +$${event.cost.toFixed(6)}` : ''}`;
            recent.appendChild(row);
        }
    }

    renderSparkline(events) {
        const samples = events.map(event => event.cost);
        const maximum = Math.max(0.000001, ...samples);
        const points = samples.map((value, index) => `${index * 180 / Math.max(1, samples.length - 1)},${23 - value / maximum * 20}`);
        document.getElementById('spend-sparkline').setAttribute('points', points.length > 1 ? points.join(' ') : '0,23 180,23');
    }
}
