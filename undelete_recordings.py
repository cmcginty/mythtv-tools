#!/usr/bin/env python2
# -*- coding: UTF-8 -*-
"""
# Undelete MythTV Recordings
#
# This python script provides a command line tool to search and undelete all recordings.
# Author: Raymond Wagner, Patrick C. McGinty
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from optparse import OptionParser
import re
import sys

from MythTV import MythBE, MythBEError, MythLog, MythDBError
from MythTV.altdict import DictData
from MythTV.static import BACKEND_SEP

# MythTV datetime's (dt.py) custom tzinfo (posixtzinfo) has a bug that can
# corrupt the recording's timestmap in the client. We don't really care about
# local date format, therefore the bug can be fixed by setting all timestamps to
# integers and bypassing and conversion bugs.
TIMESTAMP_FIELD_INDEX = 4
DictData._trans[TIMESTAMP_FIELD_INDEX] = int      # pylint:disable=protected-access
DictData._inv_trans[TIMESTAMP_FIELD_INDEX] = str  # pylint:disable=protected-access


def rec_to_string(rec):
    """Return string value of recording r"""
    fulltitle = ' - '.join(
        [rec.title, rec.subtitle]) if rec.subtitle else rec.title
    return str('[%s] %s' % (rec.starttime, fulltitle))


def list_recs(recs):
    """Print all recordings to the terminal.

    Returns dict of recordings, with integer keys
    """
    print('Below is a list of matching recordings:')
    recs = dict(enumerate(recs.values()))
    for i, rec in recs.items():
        print('  %d. %s' % (i, rec_to_string(rec)))
    return recs


def undelete_all(backend, recs):
    """Undeletes all recordings from the dict recs.

    Send an UNDELETE_RECORDING protocol message to the backend and test for
    failure.
    """
    for rec in recs.values():
        print('undelete ' + rec_to_string(rec))
        cmd = BACKEND_SEP.join(['UNDELETE_RECORDING', rec.to_string()])
        res = backend.backendCommand(cmd)
        if int(res) != 0:
            raise MythBEError("undelete failed")


def interactive_undelete(backend, recs):
    """Prompt user what recordings to undelete before executing."""
    recs = dict(enumerate(recs))
    list_recs(recs)
    try:
        while len(recs) > 0:
            cmd_input = raw_input("> ")
            if cmd_input in ('help', ''):
                print(
                    "'ok' or 'yes' to confirm, and undelete all recordings in the current list.\n"
                    "'list'        to reprint the list.\n"
                    "<int>         to remove the recording from the list, and leave unchanged."
                )
            elif cmd_input in ('yes', 'ok'):
                undelete_all(backend, recs)
                break
            elif cmd_input in ('list',):
                recs = list_recs(recs)
            else:
                try:
                    recs.pop(int(cmd_input))
                except ValueError:
                    print('invalid input')
    except KeyboardInterrupt:
        pass
    except EOFError:
        pass


def main():
    """Startup function."""
    parser = OptionParser(usage="usage: %prog [options]")
    parser.add_option(
        "--verbose",
        action="store_true",
        default=False,
        help="enable verbose output of MythTV API")
    parser.add_option(
        '-f',
        "--force",
        action="store_true",
        default=False,
        help="non-interactive mode, answer 'yes' to all questions")
    parser.add_option(
        '-t',
        "--title",
        action="store",
        type="string",
        help="limit recordings that match title")

    opts, _ = parser.parse_args()
    MythLog._setlevel('unknown' if opts.verbose else 'err')  # pylint:disable=protected-access

    try:
        backend = MythBE()
        recs = [
            r for r in list(backend.getRecordings()) if r.recgroup == 'Deleted'
        ]
        if opts.title:
            recs = [
                r for r in recs
                if re.findall(opts.title, r.title, re.IGNORECASE)
            ]
        if len(recs) == 0:
            print('no matching recordings found')
            sys.exit(0)
        if opts.force:
            undelete_all(backend, recs)
        else:
            interactive_undelete(backend, recs)
    except MythDBError as e:
        if e.name == 'DB_CREDENTIALS':
            print("ERROR: Could not find MythDB host:port OR correct login "
                  "credentials!")
            sys.exit(-1)
        else:
            raise
    sys.exit(0)


if __name__ == '__main__':
    main()
