#!/bin/bash
#
# Build Consilium Agent into a single executable binary using Nuitka.
# Output: ./dist/consilium-<arch>-<os> (only depends on glibc).
#

set -euo pipefail

# Determine project root directory (parent of tools/ folder)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

STAGE_DIR=""
VENV_DIR="$ROOT_DIR/.venv_nuitka"
OUTPUT_DIR="$ROOT_DIR/dist"
OUTPUT_BASE_NAME="consilium"
OUTPUT_NAME=""  # Will be set with platform suffix
ENTRY_SCRIPT="bin/consilium-main.py"
PYTHONPATH_ADD="lib"
BUILD_DEBUG="0"
BUILD_DIR="$ROOT_DIR/build"
AUDIT_FLAGS=()

log() { echo "INFO: $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

show_help() {
    cat <<EOF
Build Consilium Agent into a single executable binary using Nuitka.

Usage: $0 [OPTIONS]

Options:
  debug, --debug    Enable Nuitka debug mode with execution tracing
  -h, --help        Show this help message and exit

Environment Variables:
  NUITKA_DEBUG      Set to "1" to enable debug mode (legacy method)

Examples:
  # Normal build
  $0

  # Debug build
  $0 --debug

  # Using environment variable
  NUITKA_DEBUG=1 $0

Output:
  The compiled binary will be created at: $OUTPUT_DIR/$OUTPUT_NAME

EOF
    exit 0
}

detect_platform() {
    local arch="$(uname -m)"
    local os_name=""

    # Normalize architecture name
    case "$arch" in
        x86_64|amd64)
            arch="x86_64"
            ;;
        arm64|aarch64)
            if [[ "$OSTYPE" == "darwin"* ]]; then
                arch="arm64"  # macOS uses arm64
            else
                arch="aarch64"  # Linux uses aarch64
            fi
            ;;
        *)
            fail "Unsupported architecture: $arch"
            ;;
    esac

    # Determine OS name
    if [[ "$OSTYPE" == "darwin"* ]]; then
        os_name="darwin"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        os_name="linux"
    else
        fail "Unsupported OS: $OSTYPE"
    fi

    OUTPUT_NAME="${OUTPUT_BASE_NAME}-${arch}-${os_name}"
    log "Target platform: $arch-$os_name"
}

cleanup_previous() {
    log "Removing previous build artifacts"
    rm -rf "$OUTPUT_DIR/${OUTPUT_BASE_NAME}"-* "$BUILD_DIR"
}

setup_venv() {
    log "Creating virtual environment"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --upgrade pip >/dev/null
    "$VENV_DIR/bin/pip" install nuitka textual rich tomli pygments >/dev/null
}

