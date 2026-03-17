# Discord Webhook Uploader

A Python desktop application for monitoring a folder and automatically uploading files to a Discord webhook, with a Windows-focused GUI, tray integration, and versioned release history.

## Overview

This repository contains the current application entry point and a reconstructed release history from **v1.0** through **v2.0.5**.

The older versions are preserved through the Git commit history, tags, and releases rather than being stored as duplicate source files in the latest branch snapshot.

## Features

- Automatic folder monitoring
- Manual instant upload
- Discord webhook integration
- Desktop graphical interface
- System tray support
- Run / pause control
- Local configuration handling
- Historical version tracking through Git tags and releases

## Repository Structure

```text
Discord-Webhook-Uploader/
├─ main.py
├─ README.md
├─ CHANGELOG.md
├─ .gitignore
├─ requirements.txt
└─ LICENSE
```

## Tech Stack

- Python
- PySide6
- Requests
- Send2Trash
- Pillow
- PyStray
- CustomTkinter

## Requirements

- Windows 10 or Windows 11
- Python 3.10 or newer

## Installation

Clone the repository:

```bash
git clone <REPO_URL>
```

Enter the project folder:

```bash
cd discord-webhook-uploader
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Running

Run the current version:

```bash
python main.py
```

## Version History

The project history is tracked through:

- Git commit history
- Git tags
- GitHub releases
- `CHANGELOG.md`

## Notes

- This project is focused on desktop usage on Windows.
- Older source snapshots were used to reconstruct the changelog and release history.
- Review older versions for sensitive data before making the repository public.

## License

The project license is defined in `LICENSE`.
