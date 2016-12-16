"""
A set of helper methods necessary to work with the MythTV API.
"""

import os

import MythTV
from MythTV.altdict import DictData


class JobStatus(object):
    """MythTV Job status values."""
    UNKNOWN = 0x000
    QUEUED = 0x001
    PENDING = 0x002
    STARTING = 0x003
    RUNNING = 0x004
    STOPPING = 0x005
    PAUSED = 0x006
    RETRY = 0x007
    ERRORING = 0x008
    ABORTING = 0x009
    DONE = 0x100
    FINISHED = 0x110
    ABORTED = 0x120
    ERRORED = 0x130
    CANCELLED = 0x140


def patch_mythtv_time_api():
    """
    MythTV datetime's (dt.py) custom tzinfo (posixtzinfo) has a bug that can corrupt the recording's
    timestmap in the client. We don't really care about local date format, therefore the bug can be
    fixed by setting all timestamps to integers and bypassing and conversion bugs.
    """
    TIMESTAMP_FIELD_INDEX = 4
    DictData._trans[TIMESTAMP_FIELD_INDEX] = int      # pylint:disable=protected-access
    DictData._inv_trans[TIMESTAMP_FIELD_INDEX] = str  # pylint:disable=protected-access


def recording_name(rec):
    """Return string value of recording."""
    title = ' - '.join([rec.title, rec.subtitle]) if rec.subtitle else rec.title
    return '"{}" @ {} ({})'.format(title, rec.starttime, rec.basename)


def recording_file_path(db, rec):
    """
    Return the full path to the recording on the file system.

    :raises RuntimeError: If file does not exists at the expected location.
    """
    storage = MythTV.findfile('/' + rec.basename, rec.storagegroup, db=db)
    if not storage:
        raise RuntimeError('Local access to recording not found.')
    return os.path.join(storage.dirname, rec.basename)
