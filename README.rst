av1transcoder
=============

Encode AV1 videos with ffmpeg and libaom-av1.

This tool takes input videos and encodes them to AV1, using the libaom-av1 encoder.
At the time of writing this, the encoder is still very slow and can’t fully utilize modern multicore CPUs.
To mitigate this issue, this program splits the input at scene cuts and encodes multiple scenes in parallel,
allowing full CPU utilization and therefore faster encode times.

How is this done?
-----------------

This program takes multiple passes over the input file:

First, it uses the ffmpeg scenecut filter to determine the scene cuts.
This is done to avoid splitting the video in the middle of a scene,
because such a split causes an artificial and unneccessary bitrate spike.
It then filters out short scenes, so that the overall scene length falls between some acceptable lower and upper bound.
(Beware: Upper bounds are currently not implemented!)
It will then start an ffmpeg instance for each found scene, encoding the scenes independently in parallel.
Only a limited, configurable number of instances will run at any time to not overload the system.
Each running encoding will be performed outputting into a temporary directory,
and moved into a central scene repository directory on completion.
This ensures that only completed scene encodes are kept, making the process fully stoppable and resumable at any time.
Incomplete and aborted or otherwise failing encodes will be thrown away.

On resume, the program picks up any finished work, like finished
scenes in the scene repository and skips them, avoiding duplicate work.

When all scenes are encoded, the ffmpeg concat demuxer is used to join all scenes into a single video file.


Two-Pass mode: Technical details
++++++++++++++++++++++++++++++++

Two-Pass mode uses a simple scheduler to ensure high load throughout the encoding process, avoiding single, long running
encoding processes remaining at the end of the encodin process and artificially delaying the whole process.

This is done by doing all first pass encodes first and then use the first pass log file size as simple metric to estimate
the second-pass runtime and schedule the second passes accordingly.
The used metric assumes there is a linear correlation between first-pass log file size and second-pass encoding time.
When the encoding tasks are sorted by the log file size and therefore by the assumed relative run time, the program will
start encoding long running scenes first. This will result in better multicore usage at the end of the processing.
It avoids starting long scenes, like the ending credits, at the end of the processs, and therefore lessens the impact of
a single, long encode delaying the whole process. With this scheduling approach, it is way more likely that the
last running encodings will be encoding short and easy scenes and therefore having less overall delay.

Requirements
------------

- Python >= 3.7 (3.6 may work, but is untested)
- recent ffmpeg with enabled and recent libaom-av1 (git master builds from around November 2019 for both ffmpeg and libaom-av1 work)

Install
-------

Install latest version: **pip install .**.


Usage
-----

Execute *av1transcoder* after installation or run *av1transcoder-runner.py* from the source tree.
The program expects one or more video files as positional arguments. Each given video file will be transcoded to AV1.
The encoding process can be controlled using several optional command line switches.
Use the --help switch to view all possible parameters with explanations.

Important
+++++++++
Due to a bug in the Python argument parser module (https://bugs.python.org/issue9334),
The values for --global-parameters and --encoder-parameters MUST NOT begin with a dash.
For example "-pix_fmt yuv420p" is NOT ALLOWED, and will cause an error during the parsing step. This can’t be fixed from my side.
But since ffmpeg always begin with a dash, you’ll run into this issue. As a workaround, begin the custom ffmpeg parameters with a space,
for example " -pix_fmt yuv420p" (note the space between " and -).


About
-----

Copyright (C) 2019, Thomas Hess

This program is licensed under the GNU GENERAL PUBLIC LICENSE Version 3.
See the LICENSE file for details.
