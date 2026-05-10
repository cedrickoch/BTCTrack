"""Generate a bcrypt hash for the privacy-mode password.

Usage:
    python scripts/hash_mask_password.py

Prompts for a password (with confirmation), prints a single line ready to
paste into `.env`:

    MASK_PASSWORD_HASH=<bcrypt-hash>

If the env var is unset, privacy mode is disabled.
"""

from __future__ import annotations

import getpass
import sys

import bcrypt


def main() -> int:
    pw = getpass.getpass("New privacy-mode password: ")
    if not pw:
        print("Aborted: empty password.", file=sys.stderr)
        return 1
    confirm = getpass.getpass("Confirm password: ")
    if pw != confirm:
        print("Aborted: passwords do not match.", file=sys.stderr)
        return 1

    hashed = bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    print()
    print("Add this line to your .env file:")
    print()
    print(f"MASK_PASSWORD_HASH={hashed}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
