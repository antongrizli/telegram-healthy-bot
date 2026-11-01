"""Admission messages; command names are stable across languages."""


def install(locales):
    messages = {
        'access_pending': (
            'Your access request is waiting for administrator approval.',
            'Заявка на доступ отправлена. Дождитесь одобрения администратора.',
            'Заявку на доступ надіслано. Дочекайтеся схвалення адміністратора.',
            'Wniosek o dostęp oczekuje na zatwierdzenie administratora.',
            'Deine Zugangsanfrage wartet auf die Freigabe durch einen Administrator.',
            'Erişim isteğiniz yönetici onayını bekliyor.',
            'Tu solicitud de acceso está pendiente de aprobación del administrador.'),
        'access_rejected': (
            'Your access request was declined. Contact the administrator.',
            'Заявка на доступ отклонена. Обратитесь к администратору.',
            'Заявку на доступ відхилено. Зверніться до адміністратора.',
            'Wniosek o dostęp został odrzucony. Skontaktuj się z administratorem.',
            'Deine Zugangsanfrage wurde abgelehnt. Kontaktiere den Administrator.',
            'Erişim isteğiniz reddedildi. Yöneticiyle iletişime geçin.',
            'Tu solicitud de acceso fue rechazada. Contacta al administrador.'),
        'access_blocked': (
            'Access denied. Contact the administrator.', 'Доступ закрыт. Обратитесь к администратору.',
            'Доступ закрито. Зверніться до адміністратора.', 'Brak dostępu. Skontaktuj się z administratorem.',
            'Zugriff verweigert. Kontaktiere den Administrator.', 'Erişim engellendi. Yöneticiyle iletişime geçin.',
            'Acceso denegado. Contacta al administrador.'),
        'access_approved': (
            'Your access has been approved! Send /start to set up your profile.',
            'Доступ одобрен! Отправьте /start, чтобы настроить профиль.',
            'Доступ схвалено! Надішліть /start, щоб налаштувати профіль.',
            'Dostęp zatwierdzony! Wyślij /start, aby skonfigurować profil.',
            'Dein Zugang wurde freigegeben! Sende /start, um dein Profil einzurichten.',
            'Erişiminiz onaylandı! Profilinizi oluşturmak için /start gönderin.',
            '¡Acceso aprobado! Envía /start para configurar tu perfil.'),
        'access_admin_request': (
            '🔐 Access request\n<b>{name}</b> · {username}\nID: <code>{id}</code>\n\n/approve {id}\n/reject {id}\nAll requests: /pending',
            '🔐 Заявка на доступ\n<b>{name}</b> · {username}\nID: <code>{id}</code>\n\n/approve {id}\n/reject {id}\nВсе заявки: /pending',
            '🔐 Заявка на доступ\n<b>{name}</b> · {username}\nID: <code>{id}</code>\n\n/approve {id}\n/reject {id}\nУсі заявки: /pending',
            '🔐 Wniosek o dostęp\n<b>{name}</b> · {username}\nID: <code>{id}</code>\n\n/approve {id}\n/reject {id}\nWszystkie wnioski: /pending',
            '🔐 Zugangsanfrage\n<b>{name}</b> · {username}\nID: <code>{id}</code>\n\n/approve {id}\n/reject {id}\nAlle Anfragen: /pending',
            '🔐 Erişim isteği\n<b>{name}</b> · {username}\nID: <code>{id}</code>\n\n/approve {id}\n/reject {id}\nTüm istekler: /pending',
            '🔐 Solicitud de acceso\n<b>{name}</b> · {username}\nID: <code>{id}</code>\n\n/approve {id}\n/reject {id}\nTodas las solicitudes: /pending'),
        'access_usage': (
            'Commands: /pending [page], /approve ID, /reject ID. Use a private chat.',
            'Команды: /pending [страница], /approve ID, /reject ID. Используйте личный чат.',
            'Команди: /pending [сторінка], /approve ID, /reject ID. Використовуйте приватний чат.',
            'Polecenia: /pending [strona], /approve ID, /reject ID. Użyj czatu prywatnego.',
            'Befehle: /pending [Seite], /approve ID, /reject ID. Nutze einen privaten Chat.',
            'Komutlar: /pending [sayfa], /approve ID, /reject ID. Özel sohbet kullanın.',
            'Comandos: /pending [página], /approve ID, /reject ID. Usa un chat privado.'),
        'access_pending_title': (
            '🔐 Pending requests', '🔐 Заявки на одобрение', '🔐 Заявки на схвалення',
            '🔐 Oczekujące wnioski', '🔐 Offene Anfragen', '🔐 Bekleyen istekler', '🔐 Solicitudes pendientes'),
        'access_pending_empty': (
            'No pending requests on this page.', 'На этой странице нет заявок.', 'На цій сторінці немає заявок.',
            'Brak wniosków na tej stronie.', 'Keine offenen Anfragen auf dieser Seite.',
            'Bu sayfada bekleyen istek yok.', 'No hay solicitudes pendientes en esta página.'),
        'access_decision_done': (
            'Request {id}: {status}.', 'Заявка {id}: {status}.', 'Заявка {id}: {status}.',
            'Wniosek {id}: {status}.', 'Anfrage {id}: {status}.', 'İstek {id}: {status}.', 'Solicitud {id}: {status}.'),
        'access_status_approved': ('approved', 'одобрена', 'схвалена', 'zatwierdzony', 'freigegeben', 'onaylandı', 'aprobada'),
        'access_status_rejected': ('declined', 'отклонена', 'відхилена', 'odrzucony', 'abgelehnt', 'reddedildi', 'rechazada'),
        'access_not_pending': (
            'Request not found or already decided.', 'Заявка не найдена или уже рассмотрена.',
            'Заявку не знайдено або вже розглянуто.', 'Nie znaleziono wniosku lub już go rozpatrzono.',
            'Anfrage nicht gefunden oder bereits bearbeitet.', 'İstek bulunamadı veya zaten karara bağlandı.',
            'Solicitud no encontrada o ya resuelta.'),
        'access_notify_failed': (
            'Decision saved, but the user could not be notified.', 'Решение сохранено, но уведомить пользователя не удалось.',
            'Рішення збережено, але сповістити користувача не вдалося.', 'Decyzja zapisana, ale nie udało się powiadomić użytkownika.',
            'Entscheidung gespeichert, aber die Benachrichtigung konnte nicht zugestellt werden.',
            'Karar kaydedildi ancak kullanıcıya bildirim gönderilemedi.', 'Decisión guardada, pero no se pudo notificar al usuario.'),
    }
    messages.update({
        'btn_access_menu': ('🔐 User access', '🔐 Доступ пользователей', '🔐 Доступ користувачів',
            '🔐 Dostęp użytkowników', '🔐 Benutzerzugang', '🔐 Kullanıcı erişimi', '🔐 Acceso de usuarios'),
        'btn_access_pending': ('⏳ Access requests', '⏳ Заявки на доступ', '⏳ Заявки на доступ',
            '⏳ Wnioski o dostęp', '⏳ Zugangsanfragen', '⏳ Erişim istekleri', '⏳ Solicitudes de acceso'),
        'btn_access_back': ('⬅️ Admin panel', '⬅️ В админ-панель', '⬅️ До адмін-панелі',
            '⬅️ Panel administratora', '⬅️ Adminbereich', '⬅️ Yönetici paneli', '⬅️ Panel de administración'),
        'btn_access_approve': ('✅ Approve', '✅ Одобрить', '✅ Схвалити', '✅ Zatwierdź',
            '✅ Freigeben', '✅ Onayla', '✅ Aprobar'),
        'btn_access_reject': ('❌ Decline', '❌ Отклонить', '❌ Відхилити', '❌ Odrzuć',
            '❌ Ablehnen', '❌ Reddet', '❌ Rechazar'),
        'access_menu_title': ('🔐 User access\nOpen access requests to approve or decline users.',
            '🔐 Доступ пользователей\nОткройте заявки, чтобы одобрить или отклонить доступ.',
            '🔐 Доступ користувачів\nВідкрийте заявки, щоб схвалити або відхилити доступ.',
            '🔐 Dostęp użytkowników\nOtwórz wnioski, aby zatwierdzić lub odrzucić dostęp.',
            '🔐 Benutzerzugang\nÖffne die Anfragen, um den Zugang freizugeben oder abzulehnen.',
            '🔐 Kullanıcı erişimi\nOnaylamak veya reddetmek için erişim isteklerini açın.',
            '🔐 Acceso de usuarios\nAbre las solicitudes para aprobar o rechazar el acceso.'),
        'access_list_back': ('⬅️ User access', '⬅️ Доступ пользователей', '⬅️ Доступ користувачів',
            '⬅️ Dostęp użytkowników', '⬅️ Benutzerzugang', '⬅️ Kullanıcı erişimi', '⬅️ Acceso de usuarios'),
    })
    for key, translations in messages.items():
        for language, text in zip(('en', 'ru', 'uk', 'pl', 'de', 'tr', 'es'), translations):
            locales[language][key] = text
