"""Interactive registration wizard.

Remote Play needs a one-time setup before you can stream:

  1. Link your PSN account (OAuth) -- once per PSN user, ever.
  2. Link this PC to the console with the 8-digit PIN -- once per console.

You run this yourself (it needs your PSN login in a browser). Nothing here ever
sees your password: Sony's login happens in your own browser, and you only paste
back the URL of the final "redirect" page.

Usage:
    python -m ps5capture.register
    python -m ps5capture.register --new-account     # force a fresh PSN login
"""
from __future__ import annotations

import sys

from pyremoteplay import RPDevice
from pyremoteplay import oauth


def _login_new_account(profiles):
    """Walk the user through PSN OAuth and save the account profile."""
    url = oauth.get_login_url()
    print("\n" + "=" * 70)
    print("STEP 1 - Link your PSN account")
    print("=" * 70)
    print("\n1. Open this URL in your web browser and sign in to PSN:\n")
    print("   " + url + "\n")
    print("2. After signing in you'll land on a blank/'redirect' page.")
    print("   Copy the FULL URL of that page from the address bar.\n")
    redirect = input("Paste the redirect URL here: ").strip()
    if not redirect:
        print("No URL entered. Aborting.")
        sys.exit(1)
    # profiles.new_user() runs oauth.get_account_info + format + save in one step.
    profiles.new_user(redirect, save=True)
    print("PSN account linked and saved.\n")


def _choose_user(users):
    if len(users) == 1:
        return users[0]
    print("\nAvailable PSN accounts:")
    for i, name in enumerate(users):
        print(f"  [{i}] {name}")
    while True:
        choice = input("Pick an account number: ").strip()
        if choice.isdigit() and 0 <= int(choice) < len(users):
            return users[int(choice)]
        print("Invalid choice.")


def main() -> int:
    profiles = RPDevice.get_profiles()
    users = RPDevice.get_all_users(profiles)

    if not users or "--new-account" in sys.argv:
        _login_new_account(profiles)
        profiles = RPDevice.get_profiles()
        users = RPDevice.get_all_users(profiles)

    if not users:
        print("No PSN account is linked. Re-run and complete the login step.")
        return 1

    username = _choose_user(users)

    print("\n" + "=" * 70)
    print("STEP 2 - Link this PC to your PS5")
    print("=" * 70)
    print("\nOn the PS5:  Settings > System > Remote Play > turn it ON.")
    print("Then:        Settings > System > Remote Play > Link Device")
    print("That shows an 8-digit PIN. Make sure the SAME PSN account "
          f"('{username}') is signed in on the console.\n")

    ip = input("PS5 IP address [192.168.20.38]: ").strip() or "192.168.20.38"
    device = RPDevice(ip)

    print(f"\nContacting {ip} ...")
    status = device.get_status()
    if not status:
        print("Could not reach the console. Check:")
        print("  - the IP is correct and on the same LAN")
        print("  - the PS5 is powered on (not fully off)")
        print("  - Remote Play is enabled on the console")
        return 1
    print(f"Console reachable. Status: {device.status_name}")

    pin = input("\nEnter the 8-digit Link PIN from the PS5: ").strip().replace("-", "")
    if not (pin.isdigit() and len(pin) == 8):
        print("PIN must be 8 digits.")
        return 1

    print("Registering ...")
    try:
        # register() expects the PIN as a string.
        result = device.register(username, pin, save=True)
    except Exception as exc:  # noqa: BLE001 - surface any backend error plainly
        print(f"Registration failed: {exc}")
        return 1

    if result:
        print("\nSUCCESS - this PC is now linked to the console.")
        print("You can close this window and launch the capture app.")
        return 0

    print("\nRegistration did not succeed. Double-check the PIN and that the "
          "right PSN account is signed in on the console, then try again.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
