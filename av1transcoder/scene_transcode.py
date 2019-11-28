# Copyright (C) 2019 Thomas Hess <thomas.hess@udo.edu>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

"""
This module is responsible for transcoding individual scenes to AV1 videos.
It also merges encoded scenes into the final result video using the ffmpeg concat demuxer.
"""

from abc import abstractmethod
from concurrent.futures import ThreadPoolExecutor
import os
import pathlib
import shutil
import typing

from av1transcoder.argument_parser import Namespace
from av1transcoder.input_file import InputFile
from av1transcoder.logger import get_logger
from av1transcoder.scene_concat import ConcatFilterCommandLine
from av1transcoder.scene_cuts import Scene, SceneList
from av1transcoder.command_line import AbstractCommandLine

logger = get_logger(__name__.split(".")[-1])


class AbstractEncoderCommandLine(AbstractCommandLine):

    def __init__(self, arguments: Namespace, input_file: InputFile, scene: Scene):
        super(AbstractEncoderCommandLine, self).__init__(arguments, input_file)
        self.scene = scene

    def _add_command_line_arguments(self, arguments: Namespace):
        """
        Adds the common command line arguments.
        Concrete classes MUST overwrite this and SHOULD call super() first.
        """

        # Always overwrite for encoding passes (ffmpeg -y option).
        # The two-pass first pass requires -y to write to /dev/null or NUL
        # The single-pass and two-pass second pass write to self.in_progress_temp, which is always safe to write to.
        # Completed files are moved out of it on completion, so if files are present, it indicates that a previous
        # instance aborted. So it is safe to overwrite partial data. (There is a millisecond wide time frame
        # between finishing an encoding and moving the finished file. I’ll ignore that terminating this program during
        # that time frame _might_ cause overwriting and re-doing a single finished scene.)
        self.command_line.append("-y")
        self._add_common_encoder_options(arguments)

    def _add_common_encoder_options(self, arguments: Namespace):
        """
        The different encoder command lines share a common set of command line arguments in the middle of the
        command line.
        """
        self.command_line += ["-i", str(self.input_file.input_file)]
        filter_chain = self.get_filter_chain()
        if filter_chain:
            self.command_line += ["-vf", ",".join(filter_chain)]  # ffmpeg filters are chained using commas
        self.command_line += ["-ss", self._float_str(self.scene.begin_pts_time)]
        if not self.scene.is_end_scene:
            self.command_line += ["-to", self._float_str(self.scene.end_pts_time)]
        self.command_line += [
            "-an", "-sn",  # Only interested in video streams
            "-c:v", "libaom-av1",
            "-strict", "experimental",  # Currently required by ffmpeg. TODO: Remove this when AV1 is marked stable
        ]
        # Add the custom encoder parameters, filtering out empty elements
        self.command_line += [param for param in arguments.encoder_parameters.split(" ") if param]

    @property
    def two_pass_log_file_prefix(self) -> str:
        return f"scene_{self.scene.scene_number}"

    @property
    def output_scene_file_name(self):
        return f"scene_{self.scene.scene_number}.mkv"

    @abstractmethod
    def _get_command_dump_file_name(self) -> str:
        """Returns the file name used to dump the ffmpeg command."""
        pass
        
    def _move_output_files_to_completed_dir(self):
        """
        Move all files produced by ffmpeg into the completed directory. This is executed by the encoder command line
        run(), after ffmpeg finished.
        """
        encoded_scene = self.input_file.in_progress_dir / self.output_scene_file_name
        shutil.move(str(encoded_scene), str(self.input_file.completed_dir))
        logger.debug(f'Encoded scene "{encoded_scene}" finished. '
                     f'Moved to the completed directory "{self.input_file.completed_dir}"')


class AV1LibAomSinglePassEncoderCommandLine(AbstractEncoderCommandLine):
    """
    This class implements the generation of command line arguments suitable to encode a video to AV1 using a single
    pass encode.
    It uses libaom-av1 internally to encode the given video to AV1
    """
    def __init__(self, arguments: Namespace, input_file: InputFile, scene: Scene):
        super(AV1LibAomSinglePassEncoderCommandLine, self).__init__(arguments, input_file, scene)
        logger.info(
            f'Constructing command line to encode scene {self.scene.scene_number} '
            f'in input file "{input_file.input_file}" to AV1.'
        )
        self._add_command_line_arguments(arguments)
        logger.debug(f"Created {self.__class__.__name__} instance. Result command line: {str(self)}")

    def _add_command_line_arguments(self, arguments: Namespace):
        super(AV1LibAomSinglePassEncoderCommandLine, self)._add_command_line_arguments(arguments)
        # The common arguments are sufficient for single-pass encoding, just add the output file path
        self.command_line.append(str(self.input_file.in_progress_dir / self.output_scene_file_name))

    def _get_command_dump_file_name(self):
        return "single_pass_encode_commands.txt"

    def _get_output_file_path(self):
        return self.input_file.completed_dir / self.output_scene_file_name


