# 🚀 AgentRouter Setup Guide & WAF Bypass Proxy

![Python](https://img.shields.io/badge/python-3670A0?style=for-the-badge&logo=python&logoColor=ffdd54) ![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi) ![VS Code](https://img.shields.io/badge/VS_Code-0078D4?style=for-the-badge&logo=visual%20studio%20code&logoColor=white) ![Cursor](https://img.shields.io/badge/Cursor-000000?style=for-the-badge&logo=cursor&logoColor=white)

Полное руководство по настройке и использованию **бесплатного API** (Claude Opus 4.8, GPT-5.6-sol, 5.6) от шлюза AgentRouter в ваших любимых ИИ-редакторах. Включает локальный Python-прокси для обхода WAF (ошибки `HTTP 401 Unauthorized` и `BrokenPipeError`).

---

## 📋 Оглавление
- [🎁 Регистрация и получение API-ключа](#-регистрация-и-получение-api-ключа)
- [🛡️ Установка локального прокси (Обход WAF)](#-установка-локального-прокси-обход-waf)
- [🔌 Интеграции с редакторами](#-интеграции-с-редакторами)
  - [1. Cline / Roo Code (VS Code)](#1-cline--roo-code-vs-code)
  - [2. Официальный плагин Claude Code (VS Code)](#2-официальный-плагин-claude-code-vs-code)
  - [3. GitHub Copilot (VS Code)](#3-github-copilot-vs-code)
  - [4. Cursor IDE](#4-cursor-ide)
  - [5. Claude App (Десктоп)](#5-claude-app-десктоп)
  - [6. Claude Code CLI (Консоль)](#6-claude-code-cli-консоль)
- [⚠️ Предупреждение](#️-предупреждение)

---

## 🎁 Регистрация и получение API-ключа

AgentRouter временно раздает огромные стартовые бонусы для новых пользователей.

1. Перейдите на сайт [AgentRouter](https://agentrouter.org/register?aff=KM29) *(с бонусом +$50)*.
2. АККАУНТ ДОЛЖЕН БЫТЬ СТАРШЕ 3 ДЕКАБРЯ 2025 ГОДА ИНАЧЕ НИЧЕ НЕ ДАДУТ!!!
3. Авторизуйтесь через свой аккаунт **GitHub**.
4. **Бонусы:** Вы получите **$200** за регистрацию через GitHub, **$50** бонусом за вход, и **$25** будет начисляться каждый день за вход в панель.
5. В левом меню перейдите в **API Keys** → **Create Key**.
6. Задайте имя (например, `vscode-key`) и скопируйте полученный токен (начинается на `sk-...`).

7. Каждый день на AgentRouter дают +$25 баланса! Чтобы забрать бонус, не нужно менять API-ключ или заново настраивать прокси. Достаточно просто на сайте AgentRouter выйти из своего аккаунта и войти заново (Log out -> Log in) — $25 баланса мгновенно зачисляются на твой счет каждый день!

---

## 🛡️ Установка локального прокси (Обход WAF)

Серверы AgentRouter используют строгий WAF, блокирующий сторонние приложения по HTTP-заголовкам. Локальный прокси на Python перехватывает трафик, подменяет заголовки (User-Agent: `codex_cli_rs`) и обеспечивает чистый стриминг без обрывов.

### Шаг 1. Зависимости
Убедитесь, что у вас установлен Python (обязательно отметьте `Add Python to PATH` при установке). Откройте терминал и выполните:
```bash
pip install fastapi uvicorn httpx
```

В Linux лучше использовать пакеты из репозиториев вместо pip.

Например для Ubuntu/Debian: 

`sudo apt install python3-fastapi python3-uvicorn python3-https`

### Шаг 2. Запуск
1. Сохраните скрипт `agentrouter_proxy.py` (находится в этом репозитории) на свой компьютер.
2. Запустите его в консоли:
```bash
python agentrouter_proxy.py
```
3. Прокси запустится по адресу: `http://127.0.0.1:8318`. **Держите консоль открытой**, пока пользуетесь ИИ.

### Опционально (Linux): systemd-сервис для постоянной работы в фоне

```bash
# Создаём директорию с пользовательскими сервисами
mkdir -p ~/.config/systemd/user
```
В файл `~/.config/systemd/user/agentrouter_proxy.service` записываем следующее:

```ini
[Unit]
Description=AgentRouter Proxy Service
After=network.target

[Service]
Type=simple
# Путь к директории со скриптом
WorkingDirectory=/home/user/agentrouter-setup-guide

# Расположение python можно узнать при помощи which python
ExecStart=/usr/bin/python agentrouter_proxy.py

Restart=always
RestartSec=5

StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target

```
Далее перезугражаем сервисы и запускаем наш только что созданный:

```bash
systemctl --user daemon-reload
systemctl --user enable --now agentrouter_proxy.service
```

---

## 🔌 Интеграции с редакторами и агентами

Во всех настройках ниже мы пускаем трафик через наш локальный прокси.

### 1. Cline / Roo Code (VS Code)
1. Откройте панель расширения в VS Code.
2. Нажмите на шестеренку (Settings) → **API Provider**.
3. Выберите провайдера **Anthropic**.
4. **Base URL:** `http://127.0.0.1:8318`
5. **API Key:** Ваш ключ `sk-...`
6. **Model:** Вручную пропишите `claude-opus-4-8` или `gpt-5-5-turbo`.

### 2. Официальный плагин Claude Code (VS Code)
Откройте параметры пользователя (`Cmd/Ctrl+Shift+P` → `Open User Settings (JSON)`) и добавьте:
```json
{
  "claudeCode.environmentVariables": [
    { "name": "ANTHROPIC_AUTH_TOKEN", "value": "ВАШ_КЛЮЧ" },
    { "name": "ANTHROPIC_BASE_URL", "value": "http://127.0.0.1:8318" },
    { "name": "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY", "value": "1" },
    { "name": "ANTHROPIC_MODEL", "value": "claude-opus-4-8" }
  ],
  "claudeCode.disableLoginPrompt": true,
  "claudeCode.initialPermissionMode": "acceptEdits"
}
```
*Затем в настройках самого расширения включите **Disable Login Prompt** и перезапустите редактор.*

### 3. GitHub Copilot (VS Code)
1. Откройте боковую панель Copilot Chat.
2. Перейдите в **Управление моделями** (Manage Models) → **Добавить модель** (Add Model) → **Пользовательская конечная точка** (Custom Endpoint).
3. **Название:** `AgentRouter`
4. **API Key:** Ваш ключ `sk-...`
5. **Формат:** 
   - Для Claude Opus выбирайте `Messages`
   - Для GPT-5.6-sol выбирайте `Chat Completions`
6. **Model ID:** `claude-opus-4-8` или `gpt-5.6-sol`
7. **Base URL:** `http://127.0.0.1:8318` (для ## 🔀 Аддендум: Прямой Claude Opus & Мост Anthropic → OpenAI & Обход WAF

### 4. OpenCode
В конфигурационный файл `~/.config/opencode/opencode.jsonc` добавить следующее в блок `providers`:
```json
    "agentrouter": {
      "npm": "@ai-sdk/anthropic",
      "name": "agentrouter",
      "options": {
        "baseURL": "http://127.0.0.1:8318/v1",
        "apiKey": "sk-***" // Ваш ключ
      },
      "models": {
        "claude-opus-4-6": {
          "name": "Claude Opus 4.6",
        },
        "claude-opus-4-7": {
          "name": "Claude Opus 4.7",
        },
        "claude-opus-4-8": {
          "name": "Claude Opus 4.8",
        },
      }
    }
```

> **Актуально на 21 июля 2026:**
> 1. **ОПУС ПОЧИНИЛИ!** Разработчики AgentRouter устранили панику `interface conversion: interface {} is nil` при работе с инструментами (Tool Calling). Теперь оригинальные модели `claude-opus-*` работают стабильно напрямую.
> 2. В связи с этим наш локальный прокси **по умолчанию работает в режиме прямого проксирования Claude** (`AGENTROUTER_BRIDGE=false`). Вы получаете чистый нативный Claude Opus.
> 3. При этом в прокси встроен автоматический **WAF-байпас (Cyrillic-Bypass)**, который спасает от ошибок `500 sensitive_words_detected` при отправке больших системных промптов (вроде Мастер-Конспектов).

### 🛠️ 1. Как работает обход WAF (Cyrillic-Bypass)
Прокси прозрачно выполняет двунаправленную замену:
- При отправке запроса на AgentRouter все английские буквы `c` в тексте промпта заменяются на визуально идентичную русскую `с`. Это ломает сигнатуры WAF, и запросы гарантированно проходят с кодом `200 OK`.
- При получении ответа от модели все русские `с` заменяются обратно на английские `c`. В итоге Claude Code получает синтаксически идеальный английский код без повреждения символов.

### 🌉 2. Мост на OpenAI (gpt-5.6-sol / glm-5.2)
Если вам по какой-то причине нужно перевести трафик на OpenAI-совместимые модели, вы можете активировать мост (перевод Anthropic `/v1/messages` -> OpenAI `/v1/chat/completions`):
- Модель **`gpt-5.6-sol`** (мощная модель, отлично понимает системные промпты и вызовы инструментов).
- Модель **`glm-5.2`** (отличается высокой скоростью и низкой стоимостью).

#### Как управлять режимами
Быстрее всего запускать прокси через `run_proxy.bat` на Рабочем столе (там есть интерактивное меню). 

Если вы запускаете вручную, используйте переменные окружения:

**Для Windows (PowerShell):**
```powershell
# Запуск чистого Claude Opus (по умолчанию):
$env:AGENTROUTER_BRIDGE="false"
python agentrouter_proxy.py

# Мост на gpt-5.6-sol:
$env:AGENTROUTER_BRIDGE="true"
$env:AGENTROUTER_BRIDGE_MODEL="gpt-5.6-sol"
python agentrouter_proxy.py

# Мост на GLM-5.2:
$env:AGENTROUTER_BRIDGE="true"
$env:AGENTROUTER_BRIDGE_MODEL="glm-5.2"
python agentrouter_proxy.py
```

**Для Linux / macOS (Bash):**
```bash
# Запуск чистого Claude Opus (по умолчанию):
export AGENTROUTER_BRIDGE="false"
python agentrouter_proxy.py
```

### 📋 Поддерживаемые функции
- ✅ Обычный текстовый диалог (со стримингом и без)
- ✅ Сложные системные промпты (включая Мастер-Конспекты)
- ✅ Вызовы инструментов (Tool Use / Function Calling) — чтение, запись и редактирование файлов в нативном и мостовом режимах
- ✅ Мультимодальность (передача изображений)
- ✅ Переопределение параметров `temperature`, `top_p`, `max_tokens`�рументов (Tool Calling). Попытка запустить оригинальный Claude Code / Cline напрямую приводит к системной панике на их сервере (`Panic detected, error: interface conversion: interface {} is nil`). 
> 2. Кроме того, WAF AgentRouter жестко блокирует длинные промпты (особенно системные инструкции) с ошибкой `500 sensitive_words_detected`.

Наш прокси решает обе проблемы полностью **в автоматическом режиме**:

### 🛠️ 1. Автоматический обход WAF (Cyrillic-Bypass)
Прокси прозрачно выполняет двунаправленную замену:
- При отправке запроса на AgentRouter все английские буквы `c` в тексте промпта заменяются на визуально идентичную русскую `с`. Это ломает сигнатуры WAF, и запросы гарантированно проходят с кодом `200 OK`.
- При получении ответа от модели все русские `с` заменяются обратно на английские `c`. В итоге Claude Code получает синтаксически идеальный английский код без повреждения символов.

### 🌉 2. Мост Anthropic → OpenAI (gpt-5.6-sol / glm-5.2)
Поскольку оригинальный Claude с инструментами падает в панику на стороне AgentRouter, прокси по умолчанию переводит все вызовы Anthropic `/v1/messages` в OpenAI-совместимый формат:
- По умолчанию запросы направляются на **`gpt-5.6-sol`** (мощную модель, отлично понимающую системные промпты и вызовы инструментов).
- Вы также можете использовать модель **`glm-5.2`** (отличается высокой скоростью и низкой стоимостью).

#### Как переключать модели
Модели переключаются через переменную окружения `AGENTROUTER_BRIDGE_MODEL` при запуске прокси:

**Для Windows (PowerShell):**
```powershell
# Переключить на GLM-5.2 (работает быстро и дешево):
$env:AGENTROUTER_BRIDGE_MODEL="glm-5.2"
python agentrouter_proxy.py

# Вернуть gpt-5.6-sol (по умолчанию):
$env:AGENTROUTER_BRIDGE_MODEL="gpt-5.6-sol"
python agentrouter_proxy.py
```

**Для Linux / macOS (Bash):**
```bash
# Переключить на GLM-5.2:
export AGENTROUTER_BRIDGE_MODEL="glm-5.2"
python agentrouter_proxy.py
```

### 📋 Поддерживаемые функции моста
- ✅ Обычный текстовый диалог (со стримингом и без)
- ✅ Сложные системные промпты (включая Мастер-Конспекты)
- ✅ Вызовы инструментов (Tool Use / Function Calling) — чтение, запись и редактирование файлов
- ✅ Мультимодальность (передача изображений)
- ✅ Переопределение параметров `temperature`, `top_p`, `max_tokens`


> 💡 **Ежедневный бонус $25 баланса:**
> Каждый день на AgentRouter дают **+$25 баланса**! Чтобы забрать бонус, **не нужно менять API-ключ** или заново настраивать прокси. Достаточно просто на сайте AgentRouter **выйти из своего аккаунта и войти заново (Log out -> Log in)** — $25 баланса мгновенно зачисляются на твой счет каждый день!




## 💰 Актуальный список моделей AgentRouter (5 моделей)

| ID Модели | Тип эндпоинта | Model Ratio | Completion Ratio | Вход (Prompt) | Выход (Completion) |
|---|---|---|---|---|---|
| `claude-opus-4-8` | Anthropic / OpenAI | 4.0x | 5.0x | **$8.00** / 1M | **$40.00** / 1M |
| `gpt-5.6-sol` | OpenAI | 2.0x | 5.0x | **$4.00** / 1M | **$20.00** / 1M |
| `gpt-5.6-sol` | OpenAI | 3.0x | 1.5x | **$6.00** / 1M | **$9.00** / 1M |
| `glm-5.2` | OpenAI | 2.0x | 2.0x | **$4.00** / 1M | **$8.00** / 1M |
| `kimi-k3` | OpenAI | 2.0x | 2.0x | **$4.00** / 1M | **$8.00** / 1M |


---

## 🌐 Connected Ecosystem & Sister Projects

Part of the **Адольф Петушков (Adolf Petushkov)** open-source engineering ecosystem:

| Project | Domain | Live Demo & Description |
| :--- | :--- | :--- |
| 🦷 **[DENTE CRM](https://github.com/marko1olo/dental-crm)** | Clinical AI | [Live Demo](https://marko1olo.github.io/dental-crm/) — Enterprise FDI odontogram, ICD-10 diagnostics & 3D DICOM |
| 📡 **[StomChat](https://github.com/marko1olo/stomchat)** | Clinical AI | [Live Demo](https://marko1olo.github.io/stomchat/) — Omni-channel dental operator chat dispatcher (WA/TG) & telemetry |
| 🤖 **[Avito Dental AI](https://github.com/marko1olo/avito-dental-ai-bot)** | Clinical AI | [Live Demo](https://marko1olo.github.io/avito-dental-ai-bot/) — Zero-hallucination lead intake bot with deterministic veto layer |
| 🛡️ **[AgentRouter](https://github.com/marko1olo/agentrouter-setup-guide)** | Dev Tools | [Live Demo](https://marko1olo.github.io/agentrouter-setup-guide/) — Claude Code CLI WAF bypass proxy, homoglyph sanitizer & config matrix |
| 📊 **[Token Audit](https://github.com/marko1olo/token-audit)** | Dev Tools | [Live Demo](https://marko1olo.github.io/token-audit/) — Real-time LLM token cost waterfall & cyberpunk chronicles |
| 🎛️ **[Nexus Media](https://github.com/marko1olo/nexus-media-engine)** | Audio DSP | [Live Demo](https://marko1olo.github.io/nexus-media-engine/) — Real-time Web Audio DSP, 60 FPS FFT visualizer & ambilight |
| 📻 **[dvachbot](https://github.com/marko1olo/dvachbot)** | Media Pipeline | [Live Demo](https://marko1olo.github.io/dvachbot/) — Async imageboard stream transcoder & Telegram publisher |
| 🌊 **[Hecton-8](https://github.com/marko1olo/Hecton8)** | Game Engine | [Live Demo](https://marko1olo.github.io/Hecton8/) — NASA-punk deep sea noir submarine engine on Unity 6000 (0B GC) |
| 🏢 **[Gigahrush](https://github.com/marko1olo/gigahrush)** | Game Engine | [Live Demo](https://marko1olo.github.io/gigahrush/) — 2.5D DDA raycasting, cellular gas physics & Samosbor Web CLI |
| 🌌 **[Starcluster](https://github.com/Jirnyak/starcluster)** | Deep Tech | [Live Demo](https://jirnyak.github.io/starcluster/) — 10,000-star N-body gravitational simulation & Keplerian economy |
| 🧲 **[OOMMF](https://github.com/Jirnyak/oommf)** | Deep Tech | [Live Demo](https://jirnyak.github.io/oommf/) — Landau-Lifshitz-Gilbert 3D micromagnetic vector lattice |
| 🍏 **[Macromac](https://github.com/Jirnyak/macromac)** | Automation | [Live Demo](https://jirnyak.github.io/macromac/) — macOS HID event injection, JSON macro schemas & CoreGraphics |

### 👨‍💻 Author & Lead Architect
**Адольф Петушков (Adolf Petushkov)** — Game Engine Internals, Autonomous AI Systems, Zero-GC High-Concurrency Architecture.  
GitHub: [@marko1olo](https://github.com/marko1olo)
