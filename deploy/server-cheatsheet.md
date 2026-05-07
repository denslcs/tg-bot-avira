# Шпаргалка: сервер и боты Shard Creator

Условия: Linux VPS, пользователь `root`, проект `/root/shard-creator-bot`, службы `shard-creator-main-bot` и `shard-creator-support-bot`.

---

## Support-группа (форум)

- **Сообщения по тикетам** (текст от пользователей и служебные пометки) уходят **только в созданные темы** (`message_thread_id`), не в General.
- **Анонимные отзывы** после оценки тикета копятся в отдельной теме: задай `SUPPORT_FEEDBACK_THREAD_ID` или оставь `0` — бот создаст тему «Отзывы (анонимно)» при первом отзыве (id хранится в `bot_meta`).
- В **General** по-прежнему: общение, команды (`/sla`, `/report`, …), фоновые **SLA** (интервал `SLA_ALERT_INTERVAL_HOURS`, по умолчанию 8 ч) и **еженедельная автосводка**.

## Основной бот в админ-группе (топики)

- Ответы пользователю из тем шлёт **support-бот**. У **основного** бота по умолчанию пересылка из топиков **выключена** (`MAIN_BOT_RELAY_SUPPORT_TOPICS=0` в `.env` или переменная не задана).
- Команды основного бота в группе: `/admin@Юзернейм_основного_бота` (или отключи «Group Privacy» у бота в @BotFather, если нужны команды без `@бот`).

## Подключиться с ПК (PowerShell)

```powershell
ssh root@95.164.53.78
```

Пароль не отображается при вводе — это нормально.

---

## Остановить / запустить / перезапустить ботов (systemd)

```bash
sudo systemctl stop shard-creator-main-bot shard-creator-support-bot
sudo systemctl start shard-creator-main-bot shard-creator-support-bot
sudo systemctl restart shard-creator-main-bot shard-creator-support-bot
```

Проверка без «листателя»:

```bash
sudo systemctl status shard-creator-main-bot --no-pager
sudo systemctl status shard-creator-support-bot --no-pager
```

Включены ли в автозапуск:

```bash
sudo systemctl is-enabled shard-creator-main-bot shard-creator-support-bot
```

---

## Если что-то сломалось — логи

Последние 80 строк:

```bash
sudo journalctl -u shard-creator-main-bot -n 80 --no-pager
sudo journalctl -u shard-creator-support-bot -n 80 --no-pager
```

Лог «в реальном времени» (выйти: Ctrl+C):

```bash
sudo journalctl -u shard-creator-main-bot -f
```

После правки unit-файлов:

```bash
sudo systemctl daemon-reload
sudo systemctl restart shard-creator-main-bot shard-creator-support-bot
```

---

## Ручной запуск (только для отладки)

Сначала останови systemd, иначе будет два процесса одного бота:

```bash
sudo systemctl stop shard-creator-main-bot
# или оба: sudo systemctl stop shard-creator-main-bot shard-creator-support-bot
```

Потом:

```bash
cd /root/shard-creator-bot
source venv/bin/activate
python -m src.bot
```

Во втором SSH-окне — support:

```bash
cd /root/shard-creator-bot
source venv/bin/activate
python -m src.support_bot
```

Закончил отладку — Ctrl+C, снова:

```bash
sudo systemctl start shard-creator-main-bot shard-creator-support-bot
```

---

## Обновить код с GitHub (если проект клонирован)

Команды ниже выполняй **на сервере** после `ssh` (в PowerShell на ПК команда `systemctl` не сработает).

```bash
cd /root/shard-creator-bot
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart shard-creator-main-bot shard-creator-support-bot
```

---

## Чеклист безопасного релиза

Перед `restart` на сервере:

```bash
cd /root/shard-creator-bot
source venv/bin/activate
python -m compileall -q src tests
python -m src.selfcheck
python -m unittest tests.test_subscription_flows tests.test_payments_stars_rules -v
```

