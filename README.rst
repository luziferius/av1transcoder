av1transcoder
=============

Encode AV1 videos with ffmpeg and libaom-av1.

This tool takes input videos and encodes them to AV1, using the libaom-av1 encoder.
At the time of writing this, the encoder is still very slow and can’t fully utilize modern multicore CPUs.
To mitigate this issue, this program splits the input at scene cuts and encodes multiple scenes in parallel,
allowing full CPU utilization and therefore faster encode times.


How is the overall encodeing process done?
------------------------------------------

This program takes multiple passes over each input file:

First, it uses the ffmpeg scenecut filter to determine the scene cuts.
This is done to avoid splitting the video in the middle of a scene,
because such a split causes an artificial and unneccessary bitrate spike.
It then merges all short scenes below the minimum length threshold,
so that the overall scene length falls between some acceptable lower and upper bound.
(Beware: Upper bounds are currently not implemented!)
It will then start an ffmpeg instance for each found scene, encoding the scenes independently in parallel.
Only a limited, configurable number of instances will run at any time to not overload the system.
Each running encoding will be performed outputting into a temporary directory,
and moved into a central scene repository directory on completion.
This ensures that only completed scene encodes are kept, making the process fully stoppable and resumable at any time.
Incomplete and aborted or otherwise failing encodes will be thrown away.

On resume, the program picks up any finished work, like finished
scenes in the scene repository and skips redoing them, thus avoiding duplicate work.

When all scenes are encoded, the ffmpeg concat demuxer is used to join all scenes into a single video file.


Requirements
------------

- Python >= 3.7 (3.6 may work, but is untested)
- recent ffmpeg with enabled and recent libaom-av1 (git master builds from around November 2019 for both ffmpeg and libaom-av1 work)


Install
-------

Install latest version from the repository checkout: **pip3 install .**.



Usage
-----

Execute *av1transcoder* after installation or run *av1transcoder-runner.py* from the source tree.
The program expects one or more video files as positional arguments. Each given video file will be transcoded to AV1.
The encoding process can be controlled using several optional command line switches.
Use the --help switch to view all possible parameters with explanations. 
Please read the notes about limitations and issues below (See point "Important notes")!

Full --help output
++++++++++++++++++

For reference, here is the --help output:

```
$ av1transcoder -h
usage: av1transcoder [-h] [-o OUTPUT_DIR] [-t TEMP_DIR] [-k] [-f]
                     [-s SCENE_CUT_THRESHOLD] [-m SECONDS] [-1]
                     [--crop TOP BOTTOM LEFT RIGHT] [-e STRING] [-g STRING]
                     [-c MAX_CONCURRENT_ENCODES]
                     [--dump-commands {yes,no,only}] [--deinterlace]
                     [-L NUMBER] [-v] [-V] [--cutelog-integration]
                     [--ffmpeg EXECUTABLE_NAME] [--ffprobe EXECUTABLE_NAME]
                     [--ffmpeg-base DIRECTORY]
                     input_file [input_file ...]

Transcode video files to AV1. This program takes input video files and
transcodes the video track to the AV1 format using the libaom-av1 reference
encoder.

positional arguments:
  input_file            Input video files. All given video files will be
                        transcoded to AV1.

optional arguments:
  -h, --help            show this help message and exit
  -o OUTPUT_DIR, --output-dir OUTPUT_DIR
                        Store the result in this directory. If set and --temp-
                        dir is unset, also store the temporary data here. If
                        unset, results are stored alongside the input file.
  -t TEMP_DIR, --temp-dir TEMP_DIR
                        Store temporary data in this directory. If unset, use
                        the output directory set by --output-dir. If that is
                        unset, store the temporary data alongside the input
                        data.
  -k, --keep-temp       Keep temporary data after the transcoding process
                        finished. May help in resolving transcoding issues.
  -f, --force-overwrite
                        Force overwriting existing data. If unset and filename
                        collisions are detected, the affected input files are
                        skipped. If set, existing files will be overwritten.
  -s SCENE_CUT_THRESHOLD, --scene-cut-threshold SCENE_CUT_THRESHOLD
                        Define the threshold value for the scene cut detection
                        filter. Accepts a decimal number in the range (0,1].
                        Defaults to 0.300000
  -m SECONDS, --min-scene-length SECONDS
                        Minimal allowed scene duration in seconds. Adjacent
                        detected scenes are combined to have at least this
                        duration, if possible. This is not a hard limit. It
                        prevents splitting the input video into many small and
                        independent encoding tasks to improve encoding
                        efficiency. Defaults to 30
  -1, --single-pass     Use Single-Pass encoding instead of Two-Pass encoding.
                        Various sources indicate that this is neither
                        recommended for libaom-av1 nor saves much time
                        compared to Two-Pass encoding.
  --crop TOP BOTTOM LEFT RIGHT
                        Crop the given number of pixels from the input videos.
                        You can specify the option multiple times to give each
                        input file their own individual crop parameters. If
                        more input files are given than --crop instances, the
                        last given set of crop values will be used for all
                        remaining input files. BEWARE: This uses an ffmpeg
                        video filter, thus is incompatible with additional
                        custom video filters given using --encoder-parameters.
                        Trying to use --crop and a custom video filter at the
                        same time will cause ffmpeg to fail.
  -e STRING, --encoder-parameters STRING
                        Add custom encoder parameters to the encoding process.
                        Add all parameters as a single, quoted string. These
                        parameters will be passed directly to all ffmpeg
                        processes doing the encoding work. As an example, the
                        default value is '-pix_fmt yuv420p10le -cpu-used 4
                        -crf 15 -frame-parallel 0 -threads 1 -auto-alt-ref 1
                        -lag-in-frames 8 -enable-cdef 1 -enable-global-motion
                        1 -enable-intrabc 1', which is tuned for high quality
                        encodes of SD material, for example from DVD sources.
                        BEWARE: Due to a bug in Python argument parser
                        (https://bugs.python.org/issue9334), the parameters
                        MUST NOT begin with a dash (-) when used as --encoder-
                        parameters "<parameters>". You MUST begin the quoted
                        custom parameter string with a space character or use
                        = to specify the string, like --encoder-
                        parameters="-your-parameters-here".
  -g STRING, --global-parameters STRING
                        Add custom global parameters to all ffmpeg processes.
                        These are passed in as the first arguments to ffmpeg
                        before the input file and can be used to enable
                        hardware acceleration or similar global switches.
                        Example: '-hwaccel cuvid'. When using this to enable
                        hardware decoding, ensure that the HW decoder can
                        handle at least --max-concurrent-encodes parallel
                        decoder instances. Default is to not add parameters at
                        all, leaving everything at the default settings.
                        BEWARE: The issue described for --encoder-parameters
                        applies here, too.
  -c MAX_CONCURRENT_ENCODES, --max-concurrent-encodes MAX_CONCURRENT_ENCODES
                        Run up to this many ffmpeg instances in parallel. As
                        of writing this, libaom-av1 is bad at scaling
                        horizontally, so encode this many video scenes
                        independently and parallel to increase system load and
                        decrease encoding time. Defaults to 8
  --dump-commands {yes,no,only}
                        Dump executed ffmpeg commands in text files for later
                        examination or manual execution. The files will be
                        placed in the temporary directory. If set to 'only',
                        this program will only dump the command lines but not
                        actually execute encoding tasks. The scene detection
                        will always be executed even if set to 'only', because
                        the later steps require the data to be present.
                        Defaults to 'no'. Setting to a non-default value
                        implies setting '--keep-temp'.
  --deinterlace         Deinterlace the interlaced input video using the yadif
                        video filter.
  -L NUMBER, --limit-encodes NUMBER
                        Stop after encoding this number of scenes. Useful, if
                        you plan to split the encoding process over multiple
                        sessions. If given, this program will encode this
                        NUMBER of previously not encoded scenes. Only if all
                        scenes are finished, the final result will be
                        assembled from scenes. Default is to not limit the
                        number of encodes. For the sake of this option, the
                        two encodes needed for a Two-Pass encode count as one
                        encode towards this limit. For now, setting this
                        option implies --keep-temp.
  -v, --version         show program's version number and exit
  -V, --verbose         Increase output verbosity. Also show debug messages on
                        the standard output.
  --cutelog-integration
                        Connect to a running cutelog instance with default
                        settings to display the full program log. See
                        https://github.com/busimus/cutelog
  --ffmpeg EXECUTABLE_NAME
                        Specify the ffmpeg executable name. Can be a relative
                        or absolute path or a simple name. If given a simple
                        name, the system PATH variable will be searched.
                        Defaults to "ffmpeg"
  --ffprobe EXECUTABLE_NAME
                        Specify the ffprobe executable name. Can be a relative
                        or absolute path or a simple name. If given a simple
                        name, the system PATH variable will be searched.
                        Defaults to "ffprobe"
  --ffmpeg-base DIRECTORY
                        Specify the path to a custom ffmpeg installation. If
                        given, both --ffmpeg and --ffprobe arguments are
                        treated as a path relative to this path.

The resulting files are named like <input_file_name>.AV1.mkv and are placed
alongside the input file, or into the output directory given by --output-dir.
During the encoding process, each input file will have it’s own temporary
directory named <input_file_name_with_extension>.temp. The temporary directory
is placed according to the placement rules, preferring --temp-dir over
--output-dir over the input file’s directory. The output files will only
contain video tracks. You have to add back other tracks yourself, like audio
or subtitles, and mux them into the container of your choice. Files with
multiple video tracks are untested and probably won’t work. File names that
contain esoteric characters like newlines will probably break the ffmpeg
concat demuxer and will likely cause failures. Long arguments can be
abbreviated, as long as the abbreviation is unambiguous. Don’t use this
feature in scripts, because new argument switches might break previously valid
abbreviations. Arguments can be loaded from files using the @-Notation. Use
"@/path/to/file" to load arguments from the specified file. The file must
contain one argument per line. It may be useful to load a set of common
arguments from a file instead of typing them out on the command line, when you
can re-use the same set of arguments multiple times.

```

