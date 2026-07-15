"""
Sysconfig ("base") module. Mandatory -- always runs.

Handles the host-level basics every appliance variant needs regardless of
which optional modules are enabled: hostname (+ the matching /etc/hosts
entry, since hostnamectl deliberately doesn't touch that file itself),
SSH/VNC toggles via raspi-config, and two standing cron jobs:

  - a nightly reboot, for the usual "appliance that's been up for weeks
    benefits from a clean restart" reason
  - a periodic healthchecks.io ping, so an external service notices if
    this Pi stops checking in (network down, crashed, SD card died, etc.)

Cron jobs are written into /etc/cron.d/hub-appliance rather than touched
via `crontab -e`/`crontab -l` -- a drop-in file is trivially idempotent
(render + checksum-compare, same as every other config in this framework)
where mutating an existing crontab safely would mean parsing it first.
"""

from __future__ import annotations

import re
import socket

from modules.base import Module, ModuleError

CRON_PATH = "/etc/cron.d/house-mate-config"
# FIX: Added {uid} placeholder so .format() actually injects the UID
HEALTHCHECK_URL = "https://hc-ping.com/{uid}"

_HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")


class SysconfigModule(Module):
    name = "base"
    required = True

    def validate(self) -> None:
        hostname = self.settings.get("hostname", "")
        if not _HOSTNAME_RE.match(hostname):
            raise ModuleError(
                f"base.hostname '{hostname}' isn't a valid hostname label "
                f"(letters, digits, hyphens only; can't start or end with a hyphen)"
            )
        healthcheck = self.settings.get("healthcheck")
        if healthcheck is not None and not str(healthcheck).strip():
            raise ModuleError("base.healthcheck, if set, can't be an empty string")

    def install(self) -> None:
        # Update and Upgrade system before we begin
        self.runner.run_apt(["update"])
        self.runner.run_apt(["upgrade", "-y"])

        # cron ships by default on Raspberry Pi OS, but check anyway rather
        # than assume -- consistent with how every other module treats its
        # own package dependencies.
        if not self.runner.package_installed("cron"):
            self.runner.run_apt(["install", "-y", "cron"])

        # Configure VNC serv properties
        if self.settings["vnc"]:
            self.runner.run(["raspi-config", "nonint", "do_vnc", "0"])
            # self.runner.run(["raspi-config", "nonint", "do_resolution", "1280x720"])
        # Configure SSH server
        if self.settings["ssh"]:
            self.runner.run(["raspi-config", "nonint", "do_ssh", "0"])

    def configure(self) -> None:
        new_hostname = self.settings["hostname"]
        old_hostname = socket.gethostname()
        # Set the new hostname
        self.runner.run(["hostnamectl", "set-hostname", new_hostname])
        # Update /etc/hosts to reflect the change
        self._update_etc_hosts(new_hostname, old_hostname)
        # Write the standing cron jobs
        self._configure_cron()

    def _update_etc_hosts(self, new_hostname: str, old_hostname: str) -> None:
        hosts_path = "/etc/hosts"
        try:
            # Routed through self.templates rather than a bare open() so
            # --dry-run previews this against ./dry-run-output/etc/hosts
            # instead of editing the real file -- the line-rewriting logic
            # below is unchanged either way.
            existing = self.templates.existing_file_text(hosts_path) or ""
            lines = existing.splitlines(keepends=True)

            updated = False
            for i, line in enumerate(lines):
                # Strip line endings for robustness
                clean_line = line.rstrip('\r\n')

                # Match lines starting with 127.0.0.1 or 127.0.1.1 (ignoring leading whitespace)
                match = re.match(r"^\s*(127\.0\.(0|1)\.1)(\s+)(.*)$", clean_line)
                if match:
                    ip = match.group(1)
                    space = match.group(3)
                    rest = match.group(4)

                    tokens = rest.split()
                    new_tokens = []
                    replaced = False

                    for token in tokens:
                        # Replace old hostname, but strictly protect 'localhost'
                        if token == old_hostname and old_hostname.lower() != "localhost":
                            new_tokens.append(new_hostname)
                            replaced = True
                        else:
                            new_tokens.append(token)

                    # If it's the 127.0.1.1 line and we didn't replace anything,
                    # ensure the new hostname is present to prevent sudo resolution delays
                    if not replaced and ip == "127.0.1.1" and new_hostname not in tokens:
                        new_tokens.append(new_hostname)
                        replaced = True

                    if replaced:
                        lines[i] = f"{ip}{space}{' '.join(new_tokens)}\n"
                        updated = True

            # If no existing line was updated, append a new entry as a fallback
            if not updated:
                lines.append(f"127.0.1.1\t{new_hostname}\n")

            new_content = "".join(lines)
            if new_content != existing:
                self.templates.write_text(new_content, hosts_path, mode=0o644)

        except OSError as e:
            raise ModuleError(f"Failed to update {hosts_path}: {e}")

    def _configure_cron(self) -> bool:
        healthcheck_uid = self.settings.get("healthcheck")
        context = {
            # Now correctly formats the UID into the URL, or passes None if not set
            "healthcheck": HEALTHCHECK_URL.format(uid=healthcheck_uid) if healthcheck_uid else None,
        }
        return self.templates.render_to_file("cron/cron-jobs.j2", context, CRON_PATH, mode=0o644)

    def enable(self) -> None:
        # cron picks up changes to /etc/cron.d/* on its own (it checks
        # mtimes roughly every minute) -- no restart needed even when the
        # file content changed this run, just make sure the service is on.
        self.runner.run(["systemctl", "enable", "cron"], check=False)

        # Enable VNC Server
        if self.settings["vnc"]:
            self.runner.run(["systemctl", "enable", "wayvnc"])
        # Enable SSH Server
        if self.settings["ssh"]:
            self.runner.run(["systemctl", "enable", "ssh"])

    def status(self) -> bool:
        hostname_matches = socket.gethostname() == self.settings.get("hostname")
        cron_present = self.templates.existing_file_text(CRON_PATH) is not None
        return hostname_matches and cron_present
