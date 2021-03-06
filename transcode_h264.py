#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
MythTV transcoding script that supports h.264 encoding output.

Designed to be a USERJOB of the form </path to script/transcode_h264.py %JOBID%>
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from glob import glob
import argparse
import datetime
import logging
import os
import shutil
import sys
import tempfile
import time

import MythTV

import mythutils
from mythutils import JobStatus

# Remove any commercial skip points that were previously detected from the source media.
FLUSH_COMMSKIP = False

# Create a new seek table in MythTV for the newly transcoded video.
BUILD_SEEKTABLE = False

# Set the actual encoder "RF" quality (e.g. 10 to 30). This has the biggest impact on file size and
# quality level.
RF_QUALITY = 23

# encoding speeds [ultrafast, superfast, veryfast, faster, fast, medium, slow, slower]
PRESET_SPEED = 'veryfast'

# Callable that returns MythTV.Job instance
Job = None  # pylint:disable=invalid-name

# Callable that returns MythTV.Recorded instance
Recording = None  # pylint:disable=invalid-name

# Debug output of handbrake command
TRANSCODE_LOG = '/var/log/mythtv/handbrake.log'

# shell option to disable COMMAND output
NULL_OUTPUT_OPT = '>/dev/null 2>&1'
NULL_STDIO_OPT = '1>/dev/null'


def main():
    """Script startup method."""
    global Job, Recording  # pylint:disable=global-statement,invalid-name
    opts = parse_options()
    init_logging(opts.debug)
    Job = wrap_mythtv_job(opts.jobid)
    Recording = wrap_mythtv_recording(Job(), opts.chanid, opts.starttime)
    run_transcode_workflow()


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
        logging.error('Missing JOBID argument, or --chanid and --starttime.')
        sys.exit(1)

    if opts.jobid and (opts.chanid or opts.starttime):
        opts.print_help()
        logging.error('JOBID can not be combined with other options.')
        sys.exit(1)

    if opts.verbose:
        if opts.verbose == 'help':
            print(MythTV.MythLog.helptext)
            sys.exit()
        MythTV.MythLog._setlevel(opts.verbose)  # pylint:disable=protected-access
    return opts


def init_logging(debug=False):
    """Setup logging to terminal and to TRANSCODE_LOG."""
    # file logger
    format_ = '[%(asctime)s] %(levelname)8s: %(message)s'
    logging.basicConfig(filename=TRANSCODE_LOG, format=format_, level=logging.DEBUG)
    # console logger
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    logging.getLogger('').addHandler(console)
    if debug:
        console.setLevel(logging.DEBUG)


def wrap_mythtv_job(jobid=None):
    """Return a callable that returns a connected Job() instance."""
    def _fn():
        MythTV.MythDB().shared.data.clear()  # clear the SQL connection
        return MythTV.Job(jobid)
    return _fn if jobid else lambda: None


def wrap_mythtv_recording(job=None, chanid=None, starttime=None):
    """
    Return a callable that return a connected Recording() instance. The recording is specified by
    either the JOB or combination of CHANID and STARTTIME values. The MythTV API supports many
    different formats of STARTTIME.
    """
    def _wrap(_chanid, _starttime):
        def _fn():
            MythTV.MythDB().shared.data.clear()  # clear any closed SQL connections
            return MythTV.Recorded((_chanid, _starttime))
        return _fn
    return _wrap(job.chanid, job.starttime) if job else _wrap(chanid, starttime)


def run_transcode_workflow():
    """
    Perform a transcode operation on a specified MythTV recording. When complete, the original
    recording will be replaced with the new transcoded file.
    """
    start_time = time.time()
    rec = Recording()
    verify_recording_or_exit(rec)
    file_src, file_dst = get_rec_file_paths(rec)
    remove_commercials(rec, file_src)

    rec = transcode(rec, file_src, file_dst)

    create_thumbnails(file_dst)
    flush_commercial_skips(rec)
    rebuild_seek_table(rec)
    elapsed = int(time.time() - start_time)
    job_update(JobStatus.FINISHED,
               'Finished {} in {}.'.format(mythutils.recording_name(rec),
                                           datetime.timedelta(seconds=elapsed)))


def verify_recording_or_exit(rec):
    """Exit script if recording can not be transcoded."""
    if rec.recgroup.lower() == 'deleted':
        job_update(JobStatus.CANCELLED, 'Ignoring recording marked for delete.')
        sys.exit(1)
    elif rec.transcoded:
        job_update(JobStatus.CANCELLED, 'Ignoring previously transcoded recording.')
        sys.exit(1)


def get_rec_file_paths(rec):
    """Return the source and destination recording paths."""
    fsrc = mythutils.recording_file_path(rec)
    fdst = fsrc.rsplit('.', 1)[0] + '.mp4'
    return (fsrc, fdst)