find_python() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # Priority 1: Official python.org Python
        for py in /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 /Library/Frameworks/Python.framework/Versions/*/bin/python3; do
            if [[ -x "$py" ]]; then
                echo "$py"
                return 0
            fi
        done

        # Priority 2: Homebrew Python (fallback)
        if command -v python3 >/dev/null; then
            local py_path="$(command -v python3)"
            if [[ "$py_path" == *"brew"* ]] || [[ "$py_path" == "/opt/homebrew"* ]] || [[ "$py_path" == "/usr/local/Cellar"* ]]; then
                echo "$py_path"
                return 0
            fi
        fi

        # No suitable Python found
        fail "Python not found. Install either:
  - Official: https://www.python.org/downloads/macos/
  - Homebrew: brew install python"
    else
        # Linux
        command -v python3 >/dev/null || fail "python3 not found"
        echo "python3"
    fi
}

check_prereqs() {
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        command -v gcc >/dev/null || fail "gcc not found (install: sudo apt install build-essential)"
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        # Check for Xcode command line tools
        if ! xcode-select -p &>/dev/null; then
            fail "Xcode command line tools not found (install: xcode-select --install)"
        fi
    else
        fail "Unsupported OS: $OSTYPE"
    fi

    PYTHON_BIN="$(find_python)"
    local python_version="$($PYTHON_BIN --version)"

    # Show which Python we're using
    if [[ "$PYTHON_BIN" == *"/Library/Frameworks/Python.framework"* ]] || [[ "$PYTHON_BIN" == "/usr/local/bin/python3"* ]]; then
        log "Using official python.org Python: $PYTHON_BIN ($python_version)"
    elif [[ "$PYTHON_BIN" == *"brew"* ]] || [[ "$PYTHON_BIN" == *"/opt/homebrew"* ]] || [[ "$PYTHON_BIN" == *"/usr/local/Cellar"* ]]; then
        log "Using Homebrew Python: $PYTHON_BIN ($python_version)"
    else
        log "Using Python: $PYTHON_BIN ($python_version)"
    fi
}

prepare_stage() {
    STAGE_DIR="$(mktemp -d)"
    log "Preparing staging directory: $STAGE_DIR"
    cp -R "$ROOT_DIR/bin" "$STAGE_DIR/"
    cp -R "$ROOT_DIR/lib" "$STAGE_DIR/"
}

run_audit() {
    local audit_script="$ROOT_DIR/tools/audit_nuitka.py"
    if [[ ! -f "$audit_script" ]]; then
        return
    fi

    mkdir -p "$BUILD_DIR"
    local report_path="$BUILD_DIR/nuitka_audit.json"
    local markdown_path="$BUILD_DIR/nuitka_audit.md"
    local output

    output="$("$audit_script" \
        --root "$ROOT_DIR" \
        --report "$report_path" \
        --markdown "$markdown_path" \
        --print-flags)"

    if [[ -n "$output" ]]; then
        # macOS-compatible alternative to mapfile
        while IFS= read -r flag; do
            if [[ "$flag" == --* ]]; then
                AUDIT_FLAGS+=("$flag")
            elif [[ -n "$flag" ]]; then
                echo "$flag"
            fi
        done <<< "$output"
    fi
}

build_binary() {
    log "Compiling with Nuitka in onefile mode"
    export PYTHONPATH="$PYTHONPATH_ADD:${PYTHONPATH:-}"

    NUITKA_OPTS=(
        --onefile
        --standalone
        --output-filename="$OUTPUT_NAME"
        --include-package=consilium
        --include-package=rich
        --include-package=pygments.lexers
        --include-package=pygments.styles
        --include-data-dir=lib/roles=roles
    )

    # On macOS with python.org framework Python, disable static libpython
    # Nuitka will bundle the Python framework into the onefile binary
    if [[ "$OSTYPE" == "darwin"* ]]; then
        log "Building standalone binary (Python framework bundled, no external dependencies)"
        NUITKA_OPTS+=(--static-libpython=no)
    fi

    if [[ "${#AUDIT_FLAGS[@]}" -gt 0 ]]; then
        for flag in "${AUDIT_FLAGS[@]}"; do
            if [[ " ${NUITKA_OPTS[*]} " != *" $flag "* ]]; then
                NUITKA_OPTS+=("$flag")
            fi
        done
    fi

    if [[ "${BUILD_DEBUG}" == "1" ]]; then
        log "Nuitka debug mode enabled (--debug)"
        NUITKA_OPTS+=(
            --trace-execution
        )
        export NUITKA_ONEFILE_DEBUG=1
        export NUITKA_ONEFILE_TEMP_DIR="${NUITKA_ONEFILE_TEMP_DIR:-/tmp/nuitka-onefile-debug}"
        export NUITKA_ONEFILE_TRACE="${NUITKA_ONEFILE_TRACE:-1}"
    fi

    "$VENV_DIR/bin/python3" -m nuitka "${NUITKA_OPTS[@]}" "$ENTRY_SCRIPT"

    mkdir -p "$OUTPUT_DIR"
    mv "$OUTPUT_NAME" "$OUTPUT_DIR/$OUTPUT_NAME"
    chmod +x "$OUTPUT_DIR/$OUTPUT_NAME"
}

summary() {
    log "Binary built successfully: $OUTPUT_DIR/$OUTPUT_NAME"
    log ""
    log "First run - install roles:"
    log "  $OUTPUT_DIR/$OUTPUT_NAME --install"
    log ""
    log "Normal run:"
    log "  $OUTPUT_DIR/$OUTPUT_NAME [TRACE|DEBUG|INFO|WARNING|ERROR]"
    log ""
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        log "Note: Binary requires glibc version >= $(ldd --version | head -1 | awk '{print $NF}') on target system"
    else
        log "Note: Binary is fully standalone, no external dependencies required"
    fi
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                show_help
                ;;
            debug|--debug)
                BUILD_DEBUG="1"
                ;;
            *)
                fail "Unknown argument: $1 (use --help for usage information)"
                ;;
        esac
        shift
    done

    # Support legacy environment variable method
    if [[ "${NUITKA_DEBUG:-0}" == "1" ]]; then
        BUILD_DEBUG="1"
    fi
}

main() {
    trap '[[ -n "$STAGE_DIR" && -d "$STAGE_DIR" ]] && rm -rf "$STAGE_DIR"' EXIT
    parse_args "$@"
    detect_platform
    run_audit
    cleanup_previous
    check_prereqs
    prepare_stage

    pushd "$STAGE_DIR" > /dev/null
    setup_venv
    build_binary
    popd > /dev/null

    rm -rf "$STAGE_DIR"
    summary
}

main "$@"
