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
        keywordInput: $('#keyword-input'),
        btnAddKeyword: $('#btn-add-keyword'),
        keywordsList: $('#keywords-list'),
        btnSaveSettings: $('#btn-save-settings'),
        searchBarContainer: $('#search-bar-container'),
    };

    // ─── API ─────────────────────────────────────────────

    const API = {
        async getArticles(tab, search = '', page = 1, size = 50) {
            const params = new URLSearchParams({ tab, search, page, size });
            const res = await fetch(`/api/articles?${params}`);
            if (!res.ok) throw new Error('Failed to fetch articles');
            return res.json();
        },

        async getScraps(search = '', page = 1, size = 50) {
            const params = new URLSearchParams({ search, page, size });
            const res = await fetch(`/api/scraps?${params}`);
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

        async addKeyword(keyword) {
            const res = await fetch('/api/keywords', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ keyword }),
            });
            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || 'Failed to add keyword');
            }
            return res.json();
        },

        async deleteKeyword(keyword) {
            const res = await fetch(`/api/keywords/${encodeURIComponent(keyword)}`, {
                method: 'DELETE',
            });
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
        if (state.isLoading) return;
        showLoading();

        try {
            let data;
            if (state.currentTab === 'scraps') {
                data = await API.getScraps(state.searchQuery, state.currentPage, state.pageSize);
            } else {
                data = await API.getArticles(
                    state.currentTab,
                    state.searchQuery,
                    state.currentPage,
                    state.pageSize
                );
            }

            state.articles = data.articles;
            state.totalArticles = data.total;
            renderArticles(state.articles);
        } catch (err) {
            showToast('데이터 로드 실패', 'error');
            console.error(err);
            showEmptyState();
        } finally {
            hideLoading();
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

            // Sub-settings visibility
            updateNotificationSubSettings();

            // Render keywords
            renderKeywords(settings.keywords);
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

    function renderKeywords(keywords) {
        dom.keywordsList.innerHTML = '';
        keywords.forEach((kw) => {
            const chip = document.createElement('div');
            chip.className = 'keyword-chip';
            chip.innerHTML = `
                <span>${escapeHtml(kw)}</span>
                <button class="btn-remove" data-keyword="${escapeHtml(kw)}" title="삭제">
                    <span class="material-icons-round" style="font-size:14px">close</span>
                </button>
            `;

            chip.querySelector('.btn-remove').addEventListener('click', async () => {
                try {
                    await API.deleteKeyword(kw);
                    chip.style.transition = 'all 0.2s ease';
                    chip.style.opacity = '0';
                    chip.style.transform = 'scale(0.8)';
                    setTimeout(() => chip.remove(), 200);
                    showToast(`키워드 '${kw}' 삭제됨`, 'success');
                } catch (err) {
                    showToast('키워드 삭제 실패', 'error');
                }
            });

            dom.keywordsList.appendChild(chip);
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

        // Settings — add keyword
        const addKeyword = async () => {
            const kw = dom.keywordInput.value.trim();
            if (!kw) return;

            try {
                await API.addKeyword(kw);
                dom.keywordInput.value = '';
                showToast(`키워드 '${kw}' 추가됨`, 'success');
                // Re-render keywords
                const settings = await API.getSettings();
                renderKeywords(settings.keywords);
            } catch (err) {
                showToast(err.message || '키워드 추가 실패', 'error');
            }
        };

        dom.btnAddKeyword.addEventListener('click', addKeyword);
        dom.keywordInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') addKeyword();
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

        // Pull to refresh (simple version)
        let touchStartY = 0;
        let isPulling = false;

        dom.articlesContainer.addEventListener('touchstart', (e) => {
            if (dom.articlesList.scrollTop === 0) {
                touchStartY = e.touches[0].clientY;
                isPulling = true;
            }
        }, { passive: true });

        dom.articlesContainer.addEventListener('touchend', (e) => {
            if (!isPulling) return;
            isPulling = false;
            const diff = e.changedTouches[0].clientY - touchStartY;
            if (diff > 80 && state.currentTab !== 'settings') {
                loadArticles();
                showToast('새로고침 중...', '');
            }
        }, { passive: true });

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

        try {
            let data;
            if (state.currentTab === 'scraps') {
                data = await API.getScraps(state.searchQuery, state.currentPage, state.pageSize);
            } else {
                data = await API.getArticles(
                    state.currentTab,
                    state.searchQuery,
                    state.currentPage,
                    state.pageSize
                );
            }

            if (data.articles.length > 0) {
                state.articles.push(...data.articles);
                const fragment = document.createDocumentFragment();
                data.articles.forEach((article) => {
                    fragment.appendChild(createArticleCard(article));
                });
                dom.articlesList.appendChild(fragment);
            }
        } catch (err) {
            console.error(err);
        } finally {
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
