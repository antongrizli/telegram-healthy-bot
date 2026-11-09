# Упрощение журнала тренировок, ввода и плана — план реализации

> **Для исполнителя:** задачи независимо тестируемы и идут по порядку; шаги в формате `- [ ]`.
> Код пишет автор вручную; план фиксирует решения (файлы, имена, сигнатуры, тесты), а не тела функций.

**Цель:** план тренировки собирается только из тренажёров библиотеки пользователя; если для выбранной цели (руки, ноги и т.д.) тренажёров нет — бот спрашивает: добавить свои, подобрать через AI (с подтверждением) или собрать без них. Ввод выполненной тренировки текстом перестаёт засорять библиотеку.

**Архитектура:** работа только в новой системе `t2` (`src/handlers/training/*`, `src/services/training/*`, таблицы `canonical_exercises`, `workout_sessions`, `training_plans`). Каталог `src/data/exercise_catalog.yaml` остаётся справочником метаданных и источником подсказок, но больше не источником упражнений плана.

**Стек:** Python 3.12, aiogram 3, SQLAlchemy async, Gemini через `src/services/gemini.py`, pytest.

**Спецификация:** отдельного spec нет; требования — раздел «Анализ» ниже и запрос пользователя от 2026-11-09.

---

## Анализ текущего состояния

### Три параллельные системы тренировок
| Система | Вход | Таблицы | Статус |
| --- | --- | --- | --- |
| Legacy | `/workout`, WebApp, `src/handlers/workouts.py` | `exercises`, `workouts`, `workout_entries`, `workout_plans`, `workout_templates` | описана в `docs/workouts.md` |
| Дневной план | `src/handlers/workout_daily.py`, `src/services/workout_today.py` | `daily_workout_plans` (+ legacy `exercises`) | LLM уже ограничен библиотекой (`sanitize_daily_proposal`) |
| t2 | кнопка «🏋️ Тренировка», `/training`, `src/handlers/training/*` | `canonical_exercises`, `exercise_aliases`, `workout_sessions`, `training_plans` | основной путь из меню |

Кнопку `btn_workout` перехватывает `training.router` (подключён раньше в `src/main.py:138`), поэтому legacy-домашний экран достижим только через `/workout`. Библиотек тренажёров две (`exercises` и `canonical_exercises`), и они не синхронизируются. **План ниже меняет только t2**; что делать с legacy, решаете вы (см. «Вне объёма»).

### Почему план «придумывает» упражнения
1. `planner.build_plan` (`src/services/training/planner.py:118-125`) добавляет в кандидаты **все 42 записи каталога**, которых нет в библиотеке (`from_catalog_only`, штраф всего −0.5). Затем `sessions.start_from_plan` создаёт для них `CanonicalExercise` — так «придуманные» упражнения попадают в библиотеку.
2. Записи библиотеки без `muscle_primary` получают score 0 и отбрасываются (`if sc > 0.0`). Тренажёр, не найденный в каталоге (проверено: «Beinbeuger sitzend» → `None`), в план не попадёт никогда, а его место займёт каталог.

### Почему ввод засоряет библиотеку
Ваш отчёт в `parser.parse_text` даёт такие названия упражнений: `«Не выполнено: Brustpresse — 25–30 кг ·»`, `«Примечания: Ellipsentrainer»`, `«Отчёт о тренировке 2 ноября 2026»`, `«Самочувствие: Нормально»`. `sessions.add_draft_items` (`sessions.py:128-136`) вызывает `get_or_create_canonical` для **каждой** строки, в том числе `unparsed`, и каждая становится тренажёром.

### Сопутствующие дефекты
- `llm.parse_unparsed_lines`: `gemini.extract_json` возвращает `str`, а код проверяет `isinstance(data, dict)`. LLM-восстановление строк никогда не срабатывает.
- `log_text.py:89-95`: язык захардкожен как `lang="ru"`.
- `plan.py:handle_resize_plan`: делает `flush` без `commit` (ревизия теряется), всегда −15 мин и показывает `minutes=30` вместо фактических.
- Сборка контекста плана (`lib_dicts` + история) скопирована в `hub.py:174-209` и `plan.py:180-211`; история грузится N+1 запросами.

