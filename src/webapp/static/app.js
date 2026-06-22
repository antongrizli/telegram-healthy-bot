// Initialize Telegram WebApp SDK
const tg = window.Telegram ? window.Telegram.WebApp : null;

if (tg) {
    tg.ready();
    tg.expand();
    // Enable haptics on load
    if (tg.HapticFeedback) {
        tg.HapticFeedback.impactOccurred('medium');
    }
}

// Global Application State
const state = {
    user: tg && tg.initDataUnsafe ? tg.initDataUnsafe.user : null,
    initData: tg ? tg.initData : "",
    userLanguage: "en",
    currentTab: "dashboard",
    currentRange: "7d",
    charts: {
        donut: null,
        nutrition: null,
        macro: null,
        weight: null
    }
};

// Common Headers for API Requests
const getHeaders = () => {
    return {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": state.initData
    };
};

// UI Elements
const els = {
    userName: document.getElementById("user-name"),
    userStreak: document.getElementById("user-streak-status"),
    freezesLeft: document.getElementById("freezes-left"),
    loading: document.getElementById("loading-overlay"),
    errorCard: document.getElementById("error-card"),
    errorMessage: document.getElementById("error-message")
};

// Translate entire page based on target language
const translatePage = (lang) => {
    const dictionary = LOCALES[lang] || LOCALES["en"];

    // Set document title
    document.title = dictionary.webapp_title || document.title;

    // Translate standard elements
    document.querySelectorAll("[data-i18n]").forEach(el => {
        const key = el.dataset.i18n;
        if (dictionary[key]) {
            el.innerText = dictionary[key];
        }
    });
};

// Fetch user settings and apply localization
const initLocalization = async () => {
    // Default fallback from Telegram client, or English
    let clientLang = "en";
    if (state.user && state.user.language_code) {
        clientLang = state.user.language_code;
    }

    // Standardize clientLang to supported ones
    if (!["en", "ru", "uk", "pl", "de", "tr", "es"].includes(clientLang)) {
        clientLang = "en";
    }

    state.userLanguage = clientLang;

    try {
        const res = await fetch("/api/user/settings", { headers: getHeaders() });
        if (res.ok) {
            const data = await res.json();
            if (data.language) {
                state.userLanguage = data.language;
            }
            if (data.name) {
                els.userName.innerText = data.name;
            }
        }
    } catch (e) {
        console.error("Failed to load user settings, using client/fallback language", e);
    }

    // Run translation
    translatePage(state.userLanguage);

    if (!state.user) {
        const dict = LOCALES[state.userLanguage] || LOCALES["en"];
        els.userName.innerText = dict.webapp_dev_mode;
    }
};

// Initialize App
document.addEventListener("DOMContentLoaded", async () => {
    // 1. Setup avatar placeholder
    if (state.user && state.user.photo_url) {
        document.getElementById("avatar-placeholder").innerHTML = `<img src="${state.user.photo_url}" style="width:100%; height:100%; border-radius:50%; object-fit:cover;">`;
    }

    // 2. Load settings and apply localization
    await initLocalization();

    // 3. Set active tab based on URL query param or hash deep link (e.g. ?tab=health-card)
    const urlParams = new URLSearchParams(window.location.search);
    const tabParam = urlParams.get("tab");
    const hash = window.location.hash;

    if (tabParam && ["dashboard", "charts", "achievements", "health-card"].includes(tabParam)) {
        switchTab(tabParam);
    } else if (hash) {
        const route = hash.replace("#/", "");
        if (["dashboard", "charts", "achievements", "health-card"].includes(route)) {
            switchTab(route);
        } else {
            loadTab("dashboard");
        }
    } else {
        loadTab("dashboard");
    }

    // 4. Setup Range Switchers on Trends tab
    const toggleButtons = document.querySelectorAll("#range-toggle .toggle-btn");
    toggleButtons.forEach(btn => {
        btn.addEventListener("click", (e) => {
            toggleButtons.forEach(b => b.classList.remove("active"));
            e.target.classList.add("active");
            state.currentRange = e.target.dataset.range;
            loadTrendsCharts();
        });
    });
});

