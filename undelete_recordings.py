#!/usr/bin/env python2
# -*- coding: UTF-8 -*-
#---------------------------
# Name: undelete_recordings.py
# Python Script
# Author: Raymond Wagner, Patrick C. McGinty
# Purpose
#   This python script provides a command line tool to search and
#   undelete all recordings.
#--------------------------

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from MythTV import MythBE, MythBEError, MythLog, MythDBError
from MythTV.static import BACKEND_SEP
from optparse import OptionParser
import re
import sys

# MythBE backend instance
BACKEND = None

# MythTV datetime's (dt.py) custom tzinfo (posixtzinfo) has a bug that can
# corrupt the recording's timestmap in the client. We don't really care about
# local date format, therefore the bug can be fixed by setting all timestamps to
# integers and bypassing and conversion bugs.
from MythTV.altdict import DictData
TIMESTAMP_FIELD_INDEX = 4
DictData._trans[TIMESTAMP_FIELD_INDEX] = int
DictData._inv_trans[TIMESTAMP_FIELD_INDEX] = str

def rec_toString(r):
    fulltitle = ' - '.join([r.title,r.subtitle]) if r.subtitle else r.title
    return str('[%s] %s' % (r.starttime, fulltitle))

def list_recs(recs):
    print('Below is a list of matching recordings:')
    recs = dict(enumerate(recs.values()))
    for i, rec in recs.items():
        print('  %d. %s' % (i, rec_toString(rec)))
    return recs


def undelete(rec):
    """Undeletes a recording.

    In the usual case only the recgroup and autoexpire value need to be modified.
    Setting the deletepending value is done in event that the backend expired the
    recording.
    """
    print('undelete ' + rec_toString(rec))
    cmd = BACKEND_SEP.join(['UNDELETE_RECORDING', rec.toString()])
    res = BACKEND.backendCommand(cmd)
    if int(res) != 0:
        raise MythBEError("undelete failed")


parser = OptionParser(usage="usage: %prog [options]")
parser.add_option("--verbose", action="store_true", default=False,
                  help="enable verbose output of MythTV API")
parser.add_option('-f', "--force", action="store_true", default=False,
                  help="non-interactive mode, answer 'yes' to all questions")
parser.add_option('-t', "--title", action="store", type="string",
                  help="limit recordings that match title")

opts, args = parser.parse_args()
MythLog._setlevel('unknown' if opts.verbose else 'err')

param = {'recgroup': 'Deleted', 'title': opts.title, }

try:
    BACKEND = MythBE()
    recs = [r for r in list(BACKEND.getRecordings())
            if r.recgroup == 'Deleted']
    if opts.title:
        recs = [r for r in recs if re.findall(
            opts.title, r.title, re.IGNORECASE)]
    if len(recs) == 0:
        print('no matching recordings found')
        sys.exit(0)
    if opts.force:
        for rec in recs:
            undelete(rec)
        sys.exit(0)
except MythDBError as e:
    if 'DB_CREDENTIALS' == e.ename:
        print("ERROR: Could not find MythDB host:port OR correct login "
              "credentials!")
        sys.exit(-1)
    else:
        raise

recs = dict(enumerate(recs))
try:
    list_recs(recs)
    while len(recs) > 0:
        inp = raw_input("> ")
        if inp in ('help', ''):
            print("'ok' or 'yes' to confirm, and undelete all recordings in the current list.\n"
                  "'list'        to reprint the list.\n"
                  "<int>         to remove the recording from the list, and leave unchanged.")
        elif inp in ('yes', 'ok'):
            for rec in recs.values():
                undelete(rec)
            break
        elif inp in ('list',):
            recs = list_recs(recs)
        else:
            try:
                recs.pop(int(inp))
            except:
                print('invalid input')
except KeyboardInterrupt:
    pass
except EOFError:
    pass