---

## Глобальные ограничения
- Никаких изменений в legacy-таблицах и `src/handlers/workouts.py` / `workout_daily.py`.
- Любое добавление в библиотеку, предложенное AI, требует подтверждения кнопкой (как в «Добавить тренажёр» → `eq_ai_ok`).
- Названия тренажёров ≤ `library.MAX_NAME_LEN` (100), сохраняются как ввёл пользователь.
- Все новые строки UI — в `src/utils/training_locales.py` на 7 языках (`LANGS = en, ru, uk, pl, de, tr, es`).
- Ошибка или таймаут LLM не ломает поток: всегда есть путь без AI.
- Группы целей — ключи `focus.FOCUS_GROUPS`: `back, chest, legs, arms, shoulders, abs, fullbody, cardio`.

## Что проверить на ревью
1. **Пустая библиотека + «Составить план»** — ожидается экран «тренажёров нет» с тремя вариантами, а не пустой план и не каталог.
2. **`fullbody` при частичном покрытии** (есть грудь и спина, нет ног) — план собирается из имеющегося; пробел показывается как предупреждение, а не блокирует.
3. **AI предложил тренажёр, который уже есть под другим именем** («Chest press» при наличии «Brustpresse») — дубликат не создаётся (матч по `catalog_key`).
4. **Повторная вставка того же отчёта** — тренажёры не дублируются, мусорных записей нет.
5. **Gemini недоступен (429/таймаут)** в «Подобрать AI» — бот предлагает варианты из каталога без LLM.

Каждый пункт закреплён тестом в соответствующей задаче.

---

## Карта файлов
| Файл | Ответственность |
| --- | --- |
| Create `src/services/training/plan_context.py` | единая загрузка профиля, библиотеки и истории для плана |
| Modify `src/services/training/planner.py` | только библиотека; поиск пробелов покрытия |
| Modify `src/services/training/library.py` | теги мышц для записей; поиск по `catalog_key` |
| Modify `src/services/training/llm.py` | классификация тренажёра, подбор тренажёров по целям, фикс JSON |
| Create `src/services/training/report_text.py` | очистка вставленного отчёта (метаданные, «Не выполнено») |
| Modify `src/services/training/sessions.py` | не создавать тренажёры из нераспознанных строк |
| Create `src/handlers/training/machines_confirm.py` | общий экран «Нашёл тренажёры → Добавить все / Отмена» |
| Modify `src/handlers/training/settings.py` | перевести на общий экран |
| Modify `src/handlers/training/plan.py`, `hub.py` | пробелы покрытия, упрощённый старт, фикс resize |
| Modify `src/handlers/training/log_text.py` | очистка отчёта, разбор новых названий |

---

### Task 1: Единый контекст плана

**Files:**
- Create: `src/services/training/plan_context.py`
- Modify: `src/handlers/training/hub.py:174-209`, `src/handlers/training/plan.py:180-211`
- Modify: `src/services/training/history.py` (батч-загрузка)
- Test: `tests/unit/test_plan_context.py`

**Interfaces:**
- Produces:
  - `@dataclass PlanContext: profile: TrainingProfile | None; library: list[dict]; history_index: dict[str, ExerciseHistory]`
  - `async def load_plan_context(db: AsyncSession, user_id: int) -> PlanContext`
  - `def exercise_to_dict(ex: CanonicalExercise) -> dict` с ключами, которые сейчас собираются вручную (`id, display_name, kind, muscle_primary, muscles_secondary, pattern, equipment, weight_step, min_weight, catalog_key, is_favorite`)
  - `async def get_histories(db, user_id: int, exercise_ids: list[int]) -> dict[int, ExerciseHistory]` в `history.py`: один запрос вместо N

- [ ] **Step 1:** тест `test_exercise_to_dict_keys`: `set(exercise_to_dict(fake_ex).keys())` равно 11 ключам выше.
- [ ] **Step 2:** тест падает: `pytest tests/unit/test_plan_context.py -v` → ImportError.
- [ ] **Step 3:** реализовать модуль; заменить копии в `hub.py` и `plan.py` вызовом `load_plan_context`.
- [ ] **Step 4:** `pytest tests/unit -q` → всё зелёное.
- [ ] **Step 5:** commit `refactor(training): single plan context loader`.

