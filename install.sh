#!/bin/sh
# Install MagicDispel on macOS or Linux:
#   curl -LsSf https://raw.githubusercontent.com/v1nc3nt-continualab/magicdispel/main/install.sh | sh
#
# It installs uv (https://docs.astral.sh/uv/) if needed, which then installs
# MagicDispel with a Python of its own when the system has none that fits.
# MAGICDISPEL_NO_MODIFY_PATH=1 leaves shell startup files alone.
# MAGICDISPEL_PACKAGE installs something else, such as a local wheel to test.
set -eu

package="${MAGICDISPEL_PACKAGE:-magicdispel}"

if command -v uv >/dev/null 2>&1; then
    uv=$(command -v uv)
elif [ -x "$HOME/.local/bin/uv" ]; then
    uv="$HOME/.local/bin/uv"
else
    echo "Installing uv, which installs MagicDispel and the Python it needs..."
    if [ "${MAGICDISPEL_NO_MODIFY_PATH:-}" = 1 ]; then
        curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
    else
        curl -LsSf https://astral.sh/uv/install.sh | sh
    fi
    uv="$HOME/.local/bin/uv"
fi

echo "Installing MagicDispel..."
"$uv" tool install --upgrade "$package"
if [ "${MAGICDISPEL_NO_MODIFY_PATH:-}" != 1 ]; then
    "$uv" tool update-shell >/dev/null 2>&1 || true
fi

bin=$("$uv" tool dir --bin)
"$bin/magicdispel"
case ":$PATH:" in
    *":$bin:"*) ;;
    *) echo "  Open a new terminal window, then type magicdispel." ;;
esac
