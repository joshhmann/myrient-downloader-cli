#!/usr/bin/env bash
set -e

APP_NAME="myrient-cli"
REPO_URL="https://github.com/joshhmann/myrient-downloader-cli.git"
INSTALL_DIR="$HOME/.myrient-cli"

echo "================================"
echo "  Myrient Downloader CLI Setup"
echo "================================"
echo ""

# Check for Python 3
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    echo "[ERROR] Python 3 is required but not found."
    echo "Install it from https://www.python.org/downloads/"
    exit 1
fi

PY_VERSION=$($PYTHON --version 2>&1)
echo "[+] Found $PY_VERSION"

# Check minimum version
PY_MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 8 ]; }; then
    echo "[ERROR] Python 3.8+ is required. You have $PY_VERSION"
    exit 1
fi

# Check for pip
if ! $PYTHON -m pip --version &>/dev/null; then
    echo "[ERROR] pip is not installed."
    echo "Run: $PYTHON -m ensurepip --upgrade"
    exit 1
fi

# Clone or update repo
if [ -d "$INSTALL_DIR" ]; then
    echo "[+] Updating existing installation..."
    cd "$INSTALL_DIR"
    git pull --ff-only
else
    echo "[+] Cloning repository..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# Check for rclone
RCLONE_LOCAL_DIR="$INSTALL_DIR/bin"
mkdir -p "$RCLONE_LOCAL_DIR"

if ! command -v rclone &>/dev/null && [ ! -f "$RCLONE_LOCAL_DIR/rclone" ]; then
    echo "[!] rclone not found. Downloading standalone binary for Turbo Mode..."
    
    OS="$(uname | tr '[:upper:]' '[:lower:]')"
    ARCH="$(uname -m)"
    case "$ARCH" in
        x86_64) ARCH="amd64" ;;
        aarch64) ARCH="arm64" ;;
        armv7*) ARCH="arm" ;;
        i386|i686) ARCH="386" ;;
        *) ARCH="amd64" ;;
    esac

    RCLONE_ZIP="rclone-current-$OS-$ARCH.zip"
    RCLONE_URL="https://downloads.rclone.org/$RCLONE_ZIP"

    echo "[+] Downloading rclone ($OS-$ARCH)..."
    if command -v curl &>/dev/null; then
        curl -L -o "/tmp/$RCLONE_ZIP" "$RCLONE_URL"
    elif command -v wget &>/dev/null; then
        wget -O "/tmp/$RCLONE_ZIP" "$RCLONE_URL"
    else
        echo "[ERROR] curl or wget not found. Please install rclone manually."
        exit 1
    fi

    echo "[+] Extracting rclone..."
    if command -v unzip &>/dev/null; then
        unzip -q "/tmp/$RCLONE_ZIP" -d "/tmp/rclone-extract"
        find "/tmp/rclone-extract" -name rclone -type f -exec mv {} "$RCLONE_LOCAL_DIR/rclone" \;
        chmod +x "$RCLONE_LOCAL_DIR/rclone"
        rm -rf "/tmp/$RCLONE_ZIP" "/tmp/rclone-extract"
        echo "[+] rclone installed locally to $RCLONE_LOCAL_DIR/rclone"
    else
        echo "[ERROR] unzip not found. Please install unzip or rclone manually."
        exit 1
    fi
else
    if command -v rclone &>/dev/null; then
        echo "[+] Found system rclone: $(rclone version | head -n 1)"
    else
        echo "[+] Found local rclone: $($RCLONE_LOCAL_DIR/rclone version | head -n 1)"
    fi
fi

# Install dependencies
echo "[+] Installing dependencies..."
$PYTHON -m pip install -r requirements.txt --quiet

# Determine shell config file
SHELL_RC=""
if [ -n "$ZSH_VERSION" ] || [ "$SHELL" = "$(command -v zsh)" ]; then
    SHELL_RC="$HOME/.zshrc"
elif [ -n "$BASH_VERSION" ] || [ "$SHELL" = "$(command -v bash)" ]; then
    SHELL_RC="$HOME/.bashrc"
fi

# Create launcher script
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
LAUNCHER="$BIN_DIR/$APP_NAME"

cat > "$LAUNCHER" << EOF
#!/usr/bin/env bash
$PYTHON "$INSTALL_DIR/myrient.py" "\$@"
EOF
chmod +x "$LAUNCHER"
echo "[+] Created launcher at $LAUNCHER"

# Add to PATH if needed
if ! echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
    if [ -n "$SHELL_RC" ]; then
        if ! grep -q 'export PATH=.*\.local/bin' "$SHELL_RC" 2>/dev/null; then
            echo '' >> "$SHELL_RC"
            echo '# Myrient Downloader CLI' >> "$SHELL_RC"
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
            echo "[+] Added $BIN_DIR to PATH in $SHELL_RC"
        fi
    fi
fi

echo ""
echo "================================"
echo "  Installation complete!"
echo "================================"
echo ""
echo "Run '$APP_NAME' to start the downloader."
echo ""
if ! echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
    echo "NOTE: Restart your terminal or run:"
    echo "  source $SHELL_RC"
    echo ""
fi