---

### Task 2: Теги мышц для каждого тренажёра библиотеки

Без тегов тренажёр невидим для планировщика. Сначала пробуем каталог, затем LLM.

**Files:**
- Modify: `src/services/training/library.py`, `src/services/training/llm.py`
- Test: `tests/unit/test_machine_tagging.py`

**Interfaces:**
- Produces:
  - `llm.classify_machine(name: str, lang: str) -> dict | None`: возвращает `{"catalog_key": str|None, "muscle_primary": str, "pattern": str, "kind": "strength"|"cardio"}`. `muscle_primary` ∈ объединение значений `FOCUS_GROUPS`, `pattern` ∈ ключи `planner.PATTERN_ORDER`, `catalog_key` ∈ `get_catalog().entries` или `None`; невалидное → `None`. В промпт передаются допустимые значения и список `key: имена` каталога.
  - `library.ensure_tags(db, ex: CanonicalExercise, lang: str) -> bool`: если `muscle_primary` пуст, сначала `catalog.find_by_name`, затем `classify_machine`; заполняет поля, возвращает `True`, если что-то изменилось.
  - `library.tag_untagged(db, user_id: int, lang: str) -> int`: пакетный бэкфилл, не больше 20 за вызов.
  - `library.find_by_catalog_key(db, user_id: int, catalog_key: str) -> CanonicalExercise | None`
- Consumes: `normalize.clean_name`, `catalog.find_by_name`.

- [ ] **Step 1:** тесты:
  - `test_classify_rejects_unknown_muscle`: Gemini замокан и отвечает `{"muscle_primary":"wings",...}` → `None`.
  - `test_classify_accepts_beinbeuger`: мок `{"catalog_key":"leg_curl","muscle_primary":"hamstrings","pattern":"isolation","kind":"strength"}` → тот же dict.
  - `test_ensure_tags_prefers_catalog`: для «Latzug» LLM не вызывается (`AsyncMock.assert_not_called()`), `muscle_primary == "back_lats"`.
- [ ] **Step 2:** тесты падают.
- [ ] **Step 3:** реализовать. Вызвать `ensure_tags` в `get_or_create_canonical` для новых записей (LLM только если каталог не нашёл) и `tag_untagged` при открытии «Составить план».
- [ ] **Step 4:** тесты зелёные.
- [ ] **Step 5:** commit `feat(training): tag library machines with muscles via catalog/LLM`.

---

### Task 3: Планировщик только из библиотеки + пробелы покрытия

**Files:**
- Modify: `src/services/training/planner.py:113-148`
- Modify: `src/services/training/sessions.py:start_from_plan`
- Test: `tests/unit/test_planner_library_only.py`

**Interfaces:**
- Produces:
  - `build_plan(...)` сохраняет сигнатуру, но кандидаты = только `library_exercises`; блок каталога удаляется, `from_catalog_only` уходит из `score_candidate`.
  - `def find_coverage_gaps(library_exercises: list[dict], groups: list[str]) -> list[str]`: группа считается пробелом, если в библиотеке нет ни одной записи с `muscle_primary ∈ FOCUS_GROUPS[g]`; для `cardio` проверяется `kind == "cardio"`; для `fullbody` проверяются группы `chest, back, legs`, и в пробелы попадают сами отсутствующие подгруппы.
  - `sessions.start_from_plan` использует `item["exercise_id"]` (`db.get`) и создаёт запись через `get_or_create_canonical` только если id пуст (обратная совместимость со старыми планами).

- [ ] **Step 1:** тесты:
  - `test_plan_uses_only_library`: библиотека = [Brustpresse(chest), Latzug(back_lats)], фокус `fullbody` → `{i.name for i in plan} ⊆ {"Brustpresse","Latzug"}`.
  - `test_empty_library_gives_empty_plan`: `build_plan(..., library_exercises=[]) == []`.
  - `test_gaps_arms_missing`: библиотека без biceps/triceps → `find_coverage_gaps(lib, ["arms"]) == ["arms"]`.
  - `test_gaps_fullbody_partial`: есть chest и back, нет legs → `["legs"]`.
  - `test_cardio_gap_by_kind`: Ellipsentrainer(kind=cardio) → `find_coverage_gaps(lib, ["cardio"]) == []`.