// Tab Switcher Logic
const switchTab = (tabName) => {
    if (state.currentTab === tabName) return;

    // Update hash for deep linking
    window.location.hash = `#/${tabName}`;

    // Manage active navigation buttons
    const navItems = document.querySelectorAll(".nav-item");
    navItems.forEach(item => item.classList.remove("active"));

    const indexMap = { "dashboard": 0, "charts": 1, "achievements": 2, "health-card": 3 };
    if (navItems[indexMap[tabName]]) {
        navItems[indexMap[tabName]].classList.add("active");
    }

    // Hide all tabs, show target
    const tabs = document.querySelectorAll(".tab-content");
    tabs.forEach(tab => tab.classList.add("hidden"));

    const targetTab = document.getElementById(`${tabName}-tab`);
    if (targetTab) {
        targetTab.classList.remove("hidden");
    }

    state.currentTab = tabName;
    loadTab(tabName);

    // Trigger tiny haptic feedback
    if (tg && tg.HapticFeedback) {
        tg.HapticFeedback.selectionChanged();
    }
};

// Dispatcher to load specific tab data
const loadTab = async (tabName) => {
    showLoading(true);
    hideError();
    try {
        // Load streaks data on every tab to keep header updated
        await loadStreaksData();

        if (tabName === "dashboard") {
            await loadDashboardData();
        } else if (tabName === "charts") {
            await loadTrendsCharts();
        } else if (tabName === "achievements") {
            await loadAchievementsGrid();
        } else if (tabName === "health-card") {
            await loadHealthCard();
        }
    } catch (err) {
        showError(err.message || "Failed to load tab data");
    } finally {
        showLoading(false);
    }
};

// Show/Hide Helpers
const showLoading = (show) => {
    if (show) els.loading.classList.remove("hidden");
    else els.loading.classList.add("hidden");
};

const showError = (msg) => {
    els.errorCard.classList.remove("hidden");
    const dict = LOCALES[state.userLanguage] || LOCALES["en"];
    let debugInfo = `\n\n[Debug Info]\n`;
    debugInfo += `URL: ${window.location.href}\n`;
    debugInfo += `Telegram SDK: ${window.Telegram ? "Loaded" : "Not Loaded"}\n`;
    if (window.Telegram && window.Telegram.WebApp) {
        const tg = window.Telegram.WebApp;
        debugInfo += `initData exists: ${!!tg.initData}\n`;
        debugInfo += `initData length: ${tg.initData ? tg.initData.length : 0}\n`;
        debugInfo += `initDataUnsafe: ${JSON.stringify(tg.initDataUnsafe)}\n`;
    } else {
        debugInfo += `window.Telegram.WebApp is undefined\n`;
    }
    const errDesc = document.querySelector("#error-card p");
    if (errDesc) {
        errDesc.innerText = dict.webapp_err_desc;
    }
    els.errorMessage.innerText = msg + debugInfo;
};

const hideError = () => {
    els.errorCard.classList.add("hidden");
};

// Load Streaks Header Info
const loadStreaksData = async () => {
    try {
        const res = await fetch("/api/gamification/streaks", { headers: getHeaders() });
        if (!res.ok) throw new Error("Unauthorized access");
        const data = await res.json();

        const dict = LOCALES[state.userLanguage] || LOCALES["en"];

        // Find food logging streak
        const foodStreak = data.streaks.find(s => s.streak_type === "food_logging");
        if (foodStreak && foodStreak.current_count > 0) {
            const streakText = dict.webapp_active_streak.replace("{count}", foodStreak.current_count);
            els.userStreak.innerHTML = `<i class="fa-solid fa-fire text-orange"></i> ${streakText}`;
        } else {
            els.userStreak.innerHTML = `<i class="fa-solid fa-fire text-secondary"></i> ${dict.webapp_start_streak}`;
        }

        els.freezesLeft.innerText = dict.webapp_freezes_left.replace("{count}", data.freezes_left);
    } catch (e) {
        console.error("Streak load error:", e);
    }
};

