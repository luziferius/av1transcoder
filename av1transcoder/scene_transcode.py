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
import itertools
from concurrent.futures import ThreadPoolExecutor
import os
import pathlib
import shutil

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

    @abstractmethod
    def _add_command_line_arguments(self, arguments: Namespace):
        pass

    def _add_common_encoder_options(self, arguments: Namespace):
        """
        The different encoder command lines share a common set of command line arguments in the middle of the
        command line.
        """
        self.command_line += ["-i", str(self.input_file.input_file)]
        if arguments.deinterlace:
            self.command_line += ["-vf", "yadif"]
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
    def two_pass_log_file_prefix(self) -> pathlib.Path:
        return self.completed_dir/f"scene_{self.scene.scene_number}"


class AV1LibAomSinglePassEncoderCommandLine(AbstractEncoderCommandLine):
    """
    This class implements the generation of command line arguments suitable to encode a video to AV1 using a single
    pass encode.
    It uses libaom-av1 internally to encode the given video to AV1
    """
    def __init__(self, arguments: Namespace, input_file: InputFile, scene: Scene):
        super(AV1LibAomSinglePassEncoderCommandLine, self).__init__(arguments, input_file, scene)
        logger.info(f'Constructing command line to encode scenes in input file "{input_file.input_file}" to AV1.')
        self._add_command_line_arguments(arguments)
        logger.info(f"Created {self.__class__.__name__} instance.")

    def _add_command_line_arguments(self, arguments: Namespace):
        self.command_line += [
            "-y" if self.force_overwrite else "-n",
        ]
        self._add_common_encoder_options(arguments)
        # Now add the output file

        scene_name = f"scene_{self.scene.scene_number}.mkv"
        self.command_line.append(str(self.in_progress_dir / scene_name))

    def _get_command_dump_file_name(self):
        return "single_pass_encode_commands.txt"


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
        logger.info(f'Constructing command line to perform the first pass encode '
                    f'of scene {scene.scene_number} in input file "{input_file.input_file}" to AV1.')
        self._add_command_line_arguments(arguments)
        logger.info(f"Created {self.__class__.__name__} instance.")

    def _add_command_line_arguments(self, arguments: Namespace):
        self.command_line += [
            "-y",  # Always yes to overwrite /dev/null or NUL
        ]
        self._add_common_encoder_options(arguments)
        # See Two-Pass section of https://trac.ffmpeg.org/wiki/Encode/AV1
        # Specify the muxer and pipe the output to the system null sink.
        # For the log file name, see https://ffmpeg.org/ffmpeg.html#Video-Options
        # Make sure that each scene uses a unique log file name
        self.command_line += [
            "-pass", "1",
            # TODO: Verify that this works with arbitrary paths
            "-passlogfile", str(self.two_pass_log_file_prefix),
            "-f", "matroska",
            os.devnull
        ]
        command_line_str = f"[{', '.join(self.command_line)}]"
        logger.debug(f"Constructed command line. Result: {command_line_str}")

    def _get_command_dump_file_name(self):
        return "two_pass_encode_pass_1_commands.txt"


class AV1LibAomTwoPass2EncoderCommandLine(AbstractEncoderCommandLine):
    """
    This class implements the generation of command line arguments suitable to encode a video to AV1 using a two pass
    encode. This implements the second pass.
    It uses libaom-av1 internally to encode the given video to AV1
    """
    def __init__(self, arguments: Namespace, input_file: InputFile, scene: Scene):
        super(AV1LibAomTwoPass2EncoderCommandLine, self).__init__(arguments, input_file, scene)
        logger.info(f'Constructing command line to perform the second pass encode '
                    f'of scene {scene.scene_number} in input file "{input_file.input_file}" to AV1.')
        self._add_command_line_arguments(arguments)
        logger.info(f"Created {self.__class__.__name__} instance.")

    def _add_command_line_arguments(self, arguments: Namespace):
        self.command_line += [
            "-y" if self.force_overwrite else "-n",
        ]
        self._add_common_encoder_options(arguments)

        scene_name = f"scene_{self.scene.scene_number}.mkv"
        # See Two-Pass section of https://trac.ffmpeg.org/wiki/Encode/AV1
        # For the log file name, see https://ffmpeg.org/ffmpeg.html#Video-Options
        # Make sure that each scene uses a unique log file name
        self.command_line += [
            "-pass", "2",
            # TODO: Verify that this works with arbitrary paths
            "-passlogfile", str(self.two_pass_log_file_prefix),
            str(self.in_progress_dir / scene_name)
        ]
        command_line_str = f"[{', '.join(self.command_line)}]"
        logger.debug(f"Constructed command line. Result: {command_line_str}")

    def _get_command_dump_file_name(self):
        return "two_pass_encode_pass_2_commands.txt"


