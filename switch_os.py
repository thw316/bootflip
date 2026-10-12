#!/usr/bin/env python3
"""
Dual OS Remote Switcher (Windows <-> Ubuntu)
Connects via SSH, inspects current OS, and sets UEFI next boot entry if a switch is needed.
"""

import argparse
import json
import os
import re
import sys
import time

try:
    import paramiko
except ImportError:
    print("[ERROR] 'paramiko' library is required. Please install it using: pip install paramiko")
    sys.exit(1)


def load_config(config_path):
    """Load and validate JSON configuration file."""
    if not os.path.isabs(config_path):
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), config_path)

    if not os.path.exists(config_path):
        print(f"[ERROR] Configuration file not found at: {config_path}")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        try:
            config = json.load(f)
        except json.JSONDecodeError as e:
            print(f"[ERROR] Failed to parse JSON configuration: {e}")
            sys.exit(1)

    return config


def execute_ssh_command(client, command, sudo_password=None, timeout=15):
    """Execute command over SSH with optional sudo password piping."""
    if sudo_password:
        command = f"echo '{sudo_password}' | sudo -S {command}"

    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out_bytes = stdout.read()
    err_bytes = stderr.read()

    # Decode bytes safely
    out = out_bytes.decode("utf-8", errors="replace")
    err = err_bytes.decode("utf-8", errors="replace")
    exit_status = stdout.channel.recv_exit_status()

    return exit_status, out, err


def detect_os(client):
    """Detect remote operating system: returns 'ubuntu' or 'windows'."""
    # Test Linux kernel command
    status, out, _ = execute_ssh_command(client, "uname -s")
    if status == 0 and "Linux" in out:
        return "ubuntu"

    # Test Windows command
    status, out, _ = execute_ssh_command(client, "cmd.exe /c ver")
    if "Microsoft Windows" in out or "Windows" in out:
        return "windows"

    # Fallback check
    status, out, _ = execute_ssh_command(client, "ver")
    if "Windows" in out:
        return "windows"

    return "unknown"


def try_connect(host, port, username, password=None, key_filename=None, timeout=10):
    """Attempt an SSH connection using given credentials."""
    client = paramiko.SSHClient()
    # Ignore host key mismatch between Windows and Linux on the same IP
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            key_filename=key_filename,
            timeout=timeout,
            look_for_keys=False if password and not key_filename else True,
            allow_agent=False,
        )
        return client, None
    except Exception as e:
        client.close()
        return None, str(e)


def connect_remote(config):
    """
    Connect to remote host trying Ubuntu credentials or Windows credentials.
    Returns (client, connected_as).
    """
    host = config.get("host")
    port = config.get("port", 22)
    win_cfg = config.get("windows", {})
    ubu_cfg = config.get("ubuntu", {})

    print(f"[*] Attempting to connect to {host}:{port}...")

    # Candidates to try
    credential_candidates = [
        ("ubuntu", ubu_cfg.get("username"), ubu_cfg.get("password"), ubu_cfg.get("key_filename")),
        ("windows", win_cfg.get("username"), win_cfg.get("password"), win_cfg.get("key_filename")),
    ]

    last_error = None
    for profile_name, username, password, key_file in credential_candidates:
        if not username:
            continue
        client, err = try_connect(host, port, username, password, key_file)
        if client:
            return client, profile_name
        last_error = err

    print(f"[ERROR] Could not connect to {host}:{port}. Last error: {last_error}")
    sys.exit(1)


def switch_to_windows_from_ubuntu(client, config):
    """
    Parse efibootmgr output on Ubuntu, set BootNext to Windows Boot Manager, and reboot.
    """
    sudo_pass = config.get("ubuntu", {}).get("sudo_password") or config.get("ubuntu", {}).get("password")

    print("[*] Running efibootmgr to detect Windows boot entry...")
    status, out, err = execute_ssh_command(client, "efibootmgr", sudo_password=sudo_pass)
    if status != 0:
        print(f"[ERROR] Failed to run efibootmgr: {err or out}")
        sys.exit(1)

    # Search for Windows Boot Manager entry: e.g., Boot0000* Windows Boot Manager
    # Matches Boot0000, Boot0001, etc.
    match = re.search(r"Boot([0-9a-fA-F]{4})\*?\s+.*?(?:Windows Boot Manager|Windows)", out, re.IGNORECASE)
    if not match:
        print("[ERROR] Could not find 'Windows Boot Manager' entry in efibootmgr output:")
        print(out)
        sys.exit(1)

    win_boot_id = match.group(1)
    print(f"[+] Found Windows boot entry: Boot{win_boot_id}")

    # Set BootNext
    print(f"[*] Setting BootNext to {win_boot_id}...")
    status, out, err = execute_ssh_command(client, f"efibootmgr -n {win_boot_id}", sudo_password=sudo_pass)
    if status != 0:
        print(f"[ERROR] Failed to set BootNext: {err or out}")
        sys.exit(1)

    print("[+] Successfully set BootNext. Triggering reboot...")
    # Trigger reboot (fire and forget as connection terminates upon reboot)
    try:
        execute_ssh_command(client, "reboot", sudo_password=sudo_pass, timeout=3)
    except Exception:
        pass

    print("[SUCCESS] Remote system is rebooting into Windows!")


