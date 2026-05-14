/**
 * VC News Platform — Frontend Application
 * 4-tab SPA: 벤처공고 | KIP News | 스크랩 | 설정
 */

(function () {
    'use strict';

    // ─── State ───────────────────────────────────────────

    const state = {
        currentTab: 'vc_notices',
        articles: [],
        totalArticles: 0,
        currentPage: 1,
        pageSize: 50,
        searchQuery: '',
        settings: null,
        isLoading: false,
        searchDebounce: null,
        // 진행 중인 fetch 취소용 — 탭 전환 시 이전 요청을 즉시 중단해서
        // 응답 순서 뒤바뀜과 'database is locked' 류 일시 오류를 막는다.
        currentAbort: null,
    };

    // ─── DOM References ──────────────────────────────────

    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    const dom = {
        articlesList: $('#articles-list'),
        articlesContainer: $('#articles-container'),
        settingsPanel: $('#settings-panel'),
        loadingIndicator: $('#loading-indicator'),
        emptyState: $('#empty-state'),
        searchInput: $('#search-input'),
        searchClear: $('#search-clear'),
        btnRefresh: $('#btn-refresh'),
        toast: $('#toast'),
        // Settings
        crawlInterval: $('#crawl-interval'),
        toggleNotifications: $('#toggle-notifications'),
        toggleVc: $('#toggle-vc'),
        toggleKip: $('#toggle-kip'),
        notificationSubSettings: $('#notification-sub-settings'),
        keywordInputVc: $('#keyword-input-vc'),
        keywordInputKip: $('#keyword-input-kip'),
        btnAddKeywordVc: $('#btn-add-keyword-vc'),
        btnAddKeywordKip: $('#btn-add-keyword-kip'),
        keywordsListVc: $('#keywords-list-vc'),
        keywordsListKip: $('#keywords-list-kip'),
        btnSaveSettings: $('#btn-save-settings'),
        searchBarContainer: $('#search-bar-container'),
    };

    // ─── API ─────────────────────────────────────────────

    const API = {
        async getArticles(tab, search = '', page = 1, size = 50, signal) {
            const params = new URLSearchParams({ tab, search, page, size });
            const res = await fetch(`/api/articles?${params}`, { signal });
            if (!res.ok) throw new Error('Failed to fetch articles');
            return res.json();
        },

        async getScraps(search = '', page = 1, size = 50, signal) {
            const params = new URLSearchParams({ search, page, size });
            const res = await fetch(`/api/scraps?${params}`, { signal });
            if (!res.ok) throw new Error('Failed to fetch scraps');
            return res.json();
        },

        async toggleScrap(articleId) {
            const res = await fetch(`/api/scraps/${articleId}`, { method: 'POST' });
            if (!res.ok) throw new Error('Failed to toggle scrap');
            return res.json();
        },

        async getSettings() {
            const res = await fetch('/api/settings');
            if (!res.ok) throw new Error('Failed to fetch settings');
            return res.json();
        },

        async updateSettings(data) {
            const res = await fetch('/api/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data),
            });
            if (!res.ok) throw new Error('Failed to update settings');
            return res.json();
        },

        async addKeyword(keyword, source) {
            const res = await fetch('/api/keywords', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ keyword, source }),
            });
            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || 'Failed to add keyword');
            }
            return res.json();
        },

        async deleteKeyword(keyword, source) {
            const res = await fetch(
                `/api/keywords/${encodeURIComponent(source)}/${encodeURIComponent(keyword)}`,
                { method: 'DELETE' }
            );
            if (!res.ok) throw new Error('Failed to delete keyword');
            return res.json();
        },

        async triggerCrawl() {
            const res = await fetch('/api/crawl', { method: 'POST' });
            if (!res.ok) throw new Error('Failed to trigger crawl');
            return res.json();
        },
    };

    // ─── Toast ───────────────────────────────────────────

    let toastTimer = null;

    function showToast(message, type = '') {
        clearTimeout(toastTimer);
        dom.toast.textContent = message;
        dom.toast.className = 'toast show' + (type ? ` ${type}` : '');
        toastTimer = setTimeout(() => {
            dom.toast.classList.remove('show');
        }, 2500);
    }

    // ─── Render Articles ─────────────────────────────────

    function createArticleCard(article) {
        const card = document.createElement('div');
        card.className = 'article-card';
        card.setAttribute('data-id', article.id);

        const sourceClass = article.source === 'kip' ? 'kip' : '';
        const sourceTag = article.source === 'kip' ? 'KIP' : article.source.toUpperCase();
        const scrapClass = article.is_scrapped ? 'active' : '';

        card.innerHTML = `
            <button class="btn-scrap ${scrapClass}" data-article-id="${article.id}" title="스크랩">
                <span class="material-icons-round">${article.is_scrapped ? 'star' : 'star_border'}</span>
            </button>
            <div class="article-body">
                <div class="article-meta">
                    <span class="article-date">${article.date}</span>
                    <span class="article-source-tag ${sourceClass}">${sourceTag}</span>
                </div>
                <div class="article-title">${escapeHtml(article.title)}</div>
                <a href="${escapeHtml(article.link)}" target="_blank" rel="noopener noreferrer" class="btn-link">
                    <span class="material-icons-round">open_in_new</span>
                    링크 이동
                </a>
            </div>
        `;

        // Scrap toggle handler
        const scrapBtn = card.querySelector('.btn-scrap');
        scrapBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            try {
                const result = await API.toggleScrap(article.id);
                article.is_scrapped = result.scrapped;

                scrapBtn.classList.toggle('active', result.scrapped);
                scrapBtn.querySelector('.material-icons-round').textContent =
                    result.scrapped ? 'star' : 'star_border';

                showToast(result.message, 'success');

                // If on scraps tab and unscrapped, remove the card
                if (state.currentTab === 'scraps' && !result.scrapped) {
                    card.style.transition = 'all 0.3s ease';
                    card.style.opacity = '0';
                    card.style.transform = 'translateX(-20px)';
                    setTimeout(() => {
                        card.remove();
                        if (dom.articlesList.children.length === 0) {
                            showEmptyState();
                        }
                    }, 300);
                }
            } catch (err) {
                showToast('스크랩 처리 실패', 'error');
            }
        });

        return card;
    }

    function renderArticles(articles) {
        dom.articlesList.innerHTML = '';

        if (!articles || articles.length === 0) {
            showEmptyState();
            return;
        }

        hideEmptyState();
        const fragment = document.createDocumentFragment();
        articles.forEach((article) => {
            fragment.appendChild(createArticleCard(article));
        });
        dom.articlesList.appendChild(fragment);
    }

    function showEmptyState() {
        dom.emptyState.classList.remove('hidden');
        dom.articlesList.innerHTML = '';
    }

    function hideEmptyState() {
        dom.emptyState.classList.add('hidden');
    }

    function showLoading() {
        state.isLoading = true;
        dom.loadingIndicator.classList.remove('hidden');
        dom.emptyState.classList.add('hidden');
    }

    function hideLoading() {
        state.isLoading = false;
        dom.loadingIndicator.classList.add('hidden');
    }

    // ─── Tab Switching ───────────────────────────────────

    async function switchTab(tab) {
        state.currentTab = tab;
        state.currentPage = 1;

        // Update nav active state
        $$('.nav-item').forEach((btn) => {
            btn.classList.toggle('active', btn.dataset.tab === tab);
        });

        // Show/hide panels
        const isSettings = tab === 'settings';
        dom.articlesContainer.classList.toggle('hidden', isSettings);
        dom.settingsPanel.classList.toggle('hidden', !isSettings);
        dom.searchBarContainer.classList.toggle('hidden', isSettings);

        if (isSettings) {
            await loadSettings();
        } else {
            await loadArticles();
        }
    }

    // ─── Load Data ───────────────────────────────────────

    async function loadArticles() {
        // 이전 요청을 즉시 취소 — 탭을 빠르게 전환할 때 오래된 응답이
        // 새 탭에 덮어쓰는 것과 'AbortError' 외 다른 네트워크 에러로 둔갑하는 것을 막는다.
        if (state.currentAbort) {
            state.currentAbort.abort();
        }
        const controller = new AbortController();
        state.currentAbort = controller;

        // 탭 식별자를 캡쳐 — 응답이 돌아왔을 때 사용자가 이미 다른 탭으로
        // 옮겼다면 무시한다.
        const requestedTab = state.currentTab;

        showLoading();
        state.isLoading = true;

        try {
            let data;
            if (requestedTab === 'scraps') {
                data = await API.getScraps(
                    state.searchQuery, state.currentPage, state.pageSize, controller.signal
                );
            } else {
                data = await API.getArticles(
                    requestedTab,
                    state.searchQuery,
                    state.currentPage,
                    state.pageSize,
                    controller.signal
                );
            }

            // 응답이 도착했을 때 현재 탭이 바뀌었거나 요청이 취소된 상태라면 버린다.
            if (controller.signal.aborted || state.currentTab !== requestedTab) {
                return;
            }

            state.articles = data.articles;
            state.totalArticles = data.total;
            renderArticles(state.articles);
        } catch (err) {
            // 의도적 취소는 사용자에게 보일 오류가 아님
            if (err.name === 'AbortError') return;
            showToast('데이터 로드 실패', 'error');
            console.error(err);
            if (state.currentTab === requestedTab) {
                showEmptyState();
            }
        } finally {
            if (state.currentAbort === controller) {
                state.currentAbort = null;
                state.isLoading = false;
                hideLoading();
            }
        }
    }

    async function loadSettings() {
        try {
            const settings = await API.getSettings();
            state.settings = settings;

            dom.crawlInterval.value = String(settings.crawl_interval_minutes);
            dom.toggleNotifications.checked = settings.notifications_enabled;
            dom.toggleVc.checked = settings.notify_vc_notices;
            dom.toggleKip.checked = settings.notify_kip_news;

            updateNotificationSubSettings();

            // 소스별 키워드 렌더 — 서버는 항상 두 키를 보장.
            const kw = settings.keywords || {};
            renderKeywords('vc_notices', dom.keywordsListVc, kw.vc_notices || []);
            renderKeywords('kip_news', dom.keywordsListKip, kw.kip_news || []);
        } catch (err) {
            showToast('설정 로드 실패', 'error');
            console.error(err);
        }
    }

    function updateNotificationSubSettings() {
        const enabled = dom.toggleNotifications.checked;
        dom.notificationSubSettings.style.opacity = enabled ? '1' : '0.4';
        dom.notificationSubSettings.style.pointerEvents = enabled ? 'auto' : 'none';
    }

    function renderKeywords(source, listEl, keywords) {
        listEl.replaceChildren();
        keywords.forEach((kw) => {
            const chip = document.createElement('div');
            chip.className = 'keyword-chip';

            const label = document.createElement('span');
            label.textContent = kw;

            const btn = document.createElement('button');
            btn.className = 'btn-remove';
            btn.title = '삭제';
            const icon = document.createElement('span');
            icon.className = 'material-icons-round';
            icon.style.fontSize = '14px';
            icon.textContent = 'close';
            btn.appendChild(icon);

            btn.addEventListener('click', async () => {
                try {
                    await API.deleteKeyword(kw, source);
                    chip.style.transition = 'all 0.2s ease';
                    chip.style.opacity = '0';
                    chip.style.transform = 'scale(0.8)';
                    setTimeout(() => chip.remove(), 200);
                    showToast(`키워드 '${kw}' 삭제됨`, 'success');
                } catch (err) {
                    showToast('키워드 삭제 실패', 'error');
                }
            });

            chip.appendChild(label);
            chip.appendChild(btn);
            listEl.appendChild(chip);
        });
    }

    // ─── Event Handlers ──────────────────────────────────

    function initEventHandlers() {
        // Tab navigation
        $$('.nav-item').forEach((btn) => {
            btn.addEventListener('click', () => switchTab(btn.dataset.tab));
        });

        // Search
        dom.searchInput.addEventListener('input', () => {
            const val = dom.searchInput.value.trim();
            dom.searchClear.classList.toggle('hidden', !val);

            clearTimeout(state.searchDebounce);
            state.searchDebounce = setTimeout(() => {
                state.searchQuery = val;
                state.currentPage = 1;
                if (state.currentTab !== 'settings') {
                    loadArticles();
                }
            }, 350);
        });

        dom.searchClear.addEventListener('click', () => {
            dom.searchInput.value = '';
            dom.searchClear.classList.add('hidden');
            state.searchQuery = '';
            state.currentPage = 1;
            if (state.currentTab !== 'settings') {
                loadArticles();
            }
        });

        // Refresh / manual crawl
        dom.btnRefresh.addEventListener('click', async () => {
            dom.btnRefresh.classList.add('spinning');
            try {
                await API.triggerCrawl();
                showToast('크롤링 완료!', 'success');
                if (state.currentTab !== 'settings') {
                    await loadArticles();
                }
            } catch (err) {
                showToast('크롤링 실패', 'error');
            } finally {
                dom.btnRefresh.classList.remove('spinning');
            }
        });

        // Settings — notification master toggle
        dom.toggleNotifications.addEventListener('change', updateNotificationSubSettings);

        // Settings — add keyword (소스별)
        const addKeywordFor = async (source, inputEl, listEl) => {
            const kw = inputEl.value.trim();
            if (!kw) return;
            try {
                await API.addKeyword(kw, source);
                inputEl.value = '';
                showToast(`키워드 '${kw}' 추가됨`, 'success');
                // 해당 소스만 재렌더 — 다른 소스는 건드릴 필요 없음
                const settings = await API.getSettings();
                const kwMap = settings.keywords || {};
                renderKeywords(source, listEl, kwMap[source] || []);
            } catch (err) {
                showToast(err.message || '키워드 추가 실패', 'error');
            }
        };

        dom.btnAddKeywordVc.addEventListener('click',
            () => addKeywordFor('vc_notices', dom.keywordInputVc, dom.keywordsListVc));
        dom.keywordInputVc.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') addKeywordFor('vc_notices', dom.keywordInputVc, dom.keywordsListVc);
        });
        dom.btnAddKeywordKip.addEventListener('click',
            () => addKeywordFor('kip_news', dom.keywordInputKip, dom.keywordsListKip));
        dom.keywordInputKip.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') addKeywordFor('kip_news', dom.keywordInputKip, dom.keywordsListKip);
        });

        // Settings — save
        dom.btnSaveSettings.addEventListener('click', async () => {
            try {
                await API.updateSettings({
                    crawl_interval_minutes: parseInt(dom.crawlInterval.value, 10),
                    notifications_enabled: dom.toggleNotifications.checked,
                    notify_vc_notices: dom.toggleVc.checked,
                    notify_kip_news: dom.toggleKip.checked,
                });
                showToast('설정이 저장되었습니다', 'success');
            } catch (err) {
                showToast('설정 저장 실패', 'error');
            }
        });

        // Pull-to-refresh 제거됨 — 헤더의 새로고침 버튼만 사용.

        // Infinite scroll
        const mainContent = $('#main-content');
        mainContent.addEventListener('scroll', () => {
            if (state.isLoading || state.currentTab === 'settings') return;

            const { scrollTop, scrollHeight, clientHeight } = mainContent;
            if (scrollTop + clientHeight >= scrollHeight - 100) {
                if (state.articles.length < state.totalArticles) {
                    state.currentPage++;
                    loadMoreArticles();
                }
            }
        });
    }

    async function loadMoreArticles() {
        if (state.isLoading) return;
        state.isLoading = true;

        const controller = new AbortController();
        const previousAbort = state.currentAbort;
        state.currentAbort = controller;
        const requestedTab = state.currentTab;

        try {
            let data;
            if (requestedTab === 'scraps') {
                data = await API.getScraps(
                    state.searchQuery, state.currentPage, state.pageSize, controller.signal
                );
            } else {
                data = await API.getArticles(
                    requestedTab,
                    state.searchQuery,
                    state.currentPage,
                    state.pageSize,
                    controller.signal
                );
            }

            if (controller.signal.aborted || state.currentTab !== requestedTab) return;

            if (data.articles.length > 0) {
                state.articles.push(...data.articles);
                const fragment = document.createDocumentFragment();
                data.articles.forEach((article) => {
                    fragment.appendChild(createArticleCard(article));
                });
                dom.articlesList.appendChild(fragment);
            }
        } catch (err) {
            if (err.name !== 'AbortError') console.error(err);
        } finally {
            if (state.currentAbort === controller) {
                state.currentAbort = previousAbort;
            }
            state.isLoading = false;
        }
    }

    // ─── Utilities ───────────────────────────────────────

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // ─── Init ────────────────────────────────────────────

    function init() {
        initEventHandlers();
        // Default tab
        switchTab('vc_notices');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
