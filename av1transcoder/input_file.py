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

from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as Et

from typing import List, Dict, Optional, Tuple, Set

from av1transcoder.argument_parser import Namespace, CropValues
from av1transcoder.logger import get_logger
from av1transcoder.ffmpeg_handler import find_ffmpeg

stream_number_re = re.compile(r"scene_(?P<number>[0]|[1-9][0-9]*)\.mkv")
logger = get_logger(__name__.split(".")[-1])


class Chapter:
    def __init__(self, chapter: Et.Element, number: int):
        """
        Create a new chapter object for the given chapter XML element and chapter number
        :param chapter: The XML element containing chapter information for a single chapter.
        :param number: Integer chapter number. Must be given from outside, because the XML does not include a number
        """
        # Dictionary containing the mapping language-code to chapter name
        self.names: Dict[str, str] = {}
        self.start_time: float = float(chapter.get("start_time"))
        self.end_time: float = float(chapter.get("end_time"))
        self.number: int = number

        self._validate_chapter_times()

        title_tags = chapter.findall("""./tag[@key="title"]""")
        if len(title_tags) > 1:
            import warnings
            warn_msg = \
                f"Found more than one title tag for chapter {number}. " \
                f"This means that ffprobe implemented reading titles for multiple languages. " \
                f"All titles except the last will be lost. Please fill a bug report to get full support for " \
                f"multiple titles."
            logger.warning(warn_msg)
            warnings.warn(warn_msg)
        for tag in title_tags:  # type: Et.Element
            # TODO: Get the proper language if ffprobe supports localized chapter names.
            #       Currently, ffprobe just returns one name without a language indicator, so use "undetermined"
            self.names["und"] = tag.get("value", "")
            logger.debug(f"Found chapter title: {self.names['und']}")

    def _validate_chapter_times(self):
        if self.end_time < self.start_time:
            err_msg = f"Chapter has negative duration, begin: {self.start_time}, end: {self.end_time}"
            logger.error(err_msg)
            raise ValueError(err_msg)

    def __str__(self):
        return f"<Chapter: start: {self.start_time}, end: {self.end_time}, names: {self.names}>"


class Stream:
    def __init__(self, stream: Et.Element):
        """
        Models the relevant metadata of a media stream inside a multimedia file. Only used for video streams.
        :param stream: The XML element containing stream information for a single stream.
        """
        # The integer stream id. Used to identify and select a stream by ffprobe and ffmpeg.
        # Don’t care what exactly this is, as long as it is an integer and ffprobe and ffmpeg are consistent on this.
        self.stream_id: int = int(stream.get("index"))
        # The start time. Some streams have a (positive or negative) offset relative to the file start.
        self.start_time: float = float(stream.get("start_time"))
        # The stream duration in seconds. May be None, if not present in the gathered data.
        self.duration = self._extract_stream_duration(stream)
        # The format name.
        self.format_name: str = stream.get("codec_name")
        # Used to calculate PTS values

        self.codec_time_base: float = self._parse_time_base(stream.get("codec_time_base"))
        self.time_base: float = self._parse_time_base(stream.get("time_base"))
        # Contains a mapping from language code to stream name.
        # TODO: currently, the stream name is not read. Include this when supported by ffprobe.
        self.names: Dict[str, str] = {}

    @staticmethod
    def _parse_time_base(node_content: str) -> float:
        first, second = map(int, node_content.split("/"))
        return first / second

    def _extract_stream_duration(self, video_stream: Et.Element):
        logger.debug(f"Extracting stream duration for stream {video_stream.get('index')}")
        extraction_functions = (
            self._extract_stream_duration_using_duration_attribute,
            self._extract_stream_duration_using_duration_tag
        )
        duration_seconds = None
        for extraction_function in extraction_functions:
            duration_seconds = extraction_function(video_stream)
            if duration_seconds is not None:
                break
        else:
            logger.info("Stream duration not found.")

        return duration_seconds

    @staticmethod
    def _extract_stream_duration_using_duration_attribute(video_stream: Et.Element) -> Optional[float]:
        """
        Use a duration attribute to determine the stream duration.
        In some files (vorbis container?), the ffprobe output for a stream has a duration attribute.
        Returns this attribute value as a single float, if present, None otherwise
        :param video_stream:
        :return: float-converted 'duration' attribute value, if present, None otherwise
        """
        element_name = "duration"
        duration_seconds = video_stream.get(element_name)
        if duration_seconds is None:
            logger.debug(f"Failure: Stream has no attribute {element_name}, not using this attribute")
        else:
            duration_seconds = float(duration_seconds)
            logger.debug(f"Success: Found duration using attribute {element_name}: {duration_seconds}")
        return duration_seconds

    @staticmethod
    def _extract_stream_duration_using_duration_tag(video_stream: Et.Element) -> Optional[float]:
        """
        Use a DURATION tag to determine the stream duration.
        (Newer versions of?) mkvmerge add a DURATION tag to (some?) streams.
        Return this tag value as a single float, if present, None otherwise
        :param video_stream:
        :return: float-converted 'DURATION' tag value, if present, None otherwise
        """
        element_name = "DURATION"
        element: Et.Element = video_stream.find(f'./tag[@key="{element_name}"]')
        if element is not None:
            # Example return value for get: "00:24:15.955000000"
            # Format is "HH:MM:SS.nnnnnnnnn" where n is nanoseconds
            end_point: str = element.get("value")
            duration_string, seconds_fraction = end_point.split(".")  # type: str, str
            # Prepend the decimal point to not interpret nanoseconds as seconds.
            seconds_fraction = f"0.{seconds_fraction}"
            duration_seconds = enumerate(reversed([int(time_value) for time_value in duration_string.split(":")]))
            duration_sum = sum(time_value * 60**i for i, time_value in duration_seconds) + float(seconds_fraction)
            logger.debug(f"Success: Found duration using tag with key {element_name}: {end_point}")
            return duration_sum
        else:
            logger.debug(f"Failure: Stream has no tag with key {element_name}.")
            return None

    def __str__(self) -> str:
        return f"<Stream: stream_id: {self.stream_id}, start_time: {self.start_time}, duration: {self.duration}, " \
               f"format_name: {self.format_name}, names: {self.names}>"


