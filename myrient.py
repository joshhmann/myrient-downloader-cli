#!/usr/bin/env python3

import json
import os
import sys
import threading
import time
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
)
from textual.worker import get_current_worker
from urllib3.util.retry import Retry

BASE_URL = "https://myrient.erista.me/files/"
SETTINGS_FILE = "settings.json"
VERSION = "0.5.0"


class SettingsScreen(ModalScreen):
    BINDINGS = [("escape", "close_settings", "Close")]

    def __init__(self, current_dest, current_concurrent=1, current_extensions="", current_skip_existing=False):
        super().__init__()
        self.current_dest = current_dest
        self.current_concurrent = current_concurrent
        self.current_extensions = current_extensions
        self.current_skip_existing = current_skip_existing

    def compose(self) -> ComposeResult:
        with Container(id="settings-dialog"):
            yield Label("Settings", id="settings-title")
            yield Label("Destination Folder:")
            yield Input(value=self.current_dest, id="dest-input")
            yield Label("Concurrent Downloads:")
            yield Select(
                [("Sequential (1)", 1), ("3 files", 3), ("5 files", 5), ("10 files", 10), ("20 files", 20)],
                value=self.current_concurrent,
                id="concurrent-select"
            )
            yield Label("File Extensions (comma-separated, e.g., zip,7z,iso):")
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
            self.dismiss((new_dest, new_concurrent, new_extensions, new_skip_existing))
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
    concurrent_downloads = reactive(1)
    file_extensions = reactive("")
    skip_existing_files = reactive(False)
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
            else:
                # Restore download status if needed
                pass

    def show_error(self, message):
        self.notify(message, severity="error")
        print(f"ERROR: {message}", file=sys.stderr)

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

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(f"Current: {self.current_url}", id="url-label")
        yield DataTable(id="file-list", cursor_type="row")
        with Container(id="status-bar"):
            yield Label("Ready", id="status-text")
            yield ProgressBar(total=100, show_eta=True, id="progress")
        yield Footer()

    def on_mount(self):
        self.load_settings()
        table = self.query_one("#file-list", DataTable)
        table.add_columns("Name", "Size")
        self.load_directory_worker(self.current_url)

    def load_settings(self):
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, "r") as f:
                    settings = json.load(f)
                    # Handle case where old settings might have corrupted destination_folder
                    dest = settings.get("destination_folder", os.getcwd())
                    if isinstance(dest, (list, tuple)):
                        dest = dest[0] if len(dest) > 0 else os.getcwd()
                    self.destination_folder = dest
                    self.current_url = settings.get("last_url", BASE_URL)
                    self.concurrent_downloads = settings.get("concurrent_downloads", 1)
                    self.file_extensions = settings.get("file_extensions", "")
                    self.skip_existing_files = settings.get("skip_existing_files", False)
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
            }
            with open(SETTINGS_FILE, "w") as f:
                json.dump(settings, f, indent=4)
        except Exception as e:
            self.show_error(f"Error saving settings: {e}")

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

    @work(thread=True)
    def load_directory_worker(self, url):
        self.app.call_from_thread(setattr, self, "is_loading_dir", True)
        try:
            response = requests.get(url, timeout=30)
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
                    icon = "📁 " if is_dir else "📄 "
                    display_name = Text(icon)
                    display_name.append(text_content)

                    row_key = table.add_row(display_name, size)
                    self.row_data[row_key] = (text_content, is_dir, full_url)

                table.focus()

            self.app.call_from_thread(update_ui)

        except Exception as e:
            self.app.call_from_thread(self.show_error, f"Error loading directory: {e}")
        finally:
            self.app.call_from_thread(setattr, self, "is_loading_dir", False)

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        row_key = event.row_key
        if row_key in self.row_data:
            name, is_dir, href = self.row_data[row_key]
            if is_dir:
                self.current_url = href
                self.load_directory_worker(self.current_url)
            else:
                if self.is_downloading:
                    self.notify(
                        "Download in progress. Please wait.", severity="warning"
                    )
                else:
                    self.download_queue = [(name, is_dir, href)]
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
        for row_key, (name, is_dir, href) in self.row_data.items():
            if name.lower().startswith(query):
                # Found match
                index = table.get_row_index(row_key)
                if index is not None:
                    table.move_cursor(row=index)
                return

    def action_open_settings(self):
        def set_settings(result):
            if result:
                new_dest, new_concurrent, new_extensions, new_skip_existing = result
                self.destination_folder = new_dest
                self.concurrent_downloads = new_concurrent
                self.file_extensions = new_extensions
                self.skip_existing_files = new_skip_existing
                self.save_settings()
                self.notify(f"Settings saved. Destination: {self.destination_folder}")

        self.push_screen(SettingsScreen(self.destination_folder, self.concurrent_downloads, self.file_extensions, self.skip_existing_files), set_settings)

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
                response = requests.get(url, timeout=30)
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

        # Collect all items in current view (files AND dirs)
        items_to_process = []
        # We can use self.row_data
        for key, value in self.row_data.items():
            name, is_dir, href = value
            items_to_process.append((name, is_dir, href))

        if not items_to_process:
            self.notify("Nothing to download in this folder.", severity="warning")
            return

        self.download_queue = items_to_process
        self.start_download_worker()

    def get_retry_session(self, retries=5, backoff_factor=0.5):
        session = requests.Session()
        retry = Retry(
            total=retries,
            read=retries,
            connect=retries,
            backoff_factor=backoff_factor,
            status_forcelist=(500, 502, 504),
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
        name, url = file_info
        session = self.get_thread_local_session()
        
        if not url.startswith(BASE_URL):
            return False, name, "Invalid URL", 0
        
        rel_path = url[len(BASE_URL):]
        rel_path = unquote(rel_path)
        filepath = os.path.join(self.destination_folder, rel_path)
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        if self.skip_existing_files and os.path.exists(filepath):
            local_size = os.path.getsize(filepath)
            if local_size > 0:
                return "skipped", name, "File already exists", local_size

        retry_key = url
        with self.download_lock:
            if retry_key not in self.retry_counts:
                self.retry_counts[retry_key] = 0
        
        for attempt in range(max_retries):
            try:
                resume_header = {}
                mode = "wb"
                downloaded = 0
                
                if os.path.exists(filepath):
                    downloaded = os.path.getsize(filepath)
                    try:
                        head_resp = session.head(url, allow_redirects=True, timeout=(10, 15))
                        total_size = int(head_resp.headers.get("content-length", 0))
                        if downloaded >= total_size and total_size > 0:
                            return True, name, None, total_size
                        if downloaded > 0:
                            resume_header = {"Range": f"bytes={downloaded}-"}
                            mode = "ab"
                    except Exception:
                        pass
                
                with session.get(url, stream=True, headers=resume_header, timeout=(10, 30)) as r:
                    r.raise_for_status()
                    if r.status_code != 206:
                        mode = "wb"
                        downloaded = 0
                    total_length = int(r.headers.get("content-length", 0))
                    if mode == "ab":
                        total_length += downloaded
                    
                    with open(filepath, mode) as f:
                        for chunk in r.iter_content(chunk_size=65536):
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

    def _scan_directories(self, initial_queue, file_queue, worker, scan_done_event):
        """Scanner: walks directories concurrently and feeds files into file_queue."""
        import queue as queue_module

        files_found = 0
        dirs_scanned = 0
        dir_queue = queue_module.Queue()
        scan_lock = threading.Lock()

        # Separate initial items into dirs and files
        for name, is_dir, url in initial_queue:
            if is_dir:
                dir_queue.put((name, url))
            elif self.should_download_file(name):
                files_found += 1
                with self.download_lock:
                    self.total_files_to_download = files_found
                file_queue.put((name, url))

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
                response = session.get(dir_url, timeout=30)
                response.raise_for_status()
                items = self.parse_directory_html(response.text, dir_url)

                for item_name, item_is_dir, item_url, _ in items:
                    if worker.is_cancelled:
                        return
                    if item_is_dir:
                        dir_queue.put((item_name, item_url))
                    elif self.should_download_file(item_name):
                        with scan_lock:
                            files_found += 1
                        with self.download_lock:
                            self.total_files_to_download = files_found
                        file_queue.put((item_name, item_url))

            except Exception as e:
                self._show_error_safe(f"Error scanning {dir_name}: {e}")

        SCAN_WORKERS = 5
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
                if status == "skipped":
                    with self.download_lock:
                        self.files_skipped += 1
                elif status:
                    with self.download_lock:
                        self.files_completed += 1
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

        except Exception as e:
            self.show_error(f"Download worker crashed: {e}")
            self.is_downloading = False
            self.query_one("#status-bar").remove_class("downloading")
            progress_bar.display = False
            self.query_one("#status-text", Label).update("Error")

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
