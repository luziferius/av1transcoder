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
This module defines the command line used to join all finished scenes of an input file into the final result video.
"""

from pathlib import Path

from av1transcoder.argument_parser import Namespace
from av1transcoder.input_file import InputFile
from av1transcoder.command_line import AbstractCommandLine
from av1transcoder.logger import get_logger
from av1transcoder.natsort import natural_sorted

logger = get_logger(__name__.split(".")[-1])


class ConcatFilterCommandLine(AbstractCommandLine):

    def __init__(self, arguments: Namespace, input_file: InputFile):
        super(ConcatFilterCommandLine, self).__init__(arguments, input_file)
        self._add_command_line_arguments(arguments)
        logger.info(f"Created {self.__class__.__name__} instance.")

    def _add_command_line_arguments(self, arguments: Namespace):
        self.output_file_path = self.input_file.output_dir / f"{self.input_file.input_file.stem}.AV1.mkv"

        # “The -safe 0 […] is not required if the paths are relative.” See https://trac.ffmpeg.org/wiki/Concatenate
        self.command_line += [
            "-y" if self.force_overwrite else "-n",
            "-f", "concat",
            "-safe", "0",  # Required, because the scene listing contains absolute paths. See comment above
            "-i", str(self.scene_listing),
            "-c", "copy",
            str(self.output_file_path)
        ]
        command_line_str = f"[{', '.join(self.command_line)}]"
        logger.debug(f"Constructed command line. Result: {command_line_str}")

    def run_hook(self):
        """
        The run_hook builds the file listing which is read by the concat demuxer.
        See https://trac.ffmpeg.org/wiki/Concatenate#demuxer
        """
        logger.debug("Running run_hook(), generating the file listing for the concat muxer.")
        scenes = map(Path, natural_sorted(map(str, self.completed_dir.glob("scene_*.mkv"))))
        file_listing = "\n".join(f"file '{file.resolve()}'" for file in scenes)
        self.scene_listing.write_text(file_listing, encoding="utf-8")

    @property
    def scene_listing(self) -> Path:
        return self.temp_dir/"scene_list.txt"

    def _get_command_dump_file_name(self):
        return "merge_encoded_scenes_command.txt"