После перезапуска:

```bash
sudo systemctl status shard-creator-main-bot --no-pager
sudo systemctl status shard-creator-support-bot --no-pager
sudo journalctl -u shard-creator-main-bot -n 80 --no-pager
```

Проверить в боте вручную:
- экран `/start` и меню открываются без ошибок;
- покупка Stars отрабатывает и шлёт сообщение пользователю;
- в админ-чат продаж приходит уведомление;
- профиль отражает новый срок подписки/кредиты.

---

## Правка `.env` на сервере

```bash
nano /root/shard-creator-bot/.env
```

После сохранения:

```bash
sudo systemctl restart shard-creator-main-bot shard-creator-support-bot
```

---

## Перезагрузка VPS

```bash
sudo reboot
```

Через 1–2 минуты снова `ssh`, затем проверка:

```bash
sudo systemctl status shard-creator-main-bot --no-pager
sudo systemctl status shard-creator-support-bot --no-pager
```

---

## Типичные проблемы

| Симптом | Что сделать |
|--------|-------------|
| Бот не отвечает | `status` и `journalctl` (см. выше) |
| «Conflict» / дубли | Убедиться, что нет второго `python -m` вручную; `stop` сервисов и снова `start` |
| Менял код — старая версия | `git pull`, `restart` сервисов |
| Менял токены | правка `.env`, `restart` сервисов |

---

## PostgreSQL cutover (safe two-phase)

1) Подготовь PostgreSQL и проверь доступ:

```bash
psql "postgresql://user:password@127.0.0.1:5432/tg_bot_avira" -c "select 1;"
```

2) В `.env` оставь пока sqlite как основной backend:

```env
DB_BACKEND=sqlite
DATABASE_URL=postgresql://user:password@127.0.0.1:5432/tg_bot_avira
```

3) Сними бэкап sqlite:

```bash
cd /root/shard-creator-bot
cp data/bot.sqlite3 data/bot.sqlite3.bak_$(date +%F_%H%M%S)
```

4) Инициализируй схему в Postgres (одноразово):

```bash
cd /root/shard-creator-bot
source venv/bin/activate
DB_BACKEND=postgres DATABASE_URL="postgresql://user:password@127.0.0.1:5432/tg_bot_avira" python -m src.selfcheck
```

5) Выполни миграцию данных в Postgres:

```bash
cd /root/shard-creator-bot
source venv/bin/activate
python scripts/migrate_sqlite_to_postgres.py \
  --sqlite-path data/bot.sqlite3 \
  --postgres-url "postgresql://user:password@127.0.0.1:5432/tg_bot_avira"
```

6) Окно переключения (короткий даунтайм):

```bash
sudo systemctl stop shard-creator-main-bot shard-creator-support-bot
# повтори миграцию для финальной дельты
python scripts/migrate_sqlite_to_postgres.py --sqlite-path data/bot.sqlite3 --postgres-url "postgresql://user:password@127.0.0.1:5432/tg_bot_avira"
```

7) Переключи backend:

```env
DB_BACKEND=postgres
DATABASE_URL=postgresql://user:password@127.0.0.1:5432/tg_bot_avira
```

8) Запуск и проверка:

```bash
sudo systemctl start shard-creator-main-bot shard-creator-support-bot
cd /root/shard-creator-bot
source venv/bin/activate
python -m src.selfcheck
sudo journalctl -u shard-creator-main-bot -n 120 --no-pager
```

9) Быстрый rollback (если есть проблемы):

```env
DB_BACKEND=sqlite
```

```bash
sudo systemctl restart shard-creator-main-bot shard-creator-support-bot
```

---

## Файлы systemd (если нужно поправить пути)

```bash
sudo nano /etc/systemd/system/shard-creator-main-bot.service
sudo nano /etc/systemd/system/shard-creator-support-bot.service
sudo systemctl daemon-reload
sudo systemctl restart shard-creator-main-bot shard-creator-support-bot
```
