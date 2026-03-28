# Шпаргалка: сервер и боты Avira

Условия: Linux VPS, пользователь `root`, проект `/root/avira-bot`, службы `avira-main-bot` и `avira-support-bot`.

---

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
sudo systemctl stop avira-main-bot avira-support-bot
sudo systemctl start avira-main-bot avira-support-bot
sudo systemctl restart avira-main-bot avira-support-bot
```

Проверка без «листателя»:

```bash
sudo systemctl status avira-main-bot --no-pager
sudo systemctl status avira-support-bot --no-pager
```

Включены ли в автозапуск:

```bash
sudo systemctl is-enabled avira-main-bot avira-support-bot
```

---

## Если что-то сломалось — логи

Последние 80 строк:

```bash
sudo journalctl -u avira-main-bot -n 80 --no-pager
sudo journalctl -u avira-support-bot -n 80 --no-pager
```

Лог «в реальном времени» (выйти: Ctrl+C):

```bash
sudo journalctl -u avira-main-bot -f
```

После правки unit-файлов:

```bash
sudo systemctl daemon-reload
sudo systemctl restart avira-main-bot avira-support-bot
```

---

## Ручной запуск (только для отладки)

Сначала останови systemd, иначе будет два процесса одного бота:

```bash
sudo systemctl stop avira-main-bot
# или оба: sudo systemctl stop avira-main-bot avira-support-bot
```

Потом:

```bash
cd /root/avira-bot
source venv/bin/activate
python -m src.bot
```

Во втором SSH-окне — support:

```bash
cd /root/avira-bot
source venv/bin/activate
python -m src.support_bot
```

Закончил отладку — Ctrl+C, снова:

```bash
sudo systemctl start avira-main-bot avira-support-bot
```

---

## Обновить код с GitHub (если проект клонирован)

Команды ниже выполняй **на сервере** после `ssh` (в PowerShell на ПК команда `systemctl` не сработает).

```bash
cd /root/avira-bot
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart avira-main-bot avira-support-bot
```

---

## Правка `.env` на сервере

```bash
nano /root/avira-bot/.env
```

После сохранения:

```bash
sudo systemctl restart avira-main-bot avira-support-bot
```

---

## Перезагрузка VPS

```bash
sudo reboot
```

Через 1–2 минуты снова `ssh`, затем проверка:

```bash
sudo systemctl status avira-main-bot --no-pager
sudo systemctl status avira-support-bot --no-pager
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

## Файлы systemd (если нужно поправить пути)

```bash
sudo nano /etc/systemd/system/avira-main-bot.service
sudo nano /etc/systemd/system/avira-support-bot.service
sudo systemctl daemon-reload
sudo systemctl restart avira-main-bot avira-support-bot
```
