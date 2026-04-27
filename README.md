# HR Telegram Bot

MVP для HR-рассылок сотрудникам через Telegram: регистрация сотрудников, отправка объявлений, кнопки ответов, вопросы сотрудников, статистика и экспорт отчетов.

## Возможности

- Регистрация сотрудника в Telegram через `/start`.
- HR веб-панель с логином и паролем.
- Telegram-админка через `/admin` для сотрудников с ролью `Админ`.
- Telegram-суперпользователь из `.env` автоматически получает полный доступ.
- Список сотрудников, фильтры по отделу и должности.
- Рассылка всем, по отделу, по должности или выбранным сотрудникам.
- Суперюзер не получает обычные HR-рассылки.
- При рассылке из Telegram-админки отправитель не получает собственную рассылку.
- Кнопки под сообщением: `Таныстым`, `Келістім`, `Сұрағым бар`.
- Сохранение ответов и вопросов в базе.
- Ответ HR сотруднику через Telegram.
- Excel/PDF отчеты по каждой рассылке.
- SQLite по умолчанию, PostgreSQL через `DATABASE_URL`.

## Запуск

Скопируйте пример настроек, если `.env` еще нет:

```powershell
Copy-Item .env.example .env
```

Укажите токен Telegram-бота:

```env
BOT_TOKEN=123456:telegram-token
```

Укажите Telegram ID суперпользователя:

```env
SUPERUSER_TELEGRAM_ID=123456789
```

Запуск через Docker:

```powershell
docker compose up --build
```

Запуск без Docker:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
New-Item -ItemType Directory -Force data
uvicorn app.main:app --reload
```

Админ-панель:

```text
http://localhost:8000
```

Логин/пароль по умолчанию:

```text
admin
admin123
```

## База данных

SQLite по умолчанию:

```env
DATABASE_URL=sqlite+aiosqlite:///./data/hr_bot.db
```

Файл базы:

```text
./data/hr_bot.db
```

PostgreSQL поддерживается через:

```env
DATABASE_URL=postgresql+asyncpg://hr_bot:hr_bot_password@localhost:5432/hr_bot
```

Postgres через Docker:

```powershell
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up --build
```

## Telegram-админка

1. Сотрудник регистрируется в боте через `/start`.
2. HR или суперюзер выдает роль админа.
3. Админ пишет боту `/admin`.

В Telegram-админке доступны статистика, список сотрудников с ID, создание рассылки всем, по отделу, по должности или выбранным ID, просмотр вопросов и ответ сотруднику прямо из Telegram.

## Суперпользователь

Суперпользователь задается в `.env`:

```env
SUPERUSER_TELEGRAM_ID=123456789
```

Когда этот пользователь пройдет регистрацию через `/start`, ему автоматически выдадутся роли `Админ` и `Суперюзер`. В `/admin` у него есть раздел `Роли админов`, где можно выдавать и снимать админские роли другим сотрудникам.

## Очистка чата

```text
/clear
/clear 200
```

Команда пытается удалить последние сообщения в текущем чате. Максимум за один запуск: 500 сообщений. Telegram может не разрешить удалить часть старых сообщений или сообщений, на которые у бота нет прав.

## Структура

```text
app/
  bot.py          Telegram bot handlers
  config.py       Environment settings
  database.py     Async SQLAlchemy setup
  main.py         FastAPI app and admin routes
  models.py       Database models
  reports.py      Excel/PDF export helpers
  services.py     Business logic
  static/         CSS
  templates/      HTML templates
```
