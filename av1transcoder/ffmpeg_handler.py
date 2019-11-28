# Copyright (C) 2018 Thomas Hess <thomas.hess@udo.edu>

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
from typing import NamedTuple

from av1transcoder.logger import get_logger
from av1transcoder.argument_parser import Namespace

logger = get_logger("ffmpeg_handler")


class FFmpeg(NamedTuple):
    ffmpeg: str
    ffprobe: str


def find_ffmpeg(arguments: Namespace) -> FFmpeg:
    if arguments.ffmpeg_base is None:
        logger.debug("ffmpeg/ffprobe base is None, use the ffmpeg/ffprobe parameters as-is.")
        ffmpeg = arguments.ffmpeg
        ffprobe = arguments.ffprobe

    else:
        logger.debug("ffmpeg/ffprobe base is not None, interpret everything as a path.")
        ffmpeg_path = Path(arguments.ffmpeg_base).joinpath(arguments.ffmpeg).resolve()
        ffprobe_path = Path(arguments.ffmpeg_base).joinpath(arguments.ffprobe).resolve()
        ffmpeg = str(ffmpeg_path)
        ffprobe = str(ffprobe_path)
    result = FFmpeg(ffmpeg, ffprobe)
    logger.info(f"To be used ffmpeg/ffprobe binaries based on parameters: {result}")
    return result
