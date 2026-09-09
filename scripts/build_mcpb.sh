#!/usr/bin/env bash
# Build the livepilot-${VERSION}.mcpb bundle for Claude Desktop one-click install.
#
# The MCPB format is a ZIP archive with manifest.json at the root and the
# runtime entry point the manifest refers to (bin/livepilot.js) plus
# everything that entry point needs at runtime (the Python server, the
# Remote Script, the M4L device, the installer wrapper).
#
# Why this script exists: the README's "Easiest: Claude Desktop Extension
# (1 click)" path promises a `livepilot.mcpb` download. Every release from
# v1.17 through v1.20.2 silently shipped without the artifact because no
# build script existed. v1.20.3 fixes that by introducing this script +
# attaching the bundle to the release.
#
# Usage:
#   scripts/build_mcpb.sh                 # builds dist/livepilot-${VERSION}.mcpb
#   scripts/build_mcpb.sh --output <path> # custom output path
#   scripts/build_mcpb.sh --studiopilot <dir>
#       Also stage the StudioPilot overlay package found at <dir> (the
#       directory that contains studiopilot/), add bin/studiopilot_proxy.py,
#       and point the manifest's mcp_config at that proxy with the
#       StudioPilot env defaults. The proxy forwards stdio to the shared
#       LivePilot HTTP server on 127.0.0.1:9890/mcp.
#   scripts/build_mcpb.sh --studiopilot <dir> --python <interpreter>
#       Interpreter Claude Desktop runs the proxy with (needs fastmcp);
#       defaults to <dir>/.venv/bin/python when it exists, else python3.
#
# The bundle is deliberately lean:
#   - bin/livepilot.js is pure Node stdlib (zero npm deps) so no
#     node_modules is shipped
#   - Python deps are bootstrapped into a `.venv` directory alongside the
#     bundle (ROOT/.venv) on first launch from the bundled requirements.txt
#     (the same path npm-install users take)
#   - LivePilot_Analyzer.amxd + Remote Script are shipped so the MCPB
#     install can auto-install them via the user_config.auto_install_remote_script
#     switch in manifest.json

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="$(python3 -c "import json; print(json.load(open('manifest.json'))['version'])")"
OUTPUT_DEFAULT="$ROOT/dist/livepilot-${VERSION}.mcpb"
OUTPUT="$OUTPUT_DEFAULT"
STUDIOPILOT_SRC=""
STUDIOPILOT_PYTHON=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output)
            OUTPUT="$2"
            shift 2
            ;;
        --studiopilot)
            STUDIOPILOT_SRC="$2"
            shift 2
            ;;
        --python)
            STUDIOPILOT_PYTHON="$2"
            shift 2
            ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

STAGE="$(mktemp -d -t livepilot-mcpb.XXXXXX)"
trap "rm -rf '$STAGE'" EXIT

echo "→ Staging v${VERSION} into $STAGE"

# Files + dirs shipped inside the bundle
cp manifest.json "$STAGE/"
cp package.json "$STAGE/"
cp requirements.txt "$STAGE/"
mkdir -p "$STAGE/bin"
cp bin/livepilot.js "$STAGE/bin/"
cp -R mcp_server "$STAGE/"
cp -R remote_script "$STAGE/"
cp -R m4l_device "$STAGE/"
cp -R installer "$STAGE/"

# StudioPilot overlay: package + stdio proxy entry point + env defaults.
if [[ -n "$STUDIOPILOT_SRC" ]]; then
    if [[ ! -d "$STUDIOPILOT_SRC/studiopilot" ]]; then
        echo "error: $STUDIOPILOT_SRC/studiopilot not found" >&2
        exit 2
    fi
    echo "→ Staging StudioPilot overlay from $STUDIOPILOT_SRC"
    cp -R "$STUDIOPILOT_SRC/studiopilot" "$STAGE/studiopilot"
    cat > "$STAGE/bin/studiopilot_proxy.py" <<'PYEOF_PROXY'
#!/usr/bin/env python3
"""Claude Desktop entry point: stdio MCP proxy to the shared StudioPilot server."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from studiopilot.proxy import main  # noqa: E402

if __name__ == "__main__":
    main()
PYEOF_PROXY
    chmod +x "$STAGE/bin/studiopilot_proxy.py"
    if [[ -z "$STUDIOPILOT_PYTHON" ]]; then
        if [[ -x "$STUDIOPILOT_SRC/.venv/bin/python" ]]; then
            STUDIOPILOT_PYTHON="$STUDIOPILOT_SRC/.venv/bin/python"
        else
            STUDIOPILOT_PYTHON="python3"
        fi
    fi
    echo "→ Proxy interpreter: $STUDIOPILOT_PYTHON"
    python3 - "$STAGE/manifest.json" "$STUDIOPILOT_PYTHON" <<'PYEOF_MANIFEST'
import json, sys
path, interpreter = sys.argv[1], sys.argv[2]
m = json.load(open(path))
m["server"]["type"] = "python"
m["server"]["mcp_config"] = {
    "command": interpreter,
    "args": ["${__dirname}/bin/studiopilot_proxy.py"],
    "env": {
        "STUDIOPILOT_SERVER_URL": "http://127.0.0.1:9890/mcp",
        "STUDIOPILOT_OVERLAY": "1",
        "LIVEPILOT_TOOL_PROFILE": "studiopilot",
        "LIVEPILOT_SPLICE_DISABLE": "1",
    },
}
m["server"]["entry_point"] = "bin/studiopilot_proxy.py"
m["name"] = "studiopilot"
m["display_name"] = "StudioPilot (LivePilot fork, shared local server)"
m["description"] = "Ableton Live 12 control through the shared local StudioPilot server on 127.0.0.1:9890"
m.pop("user_config", None)  # the proxy takes no settings; the server owns the Live link
m["compatibility"] = {"platforms": ["darwin"], "runtimes": {"python": ">=3.12"}}
json.dump(m, open(path, "w"), indent=2)
PYEOF_MANIFEST
fi

# Exclude caches and editor cruft
find "$STAGE" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name "*.pyc" -delete 2>/dev/null || true
find "$STAGE" -name ".DS_Store" -delete 2>/dev/null || true

# Honor .mcpbignore for the copied m4l_device tree: user-saved Ableton presets
# (*.adv) and local re-freeze backups (*.pre-*-backup) must never ship.
find "$STAGE/m4l_device" -name "*.adv" -delete 2>/dev/null || true
find "$STAGE/m4l_device" -name "*.pre-*-backup" -delete 2>/dev/null || true

# Ensure output dir exists
mkdir -p "$(dirname "$OUTPUT")"

echo "→ Building $OUTPUT"
(cd "$STAGE" && zip -rq "$OUTPUT" . -x "*.DS_Store")

# Post-build verification
unzip -p "$OUTPUT" manifest.json | python3 -c "
import json, sys
m = json.load(sys.stdin)
assert m['name'] in ('livepilot', 'studiopilot'), f'bad name: {m[\"name\"]}'
assert m['version'] == '${VERSION}', f'bad version: {m[\"version\"]} != ${VERSION}'
assert m['server']['entry_point'] in ('bin/livepilot.js', 'bin/studiopilot_proxy.py'), f'bad entry'
print(f'✓ manifest: {m[\"name\"]} v{m[\"version\"]} (entry={m[\"server\"][\"entry_point\"]})')
"

FILES="$(unzip -l "$OUTPUT" | tail -1 | awk '{print $2}')"
SIZE="$(ls -lh "$OUTPUT" | awk '{print $5}')"
echo "✓ Built $OUTPUT ($SIZE, $FILES files)"
