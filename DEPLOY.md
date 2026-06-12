# Деплой LUM'N бота на Ubuntu
---

## Шаг 1 — Подключиться к серверу

На вашем компьютере (Windows) откройте **PowerShell** или **PuTTY** и подключитесь:

```bash
ssh root@ВАШ_IP_АДРЕС
```

Введите пароль, который дал хостинг-провайдер. Вы окажетесь внутри сервера.

---

## Шаг 2 — Обновить систему и установить Python

```bash
# Обновляем список пакетов
apt update && apt upgrade -y

# Устанавливаем Python 3.11, pip, git, утилиты
apt install -y python3.13 python3.13-venv python3-pip git nano curl
```

Проверяем Python:
```bash
python3.13 --version
# Должно вывести: Python 3.11.x
```

---

## Шаг 3 — Создать пользователя для бота (безопаснее, чем root)

```bash
# Создаём пользователя lumln
adduser --disabled-password --gecos "" lumln

# Переходим в его домашнюю папку
su - lumln
```

---

## Шаг 4 — Загрузить код на сервер

### Вариант А: Через Git (если код в репозитории)
```bash
git clone https://github.com/ВАШ_АККАУНТ/lumln.git
cd lumln
```

### Вариант Б: Загрузить с Windows через SCP (без Git)

Откройте **новый** PowerShell на вашем компьютере (не закрывая SSH-сессию):

```powershell
# Загружаем всю папку проекта на сервер
scp -r "D:\path\lumln" root@ВАШ_IP:/home/lumln/
```

Вернитесь в SSH-сессию:
```bash
cd /home/lumln
```

---

## Шаг 5 — Создать виртуальное окружение и установить зависимости

```bash
# Создаём виртуальное окружение
python3.13 -m venv venv

# Активируем его
source venv/bin/activate

# Устанавливаем зависимости
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Шаг 6 — Настроить конфиг (.env)

```bash
# Копируем шаблон
cp .env.example .env

# Открываем редактор
nano .env
```

Заполните файл (стрелками перемещаться, Ctrl+O — сохранить, Ctrl+X — выйти):

```env
BOT_TOKEN=7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ADMIN_IDS=123456789
ADMIN_PASSWORD=придумайте_пароль
YCLIENTS_URL=https://n1234567.yclients.com/...
PRIVACY_URL=https://telegra.ph/...
OFFER_URL=https://telegra.ph/...
ADMIN_PORT=8080
PROXY=BACKUP_CHAT_ID=123456789
BACKUP_HOUR=3
BACKUP_KEEP=3
```

> ⚠️ На европейском/американском сервере `PROXY=` оставьте **пустым** —
> Telegram доступен без прокси.

---

## Шаг 7 — Проверить запуск вручную

```bash
# Убедитесь что вы в папке проекта и venv активирован
source venv/bin/activate
python run.py
```

Если видите строки вроде:
```
INFO     Database ready
INFO     Bot started
INFO     Uvicorn running on http://0.0.0.0:8080
```
— всё работает. Остановите через **Ctrl+C** и переходите к следующему шагу.

---

## Шаг 8 — Настроить автозапуск через systemd

Выйдите из пользователя lumln обратно в root:
```bash
exit
```

Создайте файл службы:
```bash
nano /etc/systemd/system/lumln.service
```

Вставьте содержимое (Ctrl+Shift+V в терминале):

```ini
[Unit]
Description=LUMLN Perfume Bot
After=network.target

[Service]
Type=simple
User=lumln
WorkingDirectory=/home/lumln/
ExecStart=/home/lumln/venv/bin/python run.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Сохраните (Ctrl+O, Enter, Ctrl+X) и активируйте:

```bash
# Перезагружаем systemd
systemctl daemon-reload

# Включаем автозапуск при старте сервера
systemctl enable lumln

# Запускаем прямо сейчас
systemctl start lumln

# Проверяем статус
systemctl status lumln
```

Вы должны увидеть зелёный статус `● lumln.service — LUMLN Perfume Bot` и `Active: active (running)`.

---

## Шаг 9 — Открыть порт для веб-панели (файрвол)

```bash
# Разрешаем SSH (чтобы не потерять доступ)
ufw allow 22

# Разрешаем веб-панель
ufw allow 8080

# Включаем файрвол
ufw enable
```

После этого веб-панель будет доступна по адресу:
```
http://ВАШ_IP:8080
```

---

## Шаг 10 — Настроить Nginx + HTTPS (опционально, но рекомендуется)

Если у вас есть домен (например `lumln-bot.ru`), можно сделать красивый адрес
`https://admin.lumln-bot.ru` вместо `http://IP:8080`.

```bash
# Устанавливаем Nginx и Certbot
apt install -y nginx certbot python3-certbot-nginx

# Создаём конфиг сайта
nano /etc/nginx/sites-available/lumln
```

Вставьте:
```nginx
server {
    listen 80;
    server_name admin.ВАШ_ДОМЕН.ru;

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
    }
}
```

```bash
# Включаем сайт
ln -s /etc/nginx/sites-available/lumln /etc/nginx/sites-enabled/
nginx -t           # проверяем конфиг
systemctl restart nginx

# Получаем HTTPS-сертификат (бесплатно)
certbot --nginx -d admin.ВАШ_ДОМЕН.ru
```

После этого панель будет на `https://admin.ВАШ_ДОМЕН.ru`.

---

## Полезные команды после деплоя

```bash
# Посмотреть живые логи бота
journalctl -u lumln -f

# Перезапустить бота (например после изменений)
systemctl restart lumln

# Остановить бота
systemctl stop lumln

# Посмотреть последние 50 строк логов
journalctl -u lumln -n 50
```

---

## Резервная копия базы данных

База — файл `/home/lumln/cc_lumln/lumln.db`. Скачать на компьютер:

```powershell
# В PowerShell на вашем Windows-компьютере:
scp root@ВАШ_IP:/home/lumln/lumln.db "D:\Backups\lumln_backup.db"
```
---

## Обновление кода (если что-то поменяли)

```bash
# Загрузить новые файлы с компьютера
scp -r "D:\path\lumln" root@ВАШ_IP:/home/lumln/

# Перезапустить бота
systemctl restart lumln
```

---
