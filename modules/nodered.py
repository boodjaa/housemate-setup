from __future__ import annotations

import re
import socket

from modules.base import Module, ModuleError


class NoderedModule(Module):
    name = "nodered"
    required = False

    def validate(self) -> None:
        pass
    
    def install(self) -> None:
        # Install mosquitto server package
        NODERED_INSTALL_SCRIPT = "https://github.com/node-red/linux-installers/releases/latest/download/install-update-nodered-deb"
        if self.settings["enabled"]:
            self.runner.run_apt(["update"])
            self.runner.run_apt(["curl", "-sL", NODERED_INSTALL_SCRIPT])

    def configure(self) -> None:
        pass

    def enable(self) -> None:
        if self.settings["enabled"]:
            self.runner.run(["systemctl", "enable", "nodered"])
            self.runner.run(["systemctl", "start", "nodered"])

    def status(self) -> None:
        pass
