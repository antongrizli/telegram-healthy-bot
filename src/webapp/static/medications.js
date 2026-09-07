/* All user/OCR strings are inserted with textContent or input.value. */
let medicationData;
let medicationChart;
const mt = key => (MED_LOCALES[state.userLanguage] || MED_LOCALES.en)[key] || key;
const medRoot = () => document.getElementById('medications-tab');
const medNode = (tag, text, parent, cls) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (cls) el.className = cls;
    if (parent) parent.append(el);
    return el;
};
const medAction = (parent, label, action) => {
    const b = medNode('button', label, parent, 'btn med-button');
    b.type = 'button';
    b.onclick = async () => {
        b.disabled = true;
        try { await action(); } catch (e) { medError(e); }
        finally { b.disabled = false; }
    };
    return b;
};
const medError = () => {
    let el = document.getElementById('med-error');
    if (!el) { el = medNode('p', '', medRoot()); el.id = 'med-error'; el.setAttribute('role', 'alert'); }
    el.textContent = mt('error');
};
const medApi = async (path = '', method = 'GET', data) => {
    const res = await fetch('/api/medications' + path, {
        method, headers: getHeaders(), body: data === undefined ? undefined : JSON.stringify(data)
    });
    if (!res.ok) throw new Error('Medication request failed');
    return res.json();
};
const medField = (parent, label, type, value = '') => {
    const wrap = medNode('label', label, parent, 'med-field');
    const input = medNode('input', undefined, wrap);
    input.type = type; input.value = value;
    return input;
};
const medDays = days => days.map(d => new Intl.DateTimeFormat(state.userLanguage, {weekday: 'short', timeZone:'UTC'})
    .format(new Date(Date.UTC(2026, 0, 5 + d)))).join(', ');
const medDateTime = value => new Intl.DateTimeFormat(state.userLanguage, {
    dateStyle: 'medium', timeStyle: 'short', timeZone: medicationData.timezone || 'UTC'
}).format(new Date(value));
const medToday = () => new Intl.DateTimeFormat('en-CA', {
    timeZone: medicationData.timezone || 'UTC', year:'numeric', month:'2-digit', day:'2-digit'
}).format(new Date());

