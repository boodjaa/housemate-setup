"""
Configuration file rendering.

Wraps Jinja2 and adds the two behaviours the spec calls for:

1. Idempotency (section 15): a rendered file is only written if its content
   actually differs from what's already on disk, determined by comparing
   SHA-256 checksums rather than re-writing unconditionally.
2. Safe dry-run previews: when running with --dry-run, writes are redirected
   into a local sandbox directory that mirrors the real path, so the user
   can inspect exactly what would be written without touching the real
   system (especially important for paths like /etc/wireguard).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined


class TemplateRenderer:
    def __init__(self, templates_dir: str, dry_run: bool = False, sandbox_dir: str = "./dry-run-output"):
        self.env = Environment(
            loader=FileSystemLoader(templates_dir),
            keep_trailing_newline=True,
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.dry_run = dry_run
        self.sandbox_dir = Path(sandbox_dir)

    def render(self, template_name: str, context: dict) -> str:
        template = self.env.get_template(template_name)
        return template.render(**context)

    def _real_destination(self, dest_path: str) -> Path:
        """Map a real system path into the sandbox when in dry-run mode."""
        if not self.dry_run:
            return Path(dest_path)
        dest = Path(dest_path)
        # Strip the leading '/' so we can join it under the sandbox root.
        return self.sandbox_dir / dest.relative_to(dest.anchor)

    @staticmethod
    def _sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def render_to_file(
        self,
        template_name: str,
        context: dict,
        dest_path: str,
        mode: int = 0o644,
    ) -> bool:
        """Render a template and write it to dest_path if content changed.

        Returns True if the file was created or its content changed, False
        if an identical file already existed (i.e. nothing to do).
        """
        rendered = self.render(template_name, context)
        target = self._real_destination(dest_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.is_file():
            existing = target.read_text()
            if self._sha256(existing) == self._sha256(rendered):
                return False

        target.write_text(rendered)
        try:
            target.chmod(mode)
        except OSError:
            pass  # best-effort; sandboxed/dry-run paths may not support chmod semantics
        return True

    def existing_file_text(self, dest_path: str) -> str | None:
        """Read back a previously-rendered file (real or sandboxed), if present.

        Modules use this to recover values that must persist across reruns,
        such as a homebridge bridge 'username' or a wireguard private key,
        without regenerating them every time.
        """
        target = self._real_destination(dest_path)
        if target.is_file():
            return target.read_text()
        return None

    def write_text(self, content: str, dest_path: str, mode: int = 0o644) -> bool:
        """Write plain (non-Jinja) text content to dest_path if it changed.

        Same idempotency and dry-run sandboxing as render_to_file, for the
        cases (like a one-line apt sources.list.d entry) where running the
        content through the Jinja loader would be overkill.
        """
        target = self._real_destination(dest_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.is_file():
            existing = target.read_text()
            if self._sha256(existing) == self._sha256(content):
                return False

        target.write_text(content)
        try:
            target.chmod(mode)
        except OSError:
            pass
        return True

