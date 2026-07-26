@echo off
chcp 65001 >nul
title AgentRouter WAF Bypass Proxy
echo ==============================================
echo       AgentRouter WAF Bypass Proxy
echo ==============================================
echo.

powershell -Command "$p=(netstat -ano | Select-String ':8318 ' | Select-String 'LISTENING') -replace '.*LISTENING\s+', '' -replace '\s.*', ''; if($p){Stop-Process -Id $p -Force; Write-Host 'Cleared previous process on port 8318'}" >nul 2>&1

echo Список актуальных моделей AgentRouter:
echo.
echo [1] Direct: claude-opus-4-8 - По умолчанию - Нативный + WAF Bypass
echo [2] Bridge: gpt-5.6-sol - ТОП ПО КОДИНГУ - OpenAI Мост
echo [3] Bridge: gpt-5.5 - OpenAI Мост
echo [4] Bridge: glm-5.2 - Быстрый и дешёвый OpenAI Мост
echo [5] Bridge: kimi-k3 - Топ Фронтенд - OpenAI Мост
echo [6] Ввести СВОЁ название модели вручную
echo [7] Выход
echo.
set /p opt="Выберите опцию 1-7 [По умолчанию: 1]: "

if "%opt%"=="2" (
    set AGENTROUTER_BRIDGE=true
    set AGENTROUTER_BRIDGE_MODEL=gpt-5.6-sol
    echo [РЕЖИМ] Мост включён: gpt-5.6-sol - ТОП КОДИРОВАНИЯ
) else if "%opt%"=="3" (
    set AGENTROUTER_BRIDGE=true
    set AGENTROUTER_BRIDGE_MODEL=gpt-5.5
    echo [РЕЖИМ] Мост включён: gpt-5.5
) else if "%opt%"=="4" (
    set AGENTROUTER_BRIDGE=true
    set AGENTROUTER_BRIDGE_MODEL=glm-5.2
    echo [РЕЖИМ] Мост включён: glm-5.2
) else if "%opt%"=="5" (
    set AGENTROUTER_BRIDGE=true
    set AGENTROUTER_BRIDGE_MODEL=kimi-k3
    echo [РЕЖИМ] Мост включён: kimi-k3
) else if "%opt%"=="6" (
    set AGENTROUTER_BRIDGE=true
    set /p custom_model="Введите название модели: "
    set AGENTROUTER_BRIDGE_MODEL=%custom_model%
    echo [РЕЖИМ] Мост включён: %custom_model%
) else if "%opt%"=="7" (
    exit
) else (
    set AGENTROUTER_BRIDGE=false
    echo [РЕЖИМ] Прямой claude-opus-4-8 - Нативный + WAF Bypass
)

echo.
echo Запуск прокси на http://127.0.0.1:8318 ...
python "%~dp0agentrouter_proxy.py"
pause
