#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
MythTV transcoding script that supports h.264 encoding output.

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

import MythTV

import mythutils
from mythutils import JobStatus

# Remove any commercial skip points that were previously detected from the source media.
FLUSH_COMMSKIP = True

# Create a new seek table in MythTV for the newly transcoded video.
BUILD_SEEKTABLE = True

# Set the actual encoder "RF" quality (e.g. 10 to 30). This has the biggest impact on file size and
# quality level.
RF_QUALITY = 23


def handbrake(db, fsrc, fdst, debug):
    """Interface to configuration HandBrakeCLI command options."""

    HB_COMMAND = 'HandBrakeCLI'
    # fixed options for the transcoder command
    OPTS_GENERAL = ['--verbose']
    OPTS_VIDEO = [
        '--encoder x264',                   # h.264 encoding
        '--quality ' + str(RF_QUALITY),     # mid-quality, lower is better (18-30 is normal)
        '--x264-preset faster',             # encoding speeds [ultrafast, superfast, veryfast,
                                            #                  faster, fast, medium, slow, slower]
        '--x264-tune film',                 # image tuning (none, film, animation, ...)
        '--x264-profile high',              # encoder profile, most devices support 'high' or better
        '--h264-level 4.1',                 # profile level, most support 4.1 or better
    ]
    OPTS_PICTURE = [
        '--maxHeight=720',      # set max height
        '--modulus=2',          # make resolution divisible by 2
        '--loose-anamorphic',   # good default, allows resizing and ignores non-anamorphic
                                # video, if video is anamorphic it should play correctly
    ]
    OPTS_FILTER = [
        '--decomb',     # remove interlacing (safe for all video)
        '--detelecine', # remove telecining (safe for all video)
    ]
    OPTS_AUDIO = [
        '--audio 1',        # select 1st audio track (add more "1,2" if you want other
                            # language options
        '--aencoder faac',  # use fAAC encoder (very good quality)
        '-ab 160',          # bitrate of encoding in kbps
        '--mixdown dpl2',   # set downmix option to Dolby ProLogic II
        '--arate auto',     # set audio rate to automatic
    ]
    OPTS_SUBTITLE = [
        '--native-language english',    # the default language
        '--subtitle scan,1,2,3',        # 'scan' detects native-language subs that show/movie
                                        # should display. '1,2,3' means to copy other subtitle
                                        # tracks to output if they don't exist, that is fine
        '--subtitle-forced scan',       # only detect 'scan' subtitle track if is set to 'force'
        '--subtitle-burned scan',       # burn the 'scan' subtitle track if it was found
    ]
    OPTS_INPUT = ['--input']  # command to set the source media
    OPTS_OUTPUT = [
        '--format mp4', # set output container to MP4
        '--large-file', # allow large files >4GB (should never hit this anyway)
        '--output',     # the destination file to write
    ]
    task = MythTV.System(path=HB_COMMAND, db=db)
    task.append(*OPTS_GENERAL)
    task.append(*OPTS_VIDEO)
    task.append(*OPTS_PICTURE)
    task.append(*OPTS_FILTER)
    task.append(*OPTS_AUDIO)
    task.append(*OPTS_SUBTITLE)
    task.append(*OPTS_INPUT)
    task.append(fsrc)
    task.append(*OPTS_OUTPUT)
    task.append(fdst)
    if debug:
        print(task.path)
        return task.command('2>&1')
    else:
        return task.command('>/dev/null 2>&1')