class AV1LibAomTwoPass1EncoderCommandLine(AbstractEncoderCommandLine):
    """
    This class implements the generation of command line arguments suitable to encode a video to AV1 using a two pass
    encode. This implements the first pass.
    It uses libaom-av1 internally to encode the given video to AV1
    # TODO: The log writing probably requires changing the working directory to the temporary dir.
    # TODO: Convert all input paths to absolute paths, so that nothing depends on the CWD.
    """
    def __init__(self, arguments: Namespace, input_file: InputFile, scene: Scene):
        super(AV1LibAomTwoPass1EncoderCommandLine, self).__init__(arguments, input_file, scene)
        logger.info(
            f'Constructing command line to perform the first pass encode '
            f'of scene {scene.scene_number} in input file "{input_file.input_file}" to AV1.'
        )
        self._add_command_line_arguments(arguments)
        logger.debug(f"Created {self.__class__.__name__} instance. Result command line: {str(self)}")

    def _add_command_line_arguments(self, arguments: Namespace):
        super(AV1LibAomTwoPass1EncoderCommandLine, self)._add_command_line_arguments(arguments)
        # See Two-Pass section of https://trac.ffmpeg.org/wiki/Encode/AV1
        # Specify the muxer and pipe the output to the system null sink.
        # For the log file name, see https://ffmpeg.org/ffmpeg.html#Video-Options
        # Make sure that each scene uses a unique log file name
        self.command_line += [
            "-pass", "1",
            # TODO: Verify that this works with arbitrary paths
            "-passlogfile", str(self.input_file.in_progress_dir/self.two_pass_log_file_prefix),
            "-f", "matroska",
            os.devnull
        ]

    def _get_command_dump_file_name(self):
        return "two_pass_encode_pass_1_commands.txt"
    
    def _move_output_files_to_completed_dir(self):
        # May have produced multiple logs, if the file contains multiple video tracks.
        logs = self.input_file.in_progress_dir.glob(f"{self.two_pass_log_file_prefix}-*.log")
        log_count = 0
        for log_file in logs:
            target_file = self.input_file.completed_dir/log_file.name
            if self.force_overwrite and target_file.exists():
                logger.info(f'Log file already present: "{target_file}". Force overwriting the file.')
                target_file.unlink()
            shutil.move(str(log_file), self.input_file.completed_dir)
            log_count += 1
        logger.debug(f'Moved {log_count} log file{"s" if log_count >= 1 else ""} '
                     f'for scene {self.scene.scene_number} to the completed directory '
                     f'"{self.input_file.completed_dir}".')

    def _get_output_file_path(self):
        # Get the first log file. Don’t assume the stream id to be 0, because the video might not be the first stream.
        # Only returning one file is sufficient in this case, even if it outputs multiple logs. This will be used
        # to determine if the command line can be considered finished. (The command may output multiple logs, if
        # the input file contains multiple video streams.
        # Accessing video_streams[0] is safe, because non-video input files get filtered out beforehand
        # by av1transcoder.input_file.read_input_files().
        first_stream_id = self.input_file.video_streams[0].stream_id
        return self.input_file.completed_dir / f"{self.two_pass_log_file_prefix}-{first_stream_id}.log"


class AV1LibAomTwoPass2EncoderCommandLine(AbstractEncoderCommandLine):
    """
    This class implements the generation of command line arguments suitable to encode a video to AV1 using a two pass
    encode. This implements the second pass.
    It uses libaom-av1 internally to encode the given video to AV1
    """
    def __init__(self, arguments: Namespace, input_file: InputFile, scene: Scene):
        super(AV1LibAomTwoPass2EncoderCommandLine, self).__init__(arguments, input_file, scene)
        logger.info(
            f'Constructing command line to perform the second pass encode '
            f'of scene {scene.scene_number} in input file "{input_file.input_file}" to AV1.'
        )
        self._add_command_line_arguments(arguments)
        logger.debug(f"Created {self.__class__.__name__} instance. Result command line: {str(self)}")

    def _add_command_line_arguments(self, arguments: Namespace):
        super(AV1LibAomTwoPass2EncoderCommandLine, self)._add_command_line_arguments(arguments)

        # See Two-Pass section of https://trac.ffmpeg.org/wiki/Encode/AV1
        # For the log file name, see https://ffmpeg.org/ffmpeg.html#Video-Options
        # Make sure that each scene uses a unique log file name
        self.command_line += [
            "-pass", "2",
            # TODO: Verify that this works with arbitrary paths
            "-passlogfile", str(self.input_file.completed_dir/self.two_pass_log_file_prefix),
            str(self.input_file.in_progress_dir / self.output_scene_file_name)
        ]

    def _get_command_dump_file_name(self):
        return "two_pass_encode_pass_2_commands.txt"

    def _get_output_file_path(self):
        return self.input_file.completed_dir / self.output_scene_file_name


