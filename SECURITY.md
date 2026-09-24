# Security

MagicDispel exists to keep private data out of shared photos. A file that keeps any of it after
cleaning is therefore a security issue, and so is anything that makes MagicDispel change an
original, overwrite a file, or run code.

## Reporting

Please report such issues privately, through
[GitHub's private vulnerability reporting](https://github.com/v1nc3nt-continualab/magicdispel/security/advisories/new),
not in a public issue, so that a fix can be released before the details are public.

Describe how to build or obtain a file that shows the problem. Do not send real private photos:
a synthetic file, or a photo of nothing private carrying a made-up marker such as an invented
GPS position, is enough.

You can expect a first reply within a week. Fixes are released as soon as they are ready and
credited in the changelog, unless you would rather not be named.

## Supported versions

Only the latest release receives fixes. Upgrade with `uv tool upgrade magicdispel`, or run the
installer again.

## Out of scope

As the [privacy details](docs/PRIVACY.md) explain: data hidden inside the compressed image data
itself (steganography), what a picture shows, and sensor fingerprints.
