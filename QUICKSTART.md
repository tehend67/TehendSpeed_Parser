# 🚀 Быстрый старт TehendSpeed Parser

> Запустите парсер за 5 минут

---

## ⚡️ Экспресс-установка

### 1. Установите Python 3.10+
```bash
python --version  # должно быть 3.10 или выше
```

### 2. Клонируйте и установите
```bash
git clone https://github.com/yourusername/TehendSpeed_Parser.git
cd TehendSpeed_Parser
pip install -r requirements.txt
```

### 3. Получите учётные данные

#### API ID и API Hash
1. Перейдите на https://my.telegram.org
2. Войдите с номером телефона
3. "API development tools" → создайте приложение
4. Скопируйте `api_id` и `api_hash`

#### Bot Token
1. Напишите [@BotFather](https://t.me/BotFather)
2. Команда `/newbot`
3. Следуйте инструкциям
4. Скопируйте токен (выглядит как `1234567890:ABCdefGHIjkl...`)

### 4. Настройте .env
```bash
cp .env.example .env
nano .env  # или откройте в любом редакторе
```

Минимальная конфигурация:
```env
API_ID=12345678
API_HASH=abcdef1234567890
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
```

### 5. Запустите
```bash
python main.py
```

При первом запуске бот попросит:
- Номер телефона (для userbot)
- Код из Telegram
- 2FA пароль (если установлен)

### 6. Готово! 🎉
Откройте бота в Telegram и отправьте `/start`

---

## 🎯 Первый парсинг

1. Нажмите **"🔍 Запустить парсинг чатов"**
2. Отправьте ссылку на чат:
   ```
   @test_channel
   ```
3. Дождитесь завершения
4. Экспортируйте результат через **"🗄 База данных"**

---

## ⚠️ Важно

### Требования к userbot
- Должен состоять в чатах, которые парсите
- Для приватных чатов нужно членство
- Не используйте основной аккаунт (риск бана)

### Лимиты Telegram
- Не парсите >10 больших чатов подряд
- При FloodWait бот автоматически ждёт
- Уменьшите `WORKERS_COUNT` до 1-2 если часто FloodWait

### Приватность
- Все данные хранятся локально
- `.env` не коммитьте в git
- Используйте `ADMIN_IDS` для ограничения доступа

---

## 🐛 Частые проблемы

### "Bot token is invalid"
❌ Неверный токен  
✅ Проверьте `BOT_TOKEN` в .env

### "API_ID/API_HASH incorrect"
❌ Неверные credentials  
✅ Получите новые на my.telegram.org

### "No access to chat"
❌ Userbot не состоит в чате  
✅ Добавьте userbot-аккаунт в группу

### "FloodWait: retry in X seconds"
❌ Превышен лимит запросов  
✅ Бот автоматически ждёт и продолжает

---

## 📖 Дальнейшие шаги

- 📚 Прочитайте [README.md](README.md) для полной документации
- 🎬 Посмотрите [README_DEMO.md](README_DEMO.md) для визуальных примеров
- 💬 Напишите [@codepatche](https://t.me/codepatche) если нужна помощь

---

## 🎁 Бонус: Полезные команды

```bash
# Поиск пользователя
/search @username

# Пересечение чатов
/overlap channel1 | channel2

# Якоры (в 3+ чатах)
/anchors 3

# Тёплая аудитория → XLSX
/warm 2

# График роста
/growth @channel

# Автопарсинг каждые 24 часа
/watch add @channel 24
```

---

<div align="center">

**Готово! Теперь вы готовы парсить Telegram 🚀**

Вопросы? → [@codepatche](https://t.me/codepatche) | +380660328245

</div>
