#!/usr/bin/env python3

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

import av1transcoder.argument_parser
import av1transcoder.input_file
import av1transcoder.scene_cuts
import av1transcoder.scene_transcode
import av1transcoder.logger

logger = av1transcoder.logger.get_logger(__name__.split(".")[-1])


def main():
    arguments = av1transcoder.argument_parser.parse_args()
    av1transcoder.logger.configure_root_logger(arguments)
    input_files = av1transcoder.input_file.read_input_files(arguments)
    for input_file in input_files:
        if arguments.limit_encodes == 0:
            # When the encode limit is hit, it does not make sense to try to encode the next input file.
            # Thus abort early in this case.
            logger.info("Encoder process limit hit. Do not process any further files.")
            break
        if input_file.handle_temp_directory_creation():
            scenes = av1transcoder.scene_cuts.generate_scene_cuts(arguments, input_file)
            av1transcoder.scene_transcode.transcode_input_file(arguments, input_file, scenes)
        else:
            logger.warning(
                f'Output or temporary directory creation for input file {input_file.input_file} failed. '
                f'Skipping this file.'
            )


if __name__ == "__main__": 
    main()