- [ ] **Step 2:** тесты падают (сейчас каталог попадает в план).
- [ ] **Step 3:** реализовать.
- [ ] **Step 4:** `pytest tests/unit -q` зелёный; обновить golden-тесты планировщика, если они рассчитывали на каталог.
- [ ] **Step 5:** commit `feat(training): plan strictly from user library, report coverage gaps`.

---

### Task 4: Общий экран подтверждения тренажёров

Выносим экран из `settings.py` (задача от 2026-11-09), чтобы им пользовались и «Добавить тренажёр», и подбор под цель.

**Files:**
- Create: `src/handlers/training/machines_confirm.py` (router подключить в `src/handlers/training/__init__.py`)
- Modify: `src/handlers/training/settings.py` (`_propose_extracted_machines`, `handle_ai_machines_confirm`, `handle_ai_machines_cancel` переезжают сюда)
- Test: `tests/unit/test_machines_confirm.py`

**Interfaces:**
- Produces:
  - `async def propose_machines(message: Message, state: FSMContext, names: list[str], return_to: Literal["equipment", "plan"]) -> None`: кладёт в FSM `eq_ai_candidates=names`, `eq_ai_return=return_to` и показывает `eq_ai_found` с кнопками `t2:eq_ai_ok:0:0` и `t2:eq_ai_no:0:0`.
  - После «Добавить все»: каждый тренажёр идёт через `get_or_create_canonical` (уже с тегами из Task 2), с пропуском, если `find_by_catalog_key` нашёл существующий. Затем при `return_to == "equipment"` → `render_equipment_settings`, при `"plan"` → `plan.rebuild_current_plan(callback, state)` (Task 5).
  - `def dedupe_against_library(names: list[str], library: list[dict]) -> list[str]`: убирает имена, совпадающие с библиотекой по нормализованному имени или по `catalog.find_by_name(...)["key"]`.
- [ ] **Step 1:** тест `test_dedupe_by_catalog_key`: библиотека `[{"display_name":"Brustpresse","catalog_key":"chest_press_machine"}]`, `names=["Жим в тренажёре","Bizepsmaschine"]` → `["Bizepsmaschine"]` (пункт ревью 3).
- [ ] **Step 2–4:** red → реализация → green.
- [ ] **Step 5:** commit `refactor(training): shared machine confirmation flow`.

---

### Task 5: «Нет тренажёров для цели» → свои / AI / без них

**Files:**
- Modify: `src/handlers/training/plan.py` (`handle_generate_plan`), `src/handlers/training/hub.py` (`handle_start_workout`)
- Modify: `src/services/training/llm.py`
- Modify: `src/utils/training_locales.py`
- Test: `tests/unit/test_machine_suggestions.py`

**Поток:**
1. Перед `build_plan` вызывается `find_coverage_gaps(ctx.library, selected_groups)`.
2. Если пробелов нет, всё идёт как раньше.
3. Если пробелы есть, показывается карточка `gap_title` («Для целей: Руки — в библиотеке нет тренажёров») с кнопками:
   - `➕ Добавлю сам` → `t2:gap_add:0:0` → состояние `TrainingFSM.equipment_add`, в FSM `eq_ai_return="plan"`; после добавления план пересобирается;
   - `🤖 Подобрать AI` → `t2:gap_ai:0:0`;
   - `▶️ Без них` → `t2:gap_skip:0:0` → план из имеющегося; если план пуст, пишем `gap_empty_plan` и кнопки остаются.
4. В FSM хранится `gap_groups: list[str]`.

