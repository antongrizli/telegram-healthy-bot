// Run with Playwright available in NODE_PATH. All HTTP responses are local fixtures.
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {execFileSync} = require('node:child_process');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const dictionaries = JSON.parse(execFileSync(path.join(root, 'venv/bin/python'), ['-c',
    'import json; from src.utils.i18n_locales import LOCALES; print(json.dumps(LOCALES))'], {cwd: root, encoding: 'utf8'}));
(async () => {
    const output = fs.mkdtempSync(path.join(os.tmpdir(), 'healthy-ux-'));
    const browser = await chromium.launch({headless: true, ...(process.env.UX_BROWSER_PATH ? {executablePath: process.env.UX_BROWSER_PATH} : {})});
    try {
        for (const language of Object.keys(dictionaries)) {
            const labels = dictionaries[language];
            const page = await browser.newPage({viewport: {width: language === 'de' ? 320 : 390, height: 844}});
            const errors = [];
            page.on('pageerror', error => errors.push(error.message));
            const mutations = [];
            let failNextEdit = true;
            const dose = {id: 10, name: 'Test preparation', scheduled_at: '2026-09-12T07:00:00Z', status: 'unmarked'};
            const settings = {timezone: 'Europe/Berlin', notifications_enabled: true, frequency: 'daily', quiet_start: '22:00', quiet_end: '08:00'};
            const meal = {id: 1, name: 'Рис, лосось и овощи <img src=x onerror=alert(1)>', calories: 450, protein: 30, fat: 15, carb: 49, time: '13:00'};
            const today = {labels, settings, queue: {pending: 0, failed: 0}, drafts: [], meals: [meal],
                values: {date: '2026-09-12'}, summary: labels.ux_summary.replace('{date}', '2026-09-12').replace('{zone}', 'Europe/Berlin')
                    .replace('{count}', '2').replace('{cal}', '1340').replace('{target}', '1900').replace('{protein}', '78')
                    .replace('{protein_target}', '120').replace('{fat}', '50').replace('{carb}', '140').replace('{water}', '750')};
            await page.route('**/*', async route => {
                const request = route.request(); const url = new URL(request.url());
                if (url.hostname !== 'healthy.test') {
                    if (url.pathname.includes('chart.js')) return route.fulfill({contentType: 'application/javascript', body: 'window.Chart = class {destroy(){}};'});
                    return route.fulfill({body: ''});
                }
                if (request.method() !== 'GET') {
                    const body = request.postDataJSON(); mutations.push({path: url.pathname, body});
                    if (url.pathname.endsWith('/meals/1') && failNextEdit) {
                        failNextEdit = false;
                        return route.fulfill({status: 503, json: {error: 'temporary failure'}});
                    }
                    if (url.pathname.endsWith('/settings')) Object.assign(settings, body);
                    if (url.pathname.endsWith('/meals/1')) Object.assign(meal, body);
                    if (url.pathname.endsWith('/intakes/10')) dose.status = body.status;
                    if (url.pathname.endsWith('/drafts/22')) today.drafts = [];
                    return route.fulfill({json: {ok: true}});
                }
                const fixtures = {
                    '/api/today': today,
                    '/api/user/settings': {language, name: 'Анна', timezone: 'Europe/Berlin'},
                    '/api/gamification/streaks': {streaks: [], freezes_left: 1},
                    '/api/gamification/achievements': [],
                    '/api/medications': {intakes: [dose]},
                    '/api/charts/nutrition': {dates: ['2026-09-12'], calories: [1340], protein: [78], fat: [50], carb: [140], targets: {calories: 1900, protein: 120, fat: 60, carb: 210}},
                };
                if (fixtures[url.pathname]) return route.fulfill({json: fixtures[url.pathname]});
                const filename = url.pathname.endsWith('/') ? 'index.html' : path.basename(url.pathname);
                const asset = path.join(root, 'src/webapp/static', filename);
                const type = filename.endsWith('.js') ? 'application/javascript' : filename.endsWith('.css') ? 'text/css' : 'text/html';
                return route.fulfill({contentType: type, body: fs.readFileSync(asset)});
            });
            // Telegram can reopen the last dashboard hash or a dashboard deep link.
            const entry = language === 'ru' ? '#/dashboard' : language === 'de' ? '?tab=dashboard' : '';
            await page.goto('http://healthy.test/webapp/' + entry);
            await page.locator('#today-actions button').first().waitFor();
            await page.locator('#loading-overlay').waitFor({state: 'hidden'});
            assert.equal(await page.locator('#error-card').isVisible(), false);
            assert.equal(await page.locator('#today-actions img').count(), 0, 'Meal name must be text, not HTML');
            if (!(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))) {
                await page.screenshot({path: path.join(output, `overflow-${language}.png`), fullPage: true});
                console.log(output, await page.evaluate(() => [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth).map(e => ({tag: e.tagName, id: e.id, cls: e.className, right: e.getBoundingClientRect().right})).slice(0, 15)));
                assert.fail('No horizontal overflow: ' + language);
            }
            await page.getByRole('button', {name: labels.ux_settings, exact: true}).click();
            await page.getByLabel(labels.ux_timezone, {exact: true}).fill('UTC');
            await page.getByLabel(labels.ux_frequency, {exact: true}).selectOption('weekly');
            if (language === 'ru' || language === 'de') await page.screenshot({path: path.join(output, `settings-${language}.png`)});
            await page.getByRole('button', {name: labels.ux_save, exact: true}).click();
            await page.locator('dialog').waitFor({state: 'detached'});
            assert(mutations.some(m => m.path.endsWith('/settings') && m.body.timezone === 'UTC' && m.body.frequency === 'weekly'));
            await page.getByRole('button', {name: labels.ux_edit, exact: true}).click();
            await page.getByLabel(labels.ux_calories, {exact: true}).fill('300');
            await page.getByRole('button', {name: labels.ux_save, exact: true}).click();
            await page.getByRole('alert').filter({hasText: labels.ux_invalid}).waitFor();
            assert.equal(await page.getByLabel(labels.ux_calories, {exact: true}).inputValue(), '300');
            await page.getByRole('button', {name: labels.ux_save, exact: true}).click();
            await page.locator('dialog').waitFor({state: 'detached'});
            assert(mutations.some(m => m.path.endsWith('/meals/1') && m.body.calories === 300));
            await page.getByRole('button', {name: labels.ux_water, exact: true}).click();
            await page.getByLabel(labels.ux_water_prompt, {exact: true}).fill('250');
            await page.getByRole('button', {name: labels.ux_save, exact: true}).click();
            await page.locator('dialog').waitFor({state: 'detached'});
            assert(mutations.some(m => m.path.endsWith('/water') && m.body.amount === 250));
            await page.getByRole('button', {name: labels.ux_weight, exact: true}).click();
            await page.getByRole('spinbutton', {name: labels.ux_weight, exact: true}).fill('78.5');
            await page.getByRole('button', {name: labels.ux_save, exact: true}).click();
            await page.locator('dialog').waitFor({state: 'detached'});
            assert(mutations.some(m => m.path.endsWith('/weight') && m.body.weight === 78.5));
            await page.getByRole('button', {name: labels.btn_log_food, exact: true}).click();
            await page.getByLabel(labels.ux_meal_name, {exact: true}).fill('Apple');
            for (const [key, value] of Object.entries({calories: '100', protein: '1', fat: '2', carb: '20'})) {
                await page.getByLabel(labels['ux_' + key], {exact: true}).fill(value);
            }
            await page.getByRole('button', {name: labels.ux_save, exact: true}).click();
            await page.locator('dialog').waitFor({state: 'detached'});
            assert(mutations.some(m => m.path.endsWith('/ux/meals') && m.body.name === 'Apple'));
            await page.getByRole('button', {name: labels.med_taken, exact: true}).click();
            await page.getByRole('button', {name: labels.med_taken, exact: true}).waitFor({state: 'detached'});
            assert(mutations.some(m => m.path.endsWith('/intakes/10') && m.body.status === 'taken'));
            await page.getByRole('button', {name: labels.btn_log_food, exact: true}).click();
            await page.getByLabel(labels.ux_meal_name, {exact: true}).fill('Rice and salmon');
            await page.getByRole('button', {name: labels.ux_analyze, exact: true}).click();
            await page.locator('dialog').waitFor({state: 'detached'});
            assert(mutations.some(m => m.path.endsWith('/analyze') && m.body.description === 'Rice and salmon'));
            today.drafts = [{id: 22, analysis: {food_items: [{name: 'Rice and salmon'}], total_calories: 500}}];
            await page.evaluate(() => loadTab('dashboard'));
            await page.getByRole('button', {name: labels.btn_accept, exact: true}).click();
            await page.getByRole('button', {name: labels.btn_accept, exact: true}).waitFor({state: 'detached'});
            assert(mutations.some(m => m.path.endsWith('/drafts/22') && m.body.action === 'accept'));
            await page.locator('#loading-overlay').waitFor({state: 'hidden'});
            if (language === 'ru') await page.screenshot({path: path.join(output, 'today-ru.png'), fullPage: true});
            assert.deepEqual(errors, []);
            await page.close();
        }
        console.log(`PASS: mobile settings, meal creation/editing, AI queue/confirmation, weight, water, medication marks, error recovery, escaping, seven languages. Screenshots: ${output}`);
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
