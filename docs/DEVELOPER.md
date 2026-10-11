# Развёртывание и разработка Queuebot

Исходный код: [MixelTe/queuebot](https://github.com/MixelTe/queuebot).
Каждый оператор создаёт своего Telegram-бота и использует отдельную БД.
Один экземпляр обслуживает любые группы и личные чаты без списка разрешённых чатов.

## Выбор запуска

| Режим | Получение команд | Автоочистка | Требования |
| --- | --- | --- | --- |
| Polling | Исходящие запросы к Telegram | Проверка каждые 30 секунд | Постоянно работающий процесс; домен не нужен. |
| Собственный webhook-сервер | HTTPS → proxy → Waitress | Проверка каждые 30 секунд | Постоянно работающий процесс и публичный HTTPS. |
| WSGI-хостинг | HTTP-запросы к WSGI-приложению | Внешний защищённый `/api/ping` | WSGI и внешний сервис HTTP-проверок; фоновые задачи на хостинге не нужны. |

Нужны Python 3.12 или 3.13, Git, `venv`, исходящий HTTPS-доступ к `api.telegram.org`
и запись в `storage/`. Не запускайте polling и webhook одновременно для одного токена.
Несколько WSGI-воркеров на одном сервере допустимы, если у них общий каталог, БД и
файл `storage/updates.lock`: обновления и обслуживание выполняются последовательно.
Несколько серверов этот механизм не поддерживает.

## Подготовка Telegram

1. Создайте бота через `/newbot` в [@BotFather](https://t.me/BotFather).
2. Включите добавление в группы через `/setjoingroups` → Enable.
   [Настройки BotFather](https://core.telegram.org/bots/features#botfather).
3. В каждой группе назначьте бота администратором с возможностью отправлять,
   закреплять и удалять сообщения. Удаление нужно для временных карточек и режима `\s`.
4. Поделитесь ссылкой `https://t.me/YourQueueBot` с пользователями.

Telegram гарантирует проверку членства других пользователей через `getChatMember`,
когда бот — администратор. [Документация метода](https://core.telegram.org/bots/api#getchatmember).
Privacy Mode можно оставить включённым: бот-администратор получает сообщения группы.
[FAQ Telegram](https://core.telegram.org/bots/faq#what-messages-will-my-bot-get).
Администратор должен писать команды от личного аккаунта: анонимные отправители не поддерживаются.

## Установка из GitHub

Linux, в доступном для записи каталоге (для `/opt` могут потребоваться права администратора):

```sh
git clone https://github.com/MixelTe/queuebot.git /opt/queuebot
cd /opt/queuebot
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp config.example.txt config.txt
cp .env.example .env
```

Windows PowerShell:

```powershell
git clone https://github.com/MixelTe/queuebot.git C:\bots\queuebot
Set-Location C:\bots\queuebot
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.example.txt config.txt
Copy-Item .env.example .env
```

Для Python 3.12 замените номер версии. Окружение создавайте на целевом сервере.
Не запускайте `bafser init_project` повторно: каркас и миграции уже включены в проект.
Все следующие команды выполняйте из корня проекта. На Windows вместо
`.venv/bin/python` и `.venv/bin/bafser` используйте `.\.venv\Scripts\python.exe`
и `.\.venv\Scripts\bafser.exe`.

## Конфигурация

Заполните `config.txt`:

```ini
bot_token = ТОКЕН_ИЗ_BOTFATHER
bot_name = @YourQueueBot
webhook_token = СЛУЧАЙНЫЙ_СЕКРЕТ
url = https://queuebot.example.org/
```

`bot_name` должен точно совпадать с username, включая `@`, для команд вида
`/queue_new@YourQueueBot`. `url` — публичный HTTPS-адрес приложения без `/webhook`.
Для polling последние две строки не используются, но должны присутствовать.

Для webhook задайте случайный `webhook_token`: 1–256 символов `A-Z`, `a-z`, `0-9`, `_`, `-`.
Сгенерировать подходящий секрет:

```sh
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Повторите команду для отдельного `PING_TOKEN`, если нужен внешний ping.
Заглушка из `config.example.txt` не разрешает обработку webhook.
Секрет проверяется по заголовку `X-Telegram-Bot-Api-Secret-Token`.
[Параметры webhook Telegram](https://core.telegram.org/bots/api#setwebhook).

В `.env`:

```dotenv
DBPATH=storage/db/bot.db
DB_MYSQL=0
QUEUE_TIMEZONE=Europe/Moscow
BOT_MODE=polling
BOT_HOST=127.0.0.1
BOT_PORT=5000
PING_TOKEN=
```

`BOT_MODE` выбирает режим `python main.py` без аргументов: `polling` или `webhook`.
Явный аргумент имеет приоритет. `BOT_HOST` и `BOT_PORT` задают адрес Waitress;
на WSGI их игнорируют. Пустой `PING_TOKEN` отключает доступ к ping; для WSGI заполните
его отдельным случайным секретом.

`QUEUE_TIMEZONE` — общий часовой пояс расписаний экземпляра. Он отображается в
карточках. После изменения перезапустите процесс или перезагрузите WSGI-приложение
и проверьте расписания: установленное локальное время сохраняется.

По умолчанию SQLite. Для MySQL создайте отдельную БД с `utf8mb4`, задайте `DB_MYSQL=1`
и `DBPATH=user:password@host/database`. Для пароля со специальными символами можно
оставить `DBPATH` пустым и задать `DB_USER`, `DB_HOST`, `DB_PORT`, `DB_NAME`,
`DB_PASSWORD_FILE`; последний указывает на файл только с паролем.

`config.txt`, `config_dev.txt`, `.env`, `.venv`, `storage/` исключены из Git.
Сохраняйте конфигурацию и `storage/` при обновлении; секреты не публикуйте.

## Инициализация и polling

```sh
.venv/bin/python main.py setup
.venv/bin/python main.py polling
```

`setup` применяет миграции и синхронизирует роли, затем завершается без Telegram-запросов
и фоновых задач. Инициализация также выполняется при старте любого рабочего режима
и импорте `wsgi.py`. Для отдельного применения миграций:

```sh
.venv/bin/bafser alembic upgrade --prod
```

`--prod` выбирает БД из `DBPATH`, включая SQLite. Без флага используется БД разработки.
При установке не создавайте новую `revision`. Обновление БД добавляет таблицы имён,
webhook-обновлений и поле ожидающих обновлений сообщений, сохраняя очереди и участников.

Polling отключает webhook токена без удаления ожидающих обновлений, публикует меню
команд и начинает получение сообщений. Внешний ping в этом режиме не нужен.

## Собственный webhook-сервер

Настройте DNS и HTTPS reverse proxy для `url` с публично доверенным сертификатом.
Proxy должен передавать POST `/webhook`, JSON и секретный заголовок в
`http://127.0.0.1:5000`. TLS завершается на proxy; Waitress слушает локальный HTTP.
Пример внутри настроенного HTTPS-блока Nginx `server`:

```nginx
location /webhook {
    proxy_pass http://127.0.0.1:5000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

```sh
.venv/bin/python main.py webhook
```

Бот регистрирует `url + webhook`, публикует команды и запускает Waitress со встроенным
планировщиком. `max_connections=1` ограничивает одновременную доставку Telegram;
принимаются сообщения и нажатия кнопок. Успешный ответ возвращается после обработки
обновления в HTTP-запросе. Telegram повторяет доставку при неуспешном ответе.
[Документация Telegram](https://core.telegram.org/bots/api#setwebhook).

После выполнения команды бот сохраняет её `update_id` и список карточек, которые
нужно обновить. При ошибке отправки карточки endpoint возвращает 500. Повтор того же
webhook восстанавливает оставшиеся сообщения из актуального состояния БД, сохраняя
уже сделанный ход, запись или перестановку в очереди. ID созданных карточек и
уведомлений сохраняются вместе с прогрессом доставки: повтор редактирует их.
Восстановление работает и после перезапуска WSGI-воркера; фоновые задачи для него не нужны.

Полностью завершённые `update_id` хранятся 7 дней для распознавания повторов;
незавершённая доставка не удаляется по этому сроку. Восстановление запускается при
повторной доставке webhook от Telegram. Ошибки записываются в журналы.
Это не гарантия однократности всех внешних действий: например, Telegram может принять
`sendMessage`, но соединение оборвётся до получения ответа. Сбой внутри самой команды
до сохранения отметки её выполнения также может оставить частично выполненные изменения.

## WSGI-хостинг без фоновых задач

1. Клонируйте репозиторий в каталог пользователя хостинга, создайте virtualenv,
   установите зависимости. Заполните `config.txt`, `.env`, `url` и `PING_TOKEN`.
2. В настройках WSGI-приложения укажите virtualenv и `wsgi.py`. Если хостинг
   предоставляет собственный WSGI-файл, используйте:

   ```python
   import sys

   project = "/home/YOUR_USER/queuebot"
   sys.path.insert(0, project)
   from wsgi import application
   ```

3. Перезагрузите веб-приложение через панель хостинга. `wsgi.py` устанавливает рабочий
   каталог проекта. Импорт не запускает polling, потоки, планировщик или HTTP-сервер;
   `BOT_MODE` здесь не используется. Запросы обслуживает WSGI-сервер хостинга.
4. Один раз из консоли хостинга зарегистрируйте webhook и меню команд:

   ```sh
   cd /home/YOUR_USER/queuebot
   .venv/bin/python main.py set-webhook
   ```

   Команда завершается после настройки. Повторите её после изменения `url`, секрета
   или набора команд; после изменения локальной конфигурации также перезагрузите WSGI.
5. Настройте внешний сервис HTTP-проверок, желательно каждые 30–60 секунд.
   Предпочтительно передавать секрет заголовком:

   ```sh
   curl -fsS -H 'X-Queuebot-Ping-Token: ВАШ_PING_TOKEN' https://queuebot.example.org/api/ping
   ```

   Если сервис принимает только URL:
   `https://queuebot.example.org/api/ping?token=ВАШ_PING_TOKEN`.
   Такой URL может попасть в журнал proxy/хостинга; ограничьте доступ к журналам.
   Endpoint принимает GET и POST; правильный запрос возвращает `200 ok`, неверный
   или пустой секрет — 403.

Каждый ping проверяет расписания и удаляет устаревший служебный кэш внутри запроса.
Без ping команды и кнопки работают, но автоочистка не выполняется. Время очистки
зависит от интервала проверок; после простоя первый ping выполнит одну пропущенную
очистку. Лимит времени HTTP-запроса хостинга должен позволять Telegram-запросы
и обслуживание очередей; для большого числа групп используйте постоянный сервер.

## Постоянный процесс

Для polling и собственного webhook создайте systemd-службу
`/etc/systemd/system/queuebot.service`. Пользователь `queuebot` должен иметь доступ
к проекту и запись в `storage/`:

```ini
[Unit]
Description=Telegram Queuebot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=queuebot
Group=queuebot
WorkingDirectory=/opt/queuebot
ExecStart=/opt/queuebot/.venv/bin/python /opt/queuebot/main.py
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=10
TimeoutStopSec=20
UMask=0077

[Install]
WantedBy=multi-user.target
```

Режим задаёт `BOT_MODE` в `.env`:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now queuebot
sudo systemctl status queuebot
sudo journalctl -u queuebot -f
```

На Windows используйте Планировщик заданий: программа
`C:\bots\queuebot\.venv\Scripts\python.exe`, аргумент `main.py`, рабочий каталог
`C:\bots\queuebot`, запуск при старте компьютера и перезапуск при ошибке.
Запретите параллельные экземпляры. Файл `storage/bot.lock` также препятствует
второму самостоятельному процессу в том же каталоге.

## Обновление и резервные копии

Остановите службу; для WSGI временно отключите приложение и внешний ping.
Сделайте резервную копию БД и конфигурации, затем:

```sh
git pull --ff-only
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py setup
```

При изменении webhook или меню выполните `main.py set-webhook`. Запустите службу
или перезагрузите WSGI и возобновите ping. Проверьте `/start`, `/help` и очереди
в двух разных группах. Обычный участник записывается кнопками; настройки и имена
меняет только администратор.

После простоя выполняется одна пропущенная недельная очистка. Удаление участников
и отметка очистки сохраняются до отправки уведомления одной транзакцией. Ошибка
доставки журналируется и не вызывает повторного удаления новых записей.
Старую карточку можно обновить через `/queue_force_update`.

SQLite копируйте при остановленном приложении либо через SQLite backup API.
Для MySQL используйте его средства резервного копирования. Для восстановления
остановите приложение, верните БД и конфигурацию, затем запустите его.
БД хранит Telegram ID, имена, username, групповые имена, очереди, игры и журнал.
Обычные сообщения группы не сохраняются; карточки очередей и игр сохраняются для
обновления. Срок хранения журналов и копий выбирает оператор; секреты храните отдельно.

## Разработка и диагностика

Для отдельного тестового токена заполните `config_dev.txt` по примеру.
`python main.py polling dev` использует этот файл и `storage/db/dev.db`.
Не используйте рабочий токен для параллельной разработки.

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q bot data utils main.py wsgi.py bafser_config.py
.venv/bin/python -m pip check
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/ruff check bot data utils main.py wsgi.py bafser_config.py tests
```

Тесты используют временную SQLite-БД, настоящие миграции и подменённый Telegram HTTP.
Реальный Telegram, HTTPS/WSGI конкретного хостинга и MySQL проверяйте на сервере.
При изменении моделей обновите БД разработки, создайте следующую миграцию через
`bafser alembic revision` и проверьте её содержимое.

| Симптом | Что проверить |
| --- | --- |
| Бот не отвечает | Токен, доступ к Telegram, процесс или WSGI; `storage/logs/`. |
| Webhook не доставляется | HTTPS, `/webhook`, регистрация через `set-webhook`, секретный заголовок, перезагрузка WSGI после смены конфигурации. |
| Webhook возвращает 403 | Настоящий `webhook_token` совпадает с заголовком; заглушка запрещена. |
| Ping возвращает 403 | Заполнен `PING_TOKEN`; верный заголовок или параметр `token`. |
| Очистка на WSGI не срабатывает | Внешний сервис вызывает ping и получает 200; расписание и часовой пояс верны. |
| Conflict / другой getUpdates | Остановите другие экземпляры с этим токеном. |
| Ошибка блокировки | Каталог использует другой процесс; файл без активной блокировки не мешает запуску. |
| Управление запрещено | Отправитель и бот — администраторы группы; отправитель пишет от личного аккаунта. |
| Не найден пользователь | Проверьте настоящий `@username` или задайте имя ответом на сообщение. |
| Команда с @ игнорируется | `bot_name` соответствует username бота. |
| Карточка не обновляется | Права бота, открытая тема и существующее сообщение; используйте `/queue_force_update`. |