// Dashboard Loader (Donut and Macros)
const loadDashboardData = async () => {
    const res = await fetch("/api/charts/nutrition?range=7d", { headers: getHeaders() });
    if (!res.ok) throw new Error("Failed to load today's nutrition");
    const data = await res.json();

    // Today's values are the last elements of the arrays
    const len = data.dates.length;
    if (len === 0) return;

    const todayCal = data.calories[len - 1];
    const todayProt = data.protein[len - 1];
    const todayCarb = data.carb[len - 1];
    const todayFat = data.fat[len - 1];

    const target = data.targets;

    const dict = LOCALES[state.userLanguage] || LOCALES["en"];

    // Update center donut text
    document.getElementById("cal-intake").innerText = todayCal;
    document.getElementById("cal-target").innerText = `/ ${target.calories} ${dict.webapp_kcal}`;

    // Update progress bars
    updateProgressBar("protein", todayProt, target.protein);
    updateProgressBar("carb", todayCarb, target.carb);
    updateProgressBar("fat", todayFat, target.fat);

    // Render Calories Donut
    renderDonutChart(todayCal, target.calories);

    // Load recent achievement
    await loadRecentAchievement();
};

const updateProgressBar = (id, current, target) => {
    document.getElementById(`macro-${id.substring(0, 4)}-ratio`).innerText = `${parseInt(current)}g / ${target}g`;
    const pct = target > 0 ? Math.min(100, (current / target) * 100) : 0;
    document.getElementById(`bar-${id}`).style.width = `${pct}%`;
};

const renderDonutChart = (current, target) => {
    const ctx = document.getElementById("calories-donut-chart").getContext("2d");
    if (state.charts.donut) {
        state.charts.donut.destroy();
    }

    const remainder = Math.max(0, target - current);
    const completedColor = current > target ? '#e74c3c' : '#2ecc71'; // Red if exceeded target

    state.charts.donut = new Chart(ctx, {
        type: 'doughnut',
        data: {
            datasets: [{
                data: [current, remainder],
                backgroundColor: [completedColor, 'rgba(255,255,255,0.05)'],
                borderWidth: 0,
                borderRadius: current > 0 ? 5 : 0
            }]
        },
        options: {
            responsive: true,
            cutout: '82%',
            plugins: {
                tooltip: { enabled: false },
                legend: { display: false }
            }
        }
    });
};

const loadRecentAchievement = async () => {
    try {
        const res = await fetch("/api/gamification/achievements", { headers: getHeaders() });
        const data = await res.json();
        const unlocked = data.filter(a => a.unlocked).sort((a, b) => new Date(b.unlocked_at) - new Date(a.unlocked_at));

        if (unlocked.length > 0) {
            const dict = LOCALES[state.userLanguage] || LOCALES["en"];
            document.getElementById("recent-ach-icon").innerText = unlocked[0].icon;
            document.getElementById("recent-ach-title").innerText = dict.webapp_unlocked_title.replace("{name}", unlocked[0].name);
        }
    } catch (e) {
        console.error("Recent achievement load error:", e);
    }
};

// Trends Charts Loader
const loadTrendsCharts = async () => {
    // 1. Load nutrition chart data
    const nutRes = await fetch(`/api/charts/nutrition?range=${state.currentRange}`, { headers: getHeaders() });
    const nutData = await nutRes.json();

    renderNutritionHistoryChart(nutData);
    renderMacroHistoryChart(nutData);

    // 2. Load weight chart data
    const weightRes = await fetch(`/api/charts/weight?range=${state.currentRange === '7d' ? '30d' : state.currentRange}`, { headers: getHeaders() });
    const weightData = await weightRes.json();

    renderWeightHistoryChart(weightData);
};

const renderNutritionHistoryChart = (data) => {
    const ctx = document.getElementById("calories-history-chart").getContext("2d");
    if (state.charts.nutrition) {
        state.charts.nutrition.destroy();
    }

    // Format dates slightly for better viewing
    const labels = data.dates.map(d => {
        const parts = d.split('-');
        return `${parts[2]}/${parts[1]}`;
    });

    const dict = LOCALES[state.userLanguage] || LOCALES["en"];
    const labelText = dict.webapp_chart_calories_lbl || 'Calories (kcal)';

    state.charts.nutrition = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: labelText,
                    data: data.calories,
                    backgroundColor: 'rgba(88, 86, 214, 0.4)',
                    borderColor: '#5856d6',
                    borderWidth: 2,
                    borderRadius: 4
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                y: {
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: { color: '#a1a1b5' }
                },
                x: {
                    grid: { display: false },
                    ticks: { color: '#a1a1b5' }
                }
            }
        }
    });
};

