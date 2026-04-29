<div align="center">

# 🖨️ Anton OCR Daemon

**Local, zero-cloud PDF → Markdown converter powered by [Ollama](https://ollama.com) and `glm-ocr`.**  
Drop a PDF. Get Markdown. No API keys. No data leaves your machine.

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Ollama](https://img.shields.io/badge/Ollama-glm--ocr-000000?style=flat-square&logo=ollama&logoColor=white)](https://ollama.com)
[![macOS](https://img.shields.io/badge/macOS-Supported-999999?style=flat-square&logo=apple)](https://www.apple.com/macos/)
[![Linux](https://img.shields.io/badge/Linux-Debian%20%2F%20Ubuntu-E95420?style=flat-square&logo=ubuntu)](https://ubuntu.com)
[![License](https://img.shields.io/badge/License-MIT-22c55e?style=flat-square)](LICENSE)

</div>

---

## ✨ Features

| Feature | Details |
|---|---|
| 🔒 **100% Local** | All processing happens on-device via Ollama. Zero cloud calls. |
| ⚡ **Parallel Processing** | PDF pages are pre-encoded concurrently to minimize idle time between OCR calls |
| 📂 **Queue System** | Drop multiple PDFs at once — they are processed FIFO without conflicts |
| 🔔 **Native Notifications** | macOS push notifications for job start, completion and errors |
| 🗑️ **Auto-VRAM Cleanup** | GPU memory is freed immediately after each job (`keep_alive=0`) |
| 🛠️ **Config File** | All settings in a single `config.env` — no code editing needed |
| 🔄 **Auto-restart** | Service restarts automatically on crash via `launchd` / `systemd` |
| 🐧 **Cross-Platform** | Single installer for macOS (launchd) and Debian/Ubuntu (systemd) |

---

## 🚀 Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/MatteoTomassetti25/anton-ocr.git
cd anton-ocr

# 2. (Optional) Edit config.env to set your output directory
nano config.env

# 3. Run the installer — it handles everything
chmod +x install.sh
./install.sh
```

That's it. The daemon is now running in the background.

```bash
# Drop a PDF and watch the magic happen
cp ~/Downloads/lecture.pdf ~/anton-ocr/input/
```

---

## ⚙️ Configuration

Edit `config.env` before (or after) installation:

```bash
# Where to drop PDF files
INPUT_DIR=~/anton-ocr/input

# Where Markdown files are saved (e.g. your Obsidian vault)
OUTPUT_DIR=~/anton-ocr/output

# Ollama model
MODEL=glm-ocr:latest

# Context window — MUST be >= 16384 for glm-ocr
NUM_CTX=16384

# DPI for PDF rendering: 150 (fast) or 200 (quality)
DPI=200
```

After editing, re-run `./install.sh` to apply changes.

---

## 🧱 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       Anton OCR Daemon                          │
│                                                                 │
│  ┌──────────┐   FS event   ┌────────────┐   FIFO   ┌────────┐  │
│  │ ~/input  │ ──────────▶  │  Watchdog  │ ────────▶ │ Queue  │  │
│  └──────────┘              └────────────┘           └───┬────┘  │
│                                                         │       │
│            ┌────────────────────────────────────────────┘       │
│            ▼                                                     │
│  ┌─────────────────┐    parallel    ┌──────────────────┐        │
│  │  PDF → Images   │ ─────────────▶ │  base64 encoder  │        │
│  │  (pdf2image)    │  ThreadPool    │  (4 workers)     │        │
│  └─────────────────┘                └────────┬─────────┘        │
│                                              │ sequential        │
│                                              ▼                   │
│                                    ┌──────────────────┐         │
│                                    │  Ollama glm-ocr  │         │
│                                    │  Text Recognition│         │
│                                    └────────┬─────────┘         │
│                                             │                    │
│                           ┌─────────────────┘                   │
│                           ▼                                      │
│              ┌─────────────────────┐  ┌────────────────┐        │
│              │   Markdown output   │  │  VRAM Unload   │        │
│              │   (~/output/*.md)   │  │  keep_alive=0  │        │
│              └─────────────────────┘  └────────────────┘        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📋 Requirements

| Component | Minimum | Notes |
|---|---|---|
| Python | 3.10+ | Included in installer |
| Ollama | Latest | Auto-installed |
| RAM | 4 GB | 8 GB recommended |
| GPU | Not required | Apple Silicon / NVIDIA/AMD accelerated if available |
| Poppler | Any | Auto-installed via Homebrew or apt |

---

## 🖥️ Platform Support

### macOS
- Runs as a **LaunchAgent** (user-level background service)
- Starts automatically at login
- Native push notifications via `osascript`

### Linux (Debian / Ubuntu)
- Runs as a **systemd user service**
- Starts automatically at login
- No notification support (silent mode)

---

## 🔧 Service Management

### macOS
```bash
# Check status
launchctl list | grep com.anton.ocr

# View live logs
tail -f ~/anton-ocr/logs/daemon.log

# Restart
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.anton.ocr.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.anton.ocr.plist
```

### Linux
```bash
# Check status
systemctl --user status anton-ocr

# View live logs
journalctl --user -u anton-ocr -f

# Restart
systemctl --user restart anton-ocr
```

---

## 🗑️ Uninstall

```bash
chmod +x uninstall.sh
./uninstall.sh
```

Your files in `~/anton-ocr/` are preserved.

---

## 📁 Project Structure

```
anton-ocr/
├── ocr_daemon.py          # Main daemon (queue, pipeline, OCR)
├── config.env             # User configuration
├── install.sh             # Cross-platform installer
├── uninstall.sh           # Uninstaller
├── launchd/
│   └── com.anton.ocr.plist  # macOS LaunchAgent template
├── systemd/
│   └── anton-ocr.service    # Linux systemd unit template
└── logs/                  # Runtime logs (auto-created)
```

---

## 🐛 Troubleshooting

**OCR output is empty or garbled**
> Ensure `NUM_CTX=16384` or higher in `config.env`. The default Ollama context (4096) is insufficient for image processing with `glm-ocr`.

**`No module named 'ollama'` error**
> The daemon is using the wrong Python binary. Re-run `./install.sh` to regenerate the service with the correct virtualenv path.

**Ollama is not responding**
> On macOS, open the Ollama app manually once. On Linux, run: `sudo systemctl enable --now ollama`

**PDF not detected**
> Ensure the file is fully written to disk before processing. The daemon waits for file size to stabilize automatically.

---

## 📄 License

MIT © [Matteo Tomassetti](https://github.com/MatteoTomassetti25)
