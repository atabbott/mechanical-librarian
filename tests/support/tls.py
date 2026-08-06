from __future__ import annotations

import os
import subprocess
from pathlib import Path


def synthetic_tls_identity(root: Path, name: str = "review") -> tuple[Path, Path]:
    certificate = root / f"{name}.crt"
    private_key = root / f"{name}.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(private_key),
            "-out",
            str(certificate),
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    os.chmod(certificate, 0o644)
    os.chmod(private_key, 0o600)
    return certificate, private_key
