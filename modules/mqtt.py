from __future__ import annotations

import re
import socket

from modules.base import Module, ModuleError


class MqttModule(Module):
    name = "mqtt"
    required = False

    def validate(self) -> None:
        # Ensure config exists
        listener = self.settings.get("listener")
        allow_anonymous = self.settings.get("allow_anonymous")

        # Check if keys exist in the dictionary
        if listener is None or allow_anonymous is None:
            raise ModuleError(
                "Invalid mqtt configuration in config.yaml. (Expected 'listener' and 'allow_anonymous' values.)"
            )
        
    def install(self) -> None:
        # Install mosquitto server package
        if self.settings.get("enabled"):
            self.runner.run_apt(["update"])
            self.runner.run_apt(["install", "-y", "mosquitto"])

    def configure(self) -> None:
        config_path = "/etc/mosquitto/mosquitto.conf"
        listener = self.settings.get("listener", "")
        allow_anonymous = self.settings.get("allow_anonymous", False)

        # Normalize the value to handle YAML booleans (True) and strings ("true", "yes")
        is_anonymous_allowed = str(allow_anonymous).lower() in ("true", "yes", "on", "1")
        anon_val_str = "true" if is_anonymous_allowed else "false"

        # Update mosquitto config with settings from config.yaml
        try:
            with open(config_path, "r") as f:
                lines = f.readlines()
            
            listener_found = False
            anonymous_found = False
            
            for i, line in enumerate(lines):
                stripped = line.strip()
                
                # Skip empty lines and comments
                if not stripped or stripped.startswith('#'):
                    continue
                    
                parts = stripped.split()
                directive = parts[0]
                
                # Preserve original line endings (handles both \n and \r\n safely)
                ending = "\r\n" if line.endswith("\r\n") else "\n"
                
                # Replace existing active directives
                if directive == "listener" and listener:
                    lines[i] = f"listener {listener}{ending}"
                    listener_found = True
                elif directive == "allow_anonymous":
                    lines[i] = f"allow_anonymous {anon_val_str}{ending}"
                    anonymous_found = True

            # Append missing directives if they weren't found
            if not listener_found and listener:
                lines.append(f"listener {listener}\n")
            if not anonymous_found:
                lines.append(f"allow_anonymous {anon_val_str}\n")
                
            with open(config_path, "w") as f:
                f.writelines(lines)
                
        except (IOError, OSError) as e:
            raise ModuleError(f"Failed to update {config_path}: {e}")

    def enable(self) -> None:
        if self.settings.get("enabled"):
            self.runner.run(["systemctl", "enable", "mosquitto"])
            self.runner.run(["systemctl", "start", "mosquitto"])

    def status(self) -> None:
        pass