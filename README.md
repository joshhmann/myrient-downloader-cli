# Myrient Downloader CLI

A terminal-based (TUI) downloader for [Myrient](https://myrient.erista.me/), written in Python using the [Textual](https://textual.textualize.io/) framework.

Browse the Myrient file repository directly from your terminal and download files with concurrent downloads, resume support, and smart search.

## Features

- **Terminal User Interface**: Clean TUI with mouse and keyboard support.
- **Turbo Mode (rclone)**: Use [rclone](https://rclone.org/) as the download engine for maximum speed on folder downloads.
- **Concurrent Downloads**: Download multiple files at once using Python-native or Turbo mode.
- **Resume & Continue**: Automatically resumes interrupted downloads. Complete files are skipped.
- **Recursive Download**: Download entire folders and their subfolders with a single keypress.
- **File Search**: Search for files and folders by name across the current directory or the entire site.
- **Extension Filter**: Only download specific file types (e.g., `zip,7z,iso`).
- **Request Throttling**: Global request-rate limits for Python and Turbo mode to avoid hammering Myrient.
- **Live Rate Indicator**: Status bar shows current HTTP request pace vs configured caps.
- **Download Manifest**: Tracks completed files in `.myrient-downloaded.jsonl` and marks them in-browser with `✅`.
- **Settings Persistence**: Saves all preferences (destination, turbo mode, rclone path, rate limits) to `settings.json`.

## Installation

The installer now automatically attempts to install `rclone` if it's not found on your system.

### Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/joshhmann/myrient-downloader-cli/master/install.sh | bash
```

### Windows

Download and run `install.bat` from the repository.

## Usage

After installation:

```bash
myrient-cli
```

### Turbo Mode (High Speed)

For the best performance, enable **Turbo Mode** in the settings:

1. Press `?` to open Settings.
2. Toggle **Turbo (rclone)** to **On**.
3. Ensure the **Rclone Path** is correct (usually `rclone` or the full path to the executable).
4. Save settings.
5. Navigate to a folder and press `Ctrl+D`.

Turbo Mode uses `rclone`'s high-concurrency engine to bypass Python's speed limitations.

### Controls

| Key | Action |
| :--- | :--- |
| `↑` / `↓` | Navigate the file list |
| `Enter` | Open folder / Download file |
| `Type...` | Type-ahead search (jumps to match in current view) |
| `/` | **Search** files and folders (substring or glob pattern) |
| `Ctrl+D` | Download folder or export links (based on mode) |
| `?` | **Settings** (destination, concurrency, rate limits, filters) |
| `Backspace` | Go to parent folder |
| `Esc` | Clear search / Stop download (double press) |
| `Ctrl+Q` | Quit |

## Settings

Press `?` to configure:

| Setting | Description |
| :--- | :--- |
| **Destination Folder** | Where downloads are saved |
| **Operation Mode** | `Download Files` or `Export Links` (writes URLs to `myrient-links-*.txt`) |
| **Concurrent Downloads** | Main speed dial (1, 5, 8, 16, 20, 32); request/turbo governors are auto-derived from this |
| **File Extensions** | Only download these types (e.g., `zip,7z,iso`). Leave blank for all. |
| **Skip Existing Files** | Skip fully downloaded files, resume partial ones |

Tip: raise `Concurrent Downloads` gradually (e.g., `8 -> 16 -> 20`) and watch stability.
Large files (>= `1 GB`) are still capped separately under the hood for safer parallelism.

Settings are saved to `settings.json` in the app directory.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Disclaimer

This tool is an unofficial client and is not affiliated with Myrient or Erista. Please respect their service terms and bandwidth.
