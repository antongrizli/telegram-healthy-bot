// Daily actions use the same totals, ownership checks and translations as the bot.
let todayState = null;
let pendingRefresh = null;
const uxText = key => todayState?.labels[key] || key;
const uxNode = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
};
const uxButton = (key, action) => {
    const button = uxNode('button', uxText(key), 'btn ux-button');
    button.type = 'button';
    button.addEventListener('click', action);
    return button;
};
async function uxRequest(path, data, method = 'POST') {
    const response = await apiFetch(path, {method, headers: getHeaders(), ...(data === undefined ? {} : {body: JSON.stringify(data)})});
    if (!response.ok) throw new Error(uxText('ux_invalid'));
    return response.json();
}
function uxDialog(title) {
    const dialog = uxNode('dialog', undefined, 'ux-dialog');
    const heading = uxNode('h2', uxText(title));
    heading.id = 'ux-dialog-title';
    dialog.setAttribute('aria-labelledby', heading.id);
    const form = document.createElement('form');
    const error = uxNode('p', '', 'text-red');
    error.setAttribute('role', 'alert');
    dialog.append(heading, form, error, uxButton('ux_close', () => dialog.close()));
    dialog.addEventListener('close', () => dialog.remove());
    document.body.append(dialog);
    dialog.showModal();
    return {dialog, form, error};
}
function uxField(form, key, value = '', type = 'text') {
    const label = uxNode('label', uxText(key));
    const input = document.createElement('input');
    input.type = type;
    input.value = value;
    if (type === 'number') { input.step = 'any'; input.min = '0'; input.inputMode = 'decimal'; }
    label.append(input);
    form.append(label);
    return input;
}
function uxSubmit(view, action) {
    const save = uxNode('button', uxText('ux_save'), 'btn btn-primary');
    save.type = 'submit';
    view.form.append(save);
    view.form.addEventListener('submit', async event => {
        event.preventDefault();
        if (save.disabled) return;
        save.disabled = true;
        view.error.textContent = '';
        try { await action(); view.dialog.close(); await loadTab('dashboard'); }
        catch (error) { view.error.textContent = error.message; }
        finally { save.disabled = false; }
    });
}
function openMeal(meal = null) {
    const view = uxDialog(meal ? 'ux_edit' : 'btn_log_food');
    const name = uxField(view.form, 'ux_meal_name', meal?.name || '');
    name.maxLength = 500; name.required = true;
    const fields = {};
    for (const key of ['calories', 'protein', 'fat', 'carb']) {
        fields[key] = uxField(view.form, `ux_${key}`, meal?.[key] ?? '', 'number');
        fields[key].max = key === 'calories' ? '20000' : '2000';
        fields[key].required = true;
    }
    if (!meal) {
        const analyze = uxButton('btn_accept', async () => {
            if (!name.value.trim() || analyze.disabled) return;
            analyze.disabled = true;
            try {
                await uxRequest('/api/ux/analyze', {description: name.value});
                view.dialog.close();
                document.getElementById('ux-status').textContent = uxText('food_analyzing');
                clearTimeout(pendingRefresh);
                pendingRefresh = setTimeout(() => loadTab('dashboard'), 5000);
            } catch (error) { view.error.textContent = error.message; }
            finally { analyze.disabled = false; }
        });
        analyze.textContent = uxText('ux_analyze');
        view.form.append(analyze, uxNode('p', uxText('ux_manual_prompt')));
        uxRequest('/api/ux/events', {name: 'meal_opened'}).catch(() => {});
    }
    uxSubmit(view, async () => {
        const values = Object.fromEntries(Object.entries(fields).map(([key, input]) => [key, Number(input.value)]));
        await uxRequest(`/api/ux/meals${meal ? '/' + meal.id : ''}`, {name: name.value, ...values}, meal ? 'PATCH' : 'POST');
    });
    name.focus();
}
function openSimple(kind) {
    const view = uxDialog(kind === 'water' ? 'ux_water' : 'ux_weight');
    const input = uxField(view.form, kind === 'water' ? 'ux_water_prompt' : 'ux_weight', '', 'number');
    input.required = true;
    input.min = kind === 'water' ? '1' : '20.01';
    input.max = kind === 'water' ? '5000' : '500';
    uxSubmit(view, () => uxRequest('/api/ux/' + kind, {[kind === 'water' ? 'amount' : 'weight']: Number(input.value)}));
    input.focus();
}
function openUxSettings() {
    const view = uxDialog('ux_settings');
    const settings = todayState.settings;
    const timezone = uxField(view.form, 'ux_timezone', settings.timezone);
    timezone.required = true;
    view.form.append(uxButton('ux_device_zone', () => { timezone.value = Intl.DateTimeFormat().resolvedOptions().timeZone; }));
    const enabled = uxField(view.form, 'ux_notifications', '', 'checkbox');
    enabled.checked = settings.notifications_enabled;
    const label = uxNode('label', uxText('ux_frequency'));
    const frequency = document.createElement('select');
    frequency.setAttribute('aria-label', uxText('ux_frequency'));
    for (const value of ['off', 'daily', 'weekly']) {
        const option = uxNode('option', uxText('ux_' + value)); option.value = value; frequency.append(option);
    }
    frequency.value = settings.frequency;
    label.append(frequency); view.form.append(label);
    const start = uxField(view.form, 'ux_quiet_start', settings.quiet_start, 'time');
    const end = uxField(view.form, 'ux_quiet_end', settings.quiet_end, 'time');
    start.required = end.required = true;
    view.form.append(uxNode('p', uxText('ux_quiet_note')));
    uxSubmit(view, () => uxRequest('/api/ux/settings', {timezone: timezone.value, notifications_enabled: enabled.checked,
        frequency: frequency.value, quiet_start: start.value, quiet_end: end.value}));
}
async function loadTodayActions() {
    clearTimeout(pendingRefresh);
    todayState = await uxRequest('/api/today', undefined, 'GET');
    const container = document.getElementById('today-actions');
    container.replaceChildren(uxNode('h2', uxText('ux_today')));
    const summary = uxNode('p', todayState.summary, 'ux-summary');
    container.append(summary);
    const row = uxNode('div', undefined, 'ux-actions');
    row.append(uxButton('btn_log_food', () => openMeal()), uxButton('ux_weight', () => openSimple('weight')),
        uxButton('ux_water', () => openSimple('water')), uxButton('btn_medications', () => switchTab('medications')),
        uxButton('ux_settings', openUxSettings));
    container.append(row);
    const status = uxNode('p', '', 'text-secondary'); status.id = 'ux-status'; status.setAttribute('role', 'status'); container.append(status);
    if (todayState.queue.pending) {
        status.textContent = uxText('food_analyzing');
        const refresh = () => {
            if (state.currentTab !== 'dashboard') return;
            if (document.hidden || document.querySelector('dialog[open]')) { pendingRefresh = setTimeout(refresh, 5000); return; }
            loadTodayActions().catch(() => { status.textContent = uxText('ux_invalid'); });
        };
        pendingRefresh = setTimeout(refresh, 5000);
    } else if (todayState.queue.failed) { status.textContent = uxText('err_analysis_failed'); }
    for (const meal of todayState.meals) {
        const item = uxNode('div', undefined, 'ux-meal-row');
        item.append(uxNode('span', `${meal.time} · ${meal.name} · ${meal.calories} kcal`), uxButton('ux_edit', () => openMeal(meal)));
        container.append(item);
    }
    for (const draft of todayState.drafts) {
        const card = uxNode('div', undefined, 'ux-meal-row');
        card.append(uxNode('p', draft.analysis.food_items.map(i => i.name).join(' · ') + ` · ${draft.analysis.total_calories} kcal`), uxNode('p', uxText('ux_estimate')));
        for (const action of ['accept', 'cancel']) {
            const button = uxButton('btn_' + action, async () => {
                button.disabled = true;
                try { await uxRequest(`/api/ux/drafts/${draft.id}`, {action}); await loadTab('dashboard'); }
                catch (error) { status.textContent = error.message; button.disabled = false; }
            });
            card.append(button);
        }
        container.append(card);
    }
    // Due medication actions are also available directly on Today.
    try {
        const meds = await uxRequest('/api/medications', undefined, 'GET');
        const localDate = new Intl.DateTimeFormat('sv-SE', {timeZone: todayState.settings.timezone});
        for (const dose of meds.intakes.filter(i => i.status === 'unmarked' && localDate.format(new Date(i.scheduled_at)) === todayState.values.date).slice(0, 5)) {
            const row = uxNode('div', undefined, 'ux-meal-row');
            row.append(uxNode('span', '💊 ' + dose.name));
            for (const value of ['taken', 'skipped']) {
                const button = uxButton('med_' + value, async () => {
                    button.disabled = true;
                    try { await uxRequest(`/api/medications/intakes/${dose.id}`, {status: value}, 'PATCH'); await loadTodayActions(); }
                    catch (error) { status.textContent = error.message; button.disabled = false; }
                }); row.append(button);
            }
            container.append(row);
        }
    } catch (_) { status.textContent = uxText('med_error'); }
    if (new URLSearchParams(location.search).get('panel') === 'settings' && !window.uxSettingsOpened) {
        window.uxSettingsOpened = true; openUxSettings();
    }
}