CliType = typing.TypeVar(
    "T",
    AV1LibAomTwoPass1EncoderCommandLine,
    AV1LibAomTwoPass2EncoderCommandLine,
    AV1LibAomSinglePassEncoderCommandLine
)


def transcode_input_file(arguments: Namespace, input_file: InputFile, scenes: SceneList):
    """Transcode a single input file to AV1."""

    with ThreadPoolExecutor(
            max_workers=arguments.max_concurrent_encodes,
            thread_name_prefix="ffmpeg_worker") as executor:
        if arguments.enable_single_pass_encode:
            _transcode_single_pass(arguments, input_file, scenes, executor)
        else:
            _transcode_two_pass(arguments, input_file, scenes, executor)

    if input_file.all_scenes_completed:
        ConcatFilterCommandLine(arguments, input_file).run()
        _cleanup(arguments, input_file)
    else:
        logger.info(
            f'Not all encoded scenes available for input file "{input_file.input_file}". Skipping building '
            f'output video. Not deleting temporary files.'
        )


def _transcode_single_pass(arguments: Namespace, input_file: InputFile, scenes: SceneList, executor: ThreadPoolExecutor):
    """Transcode a given input file using Single-Pass encoding."""
    all_command_lines = _create_single_pass_command_lines(arguments, input_file, scenes)
    to_run_command_lines = _limit_and_filter_commands(arguments, all_command_lines)
    logger.info(f'About to start {len(to_run_command_lines)} scene encodes for input file "{input_file.input_file}".')
    if arguments.limit_encodes:
        # Both 0 and None evaluate to False, thus the division is safe.
        logger.info(f"This will use {round(len(to_run_command_lines)/arguments.limit_encodes*100)}% "
                    f"of the remaining encoder contingent.")
    run_methods = (cli.run for cli in to_run_command_lines)
    runs = executor.map((lambda run: run()), run_methods)
    # Filter out unsuccessful runs
    finished_command_lines = (
        command
        for command, _ in zip(to_run_command_lines, runs)  # Ignore the None returned by run()
        if command.finished)
    # tuple() drives the map execution
    successful_encodes = len(tuple(finished_command_lines))
    if arguments.limit_encodes is not None:
        # Subtract the number of successful encodes from the encode count contingent.
        # This alters the global state so that the next input file, if any, has this many less encodes available.
        arguments.limit_encodes -= successful_encodes


def _create_single_pass_command_lines(arguments: Namespace, input_file: InputFile, scenes: SceneList)\
        -> typing.List[AV1LibAomSinglePassEncoderCommandLine]:
    """Returns all AV1LibAomSinglePassEncoderCommandLine that need to run to finish the encode of all scenes."""
    logger.debug(f'Creating all single pass command lines for input file "{input_file.input_file}".')
    command_lines = [AV1LibAomSinglePassEncoderCommandLine(arguments, input_file, scene) for scene in scenes]

    if not command_lines:
        logger.warning(f'No command lines created for input file "{input_file.input_file}". Skipping.')

    return command_lines


def _transcode_two_pass(arguments: Namespace, input_file: InputFile, scenes: SceneList, executor: ThreadPoolExecutor):
    """Transcode a given input file using Two-Pass encoding."""

    all_command_lines_pass_1 = _create_two_pass_1_command_lines(arguments, input_file, scenes)
    to_run_pass_1 = _limit_and_filter_commands(arguments, all_command_lines_pass_1)

    logger.info(
        f'About to start {len(to_run_pass_1)} first pass scene encodes for input file "{input_file.input_file}".'
    )
    _transcode_two_pass_1(to_run_pass_1, executor)

    # Re-check after the first pass run. Only scenes with finished first passes can be encoded.
    # It does not matter if the first pass completed just now or was already finished beforehand,
    # so just collect everything that is finished. If --dump-commands is "only", finished is probably False,
    # because no encoding work was actually done. So use the second check to simulate finished encodes to force
    # generating second passes. Because the second passes won’t actually run ffmpeg,
    # it is safe to simply keep all command lines.
    finished_pass_1 = _sorted_first_passes(
        (first_pass for first_pass in all_command_lines_pass_1 if first_pass.finished or first_pass.dump_mode == "only")
    )

    all_command_lines_pass_2 = _create_two_pass_2_command_lines(arguments, finished_pass_1)
    # Limit the number of second pass encodes, if enabled. It is not guaranteed that all ready to run second passes
    # fit within the limit, for example if many finished first passes from previous runs are found.
    to_run_pass_2 = _limit_and_filter_commands(arguments, all_command_lines_pass_2)

    logger.debug(f"Sorted the second passes in descending order, based on the time consumption heuristic.")
    logger.info(
        f'About to start {len(to_run_pass_2)} second pass scene encodes for input file "{input_file.input_file}".'
    )
    if arguments.limit_encodes:
        # arguments.limit_encodes can’t be 0 here, because no InputFiles will be processed in this case.
        # If it is 0, the outer main loop terminates and won’t call transcode_input_file(). Thus the division is safe.
        logger.info(f"This will use {round(len(to_run_pass_2)/arguments.limit_encodes*100)}% "
                    f"of the remaining encoder contingent.")
    _transcode_two_pass_2(arguments, to_run_pass_2, executor)