def transcode_input_file(arguments: Namespace, input_file: InputFile, scenes: SceneList):
    """Transcode a single input file to AV1."""
    transcode_function = _transcode_single_pass if arguments.enable_single_pass_encode else _transcode_two_pass

    with ThreadPoolExecutor(
            max_workers=arguments.max_concurrent_encodes, thread_name_prefix="ffmpeg_worker") as executor:
        # Use tuple to drive the map operation
        tuple(executor.map(transcode_function, itertools.repeat(arguments), itertools.repeat(input_file), scenes))

    concat_filter = ConcatFilterCommandLine(arguments, input_file)
    if concat_filter.handle_directory_creation():
        concat_filter.run()

    _cleanup(arguments, input_file)


def _transcode_single_pass(arguments: Namespace, input_file: InputFile, scene: Scene):
    logger.info(f'Transcoding "{input_file.input_file}" using Single-Pass encoding…')
    cli = AV1LibAomSinglePassEncoderCommandLine(arguments, input_file, scene)
    # Skip encoding, if the scene is already finished.
    if (cli.completed_dir / f"scene_{cli.scene.scene_number}.mkv").exists():
        logger.info(f"Scene number {cli.scene.scene_number} already finished. Skipping.")
        return
    if cli.handle_directory_creation():
        logger.debug(f'Starting encoding process for file "{input_file.input_file}".')
        cli.run()
    _move_scene_to_finished_directory(cli)


def _transcode_two_pass(arguments: Namespace, input_file: InputFile, scene: Scene):
    logger.info(f'Transcoding "{input_file.input_file}" using Two-Pass encoding…')
    pass1 = AV1LibAomTwoPass1EncoderCommandLine(arguments, input_file, scene)
    # Skip encoding, if the scene is already finished.
    if (pass1.completed_dir / f"scene_{pass1.scene.scene_number}.mkv").exists():
        logger.info(f"Scene number {pass1.scene.scene_number} already finished. Skipping.")
        return
    if pass1.handle_directory_creation():
        logger.debug(f'Starting first pass for file "{input_file.input_file}".')
        pass1.run()
        _move_first_pass_log_to_finished_directory(pass1)
    pass2 = AV1LibAomTwoPass2EncoderCommandLine(arguments, input_file, scene)
    if pass2.handle_directory_creation() and pass1.finished:
        logger.debug(f'Starting second pass for file "{input_file.input_file}".')
        pass2.run()
        _move_scene_to_finished_directory(pass2)


def _move_scene_to_finished_directory(cli: AbstractEncoderCommandLine):
    if cli.finished and cli.dump_mode != "only":
        encoded_scene = cli.in_progress_dir / f"scene_{cli.scene.scene_number}.mkv"
        shutil.move(str(encoded_scene), str(cli.completed_dir))
        logger.debug(f'Encoded scene "{encoded_scene}" finished. '
                     f'Moved to the completed directory "{cli.completed_dir}"')


def _move_first_pass_log_to_finished_directory(cli: AV1LibAomTwoPass1EncoderCommandLine):
    if cli.finished and cli.dump_mode != "only":
        # May have produced multiple logs, if the file contains multiple video tracks.
        logs = cli.in_progress_dir.glob(f"{cli.two_pass_log_file_prefix.name}*.log")

        for log_file in logs:
            shutil.move(str(log_file), cli.completed_dir)
        logger.debug(f'Moved {len(logs)} log file{"s" if len(logs) >= 1 else ""} '
                     f'for scene {cli.scene.scene_number} to the completed directory "{cli.completed_dir}".')


def _cleanup(arguments: Namespace, input_file: InputFile):
    if not arguments.keep_temp:
        logger.info(f'Removing temporary files: "{input_file.temp_dir}"')
        input_file.temp_dir.unlink()
