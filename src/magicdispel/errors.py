"""Errors shown to the user, worded in their language (see messages.py)."""
from .messages import message


class LocalizedError(ValueError):
    """A message key plus values; str() gives the sentence in the user's language."""

    def __init__(self, key, **values):
        super().__init__(key, values)
        self.key, self.values = key, values

    def __str__(self):
        return message(self.key, **self.values)


class FormatError(LocalizedError):
    """The file cannot be rebuilt safely, so nothing is saved."""


class VerificationError(LocalizedError):
    """The rebuilt file failed a check against the original, so nothing is saved."""