class InputFile:

    IN_PROGRESS_TEMP_DIR_NAME = "in_progress_scenes"
    COMPLETED_TEMP_DIR_NAME = "completed_scenes"

    ffprobe_parameter_template = ("ffprobe", "-hide_banner",
                                  "-loglevel", "error",
                                  "-show_chapters",
                                  "-show_streams",
                                  "-print_format", "xml", "--")

    def __init__(self, input_file: Path, arguments: Namespace, crop_values: CropValues):
        self.input_file: Path = input_file.absolute().resolve()
        self.crop_values = crop_values

        self.force_overwrite = arguments.force_overwrite
        self.output_dir: Path = self._determine_output_dir(arguments)
        self.temp_dir: Path = self._determine_temp_dir(arguments)
        self.in_progress_dir = self.temp_dir / self.IN_PROGRESS_TEMP_DIR_NAME
        self.completed_dir = self.temp_dir / self.COMPLETED_TEMP_DIR_NAME

        self.ffprobe_parameters: List[str] = list(InputFile.ffprobe_parameter_template)
        self.ffprobe_parameters.append(str(self.input_file))
        self.ffmpeg_progs = find_ffmpeg(arguments)

        self.scenes = []
        self.chapters: List[Chapter] = []
        self.video_streams: List[Stream] = []

    @property
    def all_scenes_completed(self) -> bool:
        """
        Looks at the file system to determine if all required scenes are finished encoding.
        Returns True, if all scenes are present and ready to be concatenated into the final result.
        Returns False, if any scenes are missing and still require encoding.
        """
        file_names = (path.name for path in self.completed_dir.glob("scene_*.mkv"))
        scene_numbers = set(int(stream_number_re.match(file)["number"]) for file in file_names)
        required_scenes: Set[int] = set(range(len(self.scenes)))
        return scene_numbers == required_scenes

    def _determine_output_dir(self, arguments: Namespace) -> Path:
        """
        Determine the output directory for this input file by looking at the command line arguments.
        :return: Path object to the output directory
        """
        if arguments.output_dir is not None:
            output_dir = arguments.output_dir.absolute().resolve()
        else:
            # The input file path is already absolute and resolved.
            output_dir: Path = self.input_file.parent

        logger.info(
            f'Determined output directory for input file "{self.input_file}". Data will be written to "{output_dir}"'
        )
        return output_dir

    def _determine_temp_dir(self, arguments: Namespace) -> Path:
        """
        Determine the temporary data directory for this input file by looking at the command line arguments.
        This will hold encoded scenes, the scene cut list, log files, data of running encodes, etc.
        :return: Path object to the temporary data directory
        """
        if arguments.temp_dir is not None:
            temp_dir_parent = arguments.temp_dir.absolute().resolve()
        elif arguments.output_dir is not None:
            temp_dir_parent = arguments.output_dir.absolute().resolve()
        else:
            # The input file path is already absolute and resolved.
            temp_dir_parent: Path = self.input_file.parent

        temp_dir = (temp_dir_parent / f"{self.input_file.name}.temp")

        logger.info(
            f'Determined temporary directory for input file "{self.input_file}". Data will be written to "{temp_dir}"'
        )
        return temp_dir

    def handle_temp_directory_creation(self) -> bool:
        """
        Handles the creation of the temporary directory and the output directory.
        Has to be called before any command lines for this file are run().
        """
        # Uses lazy evaluation to shortcut later calls if any fails
        return self._handle_output_directory() and self._handle_temp_directory() and self._handle_temp_subdirectories()

    def _handle_temp_directory(self) -> bool:
        logger.info(f'Handling temporary directory creation for input file "{self.input_file}": '
                    f'{self.temp_dir}')
        if self.temp_dir == self.output_dir:
            # Shortcutting here, as it is already done.
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
        output_dir: Path = self.output_dir
        logger.info(f'Handling output directory creation for input file "{self.input_file}": {output_dir}')
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
            parent_mode = InputFile._traverse_path_until_existing(dir_path).stat().st_mode
            dir_path.mkdir(parent_mode, parents=True)
        except OSError:
            return False
        else:
            logger.info(f"Created requested {name_for_logging} directory: {dir_path}")
            return True

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

    def has_video_data(self) -> bool:
        """
        Returns True, if the input file has video streams.
        :return:
        """
        return bool(self.video_streams)

    def has_chapters(self) -> bool:
        """
        Returns True, if the input file has chapter markers.
        :return:
        """
        return bool(self.chapters)

    def collect_file_data(self):
        """
        Creates the Chapter and Stream instances using the gathered data.
         This function fills the chapters and video_streams attribute lists with data.
         :raises: RuntimeError if called multiple times.
        """
        logger.info(f"Collecting file data (streams and chapters) for input file: {self.input_file}")
        if self.chapters or self.video_streams:
            err_msg = f'PROGRAM LOGIC BUG: File data for input file "{self.input_file}" already collected. ' \
                      f'Calling this function again would duplicate data.'
            logger.critical(err_msg)
            raise RuntimeError(err_msg)
        chapters, streams = self._get_xml_nodes
        self._extract_chapters(chapters)
        self._extract_streams(streams)

    def _extract_chapters(self, chapters: Et.Element):
        """
        Extract the chapter data from the ffprobe output. Fills self.chapters
        :param chapters: XML Element containing the chapter data
        :return:
        """
        logger.info(f"Extracting chapter data for input file: {self.input_file}")
        for number, chapter in enumerate(chapters, start=1):  # type: int, Et.Element
            self.chapters.append(Chapter(chapter, number))
            logger.debug(f"Extracted chapter: {self.chapters[-1]}")

    def _extract_streams(self, streams: Et.Element):
        """
        Extract the video stream data from the ffprobe output. Fills self.video_streams
        :param streams: XML Element containing the stream metadata
        :return:
        """
        logger.info(f"Extracting stream data for input file: {self.input_file}")
        for video_stream in streams.findall("""*[@codec_type="video"]"""):  # type: Et.Element
            self.video_streams.append(Stream(video_stream))
            logger.debug(f"Extracted stream: {self.video_streams[-1]}")

    @property
    def _get_xml_nodes(self) -> Tuple[Et.Element, Et.Element]:
        """
        Extract the required chapters and streams nodes from the ffprobe XML output.
        :return: tuple (chapters, streams), each of type Et.Element
        """
        logger.info(f"Start extracting file data for input file: {self.input_file}")
        try:
            root: Et.ElementTree = Et.fromstring(self._run_ffprobe())
        except subprocess.CalledProcessError:
            logger.exception(f"Error executing ffprobe on {self.input_file}")
            raise
        except Et.ParseError:
            logger.exception(
                f"Executing ffprobe on {self.input_file} returned invalid XML data. Probably a bug in ffprobe.")
            raise
        chapters: Et.Element = root.find("./chapters")
        streams: Et.Element = root.find("./streams")
        logger.info(
            f"Extracted data. Required XML elements present in extracted data: "
            f"Chapters: {chapters is not None}, Streams: {streams is not None}"
        )
        return chapters, streams

    def _run_ffprobe(self):
        logger.info(f"Running ffprobe to probe input file. Using parameters: {self.ffprobe_parameters}")
        return subprocess.check_output(
                self.ffprobe_parameters,
                executable=self.ffmpeg_progs.ffprobe,
                universal_newlines=True
        )

    def __str__(self):
        # calling str(list_instance) internally calls repr() on elements. Convert manually to avoid this and use the
        # __str__() function on elements.
        streams = "[" + ", ".join((str(stream) for stream in self.video_streams)) + "]"
        chapters = "[" + ", ".join((str(chap) for chap in self.chapters)) + "]"
        return f"<InputFile:\n" \
               f"  InputFile: {self.input_file}\n" \
               f"  OutputDir: {self.output_dir}\n" \
               f"  Streams: {streams}\n" \
               f"  Chapters: {chapters}\n>"


def read_input_files(arguments: Namespace) -> List[InputFile]:
    """Reads all input files specified on the command line and parses them to InputFile instances."""
    logger.info("Extracting file data for all input files.")
    result: List[InputFile] = list()
    for input_file, crop_value in zip(arguments.input_files, arguments.crop_values):
        if not input_file.exists():
            logger.warning(f'The given input file "{input_file}" does not exist. Skipping.')
            continue
        logger.debug(f"Extracting file data for input file: {input_file}")
        in_file = InputFile(input_file, arguments, crop_value)
        in_file.collect_file_data()
        if in_file.has_video_data():
            logger.debug(f'Input file "{input_file}" contains video streams.'
                         f'Video data for this file will be transcoded.')
            result.append(in_file)
        else:
            logger.warning(f'Input file "{input_file}" does not contain any video streams: Ignoring this file.')
    return result


def _replace_path(output_dir: Path):
    """
    Encapsulate filesystem interaction: replace output_dir with a directory.
    """
    output_dir.unlink()
    output_dir.mkdir(output_dir.parent.stat().st_mode)