**Interfaces:**
- Produces:
  - `def catalog_candidates(groups: list[str], lang: str, limit: int = 6) -> list[str]`: локализованные имена из каталога, у которых `muscle_primary ∈ FOCUS_GROUPS[g]`. Это детерминированный fallback без LLM.
  - `async def llm.suggest_machines(groups: list[str], goals: dict, equipment: str | None, existing: list[str], lang: str) -> list[str]`: на вход кандидаты каталога + цели профиля (`TrainingProfile.goal`, ограничения из `Focus.limitations`); LLM выбирает 2–3 тренажёра на группу, с приоритетом названий из каталога; выход проходит `_sanitize_machine_names`. При ошибке → `[]`.
  - `async def rebuild_current_plan(event, state) -> None` в `plan.py`: повторяет `handle_generate_plan` по `selected_muscles` из FSM.
  - Обработчик `gap_ai`: `names = await llm.suggest_machines(...) or catalog_candidates(...)` → `dedupe_against_library` → `propose_machines(..., return_to="plan")`.
- Ключи локализации: `gap_title`, `gap_add`, `gap_ai`, `gap_skip`, `gap_empty_plan`, `gap_ai_thinking`.

- [ ] **Step 1:** тесты:
  - `test_catalog_candidates_arms`: `catalog_candidates(["arms"], "de")` содержит `"Bizepsmaschine"`, и у всех элементов мышцы ∈ {biceps, triceps}.
  - `test_suggest_falls_back_on_error`: Gemini → `RuntimeError` → `suggest_machines` возвращает `[]`, а обработчик берёт `catalog_candidates` (пункт ревью 5; обработчик тестируется через вынесенную функцию `pick_suggestions(groups, ...) -> list[str]`).
  - `test_suggest_excludes_existing`: мок возвращает `["Latzug","Trizepsmaschine"]`, `existing=["Latzug"]` → `["Trizepsmaschine"]`.
- [ ] **Step 2–4:** red → реализация → green.
- [ ] **Step 5:** ручная проверка в Telegram: пустая библиотека → «Составить план» → «Руки» → «Подобрать AI» → «Добавить все» → план показывает только добавленные тренажёры (пункт ревью 1).
- [ ] **Step 6:** commit `feat(training): ask user or AI to fill missing machines for goal`.

---

### Task 6: Очистка вставленного отчёта и чистый текстовый ввод

**Files:**
- Create: `src/services/training/report_text.py`
- Modify: `src/handlers/training/log_text.py:44-107`, `src/services/training/sessions.py:118-167`, `src/services/training/llm.py:parse_unparsed_lines`
- Test: `tests/unit/test_report_text.py`, фикстура в `tests/fixtures/training_parse_cases.yaml`

**Interfaces:**
- Produces:
  - `@dataclass CleanReport: body: str; feeling: Literal["easy","normal","hard"] | None; note: str | None`
  - `def clean_report(text: str) -> CleanReport`. Правила:
    - строка-заголовок `^(Отчёт о тренировке|Workout report|Trainingsbericht|…)` (все переводы `workout_report` из `src/utils/workout_locales.py`) удаляется;
    - строки с префиксом `Не выполнено:` / `Not done:` / … — это план, а не факт, они удаляются;
    - `Самочувствие: X` → `feeling` (Нормально→normal, Легко→easy, Тяжело→hard; плюс аналоги на других языках);
    - префикс `Примечания:` снимается, остаток остаётся в `body`;
    - хвост `Проанализируй…` / `Analyze…` после точки отрезается и уходит в `note`.
  - `sessions.add_draft_items`: элементы со статусом `unparsed` **не** создают `CanonicalExercise`; их `raw` добавляется в `session.note`.
  - В `log_text.handle_draft_text_input`: `clean_report` → `parse_text(..., lang=user_lang)` → для `ok`-элементов, у которых `library.match_exercise` вернул `"unknown"` или `"catalog"`, ставится `status="new_name"`.
  - Карточка черновика для `new_name` показывает кнопки (не больше 5): `➕ Добавить «X»` (`t2:dn_add:<sid>:<rev>:<line_no>`), `↔️ Это «Y»` (ближайший из библиотеки, `t2:dn_map:…`), `🗑 Пропустить` (`t2:dn_drop:…`). Сохранить черновик можно, только когда `new_name` не осталось.
  - Фикс `parse_unparsed_lines`: `json.loads` строки из `extract_json` (как в `extract_machines_from_text`); `lang` берётся из пользователя.

