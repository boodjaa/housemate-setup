from __future__ import annotations

import re
import socket

from modules.base import Module, ModuleError


class SysconfigModule(Module):
    name = "base"
    required = True

    def validate(self) -> None:
        pass
    
    def install(self) -> None:
        # Configure VNC serv properties
        if self.settings["vnc"]:
            self.runner.run(["raspi-config", "nonint", "do_vnc", "0"])
            # self.runner.run(["raspi-config", "nonint", "do_resolution", "1280x720"])

    def configure(self) -> None:
        new_hostname = self.settings["hostname"]
        old_hostname = socket.gethostname()

        # Set the new hostname
        self.runner.run(["hostnamectl", "set-hostname", new_hostname])

        # Update /etc/hosts to reflect tha change
        hosts_path = "/etc/hosts"
        try:
            with open(hosts_path, "r") as f:
                lines = f.readlines()
            
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
                
            with open(hosts_path, "w") as f:
                f.writelines(lines)
                
        except (IOError, OSError) as e:
            raise ModuleError(f"Failed to update {hosts_path}: {e}")

    def enable(self) -> None:
        # Enable VNC Server
        if self.settings["vnc"]:
            self.runner.run(["systemctl", "enable", "wayvnc"])

    def status(self) -> None:
        pass