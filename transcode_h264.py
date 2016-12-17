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
import argparse
import logging
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

# MythTV Job() instance if JOBID was supplied
JOB = None

# MythTV DB instance
DB = None

# MythTV recording instance
RECORDING = None

# shell option to disable COMMAND output
NULL_OUTPUT_OPT = '>/dev/null 2>&1'
NULL_STDIO_OPT = '1>/dev/null'


def main():
    global DB, JOB, RECORDING  # pylint:disable=global-statement
    opts = parse_options()
    init_logging(opts.debug)
    DB = MythTV.MythDB()  # pylint:disable=invalid-name
    JOB = get_mythtv_job(opts.jobid)
    RECORDING = get_mythtv_recording(JOB, opts.chanid, opts.starttime)
    run_transcode_workflow()
    job_update(JobStatus.FINISHED, 'Transcode Completed')


def parse_options():
    """Parse options and run Transcode class."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'jobid', nargs='?', type=int, help='Number assigned by MythTV when queuing a job')
    parser.add_argument(
        '--chanid', type=int, help='Use chanid for manual operation')
    parser.add_argument(
        '--starttime',
        type=int,
        help='Use generic start time (unix/formatted/etc.) of recording.')
    parser.add_argument(
        '-v',
        '--verbose',
        type=str,
        help='Verbosity level (level "help" to see available levels)')
    parser.add_argument('-d', '--debug', action='store_true', help='Enable debug output')
    opts = parser.parse_args()
    if not (opts.jobid or (opts.chanid and opts.starttime)):
        opts.print_help()
        raise ValueError('Missing JOBID argument, or --chanid and --starttime.')

    if opts.jobid and (opts.chanid or opts.starttime):
        opts.print_help()
        raise ValueError('JOBID can not be combined with other options.')

    if opts.verbose:
        if opts.verbose == 'help':
            print(MythTV.MythLog.helptext)
            sys.exit(0)
        MythTV.MythLog._setlevel(opts.verbose)  # pylint:disable=protected-access
    return opts


def init_logging(debug=False):
    FORMAT = "%(levelname)s: %(message)s"
    level = logging.INFO
    if debug:
        level = logging.DEBUG
    logging.basicConfig(format=FORMAT, level=level)


def get_mythtv_job(jobid=None):
    return MythTV.Job(jobid, db=DB) if jobid else None


def get_mythtv_recording(job=None, chanid=None, starttime=None):
    """
    The recording is specified by either the JOB or combination of CHANID and STARTTIME values.
    The MythTV API supports many different formats of STARTTIME.
    """
    if job:
        return MythTV.Recorded((job.chanid, job.startitme), db=DB)
    else:
        return MythTV.Recorded((chanid, starttime), db=DB)


def run_transcode_workflow():
    """
    Perform a transcode operation on a specified MythTV recording.  When complete, the original
    recording will be replaced with the new transcoded file.
    """
    file_src, file_dst = get_rec_file_paths(RECORDING)
    if file_src == file_dst:
        raise ValueError('Source and destination file are the same: {}\n'
                         'Was the recording transcoded already?'.format(file_src))
    rm_cutlist(file_src)
    transcode(file_src, file_dst)
    flush_commercial_skips()
    finalize_result(file_src)
    rebuild_seek_table()


def get_rec_file_paths(rec):
    fsrc = mythutils.recording_file_path(DB, rec)
    fdst = fsrc.rsplit('.', 1)[0] + '.mp4'
    return (fsrc, fdst)


def rm_cutlist(fdst):
    """
    Remove cutlist from source recording if enabled, replacing the 'fdst' file.  In the event there
    is no cutlist on the file, no changes are made.
    """
    if RECORDING.cutlist == 1:
        job_update(JobStatus.RUNNING, 'Removing Cutlist')
        ftmp = tempfile.mkstemp(dir=os.path.dirname(fdst))
        task = MythTV.System(path='mythtranscode', db=DB)
        task.append('--chanid', RECORDING.chanid)
        task.append('--starttime', RECORDING.starttime.mythformat())
        task.append('--mpeg2')
        task.append('--honorcutlist')
        task.append('-o', '"{}"'.format(ftmp))
        logging.debug(task.path)
        try:
            output = task.command(NULL_STDIO_OPT)
        except MythTV.MythError as e:
            job_update(JobStatus.ERRORED, 'Removing Cutlist failed')
            raise RuntimeError('mythtranscode failed with error: {}\n{}'.format(e.ecode, output))
        logging.debug(output)
        RECORDING.cutlist = 0
        shutil.move(ftmp, fdst)


def transcode(fsrc, fdst):
    """The main transcode workflow steps."""
    job_update(JobStatus.RUNNING,
               'Transcoding {} to mp4'.format(mythutils.recording_name(RECORDING)))
    try:
        handbrake(fsrc, fdst)
    except MythTV.MythError as e:
        job_update(JobStatus.ERRORED, 'Transcoding to mp4 failed!')
        raise RuntimeError('Handbrake failed with error: {}\n{}'.format(e.ecode, e.args[0]))

    RECORDING.transcoded = 1
    RECORDING.filesize = os.path.getsize(fdst)
    RECORDING.basename = os.path.basename(fdst)


def handbrake(fsrc, fdst):
    """Configure and run HandBrakeCLI command."""
    HB_COMMAND = 'HandBrakeCLI'
    # fixed options for the transcoder command
    OPTS_GENERAL = []
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
        '--decomb',         # remove interlacing (safe for all video)
        '--detelecine',     # remove telecining (safe for all video)
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
        '--format mp4',     # set output container to MP4
        '--output',         # the destination file to write
    ]
    task = MythTV.System(path=HB_COMMAND, db=DB)
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
    logging.debug(task.path)
    return task.command(NULL_OUTPUT_OPT)


def flush_commercial_skips():
    """
    Remove commercial skip markings the DB. This is useful if the transcoding step is going to
    physically remove the commercials from the video stream.
    """
    if FLUSH_COMMSKIP:
        for index, mark in reversed(list(enumerate(RECORDING.markup))):
            if mark.type in (RECORDING.markup.MARK_COMM_START, RECORDING.markup.MARK_COMM_END):
                del RECORDING.markup[index]
        RECORDING.bookmark = 0
        RECORDING.markup.commit()


def finalize_result(fsrc):
    """Update the recording DB entry and remove original."""
    # delete the old *.png files
    assert fsrc
    for filename in glob('%s*.png' % fsrc):
        os.remove(filename)
    RECORDING.seek.clean()
    RECORDING.update()  # save recording metadata to DB
    os.remove(fsrc)  # safe to remove original recording


def rebuild_seek_table():
    """
    Generate a new seek table for the encoding. This likely helps MythTV seek the video faster.
    """
    job_update(JobStatus.RUNNING, 'Rebuilding seektable')
    if BUILD_SEEKTABLE:
        task = MythTV.System(path='mythcommflag')
        task.append('--chanid', RECORDING.chanid)
        task.append('--starttime', RECORDING.starttime.mythformat())
        task.append('--rebuild')
        logging.debug(task.path)
        task.command(NULL_OUTPUT_OPT)


def job_update(status, comment):
    """Update the JOB status, if JOBID is provided as script parameter."""
    logging.debug(comment)
    if JOB: JOB.update({'status': status, 'comment': comment})


if __name__ == '__main__':
    try:
        main()
        sys.exit(0)
    except (RuntimeError, ValueError) as e:
        logging.error(e.message)
        sys.exit(1)
