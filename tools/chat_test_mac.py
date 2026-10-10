"""Strict SSH/SCP wrapper for the disposable Mac chat tests.

Set SRISU_CHAT_MAC_HOST (user@host), SRISU_CHAT_MAC_KEY (private-key path), and
SRISU_CHAT_MAC_KNOWN_HOSTS (previously verified host-key file). Never prints keys.
With no arguments, executes stdin as a Bash script. `upload LOCAL REMOTE` and
`fetch REMOTE LOCAL` transfer evaluation artifacts. No automatic host-key trust.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def main():
    host=os.environ.get("SRISU_CHAT_MAC_HOST","")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+",host):
        raise SystemExit("Set SRISU_CHAT_MAC_HOST to the authorized Mac's user@host")
    paths=[]
    for name in ("SRISU_CHAT_MAC_KEY","SRISU_CHAT_MAC_KNOWN_HOSTS"):
        value=os.environ.get(name,"")
        if not value or not Path(value).is_file():raise SystemExit("Set "+name+" to an existing file")
        paths.append(str(Path(value).resolve()))
    key,known=paths
    options=["-F",os.devnull,"-o","BatchMode=yes","-o","IdentitiesOnly=yes","-o","IdentityAgent=none",
        "-o","StrictHostKeyChecking=yes","-o","UserKnownHostsFile="+known,"-o","GlobalKnownHostsFile="+os.devnull,
        "-o","HostKeyAlgorithms=ssh-ed25519","-o","KexAlgorithms=curve25519-sha256","-o","ConnectTimeout=8",
        "-o","ServerAliveInterval=15","-o","ServerAliveCountMax=3","-i",key]
    mode=sys.argv[1:2]
    if mode in (["upload"],["fetch"]):
        if len(sys.argv)!=4:raise SystemExit("Use upload LOCAL REMOTE or fetch REMOTE LOCAL")
        executable=shutil.which("scp")
        if not executable:raise SystemExit("OpenSSH scp is required")
        source,destination=sys.argv[2:]
        if mode==["upload"]:destination=host+":"+destination
        else:source=host+":"+source
        return subprocess.run([executable,*options,source,destination]).returncode
    if mode:raise SystemExit("Pass a script through stdin, or use upload/fetch")
    executable=shutil.which("ssh")
    if not executable:raise SystemExit("OpenSSH ssh is required")
    return subprocess.run([executable,*options,"-T",host,"/bin/bash -s"],
        input=sys.stdin.buffer.read().replace(b"\r\n",b"\n")).returncode


if __name__=="__main__":sys.exit(main())