def remove_commercials(rec, fdst):
    """
    Remove commercials (e.g. cut list) from source recording if enabled, replacing the 'fdst' file.
    In the event there is no cut list on the file, no changes are made. The recording's cut list is
    a set of markings that indicate the start/stop points after commercial detection.

    The algorithm works in the following steps:
        1. mythcommflag tool is used to create cut list marks in the DB. This should be configured
           in the recording options.
        2. mythtranscode 'creates' a cut list of the commercials with the '--gencutlist' option.
        3. mythtranscode losslessly strips the cut points from the recording with '--honorcutlist'
           option. The new (smaller) recording is written to a '.tmp' file.
    """
    # .commflagged == 1
    #
    # mythutil --chanid --starttime (mythformat) --gencutlist
    # mythtranscode --chanid --startime (mythformat) --honorcutlist
    # copy .tmp to .mpg
    if rec.cutlist == 1:
        job_update(JobStatus.RUNNING, 'Removing cut list.')
        ftmp = tempfile.mkstemp(dir=os.path.dirname(fdst))
        task = MythTV.System(path='mythtranscode')
        task.append('--chanid', rec.chanid)
        task.append('--starttime', rec.starttime.mythformat())
        task.append('--mpeg2')  # enable lossless output
        task.append('--honorcutlist')
        task.append('-o', '"{}"'.format(ftmp))
        logging.debug(task.path)
        try:
            output = task.command(NULL_STDIO_OPT)
            logging.debug(output)
        except MythTV.MythError as e:
            job_update(JobStatus.ERRORED, 'Removing cut list failed.')
            sys.exit('mythtranscode failed with error: {}'.format(e))
        rec.cutlist = 0
        rec.update()
        shutil.move(ftmp, fdst)


def transcode(rec, fsrc, fdst):
    """The main transcode workflow steps."""
    job_update(JobStatus.RUNNING, 'Transcoding {}.'.format(mythutils.recording_name(rec)))
    try:
        handbrake(fsrc, fdst)
    except MythTV.MythError as e:
        job_update(JobStatus.ERRORED, 'Transcoding failed.')
        sys.exit('Handbrake failed with error: {}'.format(e))
    job_update(JobStatus.RUNNING, 'Handbrake finished encoding.')
    # reconnect recording DB instance in case the other has timed out
    rec = Recording()
    rec.transcoded = 1
    rec.filesize = os.path.getsize(fdst)
    rec.basename = os.path.basename(fdst)
    job_update(JobStatus.RUNNING, 'Removing seek points.')
    rec.seek.clean()
    job_update(JobStatus.RUNNING, 'Saving change to DB.')
    rec.update()
    delete_recording(fsrc)
    return rec


def handbrake(fsrc, fdst):
    """Configure and run HandBrakeCLI command."""
    HB_COMMAND = 'HandBrakeCLI'
    # static options for handbrake command-line encoder
    OPTS_GENERAL = ['--verbose']
    OPTS_VIDEO = [
        '--encoder x264',                   # h.264 encoding
        '--quality ' + str(RF_QUALITY),     # mid-quality, lower is better (18-30 is normal)
        '--x264-preset ' + str(PRESET_SPEED),  # encoding speeds
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
        '--ab 160',         # bitrate of encoding in kbps
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
        '--optimize',       # improve streaming start speed
        '--format mp4',     # set output container to MP4
        '--output',         # the destination file to write
    ]
    task = MythTV.System(path=HB_COMMAND)
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
    task.append('2>>' + TRANSCODE_LOG)
    logging.debug(task.path)
    task.command(NULL_STDIO_OPT)


def create_thumbnails(fsrc):
    """
    Manually generate video thumbnails using 3rd-party application since mythpreviewgen usually
    scrambles image output of mp4 recordings.
    """
    job_update(JobStatus.RUNNING, 'Generating recording thumbnails.')
    THUMBNAIL_COMMAND = 'ffmpegthumbnailer'
    EXTENSION = 'png'
    task = MythTV.System(path=THUMBNAIL_COMMAND)
    task.append('-q9')  # quality level 0-10
    task.append('-t10')  # seek percentage or time (hh:mm:ss)
    task.append('-i{}'.format(fsrc))  # source mp4 recording
    logging.debug(task.path)
    task.command('-s320', '-o{}.{}'.format(fsrc, EXTENSION), NULL_OUTPUT_OPT)
    # mythweb large
    task.command('-s320', '-o{}.-1.320x180.{}'.format(fsrc, EXTENSION), NULL_OUTPUT_OPT)
    # mythweb small
    task.command('-s100', '-o{}.-1.100x56.{}'.format(fsrc, EXTENSION), NULL_OUTPUT_OPT)


def flush_commercial_skips(rec):
    """
    Remove commercial skip markings the DB. This is useful if the transcoding step is going to
    physically remove the commercials from the video stream.
    """
    if FLUSH_COMMSKIP:
        job_update(JobStatus.RUNNING, 'Flushing commercial skip data.')
        for index, mark in reversed(list(enumerate(rec.markup))):
            if mark.type in (rec.markup.MARK_COMM_START, rec.markup.MARK_COMM_END):
                del rec.markup[index]
        rec.bookmark = 0
        rec.markup.commit()


def delete_recording(fsrc):
    """Remove recording and related files from storage."""
    assert fsrc
    for filename in glob('%s*.png' % fsrc):
        os.remove(filename)
    os.remove(fsrc)


def rebuild_seek_table(rec):
    """
    Generate a new seek table for the encoding. This likely helps MythTV seek the video faster.
    """
    if BUILD_SEEKTABLE:
        job_update(JobStatus.RUNNING, 'Rebuilding seektable.')
        task = MythTV.System(path='mythcommflag')
        task.append('--chanid', rec.chanid)
        task.append('--starttime', rec.starttime.mythformat())
        task.append('--rebuild')
        logging.debug(task.path)
        output = task.command(NULL_STDIO_OPT)
        logging.debug(output)


def job_update(status, comment):
    """Update the Job status, if JOBID is provided as script parameter."""
    if status in JobStatus.ANY_ERROR:
        logging.error(comment)
    else:
        logging.info(comment)
    try:
        Job().update({'status': status, 'comment': comment})
    except AttributeError:
        pass  # ignore exception if there is no MythJob


if __name__ == '__main__':
    try:
        main()
    except Exception as e:  # pylint:disable=broad-except
        job_update(JobStatus.ERRORED, str(e))
        logging.exception(e)
        sys.exit(1)
