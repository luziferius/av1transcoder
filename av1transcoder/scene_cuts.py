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
This module is responsible for computing scene cuts for each input file.
It generates the raw scene cut data using ffmpeg and then post-processes the data into a usable format.
It is also responsible for scene merging and splitting to adhere to the limits given on the command line.
"""

from pathlib import Path
import re
import shutil
import typing

from av1transcoder.argument_parser import Namespace
from av1transcoder.command_line import AbstractCommandLine
from av1transcoder.input_file import InputFile
from av1transcoder.logger import get_logger

logger = get_logger(__name__.split(".")[-1])
scene_logger = logger.getChild("Scene")
OptInt = typing.Optional[int]
OptFloat = typing.Optional[float]


timestamp_matcher_re = re.compile(
    r"frame:(?P<frame>0|[1-9][0-9]*) +pts:(?P<pts>0|[1-9][0-9]*) +pts_time:(?P<pts_time>[0-9]+\.?[0-9]*)"
)
score_matcher_re = re.compile(
    r"lavfi\.scene_score=(?P<scene_score>1\.0{6}|[0]\.[0-9]{6})"
)


class SceneCutDetectionCommandLine(AbstractCommandLine):
    """
    This class implements the generation of command line arguments suitable to detect scene cuts and store
    the found information in a text file for later processing. It uses the ffmpeg scene filter internally to
    determine the required information.
    """
    def __init__(self, arguments: Namespace, input_file: InputFile):
        super(SceneCutDetectionCommandLine, self).__init__(arguments, input_file)
        logger.info(f'Constructing command line to detect scene cuts in input file "{input_file}"')
        self.raw_timestamps_file_name = "raw_timestamps.txt"
        self.raw_timestamps_file = self.input_file.temp_dir / self.raw_timestamps_file_name
        self._add_command_line_arguments(arguments)
        if self.dump_mode == "only":
            # The scene cut detection must always run, so overwrite "only" with "yes" in this particular case.
            # This is documented in the --help output.
            logger.info("Overwriting the dump mode given on the command line "
                        "from 'only' to 'yes' for the scene detection.")
            self.dump_mode = "yes"
        logger.info(f"Created {self.__class__.__name__} instance.")

    def _add_command_line_arguments(self, arguments: Namespace):
        scene_cut_threshold_str = self._float_str(arguments.scene_cut_threshold)
        # See https://trac.ffmpeg.org/wiki/Null for information about the null filter and null muxer
        # The null filter swallows the filter stream result, which consists of the actual frames at scene cuts.
        # The filter chain writes the required meta data to disk.
        # TODO: Check if "-" meaning "no output file" works on Windows platforms. If not, it has to be "NUL" if on Win
        # TODO: Check if arbitrary paths are allowed in the filterpath file argument. If not, change the current
        #       working directory to the temporary path to the

        # Prepend the deinterlace filter and crop filter if de-interlacing / cropping is enabled.
        # On interlaced sources, de-interlacing should improve the scene cut detection accuracy.
        filter_chain = self.get_filter_chain()
        user_filter = ",".join(filter_chain) + "," if filter_chain else ""

        video_filter = f"{user_filter}" \
                       f"select='gt(scene,{scene_cut_threshold_str})',metadata=print:file=" \
                       f"'{self.input_file.in_progress_dir/self.raw_timestamps_file_name}',null"
        self.command_line += [
            "-y" if self.force_overwrite else "-n",
            "-i", str(self.input_file.input_file),
            "-vf", video_filter,
            "-an", "-sn",  # Discard unrelated input streams. This is better than encoding them for no reason.
            "-f", "null", "-"  # Use the null muxer to produce no output file
        ]
        command_line_str = f"[{', '.join(self.command_line)}]"
        logger.debug(f"Constructed command line. Result: {command_line_str}")

    def _get_command_dump_file_name(self):
        return "scene_cut_detection_command.txt"

    def _get_output_file_path(self):
        return self.raw_timestamps_file

    def _move_output_files_to_completed_dir(self):
        logger.debug(f'Searching completed, move the generated timestamp log to "{self.input_file.temp_dir}".')
        if self.finished and self.force_overwrite:
            logger.info("Overwriting finished scene detection data, because --force-overwrite was given.")
            self._get_output_file_path().unlink()
        elif self.finished:
            logger.warning(
                "Output file already exists, but --force-overwrite non given. This should not have happened! "
                "Not overwriting the output…"
            )
        else:
            shutil.move(
                str(self.input_file.in_progress_dir/self.raw_timestamps_file_name),
                str(self.input_file.temp_dir)
            )


class Scene:

    @classmethod
    def factory(cls, timestamp_match: typing.Match, score_match: typing.Match, previous_scene=None):
        scene_logger.debug("Creating Scene from timestamps.")
        end_pts = int(timestamp_match["pts"])
        end_pts_time = float(timestamp_match["pts_time"])
        end_scene_score = float(score_match["scene_score"])
        timestamp_match_number = int(timestamp_match["frame"])
        return cls(end_pts, end_pts_time, end_scene_score, timestamp_match_number, previous_scene)

    def __init__(self, end_pts: OptInt, end_pts_time: OptFloat, end_scene_score: OptFloat,
                 timestamp_match_number: OptInt, previous_scene=None):
        self.end_pts = end_pts
        self.end_pts_time = end_pts_time
        self.end_scene_score = end_scene_score
        self.timestamp_match_number = timestamp_match_number
        if previous_scene is None:
            self.scene_number = 0
            self.begin_pts = 0
            self.begin_pts_time = 0.0
        else:
            self.scene_number = previous_scene.scene_number + 1
            self.begin_pts = previous_scene.end_pts
            self.begin_pts_time = previous_scene.end_pts_time
        scene_logger.debug(f"Created {self.__class__.__name__} instance: {self}.")

    @property
    def length_seconds(self) -> OptFloat:
        if self.end_pts_time is not None:
            return self.end_pts_time - self.begin_pts_time
        else:
            return None

    @property
    def is_end_scene(self) -> bool:
        """
        The last scene has an unknown length, because the timestamp of the last video frame is unknown without
        fully decoding it. Therefore, all end times may be None, denoting the unknown length in exactly this case.
        """
        return self.end_pts_time is None

    def merge_other(self, next_scene):
        """
        Merges this with the next scene.
        :param next_scene: The to be merged scene
        :return:
        """
        self.end_pts = next_scene.end_pts
        self.end_pts_time = next_scene.end_pts_time
        self.end_scene_score = next_scene.end_scene_score

    def split(self, max_duration):
        scene_length = self.length_seconds
        if scene_length is None or scene_length <= max_duration:
            return []
        # scene_count = math.ceil(scene_length/max_duration)
        raise NotImplementedError("Splitting scenes is not implemented!")

    def __str__(self):
        result = f"Scene(" \
                 f"number={self.scene_number}, timestamp_log_frame_number={self.timestamp_match_number}, " \
                 f"begin_pts={self.begin_pts}, begin_pts_time={self.begin_pts_time}, " \
                 f"end_pts={self.end_pts}, end_pts_time={self.end_pts_time}, scene_score={self.end_scene_score})"
        return result


SceneList = typing.List[Scene]


def generate_scene_cuts(arguments: Namespace, input_file: InputFile) -> SceneList:
    """
    Generate scene cuts for the given input file.

    To ensure the scene lengths stay within the upper and lower bound, a simple greedy algorithm is used.
    The algorithm takes the scenes as detected by ffmpeg and processes them in two passes.
    First, long scenes are split into equal chunks with length equal to the upper bound and a possibly small remainder.
    This ensures that the upper bound is held. TODO: This is currently not implemented!

    Second, short, adjacent scenes are merged into larger scenes to have at least a length equal to the minimal scene
    length.
    The second pass may produce scenes that overshoot the upper bound, but only by upper_bound+lower_bound-1.
    In this scenario: Having a scene with length lower_bound-1, qualifying for a merge and a scene at exactly the upper
    bound length. These will be merged according to the greedy algorithm, creating a Scene that is longer than the upper
    bound.
    The second pass may also undershoot it’s minimal length goal by any amount, if there is not enough video material
    available. For example, when the last scene (or the whole video) is shorter than the minimal requested length.
    """
    logger.info(f'Searching the input file "{input_file.input_file}" for scene changes.')
    cli = SceneCutDetectionCommandLine(arguments, input_file)
    cli.run()
    scenes = parse_raw_timestamps_from_file(cli.raw_timestamps_file)
    # Dump the parsed raw scenes for examination
    dump_scenes_to_file(cli.input_file.temp_dir / "parsed_scenes.txt", scenes)
    # scenes = split_long_scenes(arguments, scenes, input_file)
    scenes = merge_short_scenes(arguments, scenes)
    logger.debug("All final scenes:")
    for scene in scenes:
        logger.debug(str(scene))
    # And dump the processed scenes. These will be used to split the input video.
    dump_scenes_to_file(cli.input_file.temp_dir / "final_processed_scenes.txt", scenes)
    input_file.scenes = scenes
    return scenes


def dump_scenes_to_file(filename: Path, scenes: SceneList):
    with filename.open("w", encoding="utf-8") as scenes_file:
        scenes_file.writelines(f"{scene}\n" for scene in map(str, scenes))


def parse_raw_timestamps_from_file(raw_timestamps_path: Path) -> SceneList:
    scenes: SceneList = []
    logger.info("Parsing the raw timestamps into Scenes.")
    with raw_timestamps_path.open("r") as raw_timestamps:
        line_pairs = zip(*([raw_timestamps]*2))
        for timestamp_line, score_line in line_pairs:
            timestamp_match = timestamp_matcher_re.match(timestamp_line)
            score_match = score_matcher_re.match(score_line)
            if timestamp_match and score_match:
                scenes.append(Scene.factory(timestamp_match, score_match, scenes[-1] if scenes else None))
    try:
        # If no scene changes are detected at all, the list will be empty
        # (Maybe caused by a static image video, or similar.)
        last = scenes[-1]
    except IndexError:
        last = None
    scenes.append(Scene(None, None, None, None, last))
    logger.info(f"Created {len(scenes)} Scenes.")

    return scenes


def split_long_scenes(arguments: Namespace, scenes: SceneList, input_file: InputFile) -> SceneList:
    """
    TODO: Split long scenes to be shorter than the upper bound defined in the command line arguments
    :param arguments: Parsed command line arguments. Contains the scene length upper bound
    :param scenes: The scenes as parsed from the raw scene cut time stamps. Might contain long scenes.
    :param input_file: Contains the stream metadata with the time base required to parse PTS values
    :return: List with scenes which contain no scene longer than the user defined upper limit.
    """
    pass


def merge_short_scenes(arguments: Namespace, scenes: SceneList) -> SceneList:
    logger.info(f"Begin merging short scenes, beginning with {len(scenes)} scenes.")
    if not scenes:
        return []
    current = scenes.pop(0)
    valid_scenes = [current]

    while scenes:
        next_ = scenes.pop(0)
        if current.length_seconds < float(arguments.min_scene_length):
            current.merge_other(next_)
        else:
            current = next_
            valid_scenes.append(current)
    # Fix the scene numbering, as merging created holes in the sequence
    logger.debug("Merging finished, now fix the scene numbering.")
    for index, scene in enumerate(valid_scenes):
        scene.scene_number = index
    logger.info(f"Scene merging finished: Result contains {len(valid_scenes)} scenes.")
    return valid_scenes
