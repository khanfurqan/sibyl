# Installing Sibyl

This guide assumes **you have never installed Python and have never used a
terminal.** It goes from a fresh Windows machine to your first dictation.

Every step tells you **what to do** and then **what success looks like**. If
what you see does not match, stop at that step rather than carrying on — a
later step will fail for a reason that no longer looks related.

Budget about 20 minutes, most of it waiting for downloads.

## Contents

1. [What you need first](#1-what-you-need-first)
2. [Install Python](#2-install-python)
3. [Install Ollama](#3-install-ollama)
4. [Download the clean-up model](#4-download-the-clean-up-model)
5. [Download Sibyl](#5-download-sibyl)
6. [Install Sibyl's Python libraries](#6-install-sibyls-python-libraries)
7. [Run the built-in checks](#7-run-the-built-in-checks)
8. [Start Sibyl](#8-start-sibyl)
9. [Your first dictation](#9-your-first-dictation)
10. [Make a desktop icon](#10-make-a-desktop-icon)
11. [Optional: change the settings](#11-optional-change-the-settings)
12. [If a step went wrong](#if-a-step-went-wrong)

---

## 1. What you need first

| You need | Why | How to check |
|---|---|---|
| **Windows 10 or 11** | The always-visible status dot is Windows-specific | Press `Windows` + `Pause`, or open Settings → System → About |
| **About 6 GB of free disk space** | Measured on a working install: Ollama itself 2.9 GB (it bundles graphics-card libraries, used or not), the clean-up model 1.9 GB, Python and Sibyl's libraries about 0.5 GB, the speech model about 150 MB | Open File Explorer, look at your C: drive |
| **About 3 GB of free memory (RAM)** | Both models are held in memory while Sibyl runs | Open Task Manager (`Ctrl` + `Shift` + `Esc`) → Performance → Memory |
| **A working microphone** | Obviously | Settings → System → Sound → Input, and speak |
| **An internet connection, once** | To download Python, Ollama and the models. After that Sibyl never needs it again | — |

### One thing to learn first: PowerShell

PowerShell is a window where you type commands instead of clicking. You will use
it a handful of times in this guide. To open it:

Press the `Windows` key, type `powershell`, and press `Enter`.

A dark window opens with a blinking cursor. That is it. When this guide shows a
box like this:

```powershell
python --version
```

it means: type or paste that line into the PowerShell window, then press
`Enter`. You can paste with `Ctrl` + `V` or by right-clicking.

---

## 2. Install Python

**Python** is the programming language Sibyl is written in. Sibyl is a Python
file, so your computer needs Python in order to run it — the same way a `.docx`
file needs Word.

1. Go to **<https://www.python.org/downloads/windows/>**
2. Download the latest **Windows installer (64-bit)**. Sibyl is developed and
   tested on **Python 3.12**. Anything from 3.10 upwards should work, but only
   3.12 has been measured.
3. Run the installer. **Before clicking Install, tick the box that says
   "Add python.exe to PATH"** at the bottom of the first screen.

> **Why that tick box matters.** "PATH" is the list of folders Windows searches
> when you type a command name. Without the tick, Python is installed but
> Windows cannot find it when you type `python`, and every later step in this
> guide fails with "python is not recognized". This is the single most common
> way this install goes wrong.

4. Click **Install Now** and wait.

**What success looks like.** Close PowerShell if it is open, open a new one
(settings like PATH are only read when a window opens), and run:

```powershell
python --version
```

You should see something like:

```
Python 3.12.10
```

If you instead see `'python' is not recognized...`, or the Microsoft Store
opens, see [If a step went wrong](#if-a-step-went-wrong).

---

## 3. Install Ollama

**Ollama** is a program that runs AI language models on your own computer. It is
what does Sibyl's clean-up step. Sibyl does not include a model itself; it asks
Ollama, which is running quietly on your machine, and Ollama answers.

1. Go to **<https://ollama.com/download/windows>**
2. Download and run the installer. There are no choices to make.
3. Ollama starts itself and then starts with Windows from now on. You will see a
   small llama icon in your system tray, near the clock.

**What success looks like.** In PowerShell:

```powershell
ollama --version
```

You should see a version number, for example `ollama version is 0.35.1`.

---

## 4. Download the clean-up model

A **model** is the file that does the actual thinking — roughly 1.9 GB of
learned numbers. Ollama downloads and manages it for you.

```powershell
ollama pull qwen2.5:3b
```

This downloads about 1.9 GB. It shows a progress bar. On a normal home
connection expect a few minutes.

**What success looks like.** The progress bar finishes and says `success`. Then:

```powershell
ollama list
```

shows a line with `qwen2.5:3b` in it.

> **A licence point that may apply to you.** `qwen2.5:3b` is released under a
> **research licence: non-commercial use only**. If you will dictate for paid
> work, run `ollama pull qwen2.5:1.5b` instead — that one is Apache 2.0, free
> for any use — and set `CLEANUP_MODEL=qwen2.5:1.5b` in step 11. It is slightly
> faster and slightly worse at keeping everything you said. See the
> [Licence section of the README](README.md#licence).

You do **not** need to download the speech model. Sibyl does that itself, the
first time you run it.

---

## 5. Download Sibyl

Two ways. Pick one.

**The simple way (no extra tools):**

1. On Sibyl's GitHub page, click the green **Code** button, then **Download
   ZIP**.
2. Right-click the downloaded ZIP → **Extract All**. Put it somewhere you will
   not accidentally delete — your Documents folder is fine, your Downloads
   folder is not.

**The way that makes updates easy** (needs [Git](https://git-scm.com/download/win)
installed):

1. On Sibyl's GitHub page, click the green **Code** button and copy the address
   it shows under **HTTPS**.
2. In PowerShell, type `git clone `, paste that address after it, and press
   `Enter`.

Later, `git pull` in that folder gets you the newest version.

**Now move PowerShell into that folder.** `cd` means "change directory". Type
`cd `, then drag the Sibyl folder from File Explorer into the PowerShell window
— that pastes its full path for you — then press `Enter`:

```powershell
cd "C:\Users\<your-windows-name>\Documents\Sibyl"
```

**What success looks like.** Run `dir`. You should see `sibyl.py`,
`Start-Sibyl.cmd` and `requirements.txt` listed.

**Stay in this folder for the rest of the guide.** If you close PowerShell, you
have to `cd` back here.

---

## 6. Install Sibyl's Python libraries

Sibyl uses about ten free libraries written by other people — for recording
audio, watching the keyboard, drawing the status dot, and so on. This one
command downloads all of them.

```powershell
python -m pip install -r requirements-lock.txt
```

That file names the exact version of every library, the same set Sibyl was
tested with on Python 3.12, so nothing arrives that was never tried.

This takes a couple of minutes and prints a great deal of text. That is normal.

**If you installed a Python other than 3.12** and pip stops with "No matching
distribution found", one of those exact versions was not made for your Python.
Use the looser list instead, which takes the newest version of each library that
Sibyl allows:

```powershell
python -m pip install -r requirements.txt
```

**What success looks like.** The last lines say `Successfully installed` followed
by a list of names and version numbers. A line in yellow about upgrading `pip`
itself is harmless and can be ignored.

---

## 7. Run the built-in checks

Before touching a microphone, check the parts that need no microphone. Sibyl
ships with over 500 automated tests and runs them all with one command.

```powershell
.\Start-Sibyl.cmd check
```

> **Why `Start-Sibyl.cmd` and not `Start-Sibyl.ps1`?** The real work is in
> `Start-Sibyl.ps1`, a PowerShell script, and Windows refuses to run PowerShell
> script files by default, as a safety measure against scripts arriving by
> email. `Start-Sibyl.cmd` is a small helper that asks PowerShell to run it
> with that rule lifted **for this one run only**. It changes no setting on your
> machine. You can also **double-click `Start-Sibyl.cmd`** in File Explorer to
> start Sibyl.

This takes a minute or two. **Your mouse pointer will move on its own for a few
seconds** — one test clicks the status dot with real mouse input, because that is
the only honest way to test a click. **It also borrows your clipboard** for a
moment: text you had copied is put back afterwards, but a copied picture or file
is cleared.

> **If the notch test reports failures, look at your taskbar before you worry.**
> It clicks a fixed spot just above the taskbar, so anything sitting there takes
> the clicks instead. A Sibyl that is running and idle is a small dot and does not
> interfere. A Sibyl showing a wide coloured chip — because it failed to start, or
> is mid-capture — does. Quit it from the tray icon, run the check again, then
> start it back up.

**What success looks like.** Green `[ok]` lines for Python and Ollama, then each
test file reporting its passes, and near the end an actual clean-up of a sample
sentence with fillers in it. No `FAIL` anywhere.

---

## 8. Start Sibyl

```powershell
.\Start-Sibyl.cmd
```

Or double-click `Start-Sibyl.cmd` in the Sibyl folder. Either way a window opens
and Sibyl runs in it.

The first start is the slow one: Sibyl downloads the speech model (about
150 MB) and loads both models into memory. Expect **one to three minutes**. It
only happens once. While it downloads, the dot above the taskbar says
`Downloading speech model`.

**What success looks like.** Three things:

1. The window prints the hotkey reminder and stays open. **Leave this window
   open** — closing it stops Sibyl.
2. A **small microphone on a grey disc appears just above your taskbar**,
   centred. This guide calls it **the dot**. That is Sibyl at rest. It stays on
   screen whenever Sibyl is running, and if it covers something, drag it
   anywhere; it stays where you drop it.
3. A **Sibyl icon appears in your system tray**, near the clock.

If the dot turns **red with words on it**, something needs attention — usually
Ollama. Read the words; they name the problem.

---

## 9. Your first dictation

1. Open **Notepad** and click inside it, so your cursor is blinking there.
2. **Hold down Right Ctrl and Right Alt together.** Keep holding. The dot turns
   **red** and says `Listening`.
3. Say, out loud and naturally:

   > "um so I need to buy milk eggs and bread no wait not bread I mean butter
   > and then call the bank"

4. **Let go of both keys.** The dot turns **yellow** and says `Working`, with a
   counting number of seconds.
5. After a few seconds the dot turns **green** and says `Done`, and your text
   appears in Notepad.

**What success looks like.** Notepad contains something close to one of these:

```
I need to buy milk, eggs, and butter, and then call the bank.
I need to buy milk, eggs, and butter. Then call the bank.
```

**The exact wording varies from run to run**, and both of those are correct.
Do not compare word for word. Check these four things instead:

| Check | Why it matters |
|---|---|
| The "um" and "uh" are gone | The clean-up ran |
| **"butter" is there and "bread" is not** | Your correction was applied, rather than both words being kept |
| "call the bank" is still there | Nothing was dropped |
| There is punctuation and a capital letter | The formatting ran |

That is the whole product.

**Then try hands-free.** Instead of holding, **tap** both keys quickly and let
go. The dot stays red, and a **tick** and a **cross** appear next to it. Talk
for as long as you like. Tap both keys again — or click the tick — to send. The
cross throws it away.

**That is the install finished.** Everything below is convenience.

---

## 10. Make a desktop icon

Right now Sibyl only runs while that PowerShell window is open. This step gives
you a proper desktop icon and no window at all.

```powershell
.\Start-Sibyl.cmd install
```

It asks one question: **`Start Sibyl when Windows starts? (y/n)`**. Answer `y`
if you want Sibyl always available, which is how most people run it.

**What success looks like.** Green `[ok]` lines naming each shortcut it created,
and a **Sibyl** icon on your desktop.

Now stop the Sibyl you started in step 8 (click the tray icon → **Quit**, or
press `Ctrl` + `C` in the PowerShell window), and double-click the desktop icon
instead. The grey dot comes back. **No console window appears** — that is the
point of this step.

With no console, anything Sibyl would have printed goes to a log file instead:

```
%LOCALAPPDATA%\Sibyl\sibyl.log
```

Paste that path into File Explorer's address bar to open the folder. The log
holds timings, states and errors. **It never holds what you said**, what was
pasted, or which window it went to. Nor does it hold an error's own words, which
can name folders in your Windows account: such a line says "(details not
logged)". To see them, quit Sibyl from the tray, start it with
`.\Start-Sibyl.cmd` in PowerShell instead, and repeat what went wrong. They
show in that window.

To remove both shortcuts later:

```powershell
.\Start-Sibyl.cmd uninstall
```

---

## 11. Optional: change the settings

Sibyl works with no configuration at all. Everything has a working default
baked in. But if you want to change the hotkey, swap the model, or — most
usefully — **tell the speech recogniser the names of people you talk about**,
you do it in a file called `.env`.

```powershell
copy .env.example .env
notepad .env
```

Every setting is commented out with a `#` at the front. To change one, delete
its `#` and edit the value. Save, then restart Sibyl.

**The one setting worth doing on day one** is `STT_HOTWORDS`. The speech model
cannot guess a spelling it has never heard, so give it your own names, places
and jargon, separated by commas:

```
STT_HOTWORDS=Siobhan, Joaquin, Kubernetes, Peckham
```

Measured effect: word errors more than halved, and names recognised rose from
13% to 80%, at a cost of 0.03 seconds. It is the best-value setting in the file.

Your `.env` is ignored by Git and never leaves your machine. Since it may hold
real names of real people, **do not paste it into a bug report.**

The full list of settings, with what each one does, is in the
[User Guide](USER_GUIDE.md#every-setting).

---

## If a step went wrong

| What you saw | What it means | What to do |
|---|---|---|
| `'python' is not recognized` | Python is not on PATH | Re-run the Python installer, choose **Modify**, and tick "Add python.exe to PATH". Then **open a new PowerShell window** — an open one keeps the old PATH |
| Typing `python` opens the Microsoft Store | Windows' placeholder is being found instead of real Python | Install Python from python.org as in step 2, with the PATH box ticked. Or turn the placeholder off: Settings → Apps → Advanced app settings → App execution aliases → switch off both `python.exe` entries |
| `...Start-Sibyl.ps1 cannot be loaded because running scripts is disabled` | Windows' default script policy | Use `.\Start-Sibyl.cmd` instead, as this guide does throughout. It runs the same script with that rule lifted for one run |
| `[FAIL] Ollama is not running` | Ollama is installed but not started | Run `ollama serve` in a separate PowerShell window and leave it open, or restart your PC — Ollama normally starts itself |
| `model 'qwen2.5:3b' not found` | Step 4 did not complete | Run `ollama pull qwen2.5:3b` again and wait for `success` |
| The first start hangs for minutes | It is downloading the speech model, about 150 MB; the dot says `Downloading speech model` | Wait. Only the first start does this |
| The dot turns red saying `Speech model download failed` | The connection dropped during the download | Check the connection and start Sibyl again. Only the missing files are fetched |
| `pip install` fails with `OSError ... No such file or directory` and a hint about **long path support** | Some of Sibyl's dependencies contain very long filenames. Windows limits a full path to 260 characters by default, and a deeply nested Sibyl folder uses that budget up | Put Sibyl in a short path, such as `C:\Sibyl` or `C:\Users\<your-windows-name>\Sibyl`, rather than several folders deep. Or enable long paths: the hint in pip's own message links to the instructions |
| No dot above the taskbar | Sibyl did not finish starting | Look at the PowerShell window for an error. If you used the desktop icon, read `%LOCALAPPDATA%\Sibyl\sibyl.log` |
| Holding the keys does nothing | Your keyboard reports different codes, or Right Alt is AltGr on your layout | Run `.\Start-Sibyl.cmd hotkeys`, press some keys, and see what actually arrives. Then set `HOTKEY` in `.env` |
| The dot turns red saying the microphone failed | Windows is blocking microphone access, or another program has it | Settings → Privacy & security → Microphone, and allow desktop apps |
| Text was pasted somewhere unexpected | Sibyl pastes into the window that was in front when you started speaking | Right-click the dot and choose **Paste again**. That pastes where your cursor is *now* |
| It pasted my own words, barely tidied, and the dot said `Done, your words` | The safety check rejected the model's answer and gave you your own words rather than a wrong answer. Working as designed | Nothing to fix. If it happens on every capture, run `.\Start-Sibyl.cmd ollama` to check the model is healthy |
| The text still has an "um" or "uh" in it | The model left a filler and the check trusted its answer: the check looks for changed meaning, not for fillers. A known limit | Delete it by hand |

Still stuck? Open an issue, and include the last twenty lines of
`%LOCALAPPDATA%\Sibyl\sibyl.log`. They contain no dictated text. **Do not paste
your `.env` file.**
