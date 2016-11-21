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

from MythTV import Job, Recorded, System, MythDB, findfile, MythError, MythLog
from glob import glob
from optparse import OptionParser
import os
import sys

TRANSCODER = '/usr/bin/ffmpeg'
FLUSH_COMMSKIP = True
BUILD_SEEKTABLE = True


class Transcode(object):
    db = MythDB()

    def __init__(self, jobid=None, chanid=None, starttime=None):
        self.jobid = jobid
        self.chanid = chanid
        self.startime = starttime
        self.job = self._init_job()
        self.rec = self._init_rec()

    def _init_job(self):
        job = None
        if self.jobid:
            job = Job(self.jobid, db=db)
            # ensure chanid and starttime are set
            self.chanid = job.chanid
            self.starttime = job.starttime
        return job

    def _init_recording(self):
        return Recorded((self.chanid, self.starttime), db=self.db)

    def _job_update(self, status, comment):
        if self.job:
            self.job.update({'status': status, 'comment': comment})

    def runjob(self):
        """Transcode a recording given a jobid or chanid and starttime."""
        infile, outfile, tmpfile = self.get_rec_file_paths()
        clfile = self.rm_cutlist(infile)
        self.transcode(clfile)
        self.flush_commercial_skips()
        self.finalize(outfile, tmpfile)
        self.rebuild_seek_table()
        self._job_update(272, 'Transcode Completed')

    def get_rec_file_paths(self):
        sg = findfile(
            '/' + self.rec.basename, self.rec.storagegroup, db=self.db)
        if sg is None:
            raise Exception('Local access to recording not found.')

        infile = os.path.join(sg.dirname, self.rec.basename)
        outfile = '%s.mp4' % infile.rsplit('.', 1)[0]
        tmpfile = '%s.tmp' % infile.rsplit('.', 1)[0]
        return (infile, outfile, tmpfile)

    def rm_cutlist(self, fsrc, fdst):
        """Remove cutlist from source recording if enabled."""
        if self.rec.cutlist == 1:
            self._job_update(4, 'Removing Cutlist')
            task = System(path='mythtranscode', db=db)
            try:
                output = task('--chanid "%s"' % chanid,
                              '--starttime "%s"' % time_as_arg(starttime),
                              '--mpeg2', '--honorcutlist', '-o "%s"' % fdst,
                              '2> /dev/null')
            except MythError, e:
                print('Command failed with output:\n' + e.stderr)
                self._job_update(304, 'Removing Cutlist failed')
                sys.exit(e.retcode)
            return fdst
        else:
            return fsrc

    def transcode(self, fsrc, fdst):
        self._job_update(4, 'Transcoding to mp4')
        task = System(path=transcoder, db=db)
        try:
            output = task('-i "%s"' % fsrc, '-filter:v yadif=0:-1:1',
                          '-c:v libx264', '-preset:v slow', '-crf:v 18',
                          '-strict -2', '-metadata:s:a:0', 'language="eng"',
                          '"%s"' % fdst, '2> /dev/null')
        except MythError, e:
            print('Command failed with output:\n' + e.stderr)
            self._job_update(304, 'Transcoding to mp4 failed')
            sys.exit(e.retcode)

    def flush_commercial_skips(self):
        if flush_commskip:
            for index, mark in reversed(list(enumerate(self.rec.markup))):
                if mark.type in (self.rec.markup.MARK_COMM_START,
                                 self.rec.markup.MARK_COMM_END):
                    del self.rec.markup[index]
            self.rec.bookmark = 0
            self.rec.cutlist = 0
            self.rec.markup.commit()

    def finalize(self, fsrc, fdst):
        # delete the old *.png files
        for filename in glob('%s*.png' % fsrc):
            os.remove(filename)
        self.rec.filesize = os.path.getsize(fdst)
        self.rec.transcoded = 1
        self.rec.seek.clean()
        self.rec.basename = os.path.basename(fdst)
        os.remove(fsrc)
        rec.update()  # save recording metadata to DB

    def rebuild_seek_table(self):
        self._job_update(4, 'Rebuilding seektable')
        if build_seektable:
            task = System(path='mythcommflag')
            task.command('--chanid %s' % self.chanid,
                         '--starttime %s' % time_as_arg(self.starttime),
                         '--rebuild', '2> /dev/null')

    @staticmethod
    def time_as_arg(time):
        # reformat 'time' string for use with mythtranscode/ffmpeg/mythcommflag
        arg = str(time.utcisoformat().replace(u':', '').replace(u' ', '')
                  .replace(u'T', '').replace('-', ''))
        return arg


def main():
    parser = OptionParser(usage="usage: %prog [options] [jobid]")

    parser.add_option(
        '--chanid',
        action='store',
        type='int',
        dest='chanid',
        help='Use chanid for manual operation')
    parser.add_option(
        '--starttime',
        action='store',
        type='int',
        dest='starttime',
        help='Use starttime for manual operation')
    parser.add_option(
        '-v',
        '--verbose',
        action='store',
        type='string',
        dest='verbose',
        help='Verbosity level')

    opts, args = parser.parse_args()

    if opts.verbose:
        if opts.verbose == 'help':
            print(MythLog.helptext)
            sys.exit(0)
        MythLog._setlevel(opts.verbose)

    if len(args) == 1:
        runjob(jobid=args[0])
    elif opts.chanid and opts.starttime:
        runjob(chanid=opts.chanid, starttime=opts.starttime)
    else:
        print('Script must be provided jobid, or chanid and starttime.')
        sys.exit(1)


if __name__ == '__main__':
    main()