async function loadMedications() {
    medicationData = await medApi();
    document.getElementById('med-nav-label').textContent = mt('title');
    const root = medRoot(); root.replaceChildren();
    medNode('h2', '💊 ' + mt('title'), root);
    medNode('p', medicationData.timezone, root, 'text-secondary');
    if (!medicationData.notifications_enabled) medNode('p', mt('no_notifications'), root);
    const stats = medNode('section', undefined, root, 'card');
    medNode('h3', mt('statistics'), stats);
    const grid = medNode('div', undefined, stats, 'med-stats');
    for (const key of ['total', 'taken', 'skipped', 'unmarked']) {
        const cell = medNode('div', undefined, grid);
        medNode('strong', String(medicationData.statistics[key]), cell);
        medNode('span', mt(key), cell);
    }
    if (medicationData.statistics.total) {
        medNode('p', `${mt('taken')}: ${medicationData.statistics.adherence_percent}%`, stats);
        const box = medNode('div', undefined, stats, 'med-chart');
        const canvas = medNode('canvas', undefined, box);
        if (medicationChart) medicationChart.destroy();
        if (typeof Chart !== 'undefined') medicationChart = new Chart(canvas, {
            type: 'bar', data: { labels: Object.keys(medicationData.daily).map(d => d.slice(5)),
                datasets: ['taken','skipped','unmarked'].map((key,i) => ({label:mt(key),
                    data: Object.values(medicationData.daily).map(d => d[key]),
                    backgroundColor: ['#34d399','#f87171','#94a3b8'][i]})) },
            options: {responsive:true, maintainAspectRatio:false, plugins:{legend:{labels:{color:'#cbd5e1'}}},
                scales:{x:{stacked:true,ticks:{color:'#cbd5e1'}},y:{stacked:true,beginAtZero:true,ticks:{precision:0,color:'#cbd5e1'}}}}
        });
    }
    const library = medNode('section', undefined, root, 'card');
    medNode('h3', mt('library'), library);
    medAction(library, '+ ' + mt('add'), () => medicationWizard());
    if (!medicationData.medications.length) medNode('p', mt('empty'), library);
    for (const item of medicationData.medications) {
        const row = medNode('article', undefined, library, 'med-row');
        medNode('h4', item.name, row); medNode('p', mt(item.category) + (item.details ? ' · ' + item.details : ''), row);
        medAction(row, mt('add_schedule'), () => reminderWizard(item.id));
        medAction(row, mt('edit'), () => medicationWizard(item));
        medAction(row, mt('delete'), async () => {
            if (window.confirm(mt('confirm_delete'))) { await medApi('/items/' + item.id, 'DELETE'); await loadMedications(); }
        });
    }
    const schedules = medNode('section', undefined, root, 'card');
    medNode('h3', mt('schedules'), schedules);
    if (!medicationData.reminders.length) medNode('p', mt('empty'), schedules);
    for (const r of medicationData.reminders) {
        const row = medNode('article', undefined, schedules, 'med-row');
        const product = medicationData.medications.find(m => m.id === r.medication_id);
        medNode('h4', product ? product.name : '', row);
        medNode('p', `${r.weekdays.length === 7 ? mt('daily') : medDays(r.weekdays)} · ${r.time}`, row);
        medNode('p', r.dose, row);
        medNode('p', r.end_date ? `${mt('until')} ${r.end_date}` : mt('unlimited'), row);
        medAction(row, mt('edit'), () => reminderWizard(r.medication_id, r));
        medAction(row, mt('delete'), async () => {
            if (window.confirm(mt('confirm_delete'))) { await medApi('/reminders/' + r.id, 'DELETE'); await loadMedications(); }
        });
    }
    const history = medNode('section', undefined, root, 'card');
    medNode('h3', mt('history'), history);
    if (!medicationData.intakes.length) medNode('p', mt('empty'), history);
    // Paginate in the DOM to keep long histories usable on mobile.
    let shown = 0;
    const more = medAction(history, mt('next'), () => renderHistory());
    const renderHistory = () => {
        for (const r of medicationData.intakes.slice(shown, shown + 20)) {
            const row = medNode('article', undefined, undefined, 'med-row'); history.insertBefore(row, more);
            medNode('h4', r.name, row); medNode('p', medDateTime(r.scheduled_at) + ' · ' + mt(r.status), row);
            medNode('p', r.dose, row);
            for (const status of ['taken', 'skipped']) medAction(row, mt(status), async () => {
                await medApi('/intakes/' + r.id, 'PATCH', {status}); await loadMedications();
            });
        }
        shown += 20; more.hidden = shown >= medicationData.intakes.length;
    };
    renderHistory();
}

