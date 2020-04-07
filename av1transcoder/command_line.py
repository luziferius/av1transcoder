# Copyright (C) 2018, 2019 Thomas Hess <thomas.hess@udo.edu>

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

from abc import abstractmethod
from pathlib import Path
import shlex
import subprocess
import sys
from typing import List

from av1transcoder.argument_parser import Namespace
from av1transcoder.logger import get_logger
from av1transcoder.input_file import InputFile

logger = get_logger(__name__.split(".")[-1])


class AbstractCommandLine:
    """Models a command line. This class can be used to construct ffmpeg command lines and later execute them."""

    def __init__(self, arguments: Namespace, input_file: InputFile):
        self.input_file: InputFile = input_file
        self.ffmpeg: str = self.input_file.ffmpeg_progs.ffmpeg
        self.force_overwrite: bool = arguments.force_overwrite
        self.deinterlace = arguments.deinterlace
        self.dump_mode: str = arguments.dump_commands
        self.command_line: List[str] = [
            self.ffmpeg,
            "-hide_banner",
        ]
        # Add the global parameters, filter out empty elements
        self.command_line += [param for param in arguments.global_parameters.split(" ") if param]

    def _get_crop_filter(self) -> str:
        """
        Returns the ffmpeg crop filter using the input_file’s crop parameters.
        Returns an empty string, if the crop parameters are None.
        """
        crop = self.input_file.crop_values
        if crop is None:
            return ""
        else:
            crop_filter = f"crop=w=in_w-{crop.crop_width}:h=in_h-{crop.crop_height}:x={crop.left}:y={crop.top}"
            return crop_filter

    @abstractmethod
    def _add_command_line_arguments(self, arguments: Namespace):
        pass

    @abstractmethod
    def _get_command_dump_file_name(self) -> str:
        """Returns the file name used to dump the ffmpeg command."""
        pass

    @abstractmethod
    def _get_output_file_path(self) -> Path:
        """Returns the output file path. If this file is present, the command line is considered finished."""
        pass

    @abstractmethod
    def _move_output_files_to_completed_dir(self):
        pass

    @property
    def finished(self):
        return self._get_output_file_path().exists()

    def get_filter_chain(self):
        """Returns the filter chain for this encode"""
        chain = []
        if self.deinterlace:
            chain.append("yadif")
        crop_filter = self._get_crop_filter()
        if crop_filter:
            chain.append(crop_filter)
        return chain

    def run(self):
        """
        Does nothing, if the "finished" property is True and --force-overwrite is not given. Otherwise:
        Executes the custom run_hook(), if any. Then executes ffmpeg using the constructed command line.
        Then moves the output file(s) to the output directory, if ffmpeg finished successfully.
        Executed command lines will be dumped to files if --dump-commands is not "no". Execution will be skipped,
        if dump-commands is "only" and force_execution is False (the default).
        """
        if self.finished and not self.force_overwrite:
            logger.info(f"Command line already finished and --force-overwrite not given. Skipping: {self.command_line}")
            return
        else:
            logger.info(f"Running command line: {self.command_line}")
        self.run_hook()
        if self.dump_mode != "no":
            # Make sure to quote the lines, so that the dumped commands can be safely copy&pasted into the terminal.
            # Implementation note: shlex.join() requires Python 3.8. For now, quote each token individually.
            # TODO: Replace, once Python > 3.8 is widespread enough
            cli = ' '.join(map(shlex.quote, self.command_line)) + "\n"
            # Append to the dump file, to accumulate all command lines for the respective step.
            dump_file_path = self.input_file.temp_dir/self._get_command_dump_file_name()
            with dump_file_path.open("a", encoding="utf-8") as dump_file:
                dump_file.write(cli)

        # Run command lines only if dump mode is "yes" or "no".
        if self.dump_mode != "only":
            completed = subprocess.run(self.command_line, executable=self.ffmpeg)
            if completed.returncode:
                warn_msg = f'ffmpeg command exited with non-zero return value indicating failure. ' \
                           f'Failing input file: "{self.input_file.input_file}".'
                logger.warning(warn_msg)
                print(warn_msg, file=sys.stderr)

            else:
                logger.debug(f'ffmpeg executed successfully. Command line finished: {self.command_line}')
                self._move_output_files_to_completed_dir()

    def run_hook(self):
        """
        Is executed right before ffmpeg. Does nothing by default and is meant to be overwritten when additional
        processing is needed before running ffmpeg.
        """
        pass

    @staticmethod
    def _float_str(number: float) -> str:
        """
        Return a string representation for the given floating point number. Avoiding scientific notation, because this
        is unsupported by ffmpeg. Internally using 9 decimal digit precision, which translates to nanosecond precision if
        used for time.
        Try hard to not confuse ffmpeg, by removing all trailing zeros and, in case the number is an integer, the trailing
        decimal point.
        """
        return f"{number:0.9f}".rstrip("0").rstrip(".")

    def __str__(self) -> str:
        return str(self.command_line)
