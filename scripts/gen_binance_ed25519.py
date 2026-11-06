#!/usr/bin/env python3
"""
gen_binance_ed25519.py — Create the Ed25519 keypair used for Binance
"self-generated" API keys.

The private key stays on this server (chmod 600). Only the public key is
printed, and that is what gets pasted into Binance -> API Management ->
Create API -> Self-generated (Ed25519) -> Upload public key.

Usage:
    python gen_binance_ed25519.py [--force] [--dir /path/to/keys]
"""
from __future__ import annotations

import argparse
import os
import stat
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="/opt/friday_universe/Stratex/keys")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    key_dir = args.dir
    priv_path = os.path.join(key_dir, "binance_ed25519.pem")
    pub_path = os.path.join(key_dir, "binance_ed25519.pub.pem")

    if os.path.exists(priv_path) and not args.force:
        print(f"Private key already exists at {priv_path}")
        print("Re-run with --force only if you intend to invalidate the old key.")
        return 1

    os.makedirs(key_dir, exist_ok=True)

    private_key = Ed25519PrivateKey.generate()
    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    with open(priv_path, "wb") as fh:
        fh.write(priv_pem)
    os.chmod(priv_path, stat.S_IRUSR | stat.S_IWUSR)  # 600

    with open(pub_path, "wb") as fh:
        fh.write(pub_pem)
    os.chmod(pub_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)

    print(f"Private key written: {priv_path} (chmod 600)")
    print(f"Public  key written: {pub_path}")
    print()
    print("=== PASTE THIS PUBLIC KEY INTO BINANCE ===")
    print(pub_pem.decode("ascii").strip())
    print("=== END PUBLIC KEY ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
