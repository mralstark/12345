"""Apply bounded no-cost host settings, retaining recoverable configuration copies."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run as root")
    backup = Path("/root/.cache/bratstvo-hardening") / str(time.time_ns())
    backup.mkdir(mode=0o700, parents=True)
    nginx = Path("/etc/nginx/nginx.conf")
    original = nginx.read_text()
    tls = original.replace("ssl_protocols TLSv1 TLSv1.1 TLSv1.2 TLSv1.3;", "ssl_protocols TLSv1.2 TLSv1.3;")
    changes = {
        nginx: tls,
        Path("/etc/ssh/sshd_config.d/00-bratstvo-hardening.conf"):
            "PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n"
            "X11Forwarding no\nAllowAgentForwarding no\nMaxAuthTries 3\n",
        Path("/etc/systemd/journald.conf.d/60-bratstvo-limits.conf"):
            "[Journal]\nStorage=persistent\nSystemMaxUse=200M\nRuntimeMaxUse=64M\n"
            "MaxRetentionSec=30day\nRateLimitIntervalSec=30s\nRateLimitBurst=2000\n",
        Path("/etc/apt/apt.conf.d/52bratstvo-unattended-upgrades"):
            'Unattended-Upgrade::Automatic-Reboot "false";\n',
    }
    old = {}
    try:
        for index, (target, content) in enumerate(changes.items()):
            if target.is_symlink():
                raise RuntimeError("Refusing symlinked host configuration")
            old[target] = target.read_bytes() if target.exists() else None
            if target.exists():
                shutil.copy2(target, backup / str(index))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            target.chmod(0o644)
        subprocess.run(["sshd", "-t"], check=True)
        subprocess.run(["nginx", "-t"], check=True)
    except BaseException:
        for target, content in old.items():
            if content is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(content)
        raise
    (backup / "paths.json").write_text(json.dumps({str(p): str(i) if old[p] is not None else None
                                                for i, p in enumerate(changes)}))
    subprocess.run(["systemctl", "reload", "nginx"], check=True)
    subprocess.run(["systemctl", "reload", "ssh"], check=True)
    subprocess.run(["systemctl", "restart", "systemd-journald"], check=True)
    print(json.dumps({"host_hardening": "applied", "rollback_directory": str(backup),
                      "sshd_configuration_validated": True, "automatic_reboot": False}))


if __name__ == "__main__":
    main()
