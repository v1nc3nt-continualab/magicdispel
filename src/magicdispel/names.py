"""Names for cleaned copies.

By default a copy is named after its original, without the dates, times and
timestamps that screenshots, phone cameras and chat apps put in file names
("Screenshot 2026-09-23 at 15.14.15", "IMG_20240501_123456",
"mmexport1714567890123"), and with _clean added. The rest of the name stays.
"""
import itertools
import re
import secrets

# A date, then the time that often follows it. Words that join the two, in
# the languages macOS names screenshots in, go with the time.
DATE_TIME = re.compile(r"""
    (?<!\d)
    (?: (?:19[7-9]\d|20\d\d) (?P<sep>[-_.]?) (?:0[1-9]|1[0-2]) (?P=sep) (?:0[1-9]|[12]\d|3[01])  # 2024-05-01, 20240501
      | (?:19[7-9]\d|20\d\d) 年 (?:0?[1-9]|1[0-2]) 月 (?:0?[1-9]|[12]\d|3[01]) 日?               # 2024年5月1日
      | (?:0[1-9]|[12]\d|3[01]) (?P<sep2>[-_.]) (?:0[1-9]|[12]\d|3[01]) (?P=sep2) (?:19[7-9]\d|20\d\d)  # 01.05.2024
    )
    (?: [\s_T-]*
        (?: (?:at|um|à|às|a\s+las|alle|om|kl\.?|klo|o|в|上午|下午|中午|晚上|凌晨|早上|午前|午後|오전|오후) [\s_]* )?
        (?: (?:[01]\d|2[0-3]) [0-5]\d [0-5]\d                                        # 123456
          | (?:[01]?\d|2[0-3]) (?P<tsep>[.:_-]) [0-5]\d (?P=tsep) [0-5]\d )           # 15.14.15, 3.14.15
        (?: [.,_-]?\d{1,9} )?                                                        # fractions of a second
        (?: [\s_]?[AaPp]\.?[Mm]\.? )?
    )?
    (?!\d)
""", re.VERBOSE)
# A time on its own, as in a screenshot name in a language not listed above.
TIME = re.compile(r"(?<![\w.:])(?:[01]?\d|2[0-3])(?P<sep>[.:])[0-5]\d(?P=sep)[0-5]\d(?:\s?[AaPp][Mm])?(?![\d.:])")
# Unix time in seconds or milliseconds, 2001 to 2033 ("mmexport1714567890123").
TIMESTAMP = re.compile(r"(?<!\d)1\d{9}(?:\d{3})?(?!\d)")
SEPARATORS = " _-."


def candidates(stem, suffix, naming="plain"):
    """Names for the cleaned copy, in order of preference: "plain" drops dates
    and times from the original's name, "original" keeps it, "anonymous" uses
    random names that contain nothing about the original."""
    if naming == "anonymous":
        while True:
            yield "photo_" + secrets.token_hex(16) + suffix.lower()
    base = stem if naming == "original" else without_dates(stem) or "photo"
    yield base + "_clean" + suffix
    for index in itertools.count(1):
        yield base + "_clean_" + str(index) + suffix


def without_dates(stem):
    """The stem without the dates, times and timestamps in it; the parts on
    either side are joined by one separator."""
    for pattern in (DATE_TIME, TIME, TIMESTAMP):
        while match := pattern.search(stem):
            left, right = stem[:match.start()].rstrip(SEPARATORS), stem[match.end():].lstrip(SEPARATORS)
            # Keep the separator that introduced what follows, else the one before the date.
            joint = next((c for c in stem[match.end():match.end() + 1] + stem[match.start() - 1:match.start()]
                          if c in SEPARATORS), "")
            stem = left + joint + right if left and right else left or right
    return stem
