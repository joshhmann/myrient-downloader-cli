#!/usr/bin/env python3

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote, urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.events import Key
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Select,
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    Static,
)
from textual.worker import get_current_worker
from urllib3.util.retry import Retry

BASE_URL = "https://myrient.erista.me/files/"
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
VERSION = "0.6.0"
DEFAULT_MAX_REQUESTS_PER_SECOND = 1.0
DEFAULT_RCLONE_TPS_LIMIT = 1.0
LARGE_FILE_MIN_BYTES = 1024 * 1024 * 1024
DEFAULT_LARGE_FILE_PARALLEL_LIMIT = 3
MANIFEST_FILENAME = ".myrient-downloaded.jsonl"
CONCURRENCY_GOVERNOR_MAP = {
    1: {"python_rps": 8.0, "rclone_rps": 0.0, "large_parallel": 1},
    5: {"python_rps": 40.0, "rclone_rps": 0.0, "large_parallel": 2},
    8: {"python_rps": 60.0, "rclone_rps": 0.0, "large_parallel": 3},
    16: {"python_rps": 100.0, "rclone_rps": 0.0, "large_parallel": 4},
    20: {"python_rps": 140.0, "rclone_rps": 0.0, "large_parallel": 5},
    32: {"python_rps": 220.0, "rclone_rps": 0.0, "large_parallel": 5},
}


