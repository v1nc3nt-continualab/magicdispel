"""The MagicDispel logo, shown when the command runs without photos."""
import os
import shutil
import sys

CREDITS = "Designed by VincentC, Powered by VincentC"
TAGLINE = "MagicDispel, wipe the metadata, keep every pixel!"
# Letters in the ANSI Shadow style: six rows each, every row the same width.
LETTERS = {
    "M": ["███╗   ███╗", "████╗ ████║", "██╔████╔██║", "██║╚██╔╝██║", "██║ ╚═╝ ██║", "╚═╝     ╚═╝"],
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "G": [" ██████╗ ", "██╔════╝ ", "██║  ███╗", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
    "I": ["██╗", "██║", "██║", "██║", "██║", "╚═╝"],
    "C": [" ██████╗", "██╔════╝", "██║     ", "██║     ", "╚██████╗", " ╚═════╝"],
    "D": ["██████╗ ", "██╔══██╗", "██║  ██║", "██║  ██║", "██████╔╝", "╚═════╝ "],
    "S": ["███████╗", "██╔════╝", "███████╗", "╚════██║", "███████║", "╚══════╝"],
    "P": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔═══╝ ", "██║     ", "╚═╝     "],
    "E": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"],
    "L": ["██╗     ", "██║     ", "██║     ", "██║     ", "███████╗", "╚══════╝"],
}
# Magenta through violet and blue to cyan, from the 256-color palette nearly
# every terminal has; the letters' edges are gray.
GRADIENT = [201, 165, 129, 93, 57, 63, 69, 75, 81, 87, 51]
EDGE, DIM, BOLD, RESET = "\x1b[38;5;240m", "\x1b[2m", "\x1b[1m", "\x1b[0m"
INDENT = "  "


def welcome(version, usage, stream=sys.stdout):
    """The logo, credits and tagline, then `usage`. A terminal gets the logo
    in color; anything else, such as a file, gets plain text."""
    if not stream.isatty():
        return "\n".join(["MagicDispel " + version, CREDITS, TAGLINE, "", usage])
    color = colors(stream)
    width = shutil.get_terminal_size((80, 24)).columns
    lines = logo(["MAGICDISPEL"] if width >= len(INDENT) + 82 else ["MAGIC", "DISPEL"])
    widest = max(map(len, lines))  # both words share one gradient
    art = [INDENT + (painted(line, widest) if color else line) for line in lines]
    style = (lambda code, text: code + text + RESET) if color else (lambda code, text: text)
    return "\n".join(["", *art, "",
                      INDENT + style(DIM, CREDITS),
                      INDENT + style(BOLD, TAGLINE) + "   " + style(DIM, "v" + version),
                      "", *(INDENT + line for line in usage.splitlines()), ""])


def logo(words):
    """The rows of each word, the words one under the other."""
    return ["".join(LETTERS[letter][row] for letter in word) for word in words for row in range(6)]


def painted(line, width):
    """A logo row: blocks colored by column along the gradient, edges gray."""
    out, current = [], None
    for column, char in enumerate(line):
        code = ("\x1b[38;5;%dm" % GRADIENT[min(column * len(GRADIENT) // width, len(GRADIENT) - 1)]
                if char == "█" else EDGE if char != " " else current)
        if code != current:
            out.append(code)
            current = code
        out.append(char)
    return "".join(out) + RESET


def colors(stream):
    """Whether to use color: not when NO_COLOR is set or the terminal is dumb.
    Windows consoles need their escape-sequence support switched on."""
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle, mode = kernel32.GetStdHandle(-11), ctypes.c_uint32()  # standard output
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(kernel32.GetConsoleMode(handle, ctypes.byref(mode))
                    and kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False
