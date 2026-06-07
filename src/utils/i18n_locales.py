LOCALES = {
    "en": {
        "welcome": "Welcome to *Healthy Tracker Bot*! 🍏\n\nI will help you log your food (via photo or text), track your weight, and send you daily/weekly/monthly reports with personalized recommendations using AI.\n\nLet's configure your profile first. Click *Set Up Profile* to start!",
        "btn_setup_profile": "⚙️ Set Up Profile",
        "btn_log_food": "📝 Log Food",
        "btn_log_weight": "⚖️ Log Weight",
        "btn_my_profile": "👤 My Profile",
        "btn_daily_report": "📅 Daily Report",
        "btn_weekly_report": "🗓️ Weekly Report",
        "btn_help": "ℹ️ Help",
        
        "profile_prompt_name": "Please enter your first name:",
        "profile_prompt_sex": "Select your biological sex:",
        "profile_prompt_age": "Please enter your age (in years, e.g., 28):",
        "profile_prompt_height": "Please enter your height (in cm, e.g., 175):",
        "profile_prompt_weight": "Please enter your weight (in kg, e.g., 72.5):",
        "profile_prompt_activity": "Select your physical activity level:",
        "profile_prompt_goal": "Select your fitness goal:",
        "profile_prompt_language": "Choose your default language:",
        "profile_prompt_reminders": "Do you want to enable daily/weekly reminder notifications?",
        "profile_prompt_report_time": "What time would you like to receive your *daily report*? (format HH:MM, e.g., 21:00):",
        "profile_prompt_timezone": "Please choose your timezone. By default, UTC is used. Click the button below to view a list of common timezones, or write your timezone name (e.g. Europe/London, US/Eastern):",
        
        "invalid_age": "⚠️ Invalid age. Please enter a valid number (e.g., 25):",
        "invalid_height": "⚠️ Invalid height. Please enter a number in cm (e.g., 180):",
        "invalid_weight": "⚠️ Invalid weight. Please enter a number in kg (e.g., 75.4):",
        "invalid_time": "⚠️ Invalid time format. Please write in HH:MM format (e.g., 21:30):",
        "invalid_timezone": "⚠️ Invalid timezone. Please choose from the list or write a valid timezone name (e.g., Europe/London):",
        "btn_timezone_list": "🌐 Select timezone",
        
        "profile_complete": "✅ *Profile Setup Complete!*\n\nBased on your settings, your estimated daily allowances are:\n• *Target Calories*: {calories} kcal\n• *Protein*: {protein}g\n• *Fat*: {fat}g\n• *Carbs*: {carb}g\n\nYou can start logging your food and weight now!",
        
        "profile_view": "👤 *Your Profile*:\n\n• *Name*: {name}\n• *Sex*: {sex}\n• *Age*: {age} years\n• *Height*: {height} cm\n• *Weight*: {weight} kg\n• *Activity Level*: {activity}\n• *Goal*: {goal}\n• *Language*: {language}\n• *Timezone*: {timezone}\n\n📊 *Targets*:\n• *Calories*: {target_calories} kcal\n• *Protein*: {target_protein}g | *Fat*: {target_fat}g | *Carbs*: {target_carb}g\n\n🔔 *Notifications*: {notifications}\n• *Daily Report*: {report_time}\n• *Weekly Report*: Sunday at 21:00\n• *Monthly Report*: 1st of month at 21:00",
        
        "food_prompt": "Send me a photo of your meal 📸 or describe what you ate in a text message ✍️:",
        "food_analyzing": "🔄 Analyzing your food with Gemini AI... Please wait.",
        "food_analysis_result": "🍽️ *Food Analysis*:\n\n{items}\n\n🔥 *Estimated Totals*:\n• *Calories*: {calories} kcal\n• *Protein*: {protein}g | *Fat*: {fat}g | *Carbs*: {carb}g\n\nDo you accept these estimates or need to correct them?",
        
        "btn_accept": "✅ Accept",
        "btn_correct": "✏️ Correct / Edit",
        "btn_cancel": "❌ Cancel",
        
        "food_correction_prompt": "Please describe what is incorrect (e.g. 'Actually it was 200g of potatoes and no oil'):",
        "food_logged": "✅ Food logged successfully!",
        "food_cancelled": "❌ Food log cancelled.",
        
        "weight_prompt": "Please write your current weight in kg (e.g., 73.2):",
        "weight_logged": "⚖️ Weight logged: *{weight} kg*.\n{weight_diff_str}\nKeep up the great work!",
        "weight_diff_gain": "📈 You gained *{diff:.1f} kg* since your initial baseline weight ({baseline:.1f} kg).",
        "weight_diff_loss": "📉 You lost *{diff:.1f} kg* since your initial baseline weight ({baseline:.1f} kg).",
        "weight_diff_same": "⚖️ No weight change since your initial baseline weight ({baseline:.1f} kg).",
        
        "weekly_report_header": "🗓️ *Weekly Summary & Report*",
        "monthly_report_header": "📅 *Monthly Summary & Report*",
        
        "report_calculating": "🔄 Compiling logs and generating your AI recommendations...",
        
        "help_text": "🍏 *How to use Healthy Tracker Bot*:\n\n1. *Log Food*: Send a text description of your meal or upload a picture. Review the AI-generated calories and macros, and click Accept (or Correct to change them).\n2. *Log Weight*: Use the main menu to submit your daily weight. The bot calculates your weight trends.\n3. *View Profile*: Check your profile, targets, and edit setup.\n4. *Get Reports*: Generate a daily or weekly report on demand via the main menu.\n\nIf you have any issues, feel free to use /start to restart the bot.",
        
        "admin_only": "❌ Only admins can use this command.",
        "admin_welcome": "👑 *Admin Panel*\nUse the buttons or commands to manage the bot.",
        "user_blocked": "🚫 User has been blocked.",
        "user_unblocked": "✅ User has been unblocked.",
        "broadcast_prompt": "Please send the message text you wish to broadcast to all users:",
        "broadcast_sent": "📢 Broadcast message sent to {count} users.",
        "broadcast_failed": "⚠️ Failed to send broadcast message.",
        
        "daily_reminder": "🔔 Hey! Don't forget to log your meals today to stay on track with your calorie targets!",
        
        "sex_male": "Male",
        "sex_female": "Female",
        "act_sedentary": "Sedentary (No exercise)",
        "act_light": "Lightly Active (1-3 days/wk)",
        "act_moderate": "Moderately Active (3-5 days/wk)",
        "act_active": "Very Active (6-7 days/wk)",
        "goal_lose": "Lose Weight",
        "goal_maintain": "Maintain Weight",
        "goal_gain_w": "Gain Weight",
        "goal_gain_m": "Gain Muscle",
        "lang_en": "English",
        "lang_ru": "Russian",
        "yes": "Yes",
        "no": "No",
        
        "enabled": "Enabled",
        "disabled": "Disabled"
    },
    "ru": {
        "welcome": "Добро пожаловать в *Healthy Tracker Bot*! 🍏\n\nЯ помогу вам записывать еду (по фото или тексту), отслеживать вес и получать ежедневные/еженедельные/ежемесячные отчеты с персональными рекомендациями с помощью ИИ.\n\nДавайте сначала настроим ваш профиль. Нажмите *Настроить профиль*, чтобы начать!",
        "btn_setup_profile": "⚙️ Настроить профиль",
        "btn_log_food": "📝 Записать еду",
        "btn_log_weight": "⚖️ Записать вес",
        "btn_my_profile": "👤 Мой профиль",
        "btn_daily_report": "📅 Дневной отчет",
        "btn_weekly_report": "🗓️ Недельный отчет",
        "btn_help": "ℹ️ Помощь",
        
        "profile_prompt_name": "Пожалуйста, введите ваше имя:",
        "profile_prompt_sex": "Выберите ваш биологический пол:",
        "profile_prompt_age": "Пожалуйста, введите ваш возраст (в годах, например, 28):",
        "profile_prompt_height": "Пожалуйста, введите ваш рост (в см, например, 175):",
        "profile_prompt_weight": "Пожалуйста, введите ваш вес (в кг, например, 72.5):",
        "profile_prompt_activity": "Выберите уровень вашей физической активности:",
        "profile_prompt_goal": "Выберите вашу фитнес-цель:",
        "profile_prompt_language": "Выберите язык бота:",
        "profile_prompt_reminders": "Включить напоминания (дневные/недельные)?",
        "profile_prompt_report_time": "В какое время вы хотите получать *дневной отчет*? (формат ЧЧ:ММ, например, 21:00):",
        "profile_prompt_timezone": "Пожалуйста, выберите ваш часовой пояс. По умолчанию используется UTC. Нажмите кнопку ниже для просмотра списка популярных часовых поясов или напишите название вашего часового пояса (например, Europe/Moscow, US/Eastern):",
        
        "invalid_age": "⚠️ Неверный формат возраста. Пожалуйста, введите число (например, 25):",
        "invalid_height": "⚠️ Неверный формат роста. Пожалуйста, введите число в см (например, 180):",
        "invalid_weight": "⚠️ Неверный формат веса. Пожалуйста, введите число в кг (например, 75.4):",
        "invalid_time": "⚠️ Неверный формат времени. Напишите в формате ЧЧ:ММ (например, 21:30):",
        "invalid_timezone": "⚠️ Неверный часовой пояс. Пожалуйста, выберите из списка или напишите корректное название (например, Europe/Moscow):",
        "btn_timezone_list": "🌐 Выбрать часовой пояс",
        
        "profile_complete": "✅ *Настройка профиля завершена!*\n\nНа основе ваших данных, ваши суточные нормы составляют:\n• *Цель по калориям*: {calories} ккал\n• *Белки*: {protein} г\n• *Жиры*: {fat} г\n• *Углеводы*: {carb} г\n\nТеперь вы можете начать записывать еду и вес!",
        
        "profile_view": "👤 *Ваш профиль*:\n\n• *Имя*: {name}\n• *Пол*: {sex}\n• *Возраст*: {age} лет\n• *Рост*: {height} см\n• *Вес*: {weight} кг\n• *Активность*: {activity}\n• *Цель*: {goal}\n• *Язык*: {language}\n• *Часовой пояс*: {timezone}\n\n📊 *Цели*:\n• *Калории*: {target_calories} ккал\n• *Белки*: {target_protein} г | *Жиры*: {target_fat} г | *Углеводы*: {target_carb} г\n\n🔔 *Уведомления*: {notifications}\n• *Дневной отчет*: {report_time}\n• *Недельный отчет*: Воскресенье в 21:00\n• *Ежемесячный отчет*: 1-е число месяца в 21:00",
        
        "food_prompt": "Отправьте мне фотографию блюда 📸 или опишите его текстом ✍️:",
        "food_analyzing": "🔄 Анализирую еду с помощью Gemini AI... Пожалуйста, подождите.",
        "food_analysis_result": "🍽️ *Анализ еды*:\n\n{items}\n\n🔥 *Итоговые оценки*:\n• *Калории*: {calories} ккал\n• *Белки*: {protein} г | *Жиры*: {fat} г | *Углеводы*: {carb} г\n\nВы принимаете эти оценки или хотите исправить их?",
        
        "btn_accept": "✅ Принять",
        "btn_correct": "✏️ Исправить",
        "btn_cancel": "❌ Отмена",
        
        "food_correction_prompt": "Опишите, что именно не так (например, 'На самом деле там было 200г картошки и без масла'):",
        "food_logged": "✅ Еда успешно записана!",
        "food_cancelled": "❌ Запись еды отменена.",
        
        "weight_prompt": "Пожалуйста, введите ваш текущий вес в кг (например, 73.2):",
        "weight_logged": "⚖️ Вес записан: *{weight} кг*.\n{weight_diff_str}\nПродолжайте в том же духе!",
        "weight_diff_gain": "📈 Вы набрали *{diff:.1f} кг* с момента первой записи ({baseline:.1f} кг).",
        "weight_diff_loss": "📉 Вы сбросили *{diff:.1f} кг* с момента первой записи ({baseline:.1f} кг).",
        "weight_diff_same": "⚖️ Вес не изменился с момента первой записи ({baseline:.1f} кг).",
        
        "weekly_report_header": "🗓️ *Недельный отчет и анализ*",
        "monthly_report_header": "📅 *Ежемесячный отчет и анализ*",
        
        "report_calculating": "🔄 Собираю логи и составляю ИИ-рекомендации...",
        
        "help_text": "🍏 *Как пользоваться Healthy Tracker Bot*:\n\n1. *Запись еды*: Отправьте текстовое описание вашей еды или загрузите фотографию. Проверьте рассчитанные ИИ калории и макросы, затем нажмите Принять (или Исправить для корректировки).\n2. *Запись веса*: Используйте главное меню для записи веса. Бот рассчитывает динамику изменений.\n3. *Просмотр профиля*: Проверьте ваш профиль, целевые показатели и измените настройки.\n4. *Получение отчетов*: Создайте дневной или недельный отчет по запросу через главное меню.\n\nЕсли у вас возникли проблемы, вы можете использовать /start, чтобы перезапустить бота.",
        
        "admin_only": "❌ Только администраторы могут использовать эту команду.",
        "admin_welcome": "👑 *Панель администратора*\nИспользуйте кнопки или команды для управления ботом.",
        "user_blocked": "🚫 Пользователь заблокирован.",
        "user_unblocked": "✅ Пользователь разблокирован.",
        "broadcast_prompt": "Пожалуйста, отправьте текст сообщения, которое вы хотите разослать всем пользователям:",
        "broadcast_sent": "📢 Сообщение разослано {count} пользователям.",
        "broadcast_failed": "⚠️ Не удалось разослать сообщение.",
        
        "daily_reminder": "🔔 Привет! Не забудьте записать сегодняшнюю еду, чтобы контролировать свою норму калорий!",
        
        "sex_male": "Мужской",
        "sex_female": "Женский",
        "act_sedentary": "Сидячий (Без тренировок)",
        "act_light": "Легкая активность (1-3 дн/нед)",
        "act_moderate": "Умеренная активность (3-5 дн/нед)",
        "act_active": "Высокая активность (6-7 дн/нед)",
        "goal_lose": "Сбросить вес",
        "goal_maintain": "Поддерживать вес",
        "goal_gain_w": "Набрать вес",
        "goal_gain_m": "Набрать мышечную массу",
        "lang_en": "Английский",
        "lang_ru": "Русский",
        "yes": "Да",
        "no": "Нет",
        
        "enabled": "Включены",
        "disabled": "Выключены"
    }
}

def get_text(key: str, lang: str = "en", **kwargs) -> str:
    lang = lang if lang in LOCALES else "en"
    text = LOCALES[lang].get(key, LOCALES["en"].get(key, key))
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text
