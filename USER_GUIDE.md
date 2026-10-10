# Sibyl User Guide

Everything Sibyl does, and every setting it has. If you have not installed it
yet, start with **[INSTALL.md](INSTALL.md)**.

## Contents

- [The four ways to dictate](#the-four-ways-to-dictate)
- [The dot above your taskbar](#the-dot-above-your-taskbar)
- [The right-click panel](#the-right-click-panel)
- [The tray icon](#the-tray-icon)
- [What the clean-up does, and does not do](#what-the-clean-up-does-and-does-not-do)
- [When Sibyl pastes your untidy words instead](#when-sibyl-pastes-your-untidy-words-instead)
- [Every setting](#every-setting)
- [Diagnostic commands](#diagnostic-commands)
- [The log file](#the-log-file)
- [Speed, and what makes it slow](#speed-and-what-makes-it-slow)
- [Glossary](#glossary)

---

## The four ways to dictate

Sibyl pastes into **the window that was in front when you started speaking**.
So click into Word, or Slack, or a browser text box first, and then start.

### 1. Hold to talk

**Hold Right Ctrl + Right Alt. Speak. Let go.**

The most common way. Finger order does not matter. Letting go sends it.

### 2. Tap for hands-free

**Tap both keys quickly and let go.** Sibyl keeps listening. **Tap again to
send.**

Use this for anything longer than a sentence, so you are not holding keys down
for a minute.

The boundary between a tap and a hold is **half a second** (`TAP_MAX_HOLD`). If
your quick taps keep being read as holds, raise it.

### 3. Click the dot

**Left-click the dot** above your taskbar. That starts hands-free listening, the
same as a tap. Click the **tick** to send or the **cross** to throw it away.

This is safe because the dot refuses to take focus — clicking it does not change
which window is in front, so Sibyl still knows where to paste.

### 4. Cancel

While it is listening: **click the cross**, or **press any other key**. Nothing
is sent and nothing is kept.

The one exception: in hands-free mode a stray key is ignored, because by then
you are dictating and might reasonably touch something.

> **A hard cap of two minutes** applies to one capture (`MAX_RECORD_SECONDS`).
> It exists so that walking away from a listening machine cannot fill your disk.
> **Ten seconds before it, a high, longer tone plays**, so you can finish your
> sentence. At the cap Sibyl sends what it has, and the dot says
> `Time limit, sending`.

---

## The dot above your taskbar

The dot is a small microphone on a coloured disc. It sits just above the centre
of your taskbar whenever Sibyl is running, unless you have moved it. It grows
into a chip with a word on it while it is working, then shrinks back.

| Colour | Word | What is happening |
|---|---|---|
| Grey | *(none — just the microphone)* | At rest, waiting for you |
| Yellow | `Waking up` | Starting: loading the models into memory |
| Yellow | `Downloading speech model` | First start only: fetching the speech model, about 150 MB |
| **Red** | `Listening` | Recording. A tick and a cross appear beside it |
| Yellow | `Working` + a seconds count | Transcribing and cleaning up |
| Yellow | `Still busy` + a seconds count | You pressed the hotkey while the last request was still being worked on. The press is ignored |
| Green | `Done` | Pasted. Shrinks back to a dot shortly after |
| Green | `Done, your words` | Pasted your own words, tidied only by fixed rules, because the clean-up's answer was not trusted or the clean-up failed |
| **Red** | Words naming the problem | Something failed, and the words say which step. During a capture: `Microphone failed`, `Recording failed`, `Nothing recorded`, `No speech heard`, `Transcribing failed`, `Clean-up failed`, `Paste failed`, `Recording not deleted`, or `Clipboard busy` when another program was holding the clipboard (try again in a moment). At start: `Speech model download failed`, `Speech model failed`, `Ollama not running`, `Model not installed`, `Ollama address refused` |

Red means two different things — listening and failed — but they are never
ambiguous in practice: a failure always carries words, and listening carries a
tick and a cross.

**The dot never shows what you said.** It is on screen permanently, screen
shares included, so while working it counts seconds rather than streaming your
text.

### Moving it

If the dot covers something you need, **press on it, move, and let go**. It
follows the pointer and stays where you drop it, after a restart too. It moves
in any state, even while listening, and moving it never starts or stops
anything. A press that does not move is an ordinary click.

Wherever you put it, the microphone stays where you dropped it, and the chip
grows out to its right (near the right edge of the screen, it slides left
instead). The
whole chip is always kept on screen. The tick and the cross cannot be dragged:
they stay buttons.

To put it back, right-click it and choose **Move back to the bottom**. It moves
on your main screen only.

Where you dropped it is saved in a small file beside the log,
`%LOCALAPPDATA%\Sibyl\notch.json`. It holds that position and nothing else.

---

## The right-click panel

**Right-click the dot** to open a small panel. It holds three things, and a
fourth once you have moved the dot:

| Item | What it does |
|---|---|
| The last cleaned-up text | So you can see what Sibyl actually produced |
| **Paste again** | Pastes that text **where your cursor is now** |
| **Quit Sibyl** | Stops Sibyl. The dot disappears |
| **Move back to the bottom** | Only after you have dragged the dot: puts it back above the centre of the taskbar, and forgets where you had put it |

**Paste again** is the fix for a paste that went to the wrong place. Unlike a
normal capture, it deliberately does *not* return to the original window — you
are using it because the first paste went nowhere useful, so you have already
moved somewhere better.

The stored text lives in memory only. It is not written to disk and it is gone
when Sibyl quits.

---

## The tray icon

The Sibyl logo appears in your system tray, near the clock. Its menu shows your
hotkey and your model name, and offers:

| Item | What it does |
|---|---|
| **Copy last output again** | Puts the last text back on your clipboard. It **cannot paste** — clicking a tray icon has already moved focus to the shell, so there is nowhere sensible to paste to. Press Ctrl+V yourself |
| **Quit** | Stops Sibyl |

---

## What the clean-up does, and does not do

### It removes

| Removed | Example |
|---|---|
| Fillers | "um", "uh", "er", "like", "you know", "I mean" |
| Sentences you took back | "buy bread — no wait, not bread, I mean butter" → butter only |
| Remarks about dictating | "before I forget", "just thinking out loud", "I want to dictate this" |
| Known mis-hearings | "SQ Lite" → "SQLite", by a plain rule before the model ever sees it |

### It adds

| Added | Example |
|---|---|
| Punctuation and capitals | Whisper does most of this; the clean-up tidies the rest |
| Paragraph breaks | A change of subject becomes a new paragraph |
| Bullet lists | A spoken list of things becomes bullets |

### It keeps, deliberately

| Kept | Why |
|---|---|
| Hedges: "probably", "maybe", "I think", "as well" | These carry meaning. Dropping one changes what you said. The safety check rejects any answer that loses one |
| Your actual words and phrasing | Sibyl is not a rewriter. "I reckon we should" does not become "I believe we ought to" |
| Your voice and person | First person stays first person |

### It does not

- **Answer, obey or continue.** Dictate "write me an email declining the
  invitation" and you get that sentence, tidied. Not an email.
- **Invent facts, numbers, times or names.** If an answer contains a number you
  did not say, the check throws the answer away.
- **Summarise or shorten.** An answer materially shorter than your input is
  rejected.
- **Translate.** English only.

---

## When Sibyl pastes your untidy words instead

Sometimes the text that arrives is your words almost as you said them: a "you
know" left mid-sentence, nothing reworded. **This is the safety net working, not
a bug.**

After the model answers, a piece of plain code — no model involved — checks the
answer against what you actually said. If any of these is true, the answer is
thrown away:

| Rejected because | Meaning |
|---|---|
| It grew | More than a quarter longer than what you said, plus two words. The model wrote an email or a poem instead of tidying. Measured: every obeyed instruction came back 4 to 11 times longer |
| It lost a hedge | A "probably", "maybe", "might", "perhaps", "possibly", "as well", "also", "I think" or "not sure" went missing, changing your meaning |
| It lost a "not" | A "not", "never" or "no" you said went missing while the word it belonged to stayed, so "not coming" became "coming" |
| It wrote a number you did not say | "fifty thousand", "50,000" and "50k" count as the same number; "£50" for "fifty thousand" does not |
| It added a word you did not say | An invented time, name or place. One exception: the word "that", measured harmless over 132 answers |
| It kept too little | Under 45% of your meaningful words survived |
| The model failed | Server down, timed out, answer cut off, or answered with nothing |

In all those cases Sibyl pastes **your own words, cleaned by the plain rules
only**: "um", "uh", "er" and "hmm" removed wherever they are, other fillers and
remarks about dictating removed from the start of a sentence, known mis-hearings
fixed. You get something slightly untidy rather than something wrong, and you
never lose a dictation.

When this happens the dot says `Done, your words`. Which rule rejected the
answer is written in the log, without your words.

**How often does it happen?** The project's scored test has 50 cases. Its first
33 score 24 to 26 across nine runs, so roughly seven to nine of them fail, and
the exact set shifts between runs; the other 17, all about reversed meanings,
passed 101 of 102 answers measured, and the check caught the one miss. The check caught 8 of 8 failures in one
measured run and 7 of 8 in another. On real dictation it is occasional.

**It is a safety net, not a guarantee.** In that second run it missed a filler
the model had left in the middle of a sentence. Read what lands in your document,
as you would with any dictation.

If the fallback happens on *every* capture, your model is unhealthy — check with
`.\Start-Sibyl.cmd ollama`.

---

## Every setting

Settings live in a file called `.env`, in the Sibyl folder, next to `sibyl.py`.

```powershell
copy .env.example .env
notepad .env
```

**Nothing in it is required.** Every value has a working default in the code, so
an empty `.env`, or no `.env` at all, works fine. Each line in `.env.example`
starts with a `#`, which means "ignore this line" — delete the `#` to turn a
setting on. **Restart Sibyl after editing.**

### Hotkey

| Setting | Default | What it does |
|---|---|---|
| `HOTKEY` | `ctrl_r+alt_r` | The keys you hold. One key alone, or several joined with `+`. Use `pynput` names: `alt_r`, `ctrl_r`, `alt_l`, `ctrl_l`, `f9`, `f10`, `scroll_lock`, `pause`, `insert`. Run `.\Start-Sibyl.cmd hotkeys` to see what your keyboard actually reports before choosing. Matched by numeric key code, so `alt_r` and `alt_gr` are the same key here |
| `TAP_MAX_HOLD` | `0.5` | Seconds. Release sooner than this and it was a tap (hands-free); later and it was a hold (send). The first dial worth tuning by hand |

> **Do not choose Alt on its own.** Tapping Alt is how Windows opens a program's
> menu bar, so a lone Alt fights the operating system on every press. Two keys
> avoids this.

> **On UK, German, French and other non-US layouts**, Right Alt is AltGr and
> types special characters. Pick different keys there.

### Speech to text

| Setting | Default | What it does |
|---|---|---|
| `STT_HOTWORDS` | six sample names | **The best-value setting in the file.** A comma-separated list of names, places and jargon you actually say. The recogniser cannot guess a spelling it has never heard. Measured: word errors 10.0% → 4.2%, names right 13% → 80%, cost 0.03 s, nothing invented. Set it to nothing to switch the hint off |
| `STT_MODEL` | `base.en` | The speech model. `tiny.en` < `base.en` < `small.en` < `medium.en`, trading speed for accuracy. Measured here: `small.en` was 1.5 s slower on twice the memory (724 MB vs 337 MB) and **barely better at names** (20% right against 13%, both without `STT_HOTWORDS`). Test with `--test-stt` before changing |
| `STT_COMPUTE_TYPE` | `int8` | How precisely the model's numbers are stored. `int8` is roughly 2–4× faster than `float32` on a CPU, for a small accuracy cost |
| `STT_DEVICE` | `cpu` | `cpu`, or `cuda` on a machine with an NVIDIA graphics card. On a machine without one, `cuda` raises an error rather than running slowly |
| `STT_BACKEND` | `faster-whisper` | Which speech engine. Pluggable, so trying another is a config change rather than a rewrite. Only `faster-whisper` ships today |

### The clean-up model

| Setting | Default | What it does |
|---|---|---|
| `CLEANUP_MODEL` | `qwen2.5:3b` | The model that does the tidying. Must be pulled first: `ollama pull <name>`. **Set this to `qwen2.5:1.5b` if you need a commercially usable licence** — see the [README](README.md#licence). Run `python eval_cleanup.py --model <name> --runs 3` before trusting any swap |
| `SIBYL_OLLAMA_HOST` | `http://127.0.0.1:11434` | Where Ollama is, if you moved it to another port. **Only this computer is accepted**, written `http://127.0.0.1:<port>`: anything else, `localhost` included, is refused at start with a red "Ollama address refused", and your words are pasted without the clean-up. Sibyl ignores Ollama's own `OLLAMA_HOST`, which tells Ollama where to listen |
| `MAX_TOKENS_CLEANUP` | `512` | A ceiling on how much the model may write, so a rambling model cannot turn a 5-second wait into a minute. Normal output is around 65, so this is several times the headroom. Raising it also widens the model's memory window slightly |
| `OLLAMA_TIMEOUT_COLD` | `180` | Seconds allowed for the **first** call, which waits while Ollama loads the model into memory |
| `OLLAMA_TIMEOUT_WARM` | `120` | Seconds allowed for every call after that. Two tiers, so a genuine hang looks different from a normal wake-up |
| `OLLAMA_WAIT_AT_START` | `60` | Seconds Sibyl waits at startup for Ollama to answer before showing the dot red |

### Recording

| Setting | Default | What it does |
|---|---|---|
| `MAX_RECORD_SECONDS` | `120` | Hard cap on one capture, in seconds. Protects you from walking away with the recorder running. Raising it widens the model's memory window to match, so a longer dictation still fits: 120 seconds gives a window of 3,072 word pieces, 300 seconds gives 4,096. That costs a little more memory |

---

## Diagnostic commands

Every stage runs on its own, so a failure names its own layer. From the Sibyl
folder, in PowerShell.

### Through the launcher

```powershell
.\Start-Sibyl.cmd check
```

| Command | What it does |
|---|---|
| `.\Start-Sibyl.cmd` | Starts Sibyl normally, with a console. Double-clicking it does the same |
| `.\Start-Sibyl.cmd check` | Every automated test that needs no microphone — over 500 of them — plus a live clean-up. **Moves your mouse pointer for a few seconds**, because one test clicks the dot with real mouse input, and **uses your clipboard**: text you had copied is put back, anything else (a picture, a file) is cleared |
| `.\Start-Sibyl.cmd hotkeys` | Prints every key your keyboard reports, with the number Windows sent. Use this before changing `HOTKEY` |
| `.\Start-Sibyl.cmd mic` | Records 5 seconds to `test_recording.wav` and reports how loud it was. Warns if the peak is very low |
| `.\Start-Sibyl.cmd ollama` | Checks the model runtime is up and the model is installed |
| `.\Start-Sibyl.cmd install` | Desktop icon, and optionally start with Windows |
| `.\Start-Sibyl.cmd uninstall` | Removes both shortcuts |

### Directly, for one stage at a time

```powershell
python sibyl.py --test-audio           # microphone only, no models needed
python sibyl.py --test-hotkeys         # what the keyboard actually reports
python sibyl.py --test-ollama          # runtime up? model installed?
python sibyl.py --test-stt file.wav    # transcribe a file, and report the speed
python sibyl.py --test-llm "some text" # the clean-up alone, with timings
python sibyl.py --test-pill            # the dot through a fake capture, no mic
```

`--test-llm` is the useful one when output looks wrong: it shows the model's raw
answer, the check's verdict, and what would have been pasted.

---

## The log file

Started from the desktop icon, Sibyl runs without a console, so anything it
would have printed goes here instead:

```
%LOCALAPPDATA%\Sibyl\sibyl.log
```

Paste that into File Explorer's address bar. Every line is timestamped.

**What it holds:** startup steps, state changes, timings for each stage, and
errors.

**What it never holds:** what you said, what was pasted, which window it went
to, the check's quoted words, or the model's raw reply. Those all pass through a
function that writes a placeholder to the file and shows the real text only in a
console. An automated test dictates a known phrase and then searches the log for
it, so this cannot quietly regress.

Past 1 MB the file is renamed `sibyl.old.log` at the next start, so it never
grows without limit.

The log is safe to attach to a bug report. **Your `.env` is not** — it may hold
real names of real people.

---

## Speed, and what makes it slow

Measured on a 2021 ultrabook: Intel Iris Xe graphics, no separate graphics card,
16 GB RAM.

| Dictation | Time from letting go to pasted text |
|---|---|
| 10 words | about 1 second (0.9–1.3 s measured over three runs) |
| 30–40 words | about 3 seconds (2.6–3.5 s over three runs) |
| Dictating while Sibyl is still starting | waits until both models have loaded, 1–3 minutes after a start |
| First capture after 30 idle minutes | about 20 s |

**These are ranges, not single numbers.** The same sentence varies by a few
tenths of a second between runs, and anything else using your CPU widens that. A
single timing is not a measurement — if you are comparing something, run it three
times.

**Why the idle one is slow.** Ollama unloads the model from memory after 30
idle minutes, giving you the ~2 GB back. The next capture has to load it again.
This is a deliberate trade: Sibyl is meant to sit there all day without holding
memory it is not using.

**What actually makes it slow:**

| Cause | Fix |
|---|---|
| The machine is short of memory and swapping to disk | Close something. Sibyl needs its ~2.4 GB resident |
| A bigger speech model was set | `base.en` was measured as the best trade here |
| A very long dictation | Time scales with length. This is unavoidable |

---

## Glossary

Terms used above, in plain English.

| Term | Meaning |
|---|---|
| **Model** | A large file of learned numbers that does one job — hearing speech, or tidying text. Sibyl uses two |
| **Ollama** | A separate program that runs language models on your own machine. Sibyl asks it questions over a connection to your own computer |
| **Whisper** | OpenAI's speech-recognition model. `faster-whisper` is a re-implementation that runs it quickly on an ordinary CPU |
| **CPU** | Your computer's main processor. Sibyl uses it rather than a graphics card, because most laptops do not have a suitable one |
| **PATH** | The list of folders Windows searches when you type a command name. If Python is not on it, typing `python` fails |
| **`.env`** | A plain text file of settings, read when Sibyl starts. Not shared, not committed to Git |
| **Hedge** | A word that softens a claim: "probably", "maybe", "I think". Removing one changes your meaning, so Sibyl protects them |
| **Filler** | A word with no meaning that speech naturally produces: "um", "uh", "like" |
| **Token / word piece** | The chunks a model reads text in — roughly one per short word, more for unusual names. The model can only hold so many at once |
| **The check** | Plain code that runs after the model and decides whether its answer is faithful to what you said |
| **Hotwords** | A list of names you hand to the speech model in advance, so it knows those spellings are possible |