- [ ] **Step 1:** тесты:
  - `test_clean_report_user_sample`: на вашем отчёте от 2 ноября после `clean_report` + `parse_text` получаются ровно имена `["Ellipsentrainer","Brustpresse","Latzug","Rudermaschine","Beinstrecker","Abduktion","Adduktion","Bizepsmaschine"]`, у Brustpresse 4 подхода по 35×15, `feeling == "normal"`, ни одно имя не содержит `":"`.
  - `test_unparsed_not_added_to_library`: `add_draft_items` с одним `unparsed` → `get_or_create_canonical` не вызывается (мок).
  - `test_same_report_twice_no_duplicates` (интеграционный, `tests/integration`): двойная вставка → число `canonical_exercises` пользователя не растёт после первой (пункт ревью 4).
  - `test_parse_unparsed_lines_uses_llm_json`: мок возвращает строку JSON → элемент получает `source == "llm"`.
- [ ] **Step 2–4:** red → реализация → green.
- [ ] **Step 5:** commit `fix(training): clean pasted reports, stop polluting library`.

---

### Task 7: Проще старт и длина плана

**Files:**
- Modify: `src/handlers/training/plan.py:253-275`, `src/handlers/training/keyboards.py` (`get_plan_card_keyboard`)
- Test: `tests/unit/test_resize_plan.py`

**Interfaces:**
- `t2:resize_menu:<plan_id>:<rev>:<delta>`, где `delta ∈ {-15, 15}`; кнопки «−15 мин» / «+15 мин» на карточке плана.
- `planner.resize_plan` при `+15` добавляет подход первым двум упражнениям с `< 4` подходами (сейчас только одному); при пустом добавлении — следующее упражнение библиотеки по score, если оно есть.
- Обработчик: `commit` вместо `flush`; `constraints["minutes"] += delta`; карточка показывает фактические минуты; проверка `rev` против устаревших нажатий.
- [ ] **Step 1:** тесты: `test_resize_plus_adds_sets` (два упражнения по 3 подхода → после +15 по 4), `test_resize_minus_never_below_two_sets`.
- [ ] **Step 2–4:** red → реализация → green.
- [ ] **Step 5:** commit `fix(training): persistent ±15 min plan resize`.

---

### Task 8: «Всё по плану» в живой тренировке

Самый частый случай — выполнил как запланировано. Сейчас нужно подтверждать каждый подход.

**Files:**
- Modify: `src/handlers/training/live.py`, `src/services/training/sessions.py`, `src/handlers/training/keyboards.py`
- Test: `tests/unit/test_log_all_sets.py`

**Interfaces:**
- `async def sessions.complete_entry_as_planned(db, session_id: int, entry_id: int) -> WorkoutSessionEntry`: все незавершённые подходы с известными весом и повторами (или минутами для кардио) помечаются `completed=True`. Подходы с диапазоном или `None` остаются открытыми, и карточка показывает их как сейчас.
- Кнопка `✅ Всё по плану` → `t2:ok_all:<sid>:<rev>:<entry_id>`, видна, когда у текущего упражнения ≥ 2 незавершённых подходов.
- [ ] **Step 1:** тест: 3 подхода по 35×12 → все `completed`; подход с `weight_kg=None` остаётся `completed=False`.
- [ ] **Step 2–4:** red → реализация → green.
- [ ] **Step 5:** commit `feat(training): one-tap complete exercise as planned`.

---

## Порядок и зависимости
Task 1 → 2 → 3 → 4 → 5 — последовательная цепочка (каждая использует интерфейсы предыдущей). Tasks 6, 7, 8 независимы от 4–5 и друг от друга; их можно делать после Task 1 в любом порядке.

## Вне объёма — решение за вами
1. **Legacy-система** (`/workout`, WebApp, дневные планы, таблица `exercises`). Варианты: (а) заморозить и скрыть `/workout`, оставить t2 единственным путём; (б) синхронизировать `exercises` ↔ `canonical_exercises`; (в) оставить как есть. Пока две библиотеки живут раздельно, тренажёр, добавленный в WebApp, не виден планировщику t2, и наоборот.
2. **Чистка уже созданных мусорных записей** в `canonical_exercises` (имена с `:`, длиннее 60 символов, без записей в `workout_session_entries`) — разовый скрипт после Task 6.
