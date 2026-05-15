/**
 * VC News Platform — Frontend (다중 사용자).
 *
 * 부팅 시 /api/auth/me 로 세션 확인 → 비인증이면 로그인 화면, 인증이면 메인 SPA.
 * 모든 API 호출은 동일 출처 → 쿠키 자동 포함.
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
        currentAbort: null,
        user: null,                  // { id, username, is_admin }
        authMode: 'login',           // 'login' | 'signup'
    };

    // ─── DOM ─────────────────────────────────────────────

    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    const dom = {
        authScreen: $('#auth-screen'),
        authForm: $('#auth-form'),
        authUsername: $('#auth-username'),
        authPassword: $('#auth-password'),
        authRemember: $('#auth-remember'),
        authSubmit: $('#auth-submit'),
        authSubmitLabel: $('.auth-submit-label'),
        authError: $('#auth-error'),
        app: $('#app'),
        articlesList: $('#articles-list'),
        articlesContainer: $('#articles-container'),
        settingsPanel: $('#settings-panel'),
        loadingIndicator: $('#loading-indicator'),
        emptyState: $('#empty-state'),
        searchInput: $('#search-input'),
        searchClear: $('#search-clear'),
        btnRefresh: $('#btn-refresh'),
        btnUser: $('#btn-user'),
        userLabel: $('#user-label'),
        btnLogout: $('#btn-logout'),
        toast: $('#toast'),
        settingsCrawlInterval: $('#settings-crawl-interval'),
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

    async function apiFetch(path, opts = {}) {
        const res = await fetch(path, {
            credentials: 'same-origin',
            headers: {
                ...(opts.body ? { 'Content-Type': 'application/json' } : {}),
                ...(opts.headers || {}),
            },
            ...opts,
        });
        return res;
    }

    const API = {
        async me() {
            const res = await apiFetch('/api/auth/me');
            if (res.status === 401) return null;
            if (!res.ok) throw new Error('me failed');
            return res.json();
        },
        async login(username, password, remember) {
            const res = await apiFetch('/api/auth/login', {
                method: 'POST',
                body: JSON.stringify({ username, password, remember: !!remember }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || '로그인 실패');
            return data;
        },
        async signup(username, password, remember) {
            const res = await apiFetch('/api/auth/signup', {
                method: 'POST',
                body: JSON.stringify({ username, password, remember: !!remember }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || '회원가입 실패');
            return data;
        },
        async logout() {
            await apiFetch('/api/auth/logout', { method: 'POST' });
        },
        async getArticles(tab, search = '', page = 1, size = 50, signal) {
            const params = new URLSearchParams({ tab, search, page, size });
            const res = await apiFetch(`/api/articles?${params}`, { signal });
            if (!res.ok) throw new Error('Failed to fetch articles');
            return res.json();
        },
        async getScraps(search = '', page = 1, size = 50, signal) {
            const params = new URLSearchParams({ search, page, size });
            const res = await apiFetch(`/api/scraps?${params}`, { signal });
            if (!res.ok) throw new Error('Failed to fetch scraps');
            return res.json();
        },
        async toggleScrap(articleId) {
            const res = await apiFetch(`/api/scraps/${articleId}`, { method: 'POST' });
            if (!res.ok) throw new Error('Failed to toggle scrap');
            return res.json();
        },
        async getSettings() {
            const res = await apiFetch('/api/settings');
            if (!res.ok) throw new Error('Failed to fetch settings');
            return res.json();
        },
        async updateSettings(data) {
            const res = await apiFetch('/api/settings', {
                method: 'PUT', body: JSON.stringify(data),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || 'Failed to update settings');
            }
            return res.json();
        },
        async addKeyword(keyword, source) {
            const res = await apiFetch('/api/keywords', {
                method: 'POST', body: JSON.stringify({ keyword, source }),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || 'Failed to add keyword');
            }
            return res.json();
        },
        async deleteKeyword(keyword, source) {
            const res = await apiFetch(
                `/api/keywords/${encodeURIComponent(source)}/${encodeURIComponent(keyword)}`,
                { method: 'DELETE' }
            );
            if (!res.ok) throw new Error('Failed to delete keyword');
            return res.json();
        },
        async triggerCrawl() {
            const res = await apiFetch('/api/crawl', { method: 'POST' });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || 'Failed to trigger crawl');
            }
            return res.json();
        },
    };

    // ─── Toast ───────────────────────────────────────────

    let toastTimer = null;
    function showToast(message, type = '') {
        clearTimeout(toastTimer);
        dom.toast.textContent = message;
        dom.toast.className = 'toast show' + (type ? ` ${type}` : '');
        toastTimer = setTimeout(() => dom.toast.classList.remove('show'), 2500);
    }

    // ─── Auth UI ─────────────────────────────────────────

    function showAuthScreen() {
        dom.authScreen.classList.remove('hidden');
        dom.app.classList.add('hidden');
        setAuthMode('login');
        dom.authPassword.value = '';
        // 마지막 선택 복원 — 사용자가 한 번 켜놨으면 그 상태 유지
        try {
            dom.authRemember.checked = localStorage.getItem('vcnews.remember') === '1';
        } catch (_) {
            dom.authRemember.checked = false;
        }
        dom.authUsername.focus();
    }

    function showApp() {
        dom.authScreen.classList.add('hidden');
        dom.app.classList.remove('hidden');
        dom.userLabel.textContent = state.user.username;
        dom.btnRefresh.classList.toggle('hidden', !state.user.is_admin);
        dom.settingsCrawlInterval.classList.toggle('hidden', !state.user.is_admin);
    }

    function setAuthMode(mode) {
        state.authMode = mode;
        $$('.auth-tab').forEach((b) => b.classList.toggle('active', b.dataset.mode === mode));
        dom.authSubmitLabel.textContent = mode === 'signup' ? '회원가입' : '로그인';
        const submitIcon = dom.authSubmit.querySelector('.material-icons-round');
        if (submitIcon) submitIcon.textContent = mode === 'signup' ? 'person_add' : 'login';
        dom.authPassword.autocomplete = mode === 'signup' ? 'new-password' : 'current-password';
        clearAuthError();
    }

    function showAuthError(msg) {
        dom.authError.textContent = msg;
        dom.authError.classList.remove('hidden');
    }
    function clearAuthError() {
        dom.authError.textContent = '';
        dom.authError.classList.add('hidden');
    }

    async function handleAuthSubmit(e) {
        e.preventDefault();
        clearAuthError();
        const username = dom.authUsername.value.trim();
        const password = dom.authPassword.value;
        const remember = !!dom.authRemember.checked;
        if (!username || !password) {
            showAuthError('아이디와 비밀번호를 입력하세요');
            return;
        }
        // 자동 로그인 체크 상태를 로컬에 보존 — 다음에 로그인 화면 떴을 때 복원
        try { localStorage.setItem('vcnews.remember', remember ? '1' : '0'); } catch (_) {}
        dom.authSubmit.disabled = true;
        try {
            const data = state.authMode === 'signup'
                ? await API.signup(username, password, remember)
                : await API.login(username, password, remember);
            state.user = data;
            showApp();
            await switchTab('vc_notices');
        } catch (err) {
            showAuthError(err.message || '오류가 발생했습니다');
        } finally {
            dom.authSubmit.disabled = false;
        }
    }

    async function handleLogout() {
        try { await API.logout(); } catch (_) { /* ignore */ }
        state.user = null;
        showAuthScreen();
    }

    // ─── Articles render (DOM API, no innerHTML) ─────────

    function el(tag, opts = {}, ...children) {
        const e = document.createElement(tag);
        if (opts.class) e.className = opts.class;
        if (opts.text != null) e.textContent = opts.text;
        if (opts.attrs) {
            for (const [k, v] of Object.entries(opts.attrs)) {
                e.setAttribute(k, v);
            }
        }
        if (opts.title) e.title = opts.title;
        if (opts.on) {
            for (const [evt, fn] of Object.entries(opts.on)) {
                e.addEventListener(evt, fn);
            }
        }
        for (const child of children) {
            if (child == null) continue;
            e.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
        }
        return e;
    }

    function icon(name) {
        return el('span', { class: 'material-icons-round', text: name });
    }

    function createArticleCard(article) {
        // 벤처뉴스(kip) 는 배지 없이 — 같은 탭 안에 있어 식별 불필요.
        // KVCA/KVIC 는 벤처공고 탭에서 두 소스 구분 위해 배지 유지.
        const showSourceTag = article.source !== 'kip';
        const sourceTag = article.source.toUpperCase();

        const scrapBtn = el('button',
            { class: `btn-scrap${article.is_scrapped ? ' active' : ''}`, title: '스크랩',
              attrs: { 'data-article-id': String(article.id) } },
            icon(article.is_scrapped ? 'star' : 'star_border'),
        );

        scrapBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            try {
                const result = await API.toggleScrap(article.id);
                article.is_scrapped = result.scrapped;
                scrapBtn.classList.toggle('active', result.scrapped);
                scrapBtn.replaceChildren(icon(result.scrapped ? 'star' : 'star_border'));
                showToast(result.message, 'success');
                if (state.currentTab === 'scraps' && !result.scrapped) {
                    card.style.transition = 'all 0.3s ease';
                    card.style.opacity = '0';
                    card.style.transform = 'translateX(-20px)';
                    setTimeout(() => {
                        card.remove();
                        if (dom.articlesList.children.length === 0) showEmptyState();
                    }, 300);
                }
            } catch (err) {
                showToast('스크랩 처리 실패', 'error');
            }
        });

        const meta = el('div', { class: 'article-meta' },
            el('span', { class: 'article-date', text: article.date }),
            showSourceTag
                ? el('span', { class: 'article-source-tag', text: sourceTag })
                : null,
        );

        const titleEl = el('div', { class: 'article-title', text: article.title });

        const link = el('a',
            { class: 'btn-link',
              attrs: { href: article.link, target: '_blank', rel: 'noopener noreferrer' } },
            icon('open_in_new'),
            ' 링크 이동',
        );

        const body = el('div', { class: 'article-body' }, meta, titleEl, link);
        const card = el('div',
            { class: 'article-card', attrs: { 'data-id': String(article.id) } },
            scrapBtn, body,
        );
        return card;
    }

    function renderArticles(articles) {
        dom.articlesList.replaceChildren();
        if (!articles || articles.length === 0) {
            showEmptyState();
            return;
        }
        hideEmptyState();
        const fragment = document.createDocumentFragment();
        articles.forEach((a) => fragment.appendChild(createArticleCard(a)));
        dom.articlesList.appendChild(fragment);
    }

    function showEmptyState() {
        dom.emptyState.classList.remove('hidden');
        dom.articlesList.replaceChildren();
    }
    function hideEmptyState() { dom.emptyState.classList.add('hidden'); }
    function showLoading() {
        state.isLoading = true;
        dom.loadingIndicator.classList.remove('hidden');
        dom.emptyState.classList.add('hidden');
    }
    function hideLoading() {
        state.isLoading = false;
        dom.loadingIndicator.classList.add('hidden');
    }

    // ─── Tabs ────────────────────────────────────────────

    async function switchTab(tab) {
        state.currentTab = tab;
        state.currentPage = 1;

        $$('.nav-item').forEach((btn) => {
            btn.classList.toggle('active', btn.dataset.tab === tab);
        });

        const isSettings = tab === 'settings';
        dom.articlesContainer.classList.toggle('hidden', isSettings);
        dom.settingsPanel.classList.toggle('hidden', !isSettings);
        dom.searchBarContainer.classList.toggle('hidden', isSettings);

        if (isSettings) await loadSettings();
        else await loadArticles();
    }

    // ─── Data load ───────────────────────────────────────

    async function loadArticles() {
        if (state.currentAbort) state.currentAbort.abort();
        const controller = new AbortController();
        state.currentAbort = controller;
        const requestedTab = state.currentTab;

        showLoading();

        try {
            let data;
            if (requestedTab === 'scraps') {
                data = await API.getScraps(state.searchQuery, state.currentPage, state.pageSize, controller.signal);
            } else {
                data = await API.getArticles(requestedTab, state.searchQuery, state.currentPage, state.pageSize, controller.signal);
            }
            if (controller.signal.aborted || state.currentTab !== requestedTab) return;

            state.articles = data.articles;
            state.totalArticles = data.total;
            renderArticles(state.articles);
        } catch (err) {
            if (err.name === 'AbortError') return;
            const me = await API.me().catch(() => null);
            if (!me) { state.user = null; showAuthScreen(); return; }
            showToast('데이터 로드 실패', 'error');
            console.error(err);
            if (state.currentTab === requestedTab) showEmptyState();
        } finally {
            if (state.currentAbort === controller) state.currentAbort = null;
            hideLoading();
        }
    }

    async function loadSettings() {
        try {
            const settings = await API.getSettings();
            state.settings = settings;

            if (state.user && state.user.is_admin) {
                dom.crawlInterval.value = String(settings.crawl_interval_minutes);
            }
            dom.toggleNotifications.checked = settings.notifications_enabled;
            dom.toggleVc.checked = settings.notify_vc_notices;
            dom.toggleKip.checked = settings.notify_kip_news;
            updateNotificationSubSettings();

            const kw = settings.keywords || {};
            renderKeywords('vc_notices', dom.keywordsListVc, kw.vc_notices || []);
            renderKeywords('kip_news', dom.keywordsListKip, kw.kip_news || []);
        } catch (err) {
            const me = await API.me().catch(() => null);
            if (!me) { state.user = null; showAuthScreen(); return; }
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
            const removeBtn = el('button', { class: 'btn-remove', title: '삭제' });
            const removeIcon = icon('close');
            removeIcon.style.fontSize = '14px';
            removeBtn.appendChild(removeIcon);

            const chip = el('div', { class: 'keyword-chip' },
                el('span', { text: kw }),
                removeBtn,
            );

            removeBtn.addEventListener('click', async () => {
                try {
                    await API.deleteKeyword(kw, source);
                    chip.style.transition = 'all 0.2s ease';
                    chip.style.opacity = '0';
                    chip.style.transform = 'scale(0.8)';
                    setTimeout(() => chip.remove(), 200);
                    showToast(`키워드 '${kw}' 삭제됨`, 'success');
                } catch (_) {
                    showToast('키워드 삭제 실패', 'error');
                }
            });
            listEl.appendChild(chip);
        });
    }

    // ─── Handlers ────────────────────────────────────────

    function initEventHandlers() {
        $$('.auth-tab').forEach((b) => {
            b.addEventListener('click', () => setAuthMode(b.dataset.mode));
        });
        dom.authForm.addEventListener('submit', handleAuthSubmit);

        if (dom.btnLogout) dom.btnLogout.addEventListener('click', handleLogout);

        $$('.nav-item').forEach((btn) => {
            btn.addEventListener('click', () => switchTab(btn.dataset.tab));
        });

        dom.searchInput.addEventListener('input', () => {
            const val = dom.searchInput.value.trim();
            dom.searchClear.classList.toggle('hidden', !val);
            clearTimeout(state.searchDebounce);
            state.searchDebounce = setTimeout(() => {
                state.searchQuery = val;
                state.currentPage = 1;
                if (state.currentTab !== 'settings') loadArticles();
            }, 350);
        });
        dom.searchClear.addEventListener('click', () => {
            dom.searchInput.value = '';
            dom.searchClear.classList.add('hidden');
            state.searchQuery = '';
            state.currentPage = 1;
            if (state.currentTab !== 'settings') loadArticles();
        });

        dom.btnRefresh.addEventListener('click', async () => {
            dom.btnRefresh.classList.add('spinning');
            try {
                await API.triggerCrawl();
                showToast('크롤링 완료!', 'success');
                if (state.currentTab !== 'settings') await loadArticles();
            } catch (err) {
                showToast(err.message || '크롤링 실패', 'error');
            } finally {
                dom.btnRefresh.classList.remove('spinning');
            }
        });

        dom.toggleNotifications.addEventListener('change', updateNotificationSubSettings);

        const addKeywordFor = async (source, inputEl, listEl) => {
            const kw = inputEl.value.trim();
            if (!kw) return;
            try {
                await API.addKeyword(kw, source);
                inputEl.value = '';
                showToast(`키워드 '${kw}' 추가됨`, 'success');
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

        dom.btnSaveSettings.addEventListener('click', async () => {
            const payload = {
                notifications_enabled: dom.toggleNotifications.checked,
                notify_vc_notices: dom.toggleVc.checked,
                notify_kip_news: dom.toggleKip.checked,
            };
            if (state.user && state.user.is_admin) {
                payload.crawl_interval_minutes = parseInt(dom.crawlInterval.value, 10);
            }
            try {
                await API.updateSettings(payload);
                showToast('설정이 저장되었습니다', 'success');
            } catch (err) {
                showToast(err.message || '설정 저장 실패', 'error');
            }
        });

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
                data = await API.getScraps(state.searchQuery, state.currentPage, state.pageSize, controller.signal);
            } else {
                data = await API.getArticles(requestedTab, state.searchQuery, state.currentPage, state.pageSize, controller.signal);
            }
            if (controller.signal.aborted || state.currentTab !== requestedTab) return;
            if (data.articles.length > 0) {
                state.articles.push(...data.articles);
                const fragment = document.createDocumentFragment();
                data.articles.forEach((a) => fragment.appendChild(createArticleCard(a)));
                dom.articlesList.appendChild(fragment);
            }
        } catch (err) {
            if (err.name !== 'AbortError') console.error(err);
        } finally {
            if (state.currentAbort === controller) state.currentAbort = previousAbort;
            state.isLoading = false;
        }
    }

    // ─── Init ────────────────────────────────────────────

    async function init() {
        initEventHandlers();
        try {
            const me = await API.me();
            if (me) {
                state.user = me;
                showApp();
                await switchTab('vc_notices');
            } else {
                showAuthScreen();
            }
        } catch (err) {
            showAuthScreen();
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
