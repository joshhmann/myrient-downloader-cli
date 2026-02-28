# Myrient Downloader CLI

A terminal-based (TUI) downloader for [Myrient](https://myrient.erista.me/), written in Python using the [Textual](https://textual.textualize.io/) framework.

Browse the Myrient file repository directly from your terminal and download files with concurrent downloads, resume support, and smart search.

## Features

- **Terminal User Interface**: Clean TUI with mouse and keyboard support.
- **Concurrent Downloads**: Download multiple files at once (configurable: 1, 3, 5, 10, or 20 simultaneous downloads).
- **Resume & Continue**: Automatically resumes interrupted downloads. Re-run a folder download and it picks up where it left off — complete files are skipped, partial files are resumed.
- **Recursive Download**: Download entire folders and their subfolders with a single keypress.
- **File Search**: Search for files and folders by name across the current directory, subdirectories, or the entire site. Supports substring matching and glob patterns (`*.zip`, `snes*`).
- **Extension Filter**: Only download specific file types (e.g., `zip,7z,iso`).
- **Skip Existing**: Intelligently skips already-downloaded files by verifying completeness against the server.
- **Smart Navigation**: Type-ahead search to quickly jump to items in the current listing.
- **Progress Tracking**: Real-time progress bars — per-file progress widgets for concurrent downloads.
- **Settings Persistence**: Saves all preferences (destination, concurrent count, filters) to `settings.json`.

## Installation

### Requirements

- Python 3.8+
- git

### Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/joshhmann/myrient-downloader-cli/master/install.sh | bash
```

Or manually:

```bash
git clone https://github.com/joshhmann/myrient-downloader-cli.git ~/.myrient-cli
cd ~/.myrient-cli
pip install -r requirements.txt
```

### Windows

Download and run `install.bat` from the repository, or manually:

```bash
git clone https://github.com/joshhmann/myrient-downloader-cli.git %USERPROFILE%\.myrient-cli
cd %USERPROFILE%\.myrient-cli
pip install -r requirements.txt
```

## Usage

After installation:

```bash
myrient-cli
```

Or run directly:

```bash
python myrient.py
```

### Controls

| Key | Action |
| :--- | :--- |
| `↑` / `↓` | Navigate the file list |
| `Enter` | Open folder / Download file |
| `Type...` | Type-ahead search (jumps to match in current view) |
| `/` | **Search** files and folders (substring or glob pattern) |
| `Ctrl+D` | **Download** current folder recursively |
| `?` | **Settings** (destination, concurrent downloads, filters) |
| `Backspace` | Go to parent folder |
| `Esc` | Clear search / Stop download (double press) |
| `Ctrl+Q` | Quit |

## Settings

Press `?` to configure:

| Setting | Description |
| :--- | :--- |
| **Destination Folder** | Where downloads are saved |
| **Concurrent Downloads** | Number of simultaneous downloads (1, 3, 5, 10, 20) |
| **File Extensions** | Only download these types (e.g., `zip,7z,iso`). Leave blank for all. |
| **Skip Existing Files** | Skip fully downloaded files, resume partial ones |

Settings are saved to `settings.json` in the app directory.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Disclaimer

This tool is an unofficial client and is not affiliated with Myrient or Erista. Please respect their service terms and bandwidth.
