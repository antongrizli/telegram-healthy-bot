// Fixture-only mobile smoke test. NODE_PATH must contain Playwright.
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const chartSource = process.env.WORKOUT_CHART_PATH ? fs.readFileSync(process.env.WORKOUT_CHART_PATH,'utf8') : 'window.Chart=class {constructor(){} destroy(){}};';
(async () => {
    const output = fs.mkdtempSync(path.join(os.tmpdir(), 'healthy-workouts-ui-'));
    const browser = await chromium.launch({headless:true,...(process.env.UX_BROWSER_PATH?{executablePath:process.env.UX_BROWSER_PATH}:{})});
    try {
        for (const lang of ['en','ru','uk','pl','de','tr','es']) {
            const page = await browser.newPage({viewport:{width:lang==='ru'||lang==='de'?320:390,height:844}});
            const errors = [];
            page.on('pageerror', e=>errors.push(e.message));
            await page.addInitScript(lang=>{window.Telegram={WebApp:{initData:'signed-fixture',initDataUnsafe:{user:{id:123,language_code:lang}},ready(){},expand(){}}};},lang);
            let today = null;
            let frequency = 3;
            let active = null;
            const library = [{id:1,name:'Жим лёжа <img src=x onerror=alert(1)>',kind:'strength',load_type:'external'},
                {id:2,name:'Бег',kind:'cardio',load_type:'external'}, {id:3,name:'Подтягивания на турнике',kind:'strength',load_type:'bodyweight'}];
            const items = [{exercise_id:1,sets:[{weight:20,reps:10,confirmed:true},{weight:20,reps:10,confirmed:true}]},
                {exercise_id:2,kind:'cardio',sets:[{kind:'cardio',minutes:20,intensity:'moderate'}]},
                {exercise_id:3,sets:[{weight:0,load_type:'bodyweight',reps:null,confirmed:false}]}];
            const workout = {id:1,status:'finished',name:'Тренировка A',started_at:'2026-11-02T08:00:00Z',finished_at:'2026-11-02T08:45:00Z',feedback:{effort:'normal'},
                entries:items.map(i=>({...i,name:library.find(e=>e.id===i.exercise_id).name})),analysis:{summary:'Умеренная нагрузка без повышения веса.'}};
            const mutations = [];
            await page.route('**/*', async route=>{
                const req=route.request(),url=new URL(req.url());
                if(url.hostname!=='healthy.test') return route.fulfill({contentType:'application/javascript',body:url.pathname.includes('chart.js')?chartSource:''});
                if(url.pathname.startsWith('/api/')) {
                    assert.equal(req.headers()['x-telegram-init-data'],'signed-fixture');
                    if(req.method()!=='GET') {
                        const body=req.postDataJSON();mutations.push({path:url.pathname,body});
                        if(url.pathname.endsWith('/preferences')) {frequency=body.sessions_per_week;return route.fulfill({json:{ok:true}});}
                        if(url.pathname.endsWith('/configure')) {today={id:1,revision:1,status:'draft',preferences:body.preferences,items:[],summary:''};return route.fulfill({json:today});}
                        if(url.pathname.endsWith('/generate')) {today={...today,status:'proposed',items,summary:'План из знакомых упражнений.'};return route.fulfill({json:today});}
                        if(url.pathname.endsWith('/accept')) {today.status='accepted';active={...workout,id:2,status:'active',finished_at:null,entries:[],schedule:items};return route.fulfill({json:{ok:true,workout_id:2}});}
                    }
                    if(url.pathname==='/api/user/settings')return route.fulfill({json:{language:lang,name:'Mobile fixture'}});
                    if(url.pathname==='/api/gamification/streaks')return route.fulfill({json:{streaks:[],freezes_left:1}});
                    if(url.pathname==='/api/workouts') {
                        const ex=url.searchParams.get('exercise_id');
                        const progress=ex==='2'?[{date:'2026-11-01T08:00:00Z',metrics:{kind:'cardio',minutes:20}},{date:'2026-11-02T08:00:00Z',metrics:{kind:'cardio',minutes:15}}]:[];
                        return route.fulfill({json:{timezone:'Europe/Berlin',page:0,pages:1,preferences:{sessions_per_week:frequency,minutes:40},
                            totals:{sessions:1,days:1,weeks:1,this_week:1},weekly:[{date:'2026-10-26',count:0},{date:'2026-11-02',count:1}],
                            history:[workout],active,library,progress,today_plan:today,medals:[{key:'first',icon:'🥇',name:'Первый тренировочный день',description:'Дни отдыха разрешены.',unlocked:true,progress:1,target:1}]}});
                    }
                    return route.fulfill({status:404,json:{error:'fixture route not found'}});
                }
                const file = path.join(root,'src/webapp/static',url.pathname.replace(/^\/webapp\/?/,'')||'index.html');
                if(!fs.existsSync(file))return route.fulfill({status:404,body:''});
                return route.fulfill({path:file});
            });
            await page.goto('https://healthy.test/webapp/?tab=workouts');
            await page.waitForSelector('#workouts-tab form');
            await page.locator('#workouts-tab > section').first().locator('select').selectOption('4');
            await page.waitForFunction(()=>document.querySelector('#workouts-tab > section select')?.value==='4');
            const form = page.locator('#workouts-tab form');
            await form.locator('select').nth(0).selectOption('combined');
            await form.locator('input[type=number]').fill('30');
            await form.locator('select').nth(1).selectOption('easy');
            await form.locator('textarea').fill('Без чрезмерной усталости');
            await form.locator('button[type=submit]').click();
            await page.waitForFunction(()=>document.querySelector('#workouts-tab')?.textContent.includes('План из знакомых упражнений.'));
            assert(mutations.some(m=>m.path.endsWith('/generate')));
            assert(mutations.find(m=>m.path.endsWith('/configure')).body.preferences.focus==='combined');
            await page.locator('#workouts-tab > section').nth(1).locator('button').first().click();
            await page.waitForFunction(()=>document.querySelector('#workouts-tab details')?.textContent.includes('Тренировка A'));
            const exerciseSelect = page.locator('#workouts-tab > section').nth(2).locator('select');
            // There is an active details card between sections, so the third section remains progress.
            await exerciseSelect.selectOption('2');
            await page.waitForFunction(()=>document.querySelector('#workouts-tab')?.textContent.includes('15'));
            assert.equal(await page.locator('#workouts-tab img').count(),0);
            await page.locator('#workouts-tab details').last().locator('summary').click();
            if (process.env.WORKOUT_CHART_PATH) {
                assert.deepEqual(await page.evaluate(()=>Chart.getChart(document.querySelectorAll('#workouts-tab .wo-chart canvas')[1]).data.datasets[0].data),[20,15]);
                await page.waitForTimeout(700);
            }
            await page.evaluate(()=>window.scrollTo(0,0));
            await page.screenshot({path:path.join(output,`${lang}.png`),fullPage:true});
            if (!await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1)) {
                console.log(output,lang,await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).map(e=>({tag:e.tagName,id:e.id,cls:e.className,right:e.getBoundingClientRect().right})).slice(0,16)));
            }
            assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
            assert.deepEqual(errors,[]);
            await page.close();
        }
        console.log('PASS: seven languages, 320/390px, signed headers, frequency, generate/accept, progress, cards, medals and XSS text.');
        console.log(output);
    } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
