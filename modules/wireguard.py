"""
WireGuard module (spec section 8). Always installed -- mandatory module.

Install:   apt-get install wireguard wireguard-tools.
Configure: generates a keypair (once -- reused on reruns) and renders
           /etc/wireguard/wg0.conf. The public key is written to a text
           file in the working directory and echoed in the final summary,
           since the user needs it to register this Pi with their VPN
           server.
Enable:    wg-quick@wg0, a built-in systemd template unit -- no custom
           unit file needed.
"""

from __future__ import annotations

import re

from modules.base import Module, ModuleError

WG_CONF_PATH = "/etc/wireguard/wg0.conf"
PUBLIC_KEY_OUTPUT_PATH = "./wireguard_public_key.txt"
SERVICE_NAME = "wg-quick@wg0"

_PRIVATE_KEY_LINE = re.compile(r"^\s*PrivateKey\s*=\s*(\S+)\s*$", re.MULTILINE)


class WireGuardModule(Module):
    name = "wireguard"
    required = True

    def validate(self) -> None:
        pubkey = self.settings.get("server_pubkey", "")
        if len(pubkey) != 44 or not pubkey.endswith("="):
            raise ModuleError(
                "wireguard.server_pubkey doesn't look like a valid WireGuard public key "
                "(expected a 44-character base64 string ending in '=')"
            )

    # -- install -----------------------------------------------------------
    def install(self) -> None:
        missing = [
            pkg for pkg in ("wireguard", "wireguard-tools")
            if not self.runner.package_installed(pkg)
        ]
        if not missing:
            self.logger.info("wireguard and wireguard-tools already installed, skipping install")
            return
        self.runner.run_apt(["install", "-y", *missing])

    # -- configure -----------------------------------------------------------
    def _existing_private_key(self) -> str | None:
        existing = self.templates.existing_file_text(WG_CONF_PATH)
        if not existing:
            return None
        match = _PRIVATE_KEY_LINE.search(existing)
        return match.group(1) if match else None

    def _generate_keypair(self) -> tuple[str, str]:
        private_key = self.runner.run(["wg", "genkey"]).stdout.strip()
        if self.dry_run or not private_key:
            # dry-run never executes `wg`, so fabricate placeholders that are
            # clearly not real keys, purely so the rendered preview is readable.
            private_key = private_key or "<dry-run-private-key>"
            public_key = "<dry-run-public-key>"
        else:
            public_key = self.runner.run(["wg", "pubkey"], input=private_key + "\n").stdout.strip()
        return private_key, public_key

    def _pubkey_from(self, private_key: str) -> str:
        """Derive the public key for an already-known private key.

        This is a pure, read-only computation (no system state changes),
        so unlike _generate_keypair it runs for real even in --dry-run --
        otherwise a dry-run preview against a Pi that already has a key
        configured would show a fake placeholder instead of the real
        public key the user actually needs to share.
        """
        result = self.runner.query(["wg", "pubkey"], input=private_key + "\n")
        return result.stdout.strip() if result.ok and result.stdout.strip() else "<unavailable>"

    def configure(self) -> bool:
        private_key = self._existing_private_key()
        if private_key:
            public_key = self._pubkey_from(private_key)
        else:
            private_key, public_key = self._generate_keypair()

        context = {
            "private_key": private_key,
            "address": self.settings.get("address", "10.10.0.2/24"),
            "dns": self.settings.get("dns"),
            "server_pubkey": self.settings["server_pubkey"],
            "endpoint": self.settings["endpoint"],
            "allowed_ips": self.settings.get("allowed_ips", "0.0.0.0/0"),
            "persistent_keepalive": self.settings.get("persistent_keepalive", 25),
        }
        changed = self.templates.render_to_file("wireguard/wg0.conf.j2", context, WG_CONF_PATH, mode=0o600)

        with open(PUBLIC_KEY_OUTPUT_PATH, "w") as f:
            f.write(public_key + "\n")

        self._last_public_key = public_key
        self._last_configure_changed = changed
        return changed

    # -- enable -------------------------------------------------------------
    def enable(self) -> None:
        config_changed = getattr(self, "_last_configure_changed", False)
        self.runner.run(["systemctl", "enable", SERVICE_NAME], check=False)
        if config_changed:
            self.runner.run(["systemctl", "restart", SERVICE_NAME], check=False)
        else:
            self.runner.run(["systemctl", "start", SERVICE_NAME], check=False)

    # -- status -----------------------------------------------------------
    def status(self) -> bool:
        installed = self.runner.package_installed("wireguard-tools")
        active = self.runner.query(["systemctl", "is-active", "--quiet", SERVICE_NAME]).ok
        return installed and active
