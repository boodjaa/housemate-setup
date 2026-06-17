from __future__ import annotations

import re

from modules.base import Module, ModuleError


class SysconfigModule(Module):
    name = "system"
    required = False

    # -- install -----------------------------------------------------------
    def install(self) -> None:

        # Set hostname
        self.runner.run(["hostnamectl", "set-hostname", self.settings["hostname"]])
