# 🚀 AgentRouter Setup Guide & WAF Bypass Proxy

![Python](https://img.shields.io/badge/python-3670A0?style=for-the-badge&logo=python&logoColor=ffdd54) ![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi) ![VS Code](https://img.shields.io/badge/VS_Code-0078D4?style=for-the-badge&logo=visual%20studio%20code&logoColor=white) ![Cursor](https://img.shields.io/badge/Cursor-000000?style=for-the-badge&logo=cursor&logoColor=white)

Полное руководство по настройке и использованию **бесплатного API** (Claude Opus 4.8, GPT-5.5) от шлюза AgentRouter в ваших любимых ИИ-редакторах. Включает локальный Python-прокси для обхода WAF (ошибки `HTTP 401 Unauthorized` и `BrokenPipeError`).

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
4. **Бонусы:** Вы получите **$125** за регистрацию через GitHub, **$50** бонусом за вход, и **$25** будет начисляться каждый день за вход в панель.
5. В левом меню перейдите в **API Keys** → **Create Key**.
6. Задайте имя (например, `vscode-key`) и скопируйте полученный токен (начинается на `sk-...`).

---

## 🛡️ Установка локального прокси (Обход WAF)

Серверы AgentRouter используют строгий WAF, блокирующий сторонние приложения по HTTP-заголовкам. Локальный прокси на Python перехватывает трафик, подменяет заголовки (User-Agent: `codex_cli_rs`) и обеспечивает чистый стриминг без обрывов.

### Шаг 1. Зависимости
Убедитесь, что у вас установлен Python (обязательно отметьте `Add Python to PATH` при установке). Откройте терминал и выполните:
```bash
pip install fastapi uvicorn httpx
```

### Шаг 2. Запуск
1. Сохраните скрипт `agentrouter_proxy.py` (находится в этом репозитории) на свой компьютер.
2. Запустите его в консоли:
```bash
python agentrouter_proxy.py
```
3. Прокси запустится по адресу: `http://127.0.0.1:8318`. **Держите консоль открытой**, пока пользуетесь ИИ.

---

## 🔌 Интеграции с редакторами

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
   - Для GPT-5.5 выбирайте `Chat Completions`
6. **Model ID:** `claude-opus-4-8` или `gpt-5.5`
7. **Base URL:** `http://127.0.0.1:8318` (для Claude) или `http://127.0.0.1:8318/v1` (для GPT).

### 4. Cursor IDE
1. Перейдите в настройки Cursor → раздел **Models**.
2. Отключите `Cursor Tab`.
3. Включите **Override Base URL** и пропишите: `http://127.0.0.1:8318` (или `/v1` для OpenAI формата).
4. Добавьте свой API ключ и сохраните.

### 5. Claude App (Десктоп)
1. Включите режим разработчика: `Help` → `Troubleshooting` → `Enable developer mode`.
2. Зайдите в появившиеся сетевые настройки:
   - **Gateway base URL:** `http://127.0.0.1:8318`
   - **Gateway API key:** Ваш ключ `sk-...`
   - **Gateway auth scheme:** `bearer`
3. Нажмите `Apply locally` → `Relaunch now`. В левом нижнем углу появится выбор модели.

### 6. Claude Code CLI (Консоль)
Откройте терминал (PowerShell от имени администратора) и настройте переменные среды:
```powershell
[System.Environment]::SetEnvironmentVariable('ANTHROPIC_BASE_URL', 'http://127.0.0.1:8318', 'User')
[System.Environment]::SetEnvironmentVariable('ANTHROPIC_API_KEY', 'ВАШ_КЛЮЧ', 'User')
[System.Environment]::SetEnvironmentVariable('ANTHROPIC_MODEL', 'claude-opus-4-8', 'User')
```

**Фикс вечной авторизации (Not logged in):**
```powershell
New-Item -ItemType Directory -Force -Path "$HOME\.claude"
Set-Content -Path "$HOME\.claude\.credentials.json" -Value '{"hasCompletedOnboarding": true}'
```

---

## ⚠️ Предупреждение
Этот репозиторий создан в образовательных целях. Проверяйте исходный код Python-прокси перед запуском на своем ПК. Использование API сторонних шлюзов подразумевает передачу им вашего контекста — не используйте его для обработки строго конфиденциального или NDA-кода.
