# Changelog

All notable changes to this project will be documented in this file.

## [0.5.0] - 2026-02-27

### Added
- **Concurrent Downloads**: Download multiple files simultaneously (1, 3, 5, 10, or 20). Configurable in settings.
- **File Search**: Search for files and folders across current directory, subdirectories, or entire site. Supports substring matching and glob patterns.
- **Extension Filter**: Only download specific file types (e.g., `zip,7z,iso`).
- **Skip Existing / Resume**: Skip fully downloaded files and automatically resume partial downloads.
- **Per-File Progress**: Individual progress widgets for each file during concurrent downloads.
- **Script Installer**: `install.sh` (Linux/macOS) and `install.bat` (Windows) for easy setup with `myrient-cli` command.

### Changed
- Download worker now scans all directories first, then downloads files (allows concurrent processing).
- Settings screen expanded with concurrent downloads, extension filter, and skip existing options.
- Search uses substring matching by default, glob patterns when wildcards are present.

### Removed
- Removed pip/PyPI installation method in favor of script installer.
- Removed download history logging.

## [0.4.0] - 2025-12-04

### Added
- **Network Reliability**:
    - Added 30-second timeout to all network requests (get, head) to prevent hanging.
    - Implemented a retry loop (3 attempts) for file downloads to handle interruptions.
    - Added robust resume logic to handle cases where servers ignore Range headers.
- **Packaging**: Added `publish.sh` to create PyPI package and setup scripts.
- **UI/UX**:
    - Added type-ahead functionality to match file names in the list.
    - Added progress bar when loading directories.
    - Added version number display.

### Changed
- Renamed `myrient_dl.py` script to `myrient.py`.
- Improved error reporting and recovery within the worker.

### Removed
- Removed `settings.json` from the folder and added it to `.gitignore`.
