# Start-Sibyl.ps1
#
# Launcher and test runner for Sibyl. Right-click this file and choose
# "Run with PowerShell", or from a PowerShell window:
#
#     .\Start-Sibyl.ps1            starts Sibyl normally
#     .\Start-Sibyl.ps1 check      runs every self-test that needs no microphone
#     .\Start-Sibyl.ps1 hotkeys    checks which keys can be detected
#     .\Start-Sibyl.ps1 mic        records 5 seconds to prove the microphone works
#     .\Start-Sibyl.ps1 ollama     checks the AI runtime is up and models installed
#     .\Start-Sibyl.ps1 install    desktop icon, and optionally start with Windows
#     .\Start-Sibyl.ps1 uninstall  removes both shortcuts
#
# Exists because the raw command is long and easy to get wrong, and because
# Windows PowerShell 5.1 rejects '&&' as a separator, which is a confusing
# parser error rather than a helpful message.

param([string]$Mode = "run")

Set-Location -Path $PSScriptRoot

function Test-Prerequisites {
    Write-Host "Checking prerequisites..." -ForegroundColor Cyan
    $ok = $true

    if (Get-Command python -ErrorAction SilentlyContinue) {
        Write-Host ("  [ok]   python " + (python --version 2>&1)) -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] python not found on PATH" -ForegroundColor Red
        $ok = $false
    }

    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -UseBasicParsing -TimeoutSec 5 | Out-Null
        Write-Host "  [ok]   Ollama is running" -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] Ollama is not running. Open a new window and run:  ollama serve" -ForegroundColor Red
        $ok = $false
    }
    return $ok
}

switch ($Mode.ToLower()) {

    "check" {
        if (-not (Test-Prerequisites)) { break }
        Write-Host "`n--- Hotkey logic (no keyboard needed) ---" -ForegroundColor Cyan
        python test_taplistener.py
        Write-Host "`n--- Hold to talk, tap for hands-free ---" -ForegroundColor Cyan
        python test_talk.py
        Write-Host "`n--- Recall: sending the last output again ---" -ForegroundColor Cyan
        python test_recall.py
        Write-Host "`n--- The notch (moves your mouse pointer for a few seconds) ---" -ForegroundColor Cyan
        python test_pill.py
        Write-Host "`n--- The tidy-up keeps bullets and paragraphs ---" -ForegroundColor Cyan
        python test_fillers.py
        Write-Host "`n--- Nothing left behind after a recording ---" -ForegroundColor Cyan
        python test_guard.py
        Write-Host "`n--- No console: the log, and problems shown red ---" -ForegroundColor Cyan
        python test_launch.py
        Write-Host "`n--- Scoring the speech models: the arithmetic ---" -ForegroundColor Cyan
        python test_eval_stt.py
        Write-Host "`n--- Ollama and the model ---" -ForegroundColor Cyan
        python sibyl.py --test-ollama
        Write-Host "`n--- Clean-up on sample text ---" -ForegroundColor Cyan
        python sibyl.py --test-llm "um so I need to uh buy milk eggs and bread no wait not bread I mean butter and then call the bank"
        # Last on purpose, and the only test here whose exit code is inspected. The
        # others report code health and a human reads their output; this one guards
        # a push that cannot be taken back. It sat in the middle until review on
        # 2026-09-25 pointed out that two more sections printed after its warning,
        # so the red line scrolled off exactly the screenful people actually read.
        Write-Host "`n--- The publish gate: nothing private reaches a release ---" -ForegroundColor Cyan
        python test_release.py
        if ($LASTEXITCODE -ne 0) {
            Write-Host "`n  *** THE PUBLISH GATE'S OWN TESTS FAILED. Do not publish. ***`n" -ForegroundColor Red
        }
    }

    "hotkeys" {
        Write-Host "Hold Right Ctrl + Right Alt and let go; then tap them quickly. Ctrl+C to stop.`n" -ForegroundColor Cyan
        python sibyl.py --test-hotkeys
    }

    "mic" { python sibyl.py --test-audio }

    "ollama" { python sibyl.py --test-ollama }

    "run" {
        if (-not (Test-Prerequisites)) {
            Write-Host "`nFix the above, then run this again." -ForegroundColor Yellow
            break
        }
        Write-Host "`nStarting Sibyl." -ForegroundColor Green
        Write-Host "  HOLD Right Ctrl + Right Alt, speak, let go  ->  your words, cleaned up, pasted"
        Write-Host "  TAP  them for hands-free; tap again to send"
        Write-Host "  Quit from the tray icon near the clock, or press Ctrl+C here.`n"
        python -u sibyl.py
    }

    "install" {
        # pythonw.exe is python.exe without a console window. It sits beside
        # python.exe, so ask python where it lives rather than searching PATH,
        # which on Windows can also hold a Microsoft Store stand-in.
        # Asked first, and on its own line: with python missing, the assignment
        # below fails, $pythonw stays empty, and Test-Path then throws inside
        # the 'if', so neither branch runs and install carries on to claim
        # success with a broken shortcut. Measured 2026-09-19.
        if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
            Write-Host "  [FAIL] python not found on PATH" -ForegroundColor Red
            break
        }
        $pythonw = python -c "import os, sys; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"
        if (-not $pythonw -or -not (Test-Path $pythonw)) {
            Write-Host "  [FAIL] pythonw.exe not found beside python.exe: $pythonw" -ForegroundColor Red
            break
        }
        $shell = New-Object -ComObject WScript.Shell
        $places = @([Environment]::GetFolderPath("Desktop"))
        $answer = Read-Host "Start Sibyl when Windows starts? (y/n)"
        if ($answer -match "^[yY]") { $places += [Environment]::GetFolderPath("Startup") }
        foreach ($place in $places) {
            $link = $shell.CreateShortcut((Join-Path $place "Sibyl.lnk"))
            $link.TargetPath = $pythonw
            $link.Arguments = "`"$PSScriptRoot\sibyl.py`""
            $link.WorkingDirectory = $PSScriptRoot
            $link.IconLocation = "$PSScriptRoot\sibyl.ico"
            $link.Description = "Hold Right Ctrl + Right Alt, speak, let go"
            $link.Save()
            Write-Host "  [ok]   $(Join-Path $place 'Sibyl.lnk')" -ForegroundColor Green
        }
        Write-Host "`nDouble-click Sibyl on the desktop. No window opens: look for the dot above the taskbar."
        Write-Host "The log is in $env:LOCALAPPDATA\Sibyl\sibyl.log. It never holds what you said."
    }

    "uninstall" {
        foreach ($folder in @("Desktop", "Startup")) {
            $path = Join-Path ([Environment]::GetFolderPath($folder)) "Sibyl.lnk"
            if (Test-Path $path) {
                Remove-Item $path
                Write-Host "  [ok]   removed $path" -ForegroundColor Green
            }
        }
    }

    default {
        Write-Host "Unknown option '$Mode'." -ForegroundColor Red
        Write-Host "Use one of: run, check, hotkeys, mic, ollama, install, uninstall"
    }
}
