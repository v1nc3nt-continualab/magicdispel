"""Errors shown to the user, worded in messages.py."""
from .messages import message


class UserError(ValueError):
    """A message key plus values; str() gives the sentence for the user."""

    def __init__(self, key, **values):
        super().__init__(key, values)
        self.key, self.values = key, values

    def __str__(self):
        return message(self.key, **self.values)


class InputError(UserError):
    """The argument is not a file, or not a format MagicDispel handles."""


class FormatError(UserError):
    """The file cannot be rebuilt safely, so nothing is saved."""


class VerificationError(UserError):
    """The rebuilt file failed a check against the original, so nothing is saved."""
