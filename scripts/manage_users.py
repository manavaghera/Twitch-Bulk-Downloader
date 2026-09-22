"""
Clip Studio accounts  --  who may sign in to the web page
==============================================================================

There is no sign-up on the page: only the accounts made here can get in.
Run from the project folder:

    .venv\\Scripts\\python.exe scripts\\manage_users.py add alice
    .venv\\Scripts\\python.exe scripts\\manage_users.py passwd alice
    .venv\\Scripts\\python.exe scripts\\manage_users.py remove alice
    .venv\\Scripts\\python.exe scripts\\manage_users.py list
    .venv\\Scripts\\python.exe scripts\\manage_users.py hash

Passwords are typed in hidden and stored only as salted hashes in
data\\users.json. `hash` prints a hash without saving anything, for pasting
into Streamlit secrets when the page is hosted (see clipdl\\accounts.py).
"""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipdl import accounts  # noqa: E402


def ask_password():
    first = getpass.getpass("Password (hidden): ")
    if getpass.getpass("Same password again: ") != first:
        sys.exit("The two passwords differ - nothing changed.")
    return first


def main():
    parser = argparse.ArgumentParser(description="Manage who can sign in to Clip Studio.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (("add", "create an account"), ("passwd", "change a password"),
                       ("remove", "delete an account")):
        sub.add_parser(name, help=text).add_argument("login_id")
    sub.add_parser("list", help="show the account IDs")
    sub.add_parser("hash", help="print a password hash for hosted secrets")
    args = parser.parse_args()

    if args.command == "list":
        ids = accounts.file_user_ids()
        print("\n".join(ids) if ids else "No accounts yet - the page is open to anyone.")
        print("Login applies to: %s" % accounts.login_scope())
    elif args.command == "hash":
        print(accounts.hash_password(ask_password()))
    elif args.command == "remove":
        done = accounts.remove_user(args.login_id)
        print("Removed %s." % args.login_id if done else "No account called %s." % args.login_id)
    else:
        exists = accounts.normalize(args.login_id) in accounts.file_user_ids()
        if args.command == "add" and exists:
            sys.exit("%s already exists - use passwd to change the password." % args.login_id)
        if args.command == "passwd" and not exists:
            sys.exit("No account called %s - use add." % args.login_id)
        try:
            login_id = accounts.set_user(args.login_id, ask_password())
        except ValueError as error:
            sys.exit(str(error))
        print("Saved. %s can now sign in." % login_id)


if __name__ == "__main__":
    main()
