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

from MythTV import MythDB, MythLog, MythDBError
import sys


def list_recs(recs):
    print('Below is a list of matching recordings:')
    recs = dict(enumerate(recs.values()))
    for i, rec in recs.items():
        print('  %d. [%s] %s - %s' %
              (i, rec.starttime.isoformat(), rec.title, rec.subtitle))
    return recs

# Undeletes a recording. In the usual case only the recgroup and autoexpire
# value need to be modified. Setting the deletepending value is done in event
# that the backend expired the recording.
def undelete(rec):
    print('undeleting ', str(rec))
    rec.update(recgroup="Default",autoexpire=0,deletepending=0)

param = { 'recgroup':'Deleted' }

# Valid Search Arguments:
#   title autoexpire watched closecaptioned generic hostname category subtitle
#   commflagged storagegroup partnumber cast transcoded duplicate chanid stars
#   category_type parttotal livetv hdtv subtitled starttime recgroup airdate
#   seriesid basename manualid progstart playgroup stereo showtype
#   syndicatedepisodenumber programid
#
# Runtime options:
#   --force             non-interactive, perform action on all results
#   --verbose=LEVEL     enable verbose loggin in MythTV API
arg = ''
arg_list = list(sys.argv[1:])
while len(arg_list):
    arg = arg_list.pop(0)
    if arg[:2] == '--':
        arg = arg[2:]
        if '=' in arg:
            arg = arg.split('=', 1)
            param[arg[0]] = arg[1]
        else:
            if len(arg_list):
                arg_val = arg_list.pop(0)
                if (arg_val[:2] == '--') or (arg_val[:1] == '-'):
                    arg_list.insert(0, arg_val)
                    param[arg] = ''
                else:
                    param[arg] = arg_val
            else:
                param[arg] = ''

MythLog._setlevel(param.get('verbose', 'none'))
try:
    param.pop('verbose')
except:
    pass

force = False
if 'force' in param:
    force = True
    param.pop('force')

try:
    recs = list(MythDB().searchRecorded(**param))
    if len(recs) == 0:
        print('no matching recordings found')
        sys.exit(0)
    if force:
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