class Transcode(object):
    """
    Perform a transcode operation on a specified MythTV recording.

    The recording is specified by either the JOBID or combination of CHANID and STARTTIME values.
    The MythTV API supports many different formats of starttime. When complete, the original
    recording will be replaced with the new transcoded file.
    """
    db = MythTV.MythDB()  # pylint:disable=invalid-name

    def __init__(self, jobid=None, chanid=None, starttime=None, debug=False):
        self.job = MythTV.Job(self.jobid, db=self.db) if jobid else None
        if self.job:
            self.rec = MythTV.Recorded((job.chanid, job.startitme), db=self.db)
        else:
            self.rec = MythTV.Recorded((chanid, starttime), db=self.db)
        self.debug = debug

    def _job_update(self, status, comment):
        if self.debug: print(comment)
        if self.job: self.job.update({'status': status, 'comment': comment})

    def run(self):
        """Transcode a recording given a jobid or chanid and starttime."""
        file_src, file_dst = self._get_rec_file_paths()
        self.rm_cutlist(file_src)
        self._transcode(file_src, file_dst)
        self._flush_commercial_skips()
        self._finalize(file_src)
        self._rebuild_seek_table()
        self._job_update(JobStatus.FINISHED, 'Transcode Completed')

    def _get_rec_file_paths(self):
        fsrc = mythutils.recording_file_path(self.db, self.rec)
        fdst = fsrc.rsplit('.', 1)[0] + '.mp4'
        return (fsrc, fdst)

    def rm_cutlist(self, fdst):
        """
        Remove cutlist from source recording if enabled, replacing the 'fdst' file.

        In the event there is no cutlist on the file, no changes are made.
        """
        if self.rec.cutlist == 1:
            self._job_update(JobStatus.RUNNING, 'Removing Cutlist')
            ftmp = tempfile.mkstemp(dir=os.path.dirname(fdst))
            task = MythTV.System(path='mythtranscode', db=self.db)
            task.append('--chanid', self.rec.chanid),
            task.append('--starttime', self.rec.starttime.mythformat())
            task.append('--mpeg2')
            task.append('--honorcutlist')
            task.append('-o', '"{}"'.format(ftmp))
            if self.debug: print(task.path)
            try:
                output = task.command('2> /dev/null')
            except MythTV.MythError as e:
                self._job_update(JobStatus.ERRORED, 'Removing Cutlist failed')
                raise RuntimeError('Command failed with output:\n' + e.stderr)

            if self.debug: print(output)
            self.rec.cutlist = 0
            shutil.move(ftmp, fdst)

    def _transcode(self, fsrc, fdst):
        self._job_update(JobStatus.RUNNING, 'Transcoding {} to mp4'.format(mythutils.recording_name(self.rec)))
        stdout = None
        try:
            stdout = handbrake(self.db, fsrc, fdst, debug=self.debug)
        except MythTV.MythError as e:
            self._job_update(JobStatus.ERRORED, 'Transcoding to mp4 failed!')
            raise RuntimeError('Command failed with output:\n' + e.stderr)
        except MythTV.MythFileError as e:
            self._job_update(JobStatus.ERRORED, 'Transcoding to mp4 failed!')
            raise RuntimeError('{}: {}'.format(stdout, e.message))

        self.rec.transcoded = 1
        self.rec.filesize = os.path.getsize(fdst)
        self.rec.basename = os.path.basename(fdst)

    def _flush_commercial_skips(self):
        if FLUSH_COMMSKIP:
            for index, mark in reversed(list(enumerate(self.rec.markup))):
                if mark.type in (self.rec.markup.MARK_COMM_START, self.rec.markup.MARK_COMM_END):
                    del self.rec.markup[index]
            self.rec.bookmark = 0
            self.rec.markup.commit()

    def _finalize(self, fsrc):
        """Update the recording DB entry and remove original."""
        # delete the old *.png files
        assert '' != fsrc
        for filename in glob('%s*.png' % fsrc):
            os.remove(filename)
        self.rec.seek.clean()
        self.rec.update()  # save recording metadata to DB
        os.remove(fsrc)  # safe to remove original recording

    def _rebuild_seek_table(self):
        self._job_update(JobStatus.RUNNING, 'Rebuilding seektable')
        if BUILD_SEEKTABLE:
            task = MythTV.System(path='mythcommflag')
            task.append('--chanid', self.rec.chanid)
            task.append('--starttime', self.rec.starttime.mythformat())
            task.append('--rebuild')
            if self.debug: print(task.path)
            task.command('2> /dev/null')



def main():
    """Parse options and run Transcode class."""
    parser = OptionParser(usage="usage: %prog [OPTIONS] [JOBID]")

    parser.add_option(
        '--chanid', action='store', type='int', help='Use chanid for manual operation')
    parser.add_option(
        '--starttime',
        action='store',
        type='int',
        help='Use generic start time (unix/formatted/etc.) of recording.')
    parser.add_option('-v', '--verbose', action='store', type='string', help='Verbosity level')
    parser.add_option('-d', '--debug', action='store_true', help='Enable debug output')

    opts, args = parser.parse_args()

    if opts.verbose:
        if opts.verbose == 'help':
            print(MythTV.MythLog.helptext)
            sys.exit(0)
        MythTV.MythLog._setlevel(opts.verbose)  # pylint:disable=protected-access
    del opts.verbose

    if len(args) == 1:
        transcode = Transcode(jobid=args[0], **vars(opts))
    elif opts.chanid and opts.starttime:
        transcode = Transcode(**vars(opts))
    else:
        raise ValueError('Missing JOBID argument, or --chanid and --starttime.')
    transcode.run()


if __name__ == '__main__':
    try:
        main()
        sys.exit(0)
    except (RuntimeError, ValueError) as e:
        print(e.message)
        sys.exit(1)
