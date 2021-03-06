# Copyright (C) 2017 Thomas Hess <thomas.hess@udo.edu>

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
Natural sorting lists. Used to build the concat filter file listing.
"""

import re
import typing

_REG_EXP = re.compile(r"([0-9]+)")


def try_convert_int(s: str):
    try:
        return int(s)
    except ValueError:
        return s


def alphanum_key(s):
    """
    Turn a string into a list of string and number chunks.
    "z23a" -> ["z", 23, "a"]
    """
    return [try_convert_int(c) for c in _REG_EXP.split(s)]


def natural_sorted(input_list: typing.Iterable[str], reverse: bool = False):
    """
    Sort the given list in the way that humans expect.
    """
    return sorted(input_list, key=alphanum_key, reverse=reverse)