const renderMacroHistoryChart = (data) => {
    const ctx = document.getElementById("macro-history-chart").getContext("2d");
    if (state.charts.macro) {
        state.charts.macro.destroy();
    }

    const labels = data.dates.map(d => {
        const parts = d.split('-');
        return `${parts[2]}/${parts[1]}`;
    });

    const dict = LOCALES[state.userLanguage] || LOCALES["en"];

    state.charts.macro = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: dict.webapp_protein || 'Protein',
                    data: data.protein,
                    backgroundColor: '#f1c40f',
                    borderRadius: 4
                },
                {
                    label: dict.webapp_carbs || 'Carbs',
                    data: data.carb,
                    backgroundColor: '#3498db',
                    borderRadius: 4
                },
                {
                    label: dict.webapp_fats || 'Fats',
                    data: data.fat,
                    backgroundColor: '#e74c3c',
                    borderRadius: 4
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: true,
                    labels: { color: '#a1a1b5' }
                }
            },
            scales: {
                y: {
                    stacked: true,
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: { color: '#a1a1b5' }
                },
                x: {
                    stacked: true,
                    grid: { display: false },
                    ticks: { color: '#a1a1b5' }
                }
            }
        }
    });
};

const renderWeightHistoryChart = (data) => {
    const ctx = document.getElementById("weight-history-chart").getContext("2d");
    if (state.charts.weight) {
        state.charts.weight.destroy();
    }

    const labels = data.dates.map(d => {
        const parts = d.split('-');
        return `${parts[2]}/${parts[1]}`;
    });

    state.charts.weight = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                data: data.weights,
                borderColor: '#e74c3c',
                backgroundColor: 'rgba(231, 76, 60, 0.1)',
                borderWidth: 3,
                fill: true,
                tension: 0.3,
                pointRadius: 4,
                pointBackgroundColor: '#e74c3c'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                y: {
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: { color: '#a1a1b5' }
                },
                x: {
                    grid: { display: false },
                    ticks: { color: '#a1a1b5' }
                }
            }
        }
    });
};

// Achievements Grid Loader
const loadAchievementsGrid = async () => {
    const res = await fetch("/api/gamification/achievements", { headers: getHeaders() });
    const data = await res.json();

    const grid = document.getElementById("badges-grid");
    grid.innerHTML = "";

    data.forEach(ach => {
        const card = document.createElement("div");
        card.className = `badge-card ${ach.unlocked ? 'unlocked' : ''}`;

        card.innerHTML = `
            <div class="badge-icon">${ach.icon}</div>
            <h4>${ach.name}</h4>
            <p>${ach.description}</p>
        `;
        grid.appendChild(card);
    });
};

// Health Card Loader
const loadHealthCard = async () => {
    try {
        const res = await fetch("/api/gamification/health-card", { headers: getHeaders() });
        if (res.status === 404) {
            document.getElementById("no-card-placeholder").classList.remove("hidden");
            document.getElementById("health-card-container").classList.add("hidden");
            return;
        }

        const data = await res.json();

        document.getElementById("no-card-placeholder").classList.add("hidden");
        document.getElementById("health-card-container").classList.remove("hidden");

        document.getElementById("health-score-num").innerText = data.card_data.overall_score;
        document.getElementById("coach-message").innerText = data.card_data.coach_message;

        // Populate Categories mini-cards
        const catsGrid = document.getElementById("card-categories");
        catsGrid.innerHTML = "";

        const dict = LOCALES[state.userLanguage] || LOCALES["en"];

        for (const [key, cat] of Object.entries(data.card_data.categories)) {
            const minicard = document.createElement("div");
            minicard.className = "card-mini";

            const trendIcon = cat.trend === "up" ? '<i class="fa-solid fa-arrow-trend-up text-green"></i>' :
                cat.trend === "down" ? '<i class="fa-solid fa-arrow-trend-down text-red"></i>' :
                    '<i class="fa-solid fa-right-long text-secondary"></i>';

            const translatedLabel = dict[key] || key.toUpperCase();
            minicard.innerHTML = `
                <div class="mini-score">${cat.score}%</div>
                <div class="mini-label">${translatedLabel}</div>
                <div class="mini-trend">${trendIcon}</div>
            `;
            catsGrid.appendChild(minicard);
        }
    } catch (e) {
        console.error("Health card load error:", e);
        document.getElementById("no-card-placeholder").classList.remove("hidden");
        document.getElementById("health-card-container").classList.add("hidden");
    }
};
