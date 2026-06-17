from __future__ import annotations

from modules.base import Module, ModuleError


class SysconfigModule(Module):
    name = "system"
    required = True

    def validate(self) -> None:
        pass
    
    def install(self) -> None:
        pass

    def configure(self) -> None:
        # Set hostname
        self.runner.run(["hostnamectl", "set-hostname", self.settings["hostname"]])

    def enable(self) -> None:
        pass

    def status(self) -> None:
        pass