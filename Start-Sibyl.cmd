@echo off
rem Start-Sibyl.cmd: the double-click way to run Start-Sibyl.ps1.
rem
rem Windows refuses to run PowerShell scripts by default ("running scripts is
rem disabled on this system"). This file asks PowerShell to run Start-Sibyl.ps1
rem with that rule lifted for this one run only. Nothing on your PC is changed.
rem
rem Double-click it to start Sibyl. Or, from a Command Prompt or PowerShell in
rem this folder, add an option:  Start-Sibyl.cmd check   (or install, hotkeys,
rem mic, ollama, uninstall), exactly as Start-Sibyl.ps1 describes.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Sibyl.ps1" %*
rem A double-click opens a window that would close before you could read it.
if "%~1"=="" pause