def _limit_and_filter_commands(arguments: Namespace, command_lines: typing.List[CliType]) -> typing.List[CliType]:
    if not arguments.force_overwrite:
        # If force overwrite is disabled, remove finished commands. If enabled, skip this and just keep all.
        command_lines = [command for command in command_lines if not (command.finished or command.dump_mode == "only")]
    if arguments.limit_encodes is not None:
        # Limit encode count to the available contingent
        return command_lines[:arguments.limit_encodes]
    else:
        return command_lines


def _transcode_two_pass_1(first_passes: typing.List[AV1LibAomTwoPass1EncoderCommandLine], executor: ThreadPoolExecutor):
    run_methods = (cli.run for cli in first_passes)
    runs = (executor.map((lambda run: run()), run_methods))
    finished_pass1_command_lines = (
        cli
        for cli, _ in zip(first_passes, runs)  # Ignore the None returned by run()
        if cli.finished
    )
    # The call to tuple() drives the map operation.
    successful_encodes = len(tuple(finished_pass1_command_lines))
    logger.info(f"Total of {successful_encodes} first passes finished successfully.")


def _transcode_two_pass_2(
        arguments: Namespace,
        pass2_command_lines: typing.List[AV1LibAomTwoPass2EncoderCommandLine],
        executor: ThreadPoolExecutor):
    run_methods = (cli.run for cli in pass2_command_lines)
    runs = executor.map((lambda run: run()), run_methods)
    finished_pass2_command_lines = (
        cli
        for cli, _ in zip(pass2_command_lines, runs)  # Ignore the None returned by run()
        if cli.finished
    )
    successful_encodes = len(tuple(finished_pass2_command_lines))
    logger.info(f"Total of {successful_encodes} second passes finished successfully.")
    if arguments.limit_encodes is not None:
        # Subtract the number of successful encodes from the encode count contingent.
        # This alters the global state so that the next input file, if any, has this many less encodes available.
        arguments.limit_encodes -= successful_encodes


def _create_two_pass_1_command_lines(arguments: Namespace, input_file: InputFile, scenes: SceneList)\
        -> typing.List[AV1LibAomTwoPass1EncoderCommandLine]:
    logger.debug(f'Creating all two-pass command lines for pass 1 of input file "{input_file.input_file}".')
    command_lines = [AV1LibAomTwoPass1EncoderCommandLine(arguments, input_file, scene) for scene in scenes]

    if not command_lines:
        logger.warning(f'No command lines created for input file "{input_file.input_file}". Skipping.')

    return command_lines


def _create_two_pass_2_command_lines(
        arguments: Namespace, first_passes: typing.Iterable[AV1LibAomTwoPass1EncoderCommandLine]):
    command_lines = [AV1LibAomTwoPass2EncoderCommandLine(arguments, p1.input_file, p1.scene) for p1 in first_passes]
    if command_lines:
        logger.debug(
            f'Creating all two-pass command lines for pass 2 of input file "{command_lines[0].input_file.input_file}".'
        )
    else:
        logger.warning("No pass 2 command lines built!")
    if not arguments.force_overwrite:
        # If force overwrite is disabled, remove finished. If enabled, skip this and just keep all.
        command_lines = [second_pass for second_pass in command_lines if not second_pass.finished]
    return command_lines


def _cleanup(arguments: Namespace, input_file: InputFile):
    if not arguments.keep_temp:
        logger.info(f'Removing temporary files: "{input_file.temp_dir}"')
        input_file.temp_dir.unlink()


def _sorted_first_passes(passes):

    def key(pass1: AV1LibAomTwoPass1EncoderCommandLine):
        all_logs = pass1.input_file.completed_dir.glob(f"{pass1.two_pass_log_file_prefix}*.log")
        first_log: pathlib.Path = sorted(all_logs)[0]
        size_bytes = first_log.stat().st_size
        return size_bytes

    return sorted(passes, key=key, reverse=True)