def switch_to_ubuntu_from_windows(client):
    """
    Parse bcdedit output on Windows, set bootsequence to Ubuntu GUID, and reboot.
    """
    print("[*] Querying UEFI firmware boot entries with bcdedit...")
    status, out, err = execute_ssh_command(client, "bcdedit /enum firmware")
    if status != 0:
        print(f"[ERROR] Failed to query bcdedit: {err or out}")
        print("Note: Ensure the Windows SSH user belongs to the Administrators group.")
        sys.exit(1)

    # Parse bcdedit firmware output blocks
    # Firmware blocks are separated by empty lines or section headers
    entries = re.split(r"\r?\n\r?\n+", out)
    ubuntu_guid = None

    for entry in entries:
        if "ubuntu" in entry.lower():
            guid_match = re.search(r"identifier\s+(\{[a-f0-9\-]+\})", entry, re.IGNORECASE)
            if guid_match:
                ubuntu_guid = guid_match.group(1)
                break

    if not ubuntu_guid:
        print("[ERROR] Could not find 'ubuntu' firmware entry in bcdedit output:")
        print(out)
        sys.exit(1)

    print(f"[+] Found Ubuntu firmware entry: {ubuntu_guid}")

    # Set bootsequence for next reboot
    print(f"[*] Setting one-time bootsequence to {ubuntu_guid}...")
    set_cmd = f"bcdedit /set {{fwbootmgr}} bootsequence {ubuntu_guid}"
    status, out, err = execute_ssh_command(client, set_cmd)
    if status != 0:
        print(f"[ERROR] Failed to set bootsequence: {err or out}")
        sys.exit(1)

    print("[+] Successfully set bootsequence. Triggering reboot...")
    # Trigger Windows reboot
    try:
        execute_ssh_command(client, "shutdown /r /t 0", timeout=3)
    except Exception:
        pass

    print("[SUCCESS] Remote system is rebooting into Ubuntu!")


def wait_for_reboot_and_verify(config, target_os, timeout=180, poll_interval=5, initial_wait=10):
    """
    Poll SSH after reboot until remote machine comes back online and target OS is confirmed.
    """
    host = config.get("host")
    port = config.get("port", 22)
    target_cfg = config.get(target_os, {})
    other_os = "ubuntu" if target_os == "windows" else "windows"
    other_cfg = config.get(other_os, {})

    print("\n" + "=" * 60)
    print(f"[*] Waiting for remote host ({host}) to reboot into {target_os.upper()}...")
    print(f"[*] Max timeout: {timeout}s | Giving host {initial_wait}s to initiate shutdown...")
    print("=" * 60)

    time.sleep(initial_wait)

    start_time = time.time()
    while True:
        elapsed = int(time.time() - start_time)
        if elapsed >= timeout:
            print(f"\n[ERROR] Timeout ({timeout}s) exceeded while waiting for {target_os.upper()} to boot.")
            print("[ERROR] Please verify the machine status manually.")
            sys.exit(1)

        # Try connecting with target OS credentials first, then fallback to other OS
        client = None
        for os_name, cfg in [(target_os, target_cfg), (other_os, other_cfg)]:
            u = cfg.get("username")
            p = cfg.get("password")
            k = cfg.get("key_filename")
            if not u:
                continue
            client, _ = try_connect(host, port, u, p, k, timeout=4)
            if client:
                break

        if client is not None:
            try:
                active_os = detect_os(client)
                if active_os == target_os:
                    print(f"\n[+] [Elapsed: {elapsed}s] SSH connection established on {host}:{port}!")
                    print(f"[+] Verified active OS is {target_os.upper()}!")
                    print(f"[SUCCESS] Remote dual-boot switch to {target_os.upper()} successfully verified!")
                    client.close()
                    return True
                else:
                    print(f"[-] [Elapsed: {elapsed}s] Host is responsive, but still running {active_os.upper()}. Waiting...")
            finally:
                client.close()
        else:
            print(f"[-] [Elapsed: {elapsed}s / {timeout}s] Host is rebooting... Retrying in {poll_interval}s")

        time.sleep(poll_interval)


def main():
    parser = argparse.ArgumentParser(description="Switch Dual OS remotely via SSH.")
    parser.add_argument(
        "--target",
        required=True,
        choices=["windows", "ubuntu"],
        help="Target operating system to switch to.",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to configuration file (default: config.json in script directory).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Reboot verification timeout in seconds (default: 180).",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Do not wait for reboot and OS verification.",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    client, profile = connect_remote(config)

    switched = False
    try:
        current_os = detect_os(client)
        print(f"[+] Current active OS detected: {current_os.upper()}")

        if current_os == "unknown":
            print("[ERROR] Could not determine remote operating system. Aborting.")
            sys.exit(1)

        if args.target == "windows":
            if current_os == "windows":
                print("[INFO] Target OS is already Windows. No action taken.")
                return
            elif current_os == "ubuntu":
                print("[*] Transitioning from Ubuntu to Windows...")
                switch_to_windows_from_ubuntu(client, config)
                switched = True
        elif args.target == "ubuntu":
            if current_os == "ubuntu":
                print("[INFO] Target OS is already Ubuntu. No action taken.")
                return
            elif current_os == "windows":
                print("[*] Transitioning from Windows to Ubuntu...")
                switch_to_ubuntu_from_windows(client)
                switched = True

    finally:
        client.close()

    if switched and not args.no_wait:
        wait_timeout = args.timeout or config.get("timeout", 180)
        wait_for_reboot_and_verify(config, args.target, timeout=wait_timeout)


if __name__ == "__main__":
    main()
