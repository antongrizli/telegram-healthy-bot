/* Workout names/comments are untrusted: render only with DOM textContent. */
let workoutData;
let workoutPage = 0;
let workoutExercise = null;
let workoutChart;
let workoutWeeklyChart;
let workoutLoad = 0;
const wtr = (key, values = {}) => {
    let value = (WORKOUT_LOCALES[state.userLanguage] || WORKOUT_LOCALES.en)[key] || key;
    for (const [k,v] of Object.entries(values)) value = value.replaceAll(`{${k}}`, String(v));
    return value;
};
const woNode = (tag, text, parent, cls) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (cls) el.className = cls;
    if (parent) parent.append(el);
    return el;
};
const woRoot = () => document.getElementById('workouts-tab');
const woError = (key = 'request_error') => {
    let el = document.getElementById('workout-error');
    if (!el) { el = woNode('p', '', woRoot()); el.id = 'workout-error'; el.setAttribute('role', 'alert'); }
    el.textContent = wtr(key);
};
const woAction = (parent, label, action) => {
    const b = woNode('button', label, parent, 'btn wo-button'); b.type = 'button';
    b.onclick = async () => { b.disabled = true; try { await action(); } catch { woError(); } finally { b.disabled = false; } };
    return b;
};
const woApi = async (path = '', method = 'GET', data, long = false) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), long ? 115000 : 12000);
    try {
        const res = await fetch('/api/workouts' + path, {method, headers: getHeaders(), signal: controller.signal,
            body: data === undefined ? undefined : JSON.stringify(data)});
        if (!res.ok) throw new Error('Workout request failed');
        return await res.json();
    } finally { clearTimeout(timer); }
};
const woDate = value => new Intl.DateTimeFormat(state.userLanguage, {
    dateStyle: 'medium', timeZone: workoutData.timezone || 'UTC'
}).format(new Date(value));
const woSet = s => {
    if (s.kind === 'cardio') return `${s.minutes ?? (s.minutes_min === undefined ? '?' : `${s.minutes_min}–${s.minutes_max}?`)} ${wtr('minute_unit')} · ${wtr(s.intensity || 'unknown_intensity')}`;
    const reps = s.reps === null || s.reps === undefined ? '?' : `${s.reps}${s.confirmed ? '' : '?'}`;
    if (s.load_type === 'bodyweight') return `${wtr('bodyweight')} × ${reps}`;
    const weight = s.weight ?? (s.weight_min === undefined ? '?' : `${s.weight_min}–${s.weight_max}?`);
    return `${weight} kg × ${reps}`;
};
const woField = (parent, label, tag = 'input') => {
    const wrap = woNode('label', label, parent, 'wo-field'); return woNode(tag, undefined, wrap);
};
const woOption = (select, value, label) => {
    const option = woNode('option', label, select); option.value = value; return option;
};
const woEntries = (parent, entries) => {
    for (const entry of entries) {
        const row = woNode('div', undefined, parent, 'wo-exercise');
        woNode('strong', entry.name, row);
        for (const [i,s] of (entry.sets || []).entries()) woNode('p', `${i+1}. ${woSet(s)}`, row);
        if (entry.unsaved_changes) woNode('p', wtr('draft'), row, 'text-secondary');
    }
};
function renderWorkoutCard(parent, item, draft = false) {
    const card = woNode('details', undefined, parent, 'card wo-history');
    const summary = woNode('summary', undefined, card);
    woNode('strong', item.name || wtr('title'), summary);
    woNode('span', woDate(item.finished_at || item.started_at), summary);
    if (draft) woNode('span', wtr('draft'), summary, 'text-secondary');
    woEntries(card, item.entries);
    if (draft && !item.entries.length) {
        woEntries(card, (item.schedule || []).map(i => ({...i, name: workoutData.library.find(e=>e.id===i.exercise_id)?.name || i.name || '?'})));
    }
    if (item.feedback?.effort) woNode('p', `${wtr('effort')} ${wtr(item.feedback.effort === 'unknown' ? 'skip' : item.feedback.effort)}`, card);
    if (item.feedback?.comment) woNode('p', item.feedback.comment, card);
    if (item.feedback?.pain) woNode('p', wtr('safety'), card);
    if (item.analysis?.summary) woNode('p', item.analysis.summary, card);
}
async function renderTodayPlan(parent) {
    const section = woNode('section', undefined, parent, 'card glass');
    woNode('h3', wtr('today_plan'), section);
    const plan = workoutData.today_plan;
    if (plan?.status === 'running') {
        woNode('p', wtr('busy'), section);
        woAction(section, wtr('analyze'), () => loadWorkouts());
        if (plan.retry_allowed) woAction(section, wtr('generate'), async () => {
            await woApi(`/today/${plan.id}/generate`, 'POST', {revision:plan.revision}, true); await loadWorkouts();
        });
        return;
    }
    if (plan && (plan.status === 'accepted' || (plan.status === 'proposed' && !plan.stale))) {
        woNode('p', wtr(plan.status === 'accepted' ? 'plan_accepted' : 'proposal'), section);
        woNode('p', plan.summary, section);
        if (plan.stale) woNode('p', wtr('changed'), section);
        woEntries(section, plan.items.map(i=>({...i, name:workoutData.library.find(e=>e.id===i.exercise_id)?.name || i.name || '?'})));
        if (plan.status === 'proposed') {
            if (plan.items.length) woAction(section, wtr('accept'), async () => {
                await woApi(`/today/${plan.id}/accept`, 'POST', {revision:plan.revision}); await loadWorkouts();
            });
            woAction(section, wtr('edit'), () => renderPlanForm(section, plan));
        }
        return;
    }
    if (plan?.stale) woNode('p', wtr('changed'), section);
    renderPlanForm(section, plan);
}
function renderPlanForm(section, plan) {
    if (section.querySelector('form')) return;
    const form = woNode('form', undefined, section, 'wo-fields');
    const focus = woField(form, wtr('focus_prompt'), 'select'); focus.required = true;
    woOption(focus, '', wtr('focus_prompt'));
    for (const key of ['legs','back','abs','upper','combined','custom']) woOption(focus, key, wtr('focus_'+key));
    focus.value = plan?.preferences.focus || '';
    const minutes = woField(form, wtr('budget_prompt')); minutes.type = 'number'; minutes.min = '0'; minutes.max = '240'; minutes.step = '1'; minutes.required = true;
    minutes.value = plan?.preferences.minutes ?? workoutData.preferences.minutes ?? '';
    const effort = woField(form, wtr('wish'), 'select'); effort.required = true;
    woOption(effort, '', wtr('wish')); woOption(effort, 'normal', wtr('normal')); woOption(effort, 'easy', wtr('lighter'));
    effort.value = plan?.preferences.effort || '';
    const wishes = woField(form, wtr('wishes_prompt'), 'textarea'); wishes.maxLength = 1000; wishes.value = plan?.preferences.wishes || '';
    const button = woNode('button', wtr('generate'), form, 'btn btn-primary wo-button'); button.type = 'submit';
    form.onsubmit = async event => {
        event.preventDefault();
        if (!form.reportValidity()) return;
        if (!workoutData.preferences.sessions_per_week) { woError('frequency_prompt'); return; }
        const n = Number(minutes.value);
        if ((n > 0 && n < 5) || (focus.value === 'custom' && !wishes.value.trim())) { woError(); return; }
        button.disabled = true;
        const status = woNode('p', wtr('busy'), form); status.setAttribute('role','status');
        try {
            const configured = await woApi('/configure', 'POST', {revision:plan?.revision ?? 0,
                preferences:{focus:focus.value, minutes:n, effort:effort.value, wishes:wishes.value}});
            await woApi(`/today/${configured.id}/generate`, 'POST', {revision:configured.revision}, true);
            await loadWorkouts();
        } catch { woError(); } finally { button.disabled = false; status.remove(); }
    };
}
async function loadWorkouts() {
    const token = ++workoutLoad;
    const query = new URLSearchParams({page:String(workoutPage)});
    if (workoutExercise !== null) query.set('exercise_id', String(workoutExercise));
    const data = await woApi('?' + query);
    if (token !== workoutLoad) return;
    workoutData = data; workoutPage = data.page;
    const root = woRoot(); root.replaceChildren();
    document.getElementById('workout-nav-label').textContent = wtr('title').replace('🏋️ ', '');
    woNode('h2', wtr('title'), root);
    const top = woNode('section', undefined, root, 'card glass');
    woNode('h3', `${wtr('week_sessions')}: ${data.totals.this_week} / ${data.preferences.sessions_per_week ?? '?'}`, top);
    woNode('p', `${wtr('sessions')}: ${data.totals.sessions}`, top);
    const freq = woField(top, wtr('frequency_prompt'), 'select');
    woOption(freq, '', '—');
    for (let n=1; n<=7; n++) woOption(freq, String(n), String(n));
    freq.value = String(data.preferences.sessions_per_week || '');
    freq.onchange = async () => { if (!freq.value) return; freq.disabled=true;
        try { await woApi('/preferences','POST',{sessions_per_week:Number(freq.value)}); await loadWorkouts(); }
        catch { woError(); freq.disabled=false; }
    };
    const weeklyBox = woNode('div', undefined, top, 'wo-chart');
    const weeklyCanvas = woNode('canvas', undefined, weeklyBox); weeklyCanvas.setAttribute('aria-label', wtr('this_week'));
    if (workoutWeeklyChart) workoutWeeklyChart.destroy();
    if (typeof Chart !== 'undefined') workoutWeeklyChart = new Chart(weeklyCanvas, {type:'bar', data:{
        labels:data.weekly.map(w=>w.date.slice(5)), datasets:[{label:wtr('this_week'), data:data.weekly.map(w=>w.count),backgroundColor:'#5ddbc3'}]},
        options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#cbd5e1'}}},scales:{x:{ticks:{color:'#cbd5e1'}},y:{beginAtZero:true,ticks:{precision:0,color:'#cbd5e1'}}}}});
    await renderTodayPlan(root);
    if (data.active) renderWorkoutCard(root, data.active, true);
    const progress = woNode('section', undefined, root, 'card glass'); woNode('h3', wtr('progress'), progress);
    const pick = woField(progress, wtr('exercise'), 'select'); woOption(pick, '', '—');
    for (const e of data.library) woOption(pick,String(e.id),e.name);
    pick.value = workoutExercise === null ? '' : String(workoutExercise);
    pick.onchange = async () => { workoutExercise = pick.value ? Number(pick.value) : null; try {await loadWorkouts();} catch {woError();} };
    woNode('p', wtr('progress_note'), progress, 'text-secondary');
    if (workoutChart) {workoutChart.destroy();workoutChart=null;}
    if (data.progress.length) {
        const ex = data.library.find(e=>e.id===workoutExercise);
        const key = ex.kind==='cardio' ? 'minutes' : ex.load_type==='bodyweight' ? 'known_reps' : 'max_weight';
        const values = data.progress.map(p=>key==='known_reps' && p.metrics.confirmed_sets!==p.metrics.sets ? null : p.metrics[key]);
        const label = wtr(key==='known_reps' ? 'repetitions' : key);
        const box = woNode('div',undefined,progress,'wo-chart'); const canvas = woNode('canvas',undefined,box);
        canvas.setAttribute('aria-label',label);
        if (typeof Chart!=='undefined') workoutChart = new Chart(canvas,{type:'line',data:{labels:data.progress.map(p=>woDate(p.date)),
            datasets:[{label,data:values,borderColor:'#5ddbc3',spanGaps:false}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#cbd5e1'}}},scales:{x:{ticks:{color:'#cbd5e1'}},y:{ticks:{color:'#cbd5e1'}}}}});
        for (const [i,p] of data.progress.entries()) woNode('p', `${woDate(p.date)} · ${label}: ${values[i] ?? '?'}`, progress);
        if (ex.kind !== 'cardio') woNode('p',wtr('quality'),progress,'text-secondary');
    }
    const history = woNode('section',undefined,root); woNode('h3',wtr('history'),history);
    if (!data.history.length) woNode('p',wtr('no_history'),history,'card');
    for (const item of data.history) renderWorkoutCard(history,item);
    const nav = woNode('div',undefined,history,'wo-pagination');
    if (data.page) woAction(nav,'‹',async()=>{workoutPage--;await loadWorkouts();});
    woNode('span',`${data.page+1} / ${data.pages}`,nav);
    if (data.page+1<data.pages) woAction(nav,'›',async()=>{workoutPage++;await loadWorkouts();});
    const medals = woNode('section',undefined,root); woNode('h3',wtr('medals'),medals);
    const grid = woNode('div',undefined,medals,'wo-badges');
    for (const badge of data.medals) {
        const cell = woNode('article',undefined,grid,'card wo-medal' + (badge.unlocked?' unlocked':''));
        woNode('span',badge.icon,cell,'wo-medal-icon'); woNode('h4',badge.name,cell);
        woNode('p',wtr(badge.unlocked?'unlocked':'locked'),cell); woNode('p',`${badge.progress} / ${badge.target}`,cell);
        woNode('p',badge.description,cell,'text-secondary');
    }
}
