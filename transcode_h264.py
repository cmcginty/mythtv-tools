#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
MythTv transcoding script that supports h.264 encoding output.

Designed to be a USERJOB of the form </path to script/transcode_h264.py %JOBID%>
Credits: 2015 Michael Stucky, based on Raymond Wagner's transcode wrapper stub.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from glob import glob
from optparse import OptionParser
import os
import shutil
import sys
import tempfile

from MythTV import Job, Recorded, System, MythDB, findfile, MythError, MythLog

TRANSCODER = '/usr/bin/ffmpeg'
FLUSH_COMMSKIP = True
BUILD_SEEKTABLE = True


class Transcode(object):
    """
    Perform a transcode operation on a specified MythTV recording.

    The recording is specfied by either the JOBID or combination of CHANID and STARTIME values.
    When complete, the original recording will be replaced with the new transcoded file.
    """
    db = MythDB()  # pylint:disable=invalid-name

    def __init__(self, jobid=None, chanid=None, starttime=None, debug=False):
        self.jobid = jobid
        self.chanid = chanid
        self.startime = starttime
        self.debug = debug
        self.job = self._init_job()
        self.rec = self._init_recording()

    def _init_job(self):
        job = None
        if self.jobid:
            job = Job(self.jobid, db=self.db)
            # ensure chanid and starttime are set
            self.chanid = job.chanid
            self.starttime = job.starttime
        return job

    def _init_recording(self):
        return Recorded((self.chanid, self.starttime), db=self.db)

    def _job_update(self, status, comment):
        if self.job:
            self.job.update({'status': status, 'comment': comment})

    def run(self):
        """Transcode a recording given a jobid or chanid and starttime."""
        file_src, file_dst = self._get_rec_file_paths()
        self.rm_cutlist(file_src)
        self._transcode(file_src, file_dst)
        self._flush_commercial_skips()
        self._finalize(file_src)
        self._rebuild_seek_table()
        self._job_update(272, 'Transcode Completed')

    def _get_rec_file_paths(self):
        storage = findfile(
            '/' + self.rec.basename, self.rec.storagegroup, db=self.db)
        if not storage:
            raise RuntimeError('Local access to recording not found.')

        fsrc = os.path.join(storage.dirname, self.rec.basename)
        fdst = '%s.mp4' % fsrc.rsplit('.', 1)[0]
        return (fsrc, fdst)

    def rm_cutlist(self, fdst):
        """
        Remove cutlist from source recording if enabled, replacing the 'fdst' file.

        In the event there is no cutlist on the file, no changes are made.
        """
        if self.rec.cutlist == 1:
            self._job_update(4, 'Removing Cutlist')
            ftmp = tempfile.mkstemp(dir=os.path.dirname(fdst))
            task = System(path='mythtranscode', db=self.db)
            try:
                output = task('--chanid "%s"' % self.chanid, '--starttime "%s"'
                              % self._time_as_arg(self.starttime), '--mpeg2',
                              '--honorcutlist', '-o "%s"' % ftmp,
                              '2> /dev/null')
                if self.debug:
                    print(output)
            except MythError as e:
                self._job_update(304, 'Removing Cutlist failed')
                raise RuntimeError('Command failed with output:\n' + e.stderr)
            self.rec.cutlist = 0
            shutil.move(ftmp, fdst)

    def _transcode(self, fsrc, fdst):
        self._job_update(4, 'Transcoding to mp4')
        task = System(path=TRANSCODER, db=self.db)
        try:
            output = task('-i "%s"' % fsrc, '-filter:v yadif=0:-1:1',
                          '-c:v libx264', '-preset:v slow', '-crf:v 18',
                          '-strict -2', '-metadata:s:a:0', 'language="eng"',
                          '"%s"' % fdst, '2> /dev/null')
            if self.debug:
                print(output)
        except MythError as e:
            self._job_update(304, 'Transcoding to mp4 failed')
            raise RuntimeError('Command failed with output:\n' + e.stderr)
        self.rec.transcoded = 1
        self.rec.filesize = os.path.getsize(fdst)
        self.rec.basename = os.path.basename(fdst)

    def _flush_commercial_skips(self):
        if FLUSH_COMMSKIP:
            for index, mark in reversed(list(enumerate(self.rec.markup))):
                if mark.type in (self.rec.markup.MARK_COMM_START,
                                 self.rec.markup.MARK_COMM_END):
                    del self.rec.markup[index]
            self.rec.bookmark = 0
            self.rec.markup.commit()

    def _finalize(self, fsrc):
        """Update the recording DB entry and remove original."""
        # delete the old *.png files
        for filename in glob('%s*.png' % fsrc):
            os.remove(filename)
        self.rec.seek.clean()
        self.rec.update()  # save recording metadata to DB
        os.remove(fsrc)  # safe to remove original rec

    def _rebuild_seek_table(self):
        self._job_update(4, 'Rebuilding seektable')
        if BUILD_SEEKTABLE:
            task = System(path='mythcommflag')
            task.command('--chanid %s' % self.chanid,
                         '--starttime %s' % self._time_as_arg(self.starttime),
                         '--rebuild', '2> /dev/null')

    @staticmethod
    def _time_as_arg(time):
        # reformat 'time' string for use with mythtranscode/ffmpeg/mythcommflag
        arg = str(time.utcisoformat().replace(u':', '').replace(u' ', '')
                  .replace(u'T', '').replace('-', ''))
        return arg


def main():
    """Parse options and run Transcode class."""
    parser = OptionParser(usage="usage: %prog [options] [jobid]")

    parser.add_option(
        '--chanid',
        action='store',
        type='int',
        help='Use chanid for manual operation')
    parser.add_option(
        '--starttime',
        action='store',
        type='int',
        help='Use starttime for manual operation')
    parser.add_option(
        '-v',
        '--verbose',
        action='store',
        type='string',
        help='Verbosity level')
    parser.add_option(
        '-d',
        '--debug',
        action='store',
        type='bool',
        default=False,
        help='Enable debug output')

    opts, args = parser.parse_args()

    if opts.verbose:
        if opts.verbose == 'help':
            print(MythLog.helptext)
            sys.exit(0)
        MythLog._setlevel(opts.verbose)  # pylint:disable=protected-access

    if len(args) == 1 or (opts.chanid and opts.starttime):
        transcode = Transcode(args[0] or None, **opts)
        transcode.run()
    else:
        raise ValueError(
            'Script must be provided jobid, or chanid and starttime.')


if __name__ == '__main__':
    try:
        main()
        sys.exit(0)
    except (RuntimeError, ValueError) as e:
        print(e.message)
        sys.exit(1)