class SettingsScreen(ModalScreen):
    BINDINGS = [("escape", "close_settings", "Close")]

    def __init__(
        self,
        current_dest,
        current_concurrent=16,
        current_extensions="",
        current_skip_existing=False,
        current_rclone_path="rclone",
        current_use_turbo=False,
        current_rclone_remote=":http:",
        current_operation_mode="download",
    ):
        super().__init__()
        self.current_dest = current_dest
        self.current_concurrent = current_concurrent
        self.current_extensions = current_extensions
        self.current_skip_existing = current_skip_existing
        self.current_rclone_path = current_rclone_path
        self.current_use_turbo = current_use_turbo
        self.current_rclone_remote = current_rclone_remote
        self.current_operation_mode = current_operation_mode

    def compose(self) -> ComposeResult:
        with Container(id="settings-dialog"):
            yield Label("Settings", id="settings-title")
            yield Label("Destination Folder:")
            yield Input(value=str(self.current_dest), id="dest-input")
            
            with Horizontal():
                with Vertical():
                    yield Label("Concurrent (Python):")
                    yield Select(
                        [("1", 1), ("5", 5), ("8", 8), ("16", 16), ("20", 20), ("32", 32)],
                        value=self.current_concurrent if self.current_concurrent in [1, 5, 8, 16, 20, 32] else 16,
                        id="concurrent-select"
                    )
                with Vertical():
                    yield Label("Turbo (rclone):")
                    yield Select(
                        [("Off", False), ("On", True)],
                        value=self.current_use_turbo,
                        id="turbo-select"
                    )
                with Vertical():
                    yield Label("Operation Mode:")
                    yield Select(
                        [("Download Files", "download"), ("Export Links", "links")],
                        value=self.current_operation_mode
                        if self.current_operation_mode in ["download", "links"]
                        else "download",
                        id="operation-mode-select",
                    )

            with Horizontal():
                with Vertical():
                    yield Label("Rclone Path:")
                    yield Input(value=str(self.current_rclone_path), id="rclone-path-input")
                with Vertical():
                    yield Label("Rclone Remote:")
                    yield Input(value=str(self.current_rclone_remote), id="rclone-remote-input", placeholder=":http: or myrient:")

            yield Label("File Extensions (e.g., zip,7z):")
            yield Input(value=self.current_extensions, id="extensions-input")
            
            yield Label("Skip Existing Files:")
            yield Select(
                [("No", False), ("Yes", True)],
                value=self.current_skip_existing,
                id="skip-existing-select"
            )
            with Horizontal(id="settings-buttons"):
                yield Button("Save", variant="primary", id="save-btn")
                yield Button("Cancel", variant="error", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            new_dest = self.query_one("#dest-input", Input).value
            new_concurrent = self.query_one("#concurrent-select", Select).value
            new_extensions = self.query_one("#extensions-input", Input).value
            new_skip_existing = self.query_one("#skip-existing-select", Select).value
            new_rclone_path = self.query_one("#rclone-path-input", Input).value
            new_use_turbo = self.query_one("#turbo-select", Select).value
            new_rclone_remote = self.query_one("#rclone-remote-input", Input).value
            new_operation_mode = self.query_one("#operation-mode-select", Select).value
            self.dismiss(
                (
                    new_dest,
                    new_concurrent,
                    new_extensions,
                    new_skip_existing,
                    new_rclone_path,
                    new_use_turbo,
                    new_rclone_remote,
                    new_operation_mode,
                )
            )
        else:
            self.dismiss(None)

    def action_close_settings(self):
        self.dismiss(None)


class SearchScreen(ModalScreen):
    """Screen for searching files recursively."""
    BINDINGS = [("escape", "close_search", "Close")]

    def __init__(self, current_url):
        super().__init__()
        self.current_url = current_url

    def compose(self) -> ComposeResult:
        with Container(id="search-dialog"):
            yield Label("Search Files", id="search-title")
            yield Label("Enter search pattern (wildcards supported):")
            yield Input(placeholder="*.zip or PS2", id="search-input")
            with Horizontal(id="search-options"):
                yield Label("Scope:")
                yield Select(
                    [("Current dir", "current"), ("Current & subdirs", "recursive"), ("Entire site", "all")],
                    value="current",
                    id="search-scope"
                )
            with Horizontal(id="search-buttons"):
                yield Button("Search", variant="primary", id="search-btn")
                yield Button("Cancel", variant="error", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "search-btn":
            pattern = self.query_one("#search-input", Input).value.strip()
            scope = self.query_one("#search-scope", Select).value
            if pattern:
                self.dismiss(("search", pattern, scope))
        else:
            self.dismiss(None)

    def action_close_search(self):
        self.dismiss(None)


class MyrientDownloader(App):
    TITLE = f"Myrient Downloader v{VERSION}"
    CSS = """
    #settings-dialog {
        padding: 1;
        border: solid green;
        width: 60;
        height: auto;
        background: $surface;
        align: center middle;
    }
    #settings-buttons {
        margin-top: 1;
        align: center middle;
    }
    Button {
        margin: 1;
    }
    DataTable {
        border: solid blue;
        height: 1fr;
    }
    #status-bar {
        height: auto;
        dock: bottom;
        background: $primary-darken-2;
        color: white;
        padding: 0 1;
    }
    ProgressBar {
        width: 100%;
        margin: 1 0;
        display: none;
    }
    .downloading ProgressBar {
        display: block;
    }
    #search-dialog {
        padding: 1;
        border: solid blue;
        width: 80;
        height: auto;
        background: $surface;
        align: center middle;
    }
    #search-title {
        text-align: center;
        text-style: bold;
        color: $text;
    }
    #search-buttons {
        margin-top: 1;
        align: center middle;
    }
    #rate-text {
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("ctrl+d", "download_folder", "Download Folder"),
        Binding("?", "open_settings", "Settings"),
        Binding("escape", "handle_esc", "Stop/Clear"),
        Binding("backspace", "go_up", "Up"),
        Binding("ctrl+q", "handle_quit", "Quit"),
        Binding("slash", "open_search", "Search"),
    ]

    current_url = reactive(BASE_URL)
    destination_folder = reactive(os.getcwd())
    concurrent_downloads = reactive(16)
    file_extensions = reactive("")
    skip_existing_files = reactive(False)
    rclone_path = reactive("rclone")
    rclone_remote = reactive(":http:")
    use_turbo = reactive(False)
    operation_mode = reactive("download")
    max_requests_per_second = reactive(DEFAULT_MAX_REQUESTS_PER_SECOND)
    rclone_tps_limit = reactive(DEFAULT_RCLONE_TPS_LIMIT)
    large_file_parallel_limit = reactive(DEFAULT_LARGE_FILE_PARALLEL_LIMIT)
    download_queue = []
    is_downloading = reactive(False)
    is_loading_dir = reactive(False)
    last_esc_time = 0
    download_lock = threading.Lock()
    total_files_to_download = 0
    files_completed = 0
    files_failed = 0
    files_skipped = 0
    # Retry counter per file
    retry_counts = {}
    # File listing data
    row_data = {}
    # Thread-local storage for sessions (each thread gets its own session)
    _thread_local = threading.local()
    large_file_semaphore = None
    manifest_lock = threading.Lock()
    downloaded_manifest = {}
    manifest_path = ""
    request_rate_lock = threading.Lock()
    next_request_allowed_at = 0.0
    request_timestamps = deque()
    last_rate_ui_update = 0.0

    search_query = ""
    last_search_time = 0
    SEARCH_TIMEOUT = 1.5

    def watch_is_loading_dir(self, value: bool) -> None:
        progress_bar = self.query_one("#progress", ProgressBar)
        status_text = self.query_one("#status-text", Label)

        if value:
            if not self.is_downloading:
                progress_bar.display = True
                progress_bar.total = None  # Indeterminate
                status_text.update("Loading directory...")
        else:
            if not self.is_downloading:
                progress_bar.display = False
                progress_bar.total = 100
                status_text.update("Ready")
                self._update_rate_indicator()
            else:
                # Restore download status if needed
                pass

    def show_error(self, message):
        self.notify(message, severity="error")
        self.log.error(message)

    def _update_status_label(self, message):
        """Update status label from any thread."""
        def _update():
            try:
                self.query_one("#status-text", Label).update(message)
            except Exception:
                pass
        self.app.call_from_thread(_update)

    def _show_error_safe(self, message):
        """Show error from any thread."""
        self.app.call_from_thread(lambda: self.show_error(message))

    def _set_progress_values(self, done, total):
        try:
            bar = self.query_one("#progress", ProgressBar)
            if total > 0:
                bar.total = total
                bar.progress = done
            else:
                bar.total = None
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(f"Current: {self.current_url}", id="url-label")
        yield DataTable(id="file-list", cursor_type="row")
        with Container(id="status-bar"):
            yield Label("Ready", id="status-text")
            yield Label("", id="rate-text")
            yield ProgressBar(total=100, show_eta=True, id="progress")
        yield Footer()

    def on_mount(self):
        self.load_settings()
        self._load_download_manifest()
        self._update_rate_indicator()
        table = self.query_one("#file-list", DataTable)
        table.add_columns("Name", "Size")
        self.load_directory_worker(self.current_url)

    def load_settings(self):
        try:
            # Default rclone path check
            default_rclone = "rclone"
            local_rclone = os.path.join(os.path.dirname(__file__), "bin", "rclone")
            if os.path.exists(local_rclone):
                default_rclone = local_rclone
            elif sys.platform == "win32":
                local_rclone_win = os.path.join(os.path.dirname(__file__), "bin", "rclone.exe")
                if os.path.exists(local_rclone_win):
                    default_rclone = local_rclone_win

            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, "r") as f:
                    settings = json.load(f)
                    # Handle case where old settings might have corrupted destination_folder
                    dest = settings.get("destination_folder", os.getcwd())
                    if isinstance(dest, (list, tuple)):
                        dest = dest[0] if len(dest) > 0 else os.getcwd()
                    self.destination_folder = dest
                    self.current_url = settings.get("last_url", BASE_URL)
                    self.concurrent_downloads = self._parse_int_setting(
                        settings.get("concurrent_downloads", 16),
                        16,
                        minimum=1,
                        maximum=32,
                    )
                    self.file_extensions = settings.get("file_extensions", "")
                    self.skip_existing_files = settings.get("skip_existing_files", False)
                    self.rclone_path = settings.get("rclone_path", default_rclone)
                    self.rclone_remote = settings.get("rclone_remote", ":http:")
                    self.use_turbo = settings.get("use_turbo", False)
                    self.operation_mode = (
                        settings.get("operation_mode", "download")
                        if settings.get("operation_mode", "download") in ["download", "links"]
                        else "download"
                    )
                    self._apply_governors_from_concurrency()
            else:
                self.rclone_path = default_rclone
                self._apply_governors_from_concurrency()
        except Exception as e:
            self.show_error(f"Error loading settings: {e}")

    def save_settings(self):
        try:
            settings = {
                "destination_folder": self.destination_folder,
                "last_url": self.current_url,
                "concurrent_downloads": self.concurrent_downloads,
                "file_extensions": self.file_extensions,
                "skip_existing_files": self.skip_existing_files,
                "rclone_path": self.rclone_path,
                "rclone_remote": self.rclone_remote,
                "use_turbo": self.use_turbo,
                "operation_mode": self.operation_mode,
                "max_requests_per_second": self.max_requests_per_second,
                "rclone_tps_limit": self.rclone_tps_limit,
                "large_file_parallel_limit": self.large_file_parallel_limit,
            }
            with open(SETTINGS_FILE, "w") as f:
                json.dump(settings, f, indent=4)
        except Exception as e:
            self.show_error(f"Error saving settings: {e}")

    def _apply_governors_from_concurrency(self):
        values = CONCURRENCY_GOVERNOR_MAP.get(
            int(self.concurrent_downloads),
            CONCURRENCY_GOVERNOR_MAP[16],
        )
        self.max_requests_per_second = float(values["python_rps"])
        self.rclone_tps_limit = float(values["rclone_rps"])
        self.large_file_parallel_limit = int(values["large_parallel"])

    def _derive_rclone_workers(self):
        concurrency = max(1, int(self.concurrent_downloads))
        transfers = max(4, min(concurrency * 2, 64))
        checkers = max(8, min(transfers * 2, 128))
        return transfers, checkers

    def _get_manifest_path(self):
        return os.path.join(self.destination_folder, MANIFEST_FILENAME)

    def _load_download_manifest(self):
        manifest = {}
        path = self._get_manifest_path()
        self.manifest_path = path

        if not os.path.exists(path):
            self.downloaded_manifest = manifest
            return

        try:
            with open(path, "r", encoding="utf-8") as manifest_file:
                for raw in manifest_file:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    url = entry.get("url")
                    if url:
                        manifest[url] = entry
        except Exception as e:
            self.show_error(f"Error loading manifest: {e}")
            manifest = {}

        self.downloaded_manifest = manifest

    def _is_manifest_downloaded(self, url):
        with self.manifest_lock:
            entry = self.downloaded_manifest.get(url)
        if not entry:
            return False
        local_path = entry.get("local_path")
        if not local_path:
            return True
        return os.path.exists(local_path)

    def _record_download_manifest(self, url, local_path, size_bytes=None, status="done"):
        entry = {
            "url": url,
            "local_path": local_path,
            "size_bytes": size_bytes if isinstance(size_bytes, int) else None,
            "status": status,
            "timestamp": int(time.time()),
        }

        with self.manifest_lock:
            self.downloaded_manifest[url] = entry
            target_path = self._get_manifest_path()
            self.manifest_path = target_path
            try:
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with open(target_path, "a", encoding="utf-8") as manifest_file:
                    manifest_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception as e:
                self._show_error_safe(f"Error writing manifest: {e}")

    def _parse_int_setting(self, value, default, minimum=1, maximum=32):
        try:
            parsed = int(value)
            if parsed < minimum:
                return minimum
            if parsed > maximum:
                return maximum
            return parsed
        except (TypeError, ValueError):
            return default

    def _update_rate_indicator(self, observed_rps=None, active=False):
        rclone_cap_text = "off" if float(self.rclone_tps_limit) <= 0 else f"{self.rclone_tps_limit:.1f}/s"
        http_cap_text = "off" if float(self.max_requests_per_second) <= 0 else f"{self.max_requests_per_second:.1f}/s"
        base = (
            f"HTTP cap {http_cap_text} | "
            f"rclone cap {rclone_cap_text} | "
            f"large>=1GB x{self.large_file_parallel_limit}"
        )
        if active and observed_rps is not None:
            base = (
                f"HTTP now ~{observed_rps:.1f}/s (cap {http_cap_text}) | "
                f"rclone cap {rclone_cap_text} | "
                f"large>=1GB x{self.large_file_parallel_limit}"
            )

        def _update():
            try:
                self.query_one("#rate-text", Label).update(base)
            except Exception:
                pass

        try:
            self.app.call_from_thread(_update)
        except Exception:
            _update()

    def _wait_for_request_slot(self):
        if float(self.max_requests_per_second) <= 0:
            return
        min_interval = 1.0 / max(0.1, float(self.max_requests_per_second))
        with self.request_rate_lock:
            now = time.monotonic()
            while self.request_timestamps and now - self.request_timestamps[0] > 1.0:
                self.request_timestamps.popleft()
            self.request_timestamps.append(now)
            observed_rps = float(len(self.request_timestamps))

            wait_for = self.next_request_allowed_at - now
            if wait_for > 0:
                time.sleep(wait_for)
                now = time.monotonic()
            self.next_request_allowed_at = max(now, self.next_request_allowed_at) + min_interval

            if now - self.last_rate_ui_update >= 0.3:
                self.last_rate_ui_update = now
                self._update_rate_indicator(observed_rps=observed_rps, active=True)

    def _requests_get(self, url, **kwargs):
        self._wait_for_request_slot()
        return requests.get(url, **kwargs)

    def _session_get(self, session, url, **kwargs):
        self._wait_for_request_slot()
        return session.get(url, **kwargs)

    def _session_head(self, session, url, **kwargs):
        self._wait_for_request_slot()
        return session.head(url, **kwargs)

    def on_unmount(self):
        self.save_settings()

    def parse_directory_html(self, html_content, base_url):
        soup = BeautifulSoup(html_content, "html.parser")
        items = []

        # Try to find the content table first
        content_table = soup.find("table", id="list")

        if content_table:
            # Table parsing (preferred)
            rows = content_table.find_all("tr")
            for row in rows:
                link_td = row.find("td", class_="link")
                if not link_td:
                    continue

                link = link_td.find("a")
                if not link:
                    continue

                href = link.get("href")
                text = link.text.strip()

                size_td = row.find("td", class_="size")
                size = size_td.text.strip() if size_td else "-"

                if not href:
                    continue
                if href in [
                    "../",
                    "/",
                    "./",
                    "/donate/",
                    "/non-affiliation-disclaimer/",
                    "/dmca/",
                ]:
                    continue
                if "?" in href:
                    continue
                if text == "Parent Directory":
                    continue

                full_url = urljoin(base_url, href)
                is_dir = href.endswith("/")

                items.append((text, is_dir, full_url, size))
        else:
            # Fallback parsing
            links = soup.find_all("a")
            for link in links:
                href = link.get("href")
                text = link.text.strip()

                if not href:
                    continue

                classes = link.get("class", [])
                if classes and "menu" in classes:
                    continue

                if href in [
                    "../",
                    "/",
                    "./",
                    "/donate/",
                    "/non-affiliation-disclaimer/",
                    "/dmca/",
                ]:
                    continue
                if "?" in href:
                    continue
                if text == "Parent Directory":
                    continue

                full_url = urljoin(base_url, href)
                is_dir = href.endswith("/")

                items.append((text, is_dir, full_url, "-"))

        return items

    def _parse_size_to_bytes(self, size_text):
        if not size_text:
            return None
        normalized = str(size_text).strip().upper().replace("IB", "B")
        if normalized in {"-", "", "DIR"}:
            return None
        try:
            if normalized.endswith("KB"):
                return int(float(normalized[:-2].strip()) * 1024)
            if normalized.endswith("MB"):
                return int(float(normalized[:-2].strip()) * 1024 * 1024)
            if normalized.endswith("GB"):
                return int(float(normalized[:-2].strip()) * 1024 * 1024 * 1024)
            if normalized.endswith("TB"):
                return int(float(normalized[:-2].strip()) * 1024 * 1024 * 1024 * 1024)
            if normalized.endswith("B"):
                return int(float(normalized[:-1].strip()))
            return int(float(normalized.replace(",", "")))
        except ValueError:
            return None

    def _url_to_local_path(self, url):
        rel_path = url[len(BASE_URL):] if url.startswith(BASE_URL) else os.path.basename(url)
        rel_path = unquote(rel_path)
        return os.path.join(self.destination_folder, rel_path.replace("/", os.sep))

    @work(thread=True)
    def load_directory_worker(self, url):
        self.app.call_from_thread(setattr, self, "is_loading_dir", True)
        try:
            response = self._requests_get(url, timeout=30)
            response.raise_for_status()

            items = self.parse_directory_html(response.text, url)

            # Sort: directories first, then files
            items.sort(key=lambda x: (not x[1], x[0]))

            def update_ui():
                table = self.query_one("#file-list", DataTable)
                table.clear()

                # Update URL label
                self.query_one("#url-label", Label).update(f"Current: {url}")

                self.row_data = {}  # Reset the lookup

                for text_content, is_dir, full_url, size in items:
                    is_done = (not is_dir) and self._is_manifest_downloaded(full_url)
                    if is_dir:
                        icon = "📁 "
                    else:
                        icon = "✅ " if is_done else "📄 "
                    display_name = Text(icon)
                    display_name.append(text_content)

                    row_key = table.add_row(display_name, size)
                    self.row_data[row_key] = (
                        text_content,
                        is_dir,
                        full_url,
                        self._parse_size_to_bytes(size),
                    )

                table.focus()

            self.app.call_from_thread(update_ui)

        except Exception as e:
            self.app.call_from_thread(self.show_error, f"Error loading directory: {e}")
        finally:
            self.app.call_from_thread(setattr, self, "is_loading_dir", False)

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        row_key = event.row_key
        if row_key in self.row_data:
            name, is_dir, href, size_bytes = self.row_data[row_key]
            if is_dir:
                self.current_url = href
                self.load_directory_worker(self.current_url)
            else:
                if self.is_downloading:
                    self.notify(
                        "Download in progress. Please wait.", severity="warning"
                    )
                else:
                    if self.operation_mode == "links":
                        self.download_queue = [(name, is_dir, href, size_bytes)]
                        self.start_link_export_worker()
                    else:
                        self.download_queue = [(name, is_dir, href, size_bytes)]
                        self.start_download_worker()

    def action_go_up(self):
        if self.current_url == BASE_URL:
            self.notify("Already at root.", severity="warning")
            return

        # Go up one level
        if self.current_url.endswith("/"):
            parent = self.current_url.rstrip("/")
            parent = parent.rsplit("/", 1)[0] + "/"
        else:
            parent = self.current_url.rsplit("/", 1)[0] + "/"

        if not parent.startswith(BASE_URL):
            parent = BASE_URL

        self.current_url = parent
        self.load_directory_worker(self.current_url)

    def action_handle_esc(self):
        # Clear search if active
        if self.search_query:
            self.search_query = ""
            self.query_one("#status-text", Label).update("Search cleared")
            return

        now = time.time()
        if now - self.last_esc_time < 0.5:
            # Double ESC
            if self.is_downloading:
                self.stop_download()
        else:
            # Single ESC
            if self.is_downloading:
                self.notify("Press ESC again to stop download", severity="warning")
        self.last_esc_time = now

    def action_handle_quit(self):
        self.exit()

    def on_key(self, event: Key) -> None:
        if self.is_loading_dir or self.is_downloading:
            return

        if not event.character or not event.character.isprintable():
            return

        # Ignore if a modifier is pressed (except shift)
        # Textual Key event doesn't easily expose modifiers in a way that excludes ctrl/alt combinations
        # that produce characters, but usually printable chars are fine.
        # However, we want to avoid capturing keys that might be bindings if they weren't handled.
        # But on_key runs before bindings? No, usually after if not handled?
        # In Textual, App.on_key is a handler.

        now = time.time()
        if now - self.last_search_time > self.SEARCH_TIMEOUT:
            self.search_query = ""

        self.search_query += event.character
        self.last_search_time = now

        self.query_one("#status-text", Label).update(f"Searching: {self.search_query}")
        self.perform_search()

    def perform_search(self):
        if not self.search_query:
            return

        query = self.search_query.lower()
        table = self.query_one("#file-list", DataTable)

        # Iterate through row_data to find match
        for row_key, (name, is_dir, href, _size_bytes) in self.row_data.items():
            if name.lower().startswith(query):
                # Found match
                index = table.get_row_index(row_key)
                if index is not None:
                    table.move_cursor(row=index)
                return

    def action_open_settings(self):
        def set_settings(result):
            if result:
                (
                    new_dest,
                    new_concurrent,
                    new_extensions,
                    new_skip_existing,
                    new_rclone,
                    new_turbo,
                    new_remote,
                    new_operation_mode,
                ) = result
                previous_dest = self.destination_folder
                self.destination_folder = new_dest
                self.concurrent_downloads = self._parse_int_setting(
                    new_concurrent,
                    self.concurrent_downloads,
                    minimum=1,
                    maximum=32,
                )
                self.file_extensions = new_extensions
                self.skip_existing_files = new_skip_existing
                self.rclone_path = new_rclone
                self.use_turbo = new_turbo
                self.rclone_remote = new_remote
                self.operation_mode = (
                    new_operation_mode if new_operation_mode in ["download", "links"] else "download"
                )
                self._apply_governors_from_concurrency()
                if previous_dest != self.destination_folder:
                    self._load_download_manifest()
                self.save_settings()
                self._update_rate_indicator()
                self.load_directory_worker(self.current_url)
                self.notify(f"Settings saved. Destination: {self.destination_folder}")

        self.push_screen(
            SettingsScreen(
                self.destination_folder,
                self.concurrent_downloads,
                self.file_extensions,
                self.skip_existing_files,
                self.rclone_path,
                self.use_turbo,
                self.rclone_remote,
                self.operation_mode,
            ),
            set_settings,
        )

    def action_open_search(self):
        """Open search to find files recursively."""
        def do_search(result):
            if result and isinstance(result, tuple) and result[0] == "search":
                pattern, scope = result[1], result[2]
                self._run_search(pattern, scope)

        self.push_screen(SearchScreen(self.current_url), do_search)

    @work(thread=True)
    def _run_search(self, pattern, scope):
        """Run search in background thread to avoid freezing UI."""
        import fnmatch

        has_glob = any(c in pattern for c in '*?[')

        def matches(name, pat):
            if has_glob:
                return fnmatch.fnmatch(name.lower(), pat.lower())
            else:
                return pat.lower() in name.lower()

        def search_directory(url, pat, recursive=False):
            results = []
            try:
                response = self._requests_get(url, timeout=30)
                response.raise_for_status()
                items = self.parse_directory_html(response.text, url)

                for name, is_dir, file_url, size in items:
                    if matches(name, pat):
                        results.append((name, file_url, url))

                    if is_dir and (recursive or scope == "all"):
                        sub_results = search_directory(file_url, pat, recursive=recursive)
                        results.extend(sub_results)

            except Exception as e:
                self._show_error_safe(f"Error searching {url}: {e}")

            return results

        self._update_status_label(f"Searching for '{pattern}'...")
        search_results = search_directory(self.current_url, pattern, scope == "recursive" or scope == "all")

        if search_results:
            def show_results():
                self.notify(f"Found {len(search_results)} matching files/folders!")
                name, file_url, parent_url = search_results[0]
                self.current_url = parent_url
                self.load_directory_worker(self.current_url)
                self.notify(f"Result 1 of {len(search_results)}: {name}")
            self.app.call_from_thread(show_results)
        else:
            self.app.call_from_thread(lambda: self.notify("No matching files found"))
            self._update_status_label("Ready")

    def action_download_folder(self):
        if self.is_downloading:
            self.notify("Already downloading!", severity="warning")
            return

        if self.operation_mode == "links":
            items_to_process = self._collect_current_items()

            if not items_to_process:
                self.notify("Nothing to export in this folder.", severity="warning")
                return

            self.download_queue = items_to_process
            self.start_link_export_worker()
            return

        # Turbo Mode (rclone)
        if self.use_turbo:
            self._run_rclone_folder(self.current_url)
            return

        # Collect all items in current view (files AND dirs)
        items_to_process = self._collect_current_items()

        if not items_to_process:
            self.notify("Nothing to download in this folder.", severity="warning")
            return

        self.download_queue = items_to_process
        self.start_download_worker()

    def _collect_current_items(self):
        items_to_process = []
        for _key, value in self.row_data.items():
            name, is_dir, href, size_bytes = value
            items_to_process.append((name, is_dir, href, size_bytes))
        return items_to_process

    def _fallback_to_python_download(self):
        items_to_process = self._collect_current_items()
        if not items_to_process:
            self.notify("Turbo fallback skipped: no items in current view.", severity="warning")
            return
        self.download_queue = items_to_process
        self.start_download_worker()

    def _collect_turbo_files(self, start_url, worker):
        files = []
        visited_dirs = set()
        pending_dirs = [start_url]

        while pending_dirs:
            if worker.is_cancelled:
                break
            dir_url = pending_dirs.pop()
            if dir_url in visited_dirs:
                continue
            visited_dirs.add(dir_url)

            scan_path = unquote(dir_url[len(BASE_URL):]) if dir_url.startswith(BASE_URL) else dir_url
            self._update_status_label(f"Turbo scan: {scan_path or '/'}")

            try:
                response = self._requests_get(dir_url, timeout=30)
                response.raise_for_status()
                items = self.parse_directory_html(response.text, dir_url)
            except Exception as e:
                self._show_error_safe(f"Turbo scan error: {e}")
                continue

            for name, is_dir, item_url, size_text in items:
                if is_dir:
                    pending_dirs.append(item_url)
                elif self.should_download_file(name):
                    files.append((name, item_url, self._parse_size_to_bytes(size_text)))

        return files

    def _run_rclone_copyurl_turbo(self, start_url, worker):
        files = self._collect_turbo_files(start_url, worker)
        if not files:
            self.app.call_from_thread(
                self.notify,
                "Turbo found no files to download (after filters).",
                severity="warning",
            )
            return 0, 0

        max_workers = max(1, self.concurrent_downloads)
        done_count = 0
        fail_count = 0
        previous_large_semaphore = self.large_file_semaphore
        self.large_file_semaphore = threading.Semaphore(max(1, self.large_file_parallel_limit))
        self.retry_counts = {}

        def run_download(file_info):
            name, file_url, size_bytes = file_info
            if worker.is_cancelled:
                return "cancelled", name, "Cancelled", file_url, None, size_bytes

            rel_path = unquote(file_url[len(BASE_URL):]) if file_url.startswith(BASE_URL) else name
            local_path = os.path.join(self.destination_folder, rel_path.replace("/", os.sep))
            try:
                result = self.download_single_file(file_info, worker)
            except Exception as e:
                return "failed", name, str(e), file_url, local_path, size_bytes

            status, _name, error, downloaded_size = result
            if status is True:
                final_size = downloaded_size if downloaded_size is not None else size_bytes
                return "done", name, None, file_url, local_path, final_size
            if status == "skipped":
                final_size = downloaded_size if downloaded_size is not None else size_bytes
                return "skipped", name, None, file_url, local_path, final_size
            return "failed", name, error or "Unknown error", file_url, local_path, downloaded_size

        total = len(files)
        self.app.call_from_thread(lambda: self._set_progress_values(0, total))
        self._update_status_label(
            f"Turbo fallback: in-process downloader ({max_workers} workers, no per-file rclone spawn)"
        )
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(run_download, file_info) for file_info in files]
                for future in as_completed(futures):
                    if worker.is_cancelled:
                        break
                    status, name, error, file_url, local_path, file_size = future.result()
                    if status == "failed":
                        fail_count += 1
                        self._show_error_safe(f"Turbo failed: {name}: {error}")
                    elif status == "done":
                        done_count += 1
                        self._record_download_manifest(file_url, local_path, size_bytes=file_size, status="done")
                    elif status == "skipped":
                        done_count += 1
                        self._record_download_manifest(file_url, local_path, size_bytes=file_size, status="skipped")

                    progress_done = done_count + fail_count
                    self._update_status_label(f"Turbo [{progress_done}/{total}] {done_count} done, {fail_count} failed")
                    self.app.call_from_thread(lambda d=progress_done, t=total: self._set_progress_values(d, t))
        finally:
            self.large_file_semaphore = previous_large_semaphore

        return done_count, fail_count

    @work(thread=True, exclusive=True)
    def _run_rclone_folder(self, url):
        """Execute rclone turbo download."""
        # Check rclone is available before doing anything
        rclone_bin = self.rclone_path
        if not os.path.isfile(rclone_bin) and not shutil.which(rclone_bin):
            self.app.call_from_thread(
                self.show_error,
                f"rclone not found at '{rclone_bin}'. Install it or set the correct path in Settings."
            )
            return

        self.is_downloading = True
        self.app.call_from_thread(lambda: self.query_one("#status-bar").add_class("downloading"))
        self._update_status_label("Turbo Mode: Initializing rclone...")
        worker = get_current_worker()
        fallback_to_python = False

        try:
            # 1. Calculate relative path from BASE_URL
            rel_path_raw = url[len(BASE_URL):]
            rel_path_unquoted = unquote(rel_path_raw)
            
            # 2. Construct Source (handle remote syntax)
            remote = self.rclone_remote.strip()
            if not remote.endswith(":"):
                remote += ":"
            
            # rclone expects decoded paths, not URL-encoded ones
            clean_rel_path = rel_path_unquoted.lstrip("/")
            source = f"{remote}{clean_rel_path}"
            http_url_for_copy = BASE_URL

            if remote == ":http:":
                root_url = BASE_URL.split("/files/")[0] + "/"
                source_candidates = [
                    (f"{remote}{clean_rel_path}", BASE_URL),
                    (
                        f"{remote}{'files/' + clean_rel_path if clean_rel_path else 'files/'}",
                        root_url,
                    ),
                ]
                if clean_rel_path and not clean_rel_path.endswith("/"):
                    source_candidates.extend(
                        [
                            (f"{remote}{clean_rel_path}/", BASE_URL),
                            (f"{remote}files/{clean_rel_path}/", root_url),
                        ]
                    )

                selected = None
                for candidate_source, candidate_http_url in source_candidates:
                    probe_cmd = [
                        self.rclone_path,
                        "lsf",
                        candidate_source,
                        "--max-depth",
                        "1",
                        "--timeout",
                        "20s",
                        "--http-url",
                        candidate_http_url,
                        "--http-no-head",
                    ]
                    probe_result = subprocess.run(
                        probe_cmd,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    )
                    if probe_result.returncode == 0 and (probe_result.stdout or "").strip():
                        selected = (candidate_source, candidate_http_url)
                        break

                if selected is not None:
                    source, http_url_for_copy = selected
                else:
                    self._update_status_label("Turbo: native :http: probe failed, using copyurl fallback...")
                    done_count, fail_count = self._run_rclone_copyurl_turbo(url, worker)
                    if done_count > 0:
                        self.app.call_from_thread(
                            self.notify,
                            f"Turbo Download Complete! {done_count} files transferred ({fail_count} failed).",
                        )
                    elif fail_count > 0:
                        self.app.call_from_thread(
                            self.notify,
                            f"Turbo ended with 0 completed files ({fail_count} failed).",
                            severity="warning",
                        )
                    else:
                        self.app.call_from_thread(
                            self.notify,
                            "Turbo found no files to transfer.",
                            severity="warning",
                        )
                    return
            
            # 3. Construct Destination (Windows/Linux safe)
            dest = self.destination_folder
            if rel_path_unquoted:
                # Remove trailing slashes and normalize separators
                sub_path = rel_path_unquoted.strip("/").strip("\\").replace("/", os.sep)
                if sub_path:
                    dest = os.path.join(dest, sub_path)
            
            os.makedirs(dest, exist_ok=True)
            transfers, checkers = self._derive_rclone_workers()

            cmd = [
                self.rclone_path,
                "copy",
                source,
                dest,
                "--transfers",
                str(transfers),
                "--checkers",
                str(checkers),
                "--stats", "2s",
                "--progress",
                "--user-agent", "Mozilla/5.0",
                "--buffer-size", "32M",
                "-v",
            ]
            if float(self.rclone_tps_limit) > 0:
                tps_burst = max(1, min(int(self.rclone_tps_limit * 2), 20))
                cmd.extend(["--tpslimit", str(self.rclone_tps_limit), "--tpslimit-burst", str(tps_burst)])

            if remote == ":http:":
                cmd.extend(["--http-url", http_url_for_copy, "--http-no-head"])

            self._update_status_label("Turbo: Starting rclone...")

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )

            last_lines = []
            files_done = 0
            copied_files = set()
            transferred_count_re = re.compile(r"Transferred:\s*([\d,]+)\s*/\s*([\d,]+)")
            if process.stdout is not None:
                for raw_line in process.stdout:
                    if worker.is_cancelled:
                        process.terminate()
                        break

                    line_str = raw_line.strip()
                    if not line_str:
                        continue

                    last_lines.append(line_str)
                    if len(last_lines) > 30:
                        last_lines.pop(0)

                    if ": Copied (" in line_str or ": Copied(" in line_str:
                        parts = line_str.split(": Copied")
                        fname = parts[0].rsplit(":", 1)[-1].strip() if parts else ""
                        if fname:
                            copied_files.add(fname)
                            files_done = len(copied_files)
                        else:
                            files_done += 1
                        short = fname.rsplit("/", 1)[-1] if fname else ""
                        self._update_status_label(f"Turbo [{files_done}] done: {short}")
                    elif "Transferred:" in line_str or "Checks:" in line_str:
                        match = transferred_count_re.search(line_str)
                        if match:
                            # rclone stats lines are the most reliable count for large folders.
                            transferred_count = int(match.group(1).replace(",", ""))
                            files_done = max(files_done, transferred_count)
                        self._update_status_label(f"Turbo [{files_done} done] {line_str[:120]}")
                    elif "nothing to transfer" in line_str.lower():
                        self._update_status_label("Turbo: nothing to transfer (all files already present)")
                    elif "ERROR" in line_str:
                        self._update_status_label(f"Turbo [{files_done}] ERR: {line_str[:100]}")

            process.wait()

            if process.returncode == 0:
                nothing_to_transfer = any("nothing to transfer" in line.lower() for line in last_lines)
                if nothing_to_transfer:
                    self.app.call_from_thread(
                        self.notify,
                        "Turbo complete: nothing to transfer (files may already exist).",
                        severity="warning",
                    )
                elif files_done == 0:
                    if remote == ":http:":
                        self._update_status_label("Turbo native :http: transferred 0; trying copyurl fallback...")
                        done_count, fail_count = self._run_rclone_copyurl_turbo(url, worker)
                        if done_count > 0:
                            self.app.call_from_thread(
                                self.notify,
                                f"Turbo Download Complete! {done_count} files transferred ({fail_count} failed).",
                            )
                        elif fail_count > 0:
                            self.app.call_from_thread(
                                self.notify,
                                f"Turbo ended with 0 completed files ({fail_count} failed).",
                                severity="warning",
                            )
                        else:
                            self.app.call_from_thread(
                                self.notify,
                                "Turbo found no files to transfer.",
                                severity="warning",
                            )
                    else:
                        self.app.call_from_thread(
                            self.notify,
                            "Turbo transferred 0 files; falling back to Python downloader.",
                            severity="warning",
                        )
                        fallback_to_python = True
                else:
                    self.app.call_from_thread(
                        self.notify, f"Turbo Download Complete! {files_done} files transferred."
                    )
            else:
                error_msg = last_lines[-1] if last_lines else f"Exit Code {process.returncode}"
                if "directory not found" in error_msg.lower():
                    error_msg = "Source path not found on server."
                if remote == ":http:":
                    self._update_status_label(f"Turbo native :http: failed ({error_msg[:80]}), trying copyurl fallback...")
                    done_count, fail_count = self._run_rclone_copyurl_turbo(url, worker)
                    if done_count > 0:
                        self.app.call_from_thread(
                            self.notify,
                            f"Turbo Download Complete! {done_count} files transferred ({fail_count} failed).",
                        )
                    elif fail_count > 0:
                        self.app.call_from_thread(
                            self.notify,
                            f"Turbo failed and fallback also had {fail_count} failures.",
                            severity="error",
                        )
                    else:
                        self.show_error(f"Rclone Failed: {error_msg} (fallback found no files)")
                else:
                    if "source path not found" in error_msg.lower():
                        error_msg = "Source path not found on server. Try refreshing directory."
                    self.show_error(f"Rclone Failed: {error_msg}")
        except Exception as e:
            self.show_error(f"Turbo Mode Error: {e}")
        finally:
            self.is_downloading = False
            self.app.call_from_thread(lambda: self.query_one("#status-bar").remove_class("downloading"))
            self._update_rate_indicator()
            self._update_status_label("Ready")
            if fallback_to_python:
                self.app.call_from_thread(self._fallback_to_python_download)

    def get_retry_session(self, retries=5, backoff_factor=0.5):
        session = requests.Session()
        retry = Retry(
            total=retries,
            read=retries,
            connect=retries,
            backoff_factor=backoff_factor,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD"]),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def get_thread_local_session(self):
        """Get or create a thread-local session for concurrent downloads."""
        if not hasattr(self._thread_local, 'session') or self._thread_local.session is None:
            self._thread_local.session = self.get_retry_session()
        return self._thread_local.session

    def should_download_file(self, filename):
        """Check if file should be downloaded based on extension filter."""
        if not self.file_extensions:
            return True
        allowed_extensions = [ext.strip().lower().lstrip('.') for ext in self.file_extensions.split(',')]
        allowed_extensions = [ext for ext in allowed_extensions if ext]
        if not allowed_extensions:
            return True
        file_ext = filename.split('.')[-1].lower() if '.' in filename else ''
        return file_ext in allowed_extensions

    def download_single_file(self, file_info, worker, max_retries=3):
        """Download a single file - used by both sequential and concurrent modes."""
        if len(file_info) == 3:
            name, url, size_bytes = file_info
        else:
            name, url = file_info
            size_bytes = None
        session = self.get_thread_local_session()
        is_large_file = size_bytes is not None and size_bytes >= LARGE_FILE_MIN_BYTES
        acquired_large_slot = False

        if is_large_file and self.large_file_semaphore is not None:
            self._update_status_label(
                f"Waiting for large-file slot (limit {self.large_file_parallel_limit}): {name}"
            )
            self.large_file_semaphore.acquire()
            acquired_large_slot = True
        
        if not url.startswith(BASE_URL):
            return False, name, "Invalid URL", 0
        
        rel_path = url[len(BASE_URL):]
        rel_path = unquote(rel_path)
        filepath = os.path.join(self.destination_folder, rel_path)
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        local_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
        if self.skip_existing_files and local_size > 0:
            return "skipped", name, "File already exists", local_size

        retry_key = url
        with self.download_lock:
            if retry_key not in self.retry_counts:
                self.retry_counts[retry_key] = 0
        
        downloaded = local_size
        try:
            for attempt in range(max_retries):
                try:
                    resume_header = {"Range": f"bytes={local_size}-"} if local_size > 0 else {}
                    
                    with self._session_get(session, url, stream=True, headers=resume_header, timeout=(10, 30)) as r:
                        if r.status_code == 416:
                            # Might be finished, verify with one HEAD as fallback
                            head_resp = self._session_head(session, url, allow_redirects=True, timeout=(10, 15))
                            total_size = int(head_resp.headers.get("content-length", 0))
                            if local_size >= total_size:
                                return True, name, None, total_size
                            local_size = 0
                            continue

                        r.raise_for_status()
                        
                        if r.status_code == 200:
                            local_size = 0
                            mode = "wb"
                        else:
                            mode = "ab" if local_size > 0 else "wb"
                            
                        total_length = int(r.headers.get("content-length", 0))
                        if mode == "ab":
                            total_length += local_size
                        
                        downloaded = local_size
                        with open(filepath, mode) as f:
                            for chunk in r.iter_content(chunk_size=131072):
                                if worker.is_cancelled:
                                    return False, name, "Cancelled", downloaded
                                if chunk:
                                    f.write(chunk)
                                    downloaded += len(chunk)
                    
                    return True, name, None, total_length
                except Exception as e:
                    with self.download_lock:
                        self.retry_counts[retry_key] += 1
                    if attempt == max_retries - 1:
                        return False, name, str(e), downloaded
                    else:
                        time.sleep(1)
                        continue
            
            return False, name, "Max retries exceeded", 0
        finally:
            if acquired_large_slot and self.large_file_semaphore is not None:
                self.large_file_semaphore.release()

    def _scan_directories(self, initial_queue, file_queue, worker, scan_done_event):
        """Scanner: walks directories concurrently and feeds files into file_queue."""
        import queue as queue_module

        files_found = 0
        dirs_scanned = 0
        dir_queue = queue_module.Queue()
        scan_lock = threading.Lock()

        # Separate initial items into dirs and files
        for name, is_dir, url, size_bytes in initial_queue:
            if is_dir:
                dir_queue.put((name, url))
            elif self.should_download_file(name):
                files_found += 1
                with self.download_lock:
                    self.total_files_to_download = files_found
                file_queue.put((name, url, size_bytes))

        # Thread-local sessions for scanner threads
        scan_local = threading.local()

        def get_scan_session():
            if not hasattr(scan_local, 'session'):
                scan_local.session = self.get_retry_session()
            return scan_local.session

        def scan_one_dir(dir_name, dir_url):
            nonlocal files_found, dirs_scanned
            if worker.is_cancelled:
                return

            scan_path = (
                unquote(dir_url[len(BASE_URL):])
                if dir_url.startswith(BASE_URL)
                else dir_url
            )

            with scan_lock:
                dirs_scanned += 1
            self._update_status_label(
                f"Scanning ({files_found} files, {dirs_scanned} dirs): {scan_path}"
            )

            try:
                session = get_scan_session()
                response = self._session_get(session, dir_url, timeout=30)
                response.raise_for_status()
                items = self.parse_directory_html(response.text, dir_url)

                for item_name, item_is_dir, item_url, item_size in items:
                    if worker.is_cancelled:
                        return
                    if item_is_dir:
                        dir_queue.put((item_name, item_url))
                    elif self.should_download_file(item_name):
                        item_size_bytes = self._parse_size_to_bytes(item_size)
                        with scan_lock:
                            files_found += 1
                        with self.download_lock:
                            self.total_files_to_download = files_found
                        file_queue.put((item_name, item_url, item_size_bytes))

            except Exception as e:
                self._show_error_safe(f"Error scanning {dir_name}: {e}")

        SCAN_WORKERS = 16
        with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as executor:
            futures = set()

            while True:
                if worker.is_cancelled:
                    break

                # Submit any pending directories
                try:
                    while True:
                        dir_name, dir_url = dir_queue.get_nowait()
                        future = executor.submit(scan_one_dir, dir_name, dir_url)
                        futures.add(future)
                except queue_module.Empty:
                    pass

                # Clean up completed futures
                done = {f for f in futures if f.done()}
                for f in done:
                    futures.discard(f)

                # If no futures running and queue empty, we're done
                if not futures and dir_queue.empty():
                    break

                time.sleep(0.05)

        scan_done_event.set()

    def _build_links_output_path(self):
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        return os.path.join(self.destination_folder, f"myrient-links-{timestamp}.txt")

    @work(thread=True, exclusive=True)
    def start_link_export_worker(self):
        self.is_downloading = True
        self.query_one("#status-bar").add_class("downloading")
        progress_bar = self.query_one("#progress", ProgressBar)
        progress_bar.display = True

        try:
            import queue as queue_module

            scan_queue = list(self.download_queue)
            worker = get_current_worker()
            file_queue = queue_module.Queue()
            scan_done_event = threading.Event()

            self.total_files_to_download = 0
            self.files_completed = 0
            self.files_failed = 0
            self.files_skipped = 0

            scanner = threading.Thread(
                target=self._scan_directories,
                args=(scan_queue, file_queue, worker, scan_done_event),
                daemon=True,
            )
            scanner.start()

            link_results = []
            seen = set()

            while not (scan_done_event.is_set() and file_queue.empty()):
                if worker.is_cancelled:
                    return
                try:
                    name, url, _size_bytes = file_queue.get(timeout=0.1)
                except queue_module.Empty:
                    continue

                if url not in seen:
                    seen.add(url)
                    link_results.append(url)

                self.files_completed += 1
                done = self.files_completed
                total = self.total_files_to_download
                self._update_status_label(f"[{done}/{total}] collected link: {name}")
                self.app.call_from_thread(
                    lambda d=done, t=total: self._set_progress_values(d, t)
                )

            scanner.join(timeout=5)

            if not link_results:
                self.notify("No links matched current filters.", severity="warning")
            else:
                os.makedirs(self.destination_folder, exist_ok=True)
                output_path = self._build_links_output_path()
                with open(output_path, "w", encoding="utf-8") as out:
                    out.write("\n".join(link_results))
                    out.write("\n")
                self.notify(f"Exported {len(link_results)} links to {output_path}")

            self.is_downloading = False
            self.query_one("#status-bar").remove_class("downloading")
            progress_bar.display = False
            self.query_one("#status-text", Label).update("Ready")
            self._update_rate_indicator()

        except Exception as e:
            self.show_error(f"Link export worker crashed: {e}")
            self.is_downloading = False
            self.query_one("#status-bar").remove_class("downloading")
            progress_bar.display = False
            self.query_one("#status-text", Label).update("Error")
            self._update_rate_indicator()

    @work(thread=True, exclusive=True)
    def start_download_worker(self):
        self.is_downloading = True
        self.query_one("#status-bar").add_class("downloading")
        progress_bar = self.query_one("#progress", ProgressBar)
        progress_bar.display = True

        try:
            import queue as queue_module

            scan_queue = list(self.download_queue)
            worker = get_current_worker()
            file_queue = queue_module.Queue()
            scan_done_event = threading.Event()

            self.total_files_to_download = 0
            self.files_completed = 0
            self.files_failed = 0
            self.files_skipped = 0
            self.retry_counts = {}

            max_workers = max(1, self.concurrent_downloads)
            self.large_file_semaphore = threading.Semaphore(
                max(1, self.large_file_parallel_limit)
            )

            # Start scanner thread
            scanner = threading.Thread(
                target=self._scan_directories,
                args=(scan_queue, file_queue, worker, scan_done_event),
                daemon=True,
            )
            scanner.start()

            active_count = 0

            def _update_progress(current_file=None):
                done = self.files_completed + self.files_failed + self.files_skipped
                total = self.total_files_to_download
                
                # Update progress bar
                def _update_bar():
                    try:
                        bar = self.query_one("#progress", ProgressBar)
                        if total > 0:
                            bar.total = total
                            bar.progress = done
                        else:
                            bar.total = None
                    except Exception:
                        pass
                self.app.call_from_thread(_update_bar)

                # Update status label
                scanning = " scanning..." if not scan_done_event.is_set() else ""
                workers_info = f" [{active_count} active]" if max_workers > 1 else ""
                if current_file:
                    self._update_status_label(
                        f"[{done}/{total}{workers_info}{scanning}] {current_file}"
                    )
                else:
                    self._update_status_label(
                        f"[{done}/{total}{workers_info}{scanning}] "
                        f"{self.files_completed} done, {self.files_failed} failed, {self.files_skipped} skipped"
                    )

            def _handle_result(file_info, result):
                status, name, error, size = result
                _file_name, file_url, _file_size = file_info
                local_path = self._url_to_local_path(file_url)
                if status == "skipped":
                    with self.download_lock:
                        self.files_skipped += 1
                    self._record_download_manifest(file_url, local_path, size_bytes=size, status="skipped")
                elif status:
                    with self.download_lock:
                        self.files_completed += 1
                    self._record_download_manifest(file_url, local_path, size_bytes=size, status="done")
                else:
                    with self.download_lock:
                        self.files_failed += 1
                    self._show_error_safe(f"Failed: {name}: {error}")

            if max_workers > 1:
                # Concurrent: submit downloads as scanner finds files
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    active = {}
                    results_queue = queue_module.Queue()

                    def _wrap_download(file_info):
                        """Wrapper that puts results into a queue instead of requiring polling."""
                        try:
                            result = self.download_single_file(file_info, worker)
                            results_queue.put((file_info, result, None))
                        except Exception as e:
                            results_queue.put((file_info, None, e))

                    while not (scan_done_event.is_set() and file_queue.empty() and not active):
                        if worker.is_cancelled:
                            executor.shutdown(wait=False, cancel_futures=True)
                            return

                        # 1. Collect results
                        try:
                            while True:
                                file_info, result, error = results_queue.get_nowait()
                                if error:
                                    with self.download_lock:
                                        self.files_failed += 1
                                else:
                                    _handle_result(file_info, result)
                                _update_progress()
                        except queue_module.Empty:
                            pass

                        # 2. Clean up finished futures BEFORE submitting new ones
                        active = {f: info for f, info in active.items() if not f.done()}
                        active_count = len(active)

                        # 3. Fill the pool back up
                        while len(active) < max_workers:
                            try:
                                file_info = file_queue.get_nowait()
                                future = executor.submit(_wrap_download, file_info)
                                active[future] = file_info
                                _update_progress(file_info[0])
                            except queue_module.Empty:
                                break

                        # Brief sleep only when pool is full or waiting for work
                        if len(active) >= max_workers or (file_queue.empty() and active):
                            time.sleep(0.05)

            else:
                # Sequential: download files as scanner finds them
                while not (scan_done_event.is_set() and file_queue.empty()):
                    if worker.is_cancelled:
                        return

                    try:
                        file_info = file_queue.get(timeout=0.1)
                    except queue_module.Empty:
                        continue

                    name, url = file_info
                    _update_progress(name)

                    result = self.download_single_file(file_info, worker)
                    _handle_result(file_info, result)

            scanner.join(timeout=5)

            self.is_downloading = False
            self.query_one("#status-bar").remove_class("downloading")
            progress_bar.display = False
            self.query_one("#status-text", Label).update("Ready")
            self.notify(
                f"Done! {self.files_completed} downloaded, "
                f"{self.files_failed} failed, {self.files_skipped} skipped"
            )
            self._update_rate_indicator()
            self.large_file_semaphore = None

        except Exception as e:
            self.show_error(f"Download worker crashed: {e}")
            self.is_downloading = False
            self.query_one("#status-bar").remove_class("downloading")
            progress_bar.display = False
            self.query_one("#status-text", Label).update("Error")
            self._update_rate_indicator()
            self.large_file_semaphore = None

    def stop_download(self):
        """Cancel the download worker."""
        self.workers.cancel_all()
        self.is_downloading = False
        self.query_one("#status-bar").remove_class("downloading")
        self.query_one("#status-text", Label).update("Download stopped.")
        self.notify("Download stopped by user.")


def main():
    app = MyrientDownloader()
    app.run()


if __name__ == "__main__":
    main()
