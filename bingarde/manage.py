"""Admin tasks on the command line, with the app's settings:

    python -m bingarde.manage make-admin EMAIL [--ini production.ini]
    python -m bingarde.manage demo-access [--ini production.ini]

make-admin adds EMAIL to the Admin group (and makes the user if needed), for the
first admin of a new server. In Docker:

    docker compose exec web python -m bingarde.manage make-admin you@example.com

demo-access sets up the demo (only with DEV_LOGIN): admin@example.org is an
admin, and everyone who signs in joins the group "Demo team", which may write
every build. It runs at every start of the demo and changes nothing the second
time.
"""
import argparse
import os
import sys

import transaction
from paste.deploy import loadapp

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(ini):
    """Load the app once, so the database and the settings are set up."""
    return loadapp("config:" + os.path.abspath(ini), relative_to=HERE)


def make_admin(email):
    from bingarde.lib import usermgmt
    with transaction.manager:
        user = usermgmt.make_admin(email)
        return user.email


DEMO_ADMIN = "admin@example.org"
DEMO_GROUP = "Demo team"


def demo_access():
    from sqlalchemy import func, select
    from bingarde.lib import access, identity, usermgmt
    from bingarde.model import DBSession, Group
    if not identity.dev_login_enabled():
        raise usermgmt.ManageError("demo-access only runs with DEV_LOGIN=1 (the demo setup)")
    with transaction.manager:
        if access.active_admin_count() == 0:
            usermgmt.make_admin(DEMO_ADMIN)
        if DBSession.scalar(select(Group).where(func.lower(Group.name) == DEMO_GROUP.lower())) is None:
            group = usermgmt.create_group(DEMO_GROUP, "Demo: everyone may write every build.", "*")
            usermgmt.add_rule(group, None, "write")
            access.add_matching_users(group)
            return True
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m bingarde.manage")
    parser.add_argument("--ini", default=os.path.join(HERE, "production.ini"),
                        help="settings file (default: production.ini)")
    commands = parser.add_subparsers(dest="command", required=True)
    admin = commands.add_parser("make-admin", help="add a user to the Admin group")
    admin.add_argument("email")
    commands.add_parser("demo-access", help="the demo's admin and groups (DEV_LOGIN only)")
    args = parser.parse_args(argv)

    load(args.ini)
    from bingarde.lib.usermgmt import ManageError
    try:
        if args.command == "make-admin":
            print("%s is an admin now" % make_admin(args.email))
        elif demo_access():
            print("demo access set up: %s is admin, everyone joins %r" % (DEMO_ADMIN, DEMO_GROUP))
    except ManageError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
