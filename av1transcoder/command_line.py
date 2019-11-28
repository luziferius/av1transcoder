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
from av1transcoder.ffmpeg_handler import find_ffmpeg

logger = get_logger(__name__.split(".")[-1])


class AbstractCommandLine:
    """Models a command line. This class can be used to construct ffmpeg command lines and later execute them."""

    IN_PROGRESS_TEMP_DIR_NAME = "in_progress_scenes"
    COMPLETED_TEMP_DIR_NAME = "completed_scenes"

    def __init__(self, arguments: Namespace, input_file: InputFile):
        self.finished = False
        self.input_file: InputFile = input_file
        self.ffmpeg: str = find_ffmpeg(arguments).ffmpeg
        self.force_overwrite: bool = arguments.force_overwrite
        self.temp_dir = self.input_file.temp_dir
        self.in_progress_dir = self.temp_dir / self.IN_PROGRESS_TEMP_DIR_NAME
        self.completed_dir = self.temp_dir / self.COMPLETED_TEMP_DIR_NAME
        self.dump_mode = arguments.dump_commands
        self.command_line: List[str] = [
            self.ffmpeg,
            "-hide_banner",
        ]
        # Add the global parameters, filter out empty elements
        self.command_line += [param for param in arguments.global_parameters.split(" ") if param]

    @abstractmethod
    def _add_command_line_arguments(self, arguments: Namespace):
        pass

    @abstractmethod
    def _get_command_dump_file_name(self) -> str:
        """Returns the file name used to dump the ffmpeg command."""
        pass

    def run(self):
        """
        Run ffmpeg using the constructed command line. Should only be executed if handle_directory_creation()
        returned True.
        Encapsulate the call to aid testing.
        """
        logger.info(f"Running command line: {self.command_line}")
        self.run_hook()
        if self.dump_mode != "no":
            # shlex.join() requires Python 3.8. TODO: Replace, once Python > 3.8 is widespread enough
            # For now, quote each token individually
            cli = ' '.join(map(shlex.quote, self.command_line)) + "\n"
            # Append to the dump file,  to accumulate all command lines for the respective step.
            with open(self.temp_dir/self._get_command_dump_file_name(), "a", encoding="utf-8") as dump_file:
                dump_file.write(cli)

        if self.dump_mode != "only":
            completed = subprocess.run(self.command_line, executable=self.ffmpeg)
            if completed.returncode:
                warn_msg = f'ffmpeg command exited with non-zero return value indicating failure. ' \
                           f'Failing input file: "{self.input_file.input_file}".'
                logger.warning(warn_msg)
                print(warn_msg, file=sys.stderr)
            else:
                self.finished = True
        else:
            # When only dumping, assume and pretend to have finished without issues.
            self.finished = True

    def run_hook(self):
        """
        Is executed right before ffmpeg. Does nothing by default and is meant to be overwritten when additional
        processing is needed before running ffmpeg.
        """
        pass

    def handle_directory_creation(self) -> bool:
        """
        Handles the creation of the temporary directory and the output directory.
        Has to be called before run().
        """
        # Uses lazy evaluation to shortcut later calls if any fails
        return self._handle_output_directory() and self._handle_temp_directory() and self._handle_temp_subdirectories()

    def _handle_temp_directory(self) -> bool:
        logger.info(f'Handling temporary directory creation for input file "{self.input_file.input_file}": '
                    f'{self.temp_dir}')
        if self.temp_dir == self.input_file.output_dir:
            # Shortcutting here, as it will be handled by self._handle_output_directory()
            return True
        if not self.temp_dir.exists():
            logger.debug("Temporary directory does not exist. Traversing path to the root.")
            parent = self._traverse_path_until_existing(self.temp_dir)
            if not parent.is_dir():
                # The lowest existing ancestor is a file. Try to replace it, if the user allowed this.
                logger.debug("This ancestor is a file. Try to replace with a directory, if allowed by the user.")
                if not self._replace_file_with_directory(parent):
                    return False
            return self._create_directory("temporary", self.temp_dir)
        elif self.temp_dir.is_dir():
            logger.debug(f"Temporary directory exists, doing nothing: {self.temp_dir}")
            return True
        else:
            # Output directory exists, but is a file.
            logger.debug("The temporary data path exists and is a file. "
                         "Try to replace it with a directory, if allowed by the user.")
            return self._replace_file_with_directory(self.temp_dir)

    def _handle_temp_subdirectories(self) -> bool:
        try:
            mode = self.temp_dir.stat().st_mode
            self.completed_dir.mkdir(mode, exist_ok=True)
            self.in_progress_dir.mkdir(mode, exist_ok=True)
        except (OSError, FileNotFoundError):
            return False
        else:
            return True

    def _handle_output_directory(self) -> bool:
        output_dir: Path = self.input_file.output_dir
        logger.info(f'Handling output directory creation for input file "{self.input_file.input_file}": {output_dir}')
        if not output_dir.exists():
            logger.debug("Output directory does not exist. Traversing path to the root.")
            # The path ends somewhere above the output directory. Go up the path and check the parents.
            parent = self._traverse_path_until_existing(output_dir)
            if not parent.is_dir():
                # The lowest existing ancestor is a file. Try to replace it, if the user allowed this.
                logger.debug("The existing ancestor is a file. "
                             "Try to replace with a directory, if allowed by the user.")
                if not self._replace_file_with_directory(parent):
                    return False
            return self._create_directory("output", output_dir)
        elif output_dir.is_dir():
            logger.debug(f"Output directory exists, doing nothing: {output_dir}")
            return True
        else:
            # Output directory exists, but is a file.
            logger.debug("The output path exists and is a file. "
                         "Try to replace it with a directory, if allowed by the user.")
            return self._replace_file_with_directory(output_dir)

    @staticmethod
    def _traverse_path_until_existing(path: Path) -> Path:
        while not path.exists():
            path = path.parent
        logger.debug(f"Found lowest existing ancestor of requested path: {path}")
        return path

    @staticmethod
    def _create_directory(name_for_logging: str, dir_path: Path) -> bool:
        try:
            parent_mode = AbstractCommandLine._traverse_path_until_existing(dir_path).stat().st_mode
            dir_path.mkdir(parent_mode, parents=True)
        except OSError:
            return False
        else:
            logger.info(f"Created requested {name_for_logging} directory: {dir_path}")
            return True

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

    def _replace_file_with_directory(self, directory: Path) -> bool:
        if self.force_overwrite:
            logger.warning(
                f'Replacing existing file "{directory}" with a directory, '
                f'because --force-overwrite was given.'
            )
            try:
                _replace_path(directory)
            except OSError:
                return False
            else:
                return True
        else:
            return False

    def __str__(self) -> str:
        return str(self.command_line)


def _replace_path(output_dir: Path):
    """
    Encapsulate filesystem interaction: replace output_dir with a directory.
    """
    output_dir.unlink()
    output_dir.mkdir(output_dir.parent.stat().st_mode)