function medForm(title) {
    const root = medRoot(); root.replaceChildren();
    const form = medNode('form', undefined, root, 'card med-form');
    medNode('h3', title, form);
    form.onsubmit = e => e.preventDefault();
    return form;
}
function medicationWizard(existing) {
    const draft = {...(existing || {category:'medicine', name:'', details:''})};
    const first = () => {
        const form = medForm('1 · ' + mt('title'));
        for (const key of ['medicine','vitamin','other']) medAction(form, mt(key), () => {draft.category=key; second();});
        medAction(form, mt('cancel'), loadMedications);
    };
    const second = () => {
        const form = medForm('2 · ' + mt(draft.category));
        const name = medField(form, mt('name'), 'text', draft.name); name.required=true; name.maxLength=200;
        const details = medField(form, mt('details'), 'text', draft.details); details.maxLength=500;
        const photo = medField(form, mt('photo'), 'file'); photo.accept='image/jpeg,image/png,image/webp';
        medNode('p', mt('verify'), form);
        const status = medNode('p', '', form); status.setAttribute('role','status');
        const photoKey = 'med-photo-' + (state.user?.id || 'current');
        let photoRequest = null;
        const checkPhoto = async id => {
            const result = await medApi('/photos/' + id);
            if (!form.isConnected) return;
            if (result.result) {
                name.value=result.result.name; details.value=result.result.details;
                status.textContent=mt('verify'); localStorage.removeItem(photoKey);
                photo.disabled=false;
            } else if (['failed','cancelled'].includes(result.status)) {
                status.textContent=mt('error'); localStorage.removeItem(photoKey); photo.disabled=false;
            } else {
                status.textContent=mt('queued');
                window.setTimeout(() => {if (form.isConnected) checkPhoto(id).catch(medError);}, 3000);
            }
        };
        photo.onchange = async () => {
            try {
                const file=photo.files[0]; if (!file) return;
                if (file.size > 4*1024*1024) throw new Error('Large image');
                photo.disabled=true;
                const image = await new Promise((resolve,reject) => {
                    const reader=new FileReader(); reader.onload=()=>resolve(reader.result.split(',')[1]); reader.onerror=reject; reader.readAsDataURL(file);
                });
                photoRequest=await medApi('/photos','POST',{image,mime_type:file.type});
                localStorage.setItem(photoKey,String(photoRequest.id));
                await checkPhoto(photoRequest.id);
            } catch (e) {photo.disabled=false; medError(e);}
        };
        const pending=localStorage.getItem(photoKey);
        if (pending && /^\d+$/.test(pending)) checkPhoto(pending).catch(() => localStorage.removeItem(photoKey));
        medAction(form, mt('back'), () => {draft.name=name.value;draft.details=details.value;first();});
        medAction(form, mt('save'), async () => {
            if (!form.reportValidity()) return;
            const item=await medApi('/items'+(draft.id ? '/'+draft.id : ''),draft.id?'PATCH':'POST',
                {category:draft.category,name:name.value,details:details.value});
            if (draft.id) await loadMedications(); else reminderWizard(item.id);
        });
        medAction(form, mt('cancel'), loadMedications);
    };
    first();
}
function reminderWizard(medicationId, existing) {
    const draft = {...(existing || {weekdays:[0,1,2,3,4,5,6],time:'09:00',end_date:null,dose:''}), medication_id:medicationId};
    const daysStep = () => {
        const form=medForm('3 · '+mt('schedules'));
        const selected=new Set(draft.weekdays);
        const choices=medNode('div',undefined,form,'med-days');
        const render=()=>{
            choices.replaceChildren();
            for (let d=0;d<7;d++) {
                const wrap=medNode('label',undefined,choices);
                const checkbox=medNode('input',undefined,wrap); checkbox.type='checkbox';checkbox.checked=selected.has(d);
                checkbox.onchange=()=>checkbox.checked?selected.add(d):selected.delete(d);
                medNode('span',medDays([d]),wrap);
            }
        };
        medAction(form,mt('daily'),()=>{for(let d=0;d<7;d++)selected.add(d);render();});
        medAction(form,mt('weekly'),()=>{selected.clear();render();});
        medNode('p',mt('days'),form);form.append(choices);render();
        medAction(form,mt('next'),()=>{if(!selected.size){medError();return;}draft.weekdays=[...selected].sort();timeStep();});
        medAction(form,mt('cancel'),loadMedications);
    };
    const timeStep=()=>{
        const form=medForm('4 · '+mt('time'));
        medNode('p',medicationData.timezone,form);
        const time=medField(form,mt('time'),'time',draft.time);time.required=true;
        const dose=medField(form,mt('dose'),'text',draft.dose);dose.maxLength=200;
        medAction(form,mt('back'),daysStep);
        medAction(form,mt('next'),()=>{if(!form.reportValidity())return;draft.time=time.value;draft.dose=dose.value;endStep();});
        medAction(form,mt('cancel'),loadMedications);
    };
    const endStep=()=>{
        const form=medForm('5 · '+mt('end'));
        const unlimited=medField(form,mt('unlimited'),'checkbox');unlimited.checked=!draft.end_date;
        const end=medField(form,mt('end'),'date',draft.end_date || '');end.min=medToday();end.disabled=unlimited.checked;end.required=!unlimited.checked;
        unlimited.onchange=()=>{end.disabled=unlimited.checked;end.required=!unlimited.checked;};
        medNode('p',(draft.weekdays.length===7?mt('daily'):medDays(draft.weekdays))+' · '+draft.time+' · '+medicationData.timezone,form);
        medAction(form,mt('back'),()=>{draft.end_date=unlimited.checked?null:end.value;timeStep();});
        medAction(form,mt('save'),async()=>{
            if(!form.reportValidity())return;
            draft.end_date=unlimited.checked?null:end.value;
            await medApi('/reminders'+(draft.id?'/'+draft.id:''),draft.id?'PATCH':'POST',draft);
            await loadMedications();
        });
        medAction(form,mt('cancel'),loadMedications);
    };
    daysStep();
}