Important notes
---------------

Due to a bug in the Python argument parser module (https://bugs.python.org/issue9334),
The values for --global-parameters and --encoder-parameters MUST NOT begin with a dash.
For example "-pix_fmt yuv420p" is NOT ALLOWED, and will cause an error during the parsing step. This can’t be fixed from my side.
But since ffmpeg always begin with a dash, you’ll run into this issue. As a workaround, begin the custom ffmpeg parameters with a space,
for example " -pix_fmt yuv420p" (note the space between " and -) or use = to join the option and it’s value:
--encoder-parameters="-cpu-used 3" works as expected.

Cropping
++++++++

Specifying cropping parameters using --crop is incompatible with custom encoder settings that contain ffmpeg video filters "-vf some_filter=values".
Trying to use both will result in ffmpeg complaining and aborting.

Loading arguments from files
++++++++++++++++++++++++++++

Arguments for this program can be loaded from files. use "@/path/to/file" as a parameter to load the parameters in the given file.
In the argument file, write one option per line. When setting paths, for example --temp-dir or --output-dir, you do not need to put the path
in quotation marks.
For best results (and long-term readability), use long style options and connect the option and value with =, like:
--temp-dir=/path/to/temp/directory


Two-Pass mode: Technical details
++++++++++++++++++++++++++++++++

Two-Pass mode uses a simple scheduler to ensure high load throughout the encoding process, avoiding single, long running
encoding processes remaining at the end of the encoding process and artificially delaying the whole process.

This is done by doing all first pass encodes first and then use the first pass log file size as simple metric to estimate
the second-pass runtime and schedule the second passes accordingly.
The used metric assumes that there is a linear correlation between first-pass log file size and second-pass encoding time.
When the encoding tasks are sorted by the log file size and therefore by the assumed relative run time, the program will
start encoding long running scenes first. This will result in better multicore usage at the end of the processing.
It avoids starting long scenes, like the ending credits, at the end of the processs, and therefore lessens the impact of
a single, long encode delaying the whole process. With this scheduling approach, it is way more likely that the
last running encodings will be encoding short and easy scenes and therefore having less overall delay.


About
-----

Copyright (C) 2019, Thomas Hess

This program is licensed under the GNU GENERAL PUBLIC LICENSE Version 3.
See the LICENSE file for details.
