# How Sibyl works

A walkthrough from what you see down to how it is built, for someone who has
never looked inside a dictation tool. Every technical word is explained before
it is used. If you only want to use Sibyl, the [User Guide](../USER_GUIDE.md) is
shorter.

## Contents

1. [What do you experience?](#1-what-do-you-experience)
2. [What happens to one dictation?](#2-what-happens-to-one-dictation)
3. [Why these parts and not others?](#3-why-these-parts-and-not-others)
4. [How can a laptop run an AI model at all?](#4-how-can-a-laptop-run-an-ai-model-at-all)
5. [How is privacy built in rather than promised?](#5-how-is-privacy-built-in-rather-than-promised)
6. [What is inside sibyl.py?](#6-what-is-inside-sibylpy)
7. [How it was built](#how-it-was-built)

---

## 1. What do you experience?

You click into the window you want to write in: an email, a chat, a document.
Then you **hold Right Ctrl and Right Alt, speak, and let go.** A second or a few
later, your words appear where your cursor was, tidied: no "um", no sentence you
took back, with punctuation, and with a list turned into bullets if you spoke one.

Two other ways to start, for longer dictation:

- **Tap** both keys instead of holding them. Sibyl keeps listening until you tap
  again.
- **Click the dot** (below). That also listens until you click its tick to send,
  or its cross to throw the recording away.

**The dot** is a small microphone on a coloured disc, sitting above your taskbar
whenever Sibyl runs. Its colour says what it is doing:

| Colour | Meaning |
|---|---|
| Grey | Ready |
| Red, with a tick and a cross | Listening |
| Yellow, counting seconds | Working |
| Green | Pasted |
| Red, with a few words | Something failed, and the words name the step |

It never shows what you said, because it is on screen all the time, including
when you share your screen. If it covers something, **drag it anywhere**; it
stays where you drop it, even after a restart.

One property matters more than it looks: **clicking the dot never takes the
keyboard away from the window you were in.** Windows normally hands the keyboard
to whatever you click. Sibyl pastes into the window that was in front when you
started, so a dot that grabbed the keyboard would become that window, and your
text would be pasted into the dot. The dot's window is marked with a Windows
setting that refuses the keyboard, and an automated test clicks it with the real
mouse to prove it.

---

## 2. What happens to one dictation?

![Sibyl's architecture: the hotkey starts the recorder; speech to text, then spelling fixes, then the clean-up model; a faithfulness check sends a trusted answer to the paste, and anything else to a rule clean-up of your own words](architecture.png)

Read it as a relay race. Each runner hands one thing to the next.

| Step | Receives | Does | Hands on |
|---|---|---|---|
| **1. Hotkey** | Your key press | Notes which window is in front, and starts the recorder | A signal to record |
| **2. Recorder** | Sound from your microphone | Saves it as a temporary audio file, at most two minutes | The audio file |
| **3. Speech to text** | The audio file | Turns sound into words | Raw text, exactly as heard |
| **4. Spelling fixes** | Raw text | Fixes known mis-hearings with fixed rules ("SQ Lite" becomes "SQLite") | Fixed text |
| **5. Clean-up** | Fixed text | A language model rewrites it as what you meant to write | The model's answer |
| **6. The check** | Your text and the model's answer | Compares them, and decides whether to trust the answer | A trusted answer, or a refusal |
| **7. Paste** | The text to paste | Puts the text on the clipboard, brings your window back to the front, presses Ctrl+V. If the window will not come back, it stops there: the text waits on the clipboard rather than landing somewhere else | Your words in your window |

**When the check refuses**, or the model fails outright, Sibyl does not give up.
It takes the fixed text from step 4, removes "um", "uh" and other fillers with
fixed rules, and pastes that instead. The dot says `Done, your words`. You get
something slightly untidy rather than something wrong, and you never lose a
dictation.

### What the check looks for

The model is good at tidying and occasionally bad at leaving things alone. The
check is a short piece of plain code that compares what you said with what the
model wrote. It refuses the answer if any of these is true:

| It refuses an answer that | Because |
|---|---|
| Grew by more than a quarter | A dictated "write me an email declining…" gets an email written. Every one measured came back 4 to 11 times longer |
| Lost a hedge: "probably", "maybe", "might", "I think", "as well" and a few more | Dropping one changes what you meant |
| Lost a "not", "never" or "no" while keeping the word it belonged to | "Not coming" and "coming" are opposites |
| Wrote a number you never said | "Fifty thousand", "50,000" and "50k" count as the same number; "£50" for "fifty thousand" does not |
| Added a word you never said | An invented time or name. One small word, "that", is allowed: measured harmless over 132 answers |
| Kept under 45% of your meaningful words | It summarised instead of tidying |

Each limit was set by running the check over measured answers, good and bad, and
choosing the line that refused the bad ones without refusing the good ones.
They were not guessed. The check is a safety net rather than a guarantee: in
one measured run it missed a filler the model had left mid-sentence.

---

## 3. Why these parts and not others?

Every choice below was made by measuring the alternatives on this kind of
laptop. Two words first:

- A **model** is a file of learned numbers that does one job, such as hearing
  speech or tidying text. More in section 4.
- **Ollama** is a free program that runs language models on your own computer
  and answers requests from other programs on it.

### Hearing: `base.en`, told your names

The speech model is OpenAI's Whisper `base.en`, run by `faster-whisper`. Four
setups were scored over twelve recordings of real speech; these three tell the
story:

| Setup | Names heard right | Time | Memory |
|---|---|---|---|
| `base.en` alone | 13% | — | 337 MB |
| **`base.en`, handed your names in advance** | **80%** | **0.03 s more** | **337 MB** |
| `small.en`, 3.3 times the size | 20% | 1.5 s more | 724 MB |

Handing over the names also cut word errors from 10.0% to 4.2%, and invented
nothing. So giving the recogniser your own list of names and jargon (the
`STT_HOTWORDS` setting) beat a bigger model on every count. A hint cannot fix a name the model
never learned, which is why names reach 80% and not 100%; a repeating
mis-hearing gets a fixed spelling rule instead.

The hint is passed as `hotwords`, not the older `initial_prompt`, because Whisper
listens in 30-second windows and an initial prompt only reaches the first one.

### Tidying: `qwen2.5:3b`

Three small models were scored on the same test cases, each run three times:

| Model | Score | Time | What went wrong |
|---|---|---|---|
| **`qwen2.5:3b`** | **9, 10, 10 of 10** | **4.7 s** | Dropped "I think" once |
| `qwen2.5:1.5b` | 8, 8, 8 | 3.4 s | Dropped part of a sentence every run |
| `qwen3.5:0.8b` | 8, 8, 8 | 6.3 s | Kept a corrected-away number every run |

The larger model is the default. Its licence allows research and evaluation
only, so the README explains how to switch to the 1.5B model, which allows
commercial use, with one line in your settings.

Since then the scored test has grown to 50 cases, built from failures found in
real use and in review. The model passes 41 to 42 of them. Seven attempts to improve the
prompt's wording were each measured three times; six made things worse and the
seventh did not hold up on a second measurement. Telling this model *not* to drop
hedges made it drop more of them. That is why the check exists: what the model
cannot reliably do, plain code verifies.

### Why the check is plain code, not a second model

A second model checking the first could make the same mistakes, and would add
seconds to every dictation. The failures that matter are countable: a text that
grew, a word that vanished, a number that appeared. Counting is what plain code
is perfect at, and it takes a few thousandths of a second.

The same rule runs through the whole design: **never hand a model a job a fixed
rule does perfectly.** Known mis-hearings and fillers at the start of a sentence
are fixed by rules before the model sees the text.

---

## 4. How can a laptop run an AI model at all?

### A model is a file; a runtime runs it

A model is a file, like a photo or a song. Inside is a long list of numbers,
billions of them, called **weights**. Nobody chose them: they were nudged into
place during training, by showing the model huge amounts of text and correcting
its guesses about the next word, over and over.

A file cannot do anything by itself. An `.mp3` needs a music player. In the same
way, **a model needs a runtime: the program that opens the file and does the
arithmetic.** Sibyl uses one of each, twice:

| Job | The file (model) | The program that runs it (runtime) |
|---|---|---|
| Hearing | Whisper `base.en`, about 150 MB | `faster-whisper` |
| Tidying | `qwen2.5:3b`, about 1.9 GB | Ollama |

`faster-whisper` is not a different Whisper. It is the same weights, opened by a
faster program. That is how a laptop can hear speech in a fraction of a second.

### Why speed is limited by memory, not by the processor

A language model writes one small piece of text at a time: a **token**, about
three quarters of a word. To produce each token, the processor must read **every
weight in the model**, in order. The model is a chain of multiplications, layer
after layer, with no shortcut and nothing to look up.

So for every three quarters of a word, the whole 1.9 GB file moves from memory
into the processor. Picture a chef (the processor) and a pantry (memory)
joined by a corridor. Modern processors multiply astonishingly fast, so the
chef is rarely the problem: **the width of the corridor is.** Engineers call
this being *memory-bound*.

The arithmetic, for a typical 2021 laptop:

```
The corridor moves roughly           40 GB per second
Each token needs the whole model      1.9 GB
So at most                           ~21 tokens per second
Measured on such a laptop            ~12 tokens per second
```

It lands below the ceiling, as it must: no laptop moves data at its memory's
full rated speed. The ceiling also explains three of Sibyl's choices:

- **A small model.** A model twice the size would be twice as slow, whatever the
  processor.
- **Short answers.** At about 12 tokens a second, every token the model writes
  costs time. The answer is capped, so a model that starts rambling cannot hold you
  up for a minute.
- **The model stays loaded.** Fetching 1.9 GB from disk is far slower than from
  memory, so Sibyl loads it once at start. Ollama unloads it after 30 idle
  minutes to give the memory back, which is why the first dictation after a
  break takes about 20 seconds.

**Memory is the real requirement.** Both models together hold about 2.4 GB of
memory while Sibyl runs. On a 16 GB laptop with a normal working day open, that
is comfortable; on 8 GB it is tight.

A graphics card would be faster for one reason only: its corridor is more than
ten times wider. Sibyl is built for laptops without one.

---

## 5. How is privacy built in rather than promised?

A promise can be broken by one careless change. Sibyl's privacy is in its
structure, so that breaking it would take a deliberate one.

| Rule | How it is enforced |
|---|---|
| **Your words are only ever sent to your own computer** | Everything Sibyl sends goes through the `requests` library, and every call goes to `127.0.0.1`, an address that always means "this computer". The one setting that names Ollama's address accepts nothing else, and a refused address switches the clean-up off rather than sending anywhere |
| **No proxy can reroute it** | A proxy is a go-between some networks route traffic through. Every call to Ollama is told to bypass any proxy; a test counts every call in the code and fails if one is not |
| **The log never holds what you said** | Anything printed that carries your words, a window title or the model's reply passes through one function, `private()`, which shows it in a console and writes a placeholder to the log file. A test dictates a known phrase and searches the log for it |
| **Recordings are deleted** | Each one is deleted when its dictation ends, after the paste, in code that runs even if a step crashes. Quitting mid-dictation deletes it first |
| **The clipboard is not remembered** | Each paste marks its clipboard entry with Microsoft's documented opt-out, so it stays out of Windows' clipboard history and cloud sync |
| **The dot never shows your words** | It counts seconds instead, because it is on screen during screen shares |
| **Two copies never run at once** | Sibyl holds a socket open on `127.0.0.1` port 49731 as a lock. A second copy finds it taken and stops. It never accepts a connection or sends anything |
| **Where you dragged the dot is a position, nothing else** | It is saved in `notch.json`, beside the log in `%LOCALAPPDATA%\Sibyl` |

**The one honest exception:** the first time Sibyl starts, `faster-whisper`
downloads the speech model, about 150 MB, from Hugging Face. That is a one-way
fetch of a public file. After that the model loads from your disk with no
contact at all. Nothing you say is ever part of it.

One small speed lesson hides in the first row. The address is written
`127.0.0.1`, never `localhost`, which means the same thing: Windows tries a
newer kind of address first for `localhost`, waits about two seconds for it to
fail, then falls back. Measured: 2.07 seconds a call against 0.03.

---

## 6. What is inside sibyl.py?

The whole app is one Python file, read top to bottom in nine numbered sections.
One file is deliberate: one pipeline, no web of files to hold in your head.

| Section | What lives there |
|---|---|
| 1. Config | Every setting, the log, and `private()` |
| 2. Audio cues | The beeps |
| 3. Recorder | Audio capture |
| 4. Transcription | Speech to text, behind an interface that would accept another engine |
| 5. The LLM pipeline | The clean-up prompt, the call to Ollama, the fixed rules, and `faithful`, the check |
| 6. Injection | The one-copy lock, the target window, clipboard and paste |
| 7. Hotkeys | `TapListener`, which tells a hold from a tap from a stray key |
| 8. Application | The dot (`Pill`), the tray icon, and `Sibyl`, which joins the stages |
| 9. Staged self-tests | The `--test-…` commands that run each stage alone |

**LLM** stands for *large language model*, the kind of model that tidies text.

### Why several threads

A **thread** is one line of work a program does; a program can run several at
once. Sibyl uses several because each job has a rule that forbids doing it on
another job's line:

| Thread | Does | Why it needs its own |
|---|---|---|
| **The screen** (the main thread) | Draws the dot, handles its clicks and drags | Windows lets a window be touched only by the thread that made it. Every other thread posts its updates on a queue, which this one reads |
| **The keyboard** | Listens for the hotkey | Windows silently disconnects a keyboard listener that takes too long to answer. So it never waits: it hands each press on and returns at once |
| **The workers** | Open the microphone, transcribe, clean up, paste | These take from a fraction of a second to several seconds, which neither of the threads above can afford |
| **The tray** | Runs the tray icon | The tray library needs its own loop, beside the screen's |

Two drawing details: the dot is drawn by the Pillow imaging library at four times
its size and shrunk, so its edges are smooth, then handed to Windows as a
picture with a see-through level for every pixel. And any pixel that is not fully
see-through catches clicks, which is why the dot has thin outlines and no soft
shadow.

---

## How it was built

Sibyl was built by one person, working with
[Claude Code](https://claude.com/claude-code), Anthropic's AI coding assistant.
Four habits shaped it.

**Measure first.** The product is judged by a scored test of 50 real-shaped
cases, always run three times. Three runs, because one run varies: the original
33 cases score anywhere from 24 to 26, so a change worth less than about one
case is indistinguishable from noise. One change that looked like a gain over
three runs reversed over the next three. Inputs are interleaved, never sent
twice in a row, because a sentence sent back to back behaved completely
differently (1 time in 10 against 9 in 10). Most proposals lost to this test:
seven prompt changes, a bigger speech model, two other clean-up models.

**Read the output, not just the score.** An automated test passes whatever it
does not look for. An early version of the scored test passed an answer that
invented a meeting time, because it only checked for one exact phrase. It now
flags any number that was not said.

**Test first.** Each fix began as a test that failed for the stated reason, then
passed. There are now over 500, and none needs a microphone: one moves the real
mouse, because only a real click proves the dot never takes the keyboard.

**Prove the guards.** The publish gate, the script that keeps private files out
of this repository, was reviewed four times. Then each of its guards was
deleted in turn, to see whether any test noticed. Seven guards had no test that
noticed, several of them added on a reviewer's own advice. All of them now fail
a test when removed. A guard nobody can fail is not a guard, and a review will
not tell you which ones those are.
