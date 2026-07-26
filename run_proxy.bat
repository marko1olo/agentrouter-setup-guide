@echo off
title AgentRouter WAF Bypass Proxy
echo ==============================================
echo       AgentRouter WAF Bypass Proxy
echo ==============================================
echo.

powershell -Command "$p=(netstat -ano | Select-String ':8318 ' | Select-String 'LISTENING') -replace '.*LISTENING\s+', '' -replace '\s.*', ''; if($p){Stop-Process -Id $p -Force; Write-Host 'Cleared previous process on port 8318'}" >nul 2>&1

echo Выберите режим работы / модель:
echo.
echo [1] Direct Claude Opus (Нативный режим с WAF-байпасом)
echo [2] Bridge -> gpt-5.5 (Мощный флагман OpenAI)
echo [3] Bridge -> glm-5.2 (Быстрая и дешёвая модель)
echo [4] Bridge -> claude-3-5-sonnet (Через OpenAI мост)
echo [5] Bridge -> claude-3-5-haiku (Через OpenAI мост)
echo [6] Bridge -> deepseek-r1 (Рассуждающая модель)
echo [7] Ввести СВОЁ название модели вручную
echo [8] Выход
echo.
set /p opt="Выберите опцию (1-8) [По умолчанию: 1]: "

if "%opt%"=="2" (
    set AGENTROUTER_BRIDGE=true
    set AGENTROUTER_BRIDGE_MODEL=gpt-5.5
    echo [РЕЖИМ] Мост включён -> gpt-5.5
) else if "%opt%"=="3" (
    set AGENTROUTER_BRIDGE=true
    set AGENTROUTER_BRIDGE_MODEL=glm-5.2
    echo [РЕЖИМ] Мост включён -> glm-5.2
) else if "%opt%"=="4" (
    set AGENTROUTER_BRIDGE=true
    set AGENTROUTER_BRIDGE_MODEL=claude-3-5-sonnet
    echo [РЕЖИМ] Мост включён -> claude-3-5-sonnet
) else if "%opt%"=="5" (
    set AGENTROUTER_BRIDGE=true
    set AGENTROUTER_BRIDGE_MODEL=claude-3-5-haiku
    echo [РЕЖИМ] Мост включён -> claude-3-5-haiku
) else if "%opt%"=="6" (
    set AGENTROUTER_BRIDGE=true
    set AGENTROUTER_BRIDGE_MODEL=deepseek-r1
    echo [РЕЖИМ] Мост включён -> deepseek-r1
) else if "%opt%"=="7" (
    set AGENTROUTER_BRIDGE=true
    set /p custom_model="Введите название модели (например, qwen-2.5-coder): "
    set AGENTROUTER_BRIDGE_MODEL=%custom_model%
    echo [РЕЖИМ] Мост включён -> %custom_model%
) else if "%opt%"=="8" (
    exit
) else (
    set AGENTROUTER_BRIDGE=false
    echo [РЕЖИМ] Прямой Claude Opus (Нативный + WAF Bypass)
)

echo.
echo Запуск прокси на http://127.0.0.1:8318 ...
python "%~dp0agentrouter_proxy.py"
pause
