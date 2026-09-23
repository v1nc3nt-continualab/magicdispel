"""Render a command's terminal output as a PNG in a window frame, for the README.

    python scripts/screenshot.py docs/images/welcome.png --columns 100 -- magicdispel

The command runs in a pseudo-terminal of the given width, so it sees a color
terminal, and its text is drawn with its 256-color styling. --prompt shows a
typed command line first; --replace OLD=NEW rewrites text such as long paths.
Unix only (it needs a pseudo-terminal); uses Menlo on macOS, DejaVu Sans Mono
on Linux.
"""
import argparse
import fcntl
import os
import pty
import re
import struct
import subprocess
import sys
import termios

from PIL import Image, ImageDraw, ImageFont

FONTS = [("/System/Library/Fonts/Menlo.ttc", 0, 1),
         ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", None, None)]
SIZE, LINE, PADDING, TITLE_BAR, MARGIN, RADIUS = 26, 34, 36, 56, 28, 18
BACKGROUND, FRAME, TEXT, TITLE = (22, 22, 27), (44, 44, 52), (222, 222, 226), (140, 140, 150)
LIGHTS = [(255, 95, 87), (254, 188, 46), (40, 200, 64)]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output")
    parser.add_argument("--columns", type=int, default=100)
    parser.add_argument("--title", default="Terminal")
    parser.add_argument("--prompt", help="a command line to show before the output")
    parser.add_argument("--replace", action="append", default=[], metavar="OLD=NEW")
    if "--" not in sys.argv:
        parser.error("put the command after --")
    split = sys.argv.index("--")
    args, command = parser.parse_args(sys.argv[1:split]), sys.argv[split + 1:]
    text = run(command, args.columns)
    for pair in args.replace:
        old, new = pair.split("=", 1)
        text = text.replace(old, new)
    if args.prompt:
        text = "\x1b[38;5;114m$\x1b[0m " + args.prompt + "\n" + text
    draw(text.rstrip("\n").split("\n"), args.columns, args.title).save(args.output, optimize=True)


def run(command, columns):
    """The command's output as a terminal of `columns` would show it."""
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, columns, 0, 0))
    process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                               env=dict(os.environ, TERM="xterm-256color", COLUMNS=str(columns)))
    os.close(slave)
    chunks = []
    while True:
        try:
            chunk = os.read(master, 65536)
        except OSError:  # the command has closed the terminal
            break
        if not chunk:
            break
        chunks.append(chunk)
    process.wait()
    return b"".join(chunks).decode("utf-8", "replace").replace("\r\n", "\n")


def palette(number):
    """The RGB color of an xterm 256-color palette entry."""
    base = [(0, 0, 0), (205, 49, 49), (13, 188, 121), (229, 229, 16), (36, 114, 200), (188, 63, 188),
            (17, 168, 205), (229, 229, 229), (102, 102, 102), (241, 76, 76), (35, 209, 139), (245, 245, 67),
            (59, 142, 234), (214, 112, 214), (41, 184, 219), (255, 255, 255)]
    if number < 16:
        return base[number]
    if number < 232:
        steps = [0, 95, 135, 175, 215, 255]
        number -= 16
        return steps[number // 36], steps[number // 6 % 6], steps[number % 6]
    level = 8 + 10 * (number - 232)
    return level, level, level


def fonts():
    for path, regular, bold in FONTS:
        if os.path.exists(path):
            if regular is None:
                return ImageFont.truetype(path, SIZE), ImageFont.truetype(path.replace(".ttf", "-Bold.ttf"), SIZE)
            return ImageFont.truetype(path, SIZE, index=regular), ImageFont.truetype(path, SIZE, index=bold)
    raise SystemExit("no monospace font found")


def draw(lines, columns, title):
    regular, bold = fonts()
    cell = round(regular.getlength("M"))
    width = MARGIN * 2 + PADDING * 2 + cell * columns
    height = MARGIN * 2 + TITLE_BAR + PADDING * 2 + LINE * len(lines)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas = ImageDraw.Draw(image)
    window = (MARGIN, MARGIN, width - MARGIN, height - MARGIN)
    canvas.rounded_rectangle(window, RADIUS, fill=BACKGROUND, outline=FRAME, width=2)
    canvas.rounded_rectangle((MARGIN, MARGIN, width - MARGIN, MARGIN + TITLE_BAR), RADIUS, fill=FRAME)
    canvas.rectangle((MARGIN, MARGIN + TITLE_BAR - RADIUS, width - MARGIN, MARGIN + TITLE_BAR), fill=FRAME)
    for index, color in enumerate(LIGHTS):
        x, y = MARGIN + 30 + index * 34, MARGIN + TITLE_BAR // 2
        canvas.ellipse((x - 9, y - 9, x + 9, y + 9), fill=color)
    canvas.text((width // 2, MARGIN + TITLE_BAR // 2), title, font=regular, fill=TITLE, anchor="mm")
    top = MARGIN + TITLE_BAR + PADDING
    for row, line in enumerate(lines):
        color, weight, dim, column = TEXT, regular, False, 0
        for token in re.split(r"(\x1b\[[0-9;]*m)", line):
            if token.startswith("\x1b["):
                codes = token[2:-1].split(";") if token[2:-1] else ["0"]
                if codes[:2] == ["38", "5"]:
                    color = palette(int(codes[2]))
                elif codes == ["0"]:
                    color, weight, dim = TEXT, regular, False
                elif codes == ["1"]:
                    weight = bold
                elif codes == ["2"]:
                    dim = True
                continue
            for char in token:
                fill = tuple(round(c * 0.55 + b * 0.45) for c, b in zip(color, BACKGROUND)) if dim else color
                x, y = MARGIN + PADDING + column * cell, top + row * LINE
                if char == "█":  # a terminal fills the whole cell
                    canvas.rectangle((x, y, x + cell - 1, y + LINE - 1), fill=fill)
                elif char in DOUBLE_LINES:
                    double_line(canvas, char, x, y, cell, fill)
                elif char != " ":
                    canvas.text((x, y + (LINE - SIZE) // 2), char, font=weight, fill=fill)
                column += 1
    return image


# Double-line box characters, as the sides of the cell they reach: a terminal
# draws them edge to edge, so they join across cells.
DOUBLE_LINES = {"═": "LR", "║": "UD", "╔": "DR", "╗": "DL", "╚": "UR", "╝": "UL"}


def double_line(canvas, char, x, y, cell, fill):
    """Draw a double-line box character as two strokes that meet the cell edges."""
    sides, half = DOUBLE_LINES[char], cell // 8
    middle_x, middle_y = x + cell // 2, y + LINE // 2
    for stroke in (-1, 1):  # the outer stroke, then the inner one
        if sides == "LR":
            canvas.line((x, middle_y + stroke * half, x + cell, middle_y + stroke * half), fill=fill, width=2)
        elif sides == "UD":
            canvas.line((middle_x + stroke * half, y, middle_x + stroke * half, y + LINE), fill=fill, width=2)
        else:
            # A corner turning right or left, down or up: the outer stroke turns
            # on the far side of the inner one.
            dx, dy = (1 if "R" in sides else -1), (1 if "D" in sides else -1)
            corner_x, corner_y = middle_x + stroke * dx * half, middle_y + stroke * dy * half
            canvas.line((corner_x, corner_y, x + cell if dx > 0 else x, corner_y), fill=fill, width=2)
            canvas.line((corner_x, corner_y, corner_x, y + LINE if dy > 0 else y), fill=fill, width=2)


if __name__ == "__main__":
    main()
