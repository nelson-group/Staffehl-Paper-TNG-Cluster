"""
Data loading for radial temperature profiles.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray


def _natsort_key(path: Path | str) -> list[int]:
    """
    Sorting key for natural sort. Used to sort filenames by halo ID.

    Can be used as a key to ``sorted`` or ``list.sort()`` to sort the
    list in natural order:

    >>> alist = ["file_10", "file_2", "file_1"]
    >>> sorted(alist, key=_natsort_key)
    ["file_1", "file_2", "file_10"]

    :param path: A file path, as a valid Path object or string.
    :return: A list of integers found in the text.
    """
    # yapf: disable
    return [int(c) if c.isdigit() else c for c in re.split(r"(\d+)", str(path))]
    # yapf: enable


def load_radial_profile_data(
    filepath: str | Path,
    n_mass_bins: int | None = None,
    n_radial_bins: int | None = None,
    n_temperature_bins: int | None = None
) -> tuple[NDArray, NDArray] | None:
    """
    Return the radial profile data loaded from the given file.

    Function also additionally allows for verifying the shape of the
    data. If the number of either of the bins is given, that part of the
    data shape will be verified. If none of them are given, the data
    will be returned unverified.

    :param filepath: File name and path of the numpy data file.
    :param n_mass_bins: The number of mass bins. Used for verification.
        Optional.
    :param n_radial_bins: The number of radial bins in the data, used
        for verification. Optional.
    :param n_temperature_bins: The number of temperature bins in the
        data, used for verification. Optional.
    :return: A tuple of arrays, with the first one being the histogram
        data, and the second the running averages for the histograms. If
        loading or data verification fail, returns None instead.
    """
    logging.info("Loading saved radial temperature profiles from file.")
    if not isinstance(filepath, Path):
        filepath = Path(filepath)

    if not filepath.is_file():
        logging.error(f"The given file {str(filepath)} is not a valid file.")
        return

    # load the file
    with np.load(filepath) as data:
        histograms = data["hist_mean"]
        averages = data["running_avg"]

    if not n_radial_bins and not n_temperature_bins and not n_mass_bins:
        logging.info("Returning loaded data wihout verification.")
        return histograms, averages

    if n_mass_bins:
        if histograms.shape[0] != n_mass_bins:
            logging.error(
                f"Histogram data does not have the expected length: expected "
                f"{n_mass_bins} mass bins, but found {histograms.shape[0]} "
                f"instead."
            )
            return
        if averages.shape[0] != n_mass_bins:
            logging.error(
                f"Running averages do not have the expected length: expected "
                f"{n_mass_bins} mass bins, but found {averages.shape[0]} "
                f"instead."
            )
            return

    if n_radial_bins:
        if histograms.shape[2] != n_radial_bins:
            logging.error(
                f"Histogram data does not have the expected number of radial "
                f"bins: expected {n_radial_bins} mass bins, but found "
                f"{histograms.shape[2]} instead."
            )
            return
        if averages.shape[1] != n_radial_bins:
            logging.error(
                f"Running averages do not have the expected number of radial "
                f"bins: expected {n_radial_bins} mass bins, but found "
                f"{averages.shape[1]} instead."
            )
            return

    if n_temperature_bins:
        if histograms.shape[1] != n_temperature_bins:
            logging.error(
                f"Histogram data does not have the expected number of "
                f"temperature bins: expected {n_temperature_bins} mass bins, "
                f"but found {histograms.shape[1]} instead."
            )
            return

    logging.info("Successfully loaded verified data.")
    return histograms, averages


def load_individuals_2d_profile(
    filepath: str | Path,
    expected_shape: tuple[int, int] | None = None,
    fail_fast: bool = False,
) -> Iterator[dict[str, NDArray] | None]:
    """
    Yield the histrogram data from file for every file in the directory.

    Function goes through all files in the given directory ``filepath``
    and yields the halo histogram data for every such file. The yielded
    value is a dictionary with keys 'histogram' (the histogram data as
    a 2D array), 'xedges', 'yedges' (the bin edges on both x- and y-axis),
    'halo_id' and 'halo_mass' (given in physical units).

    :param filepath: Path of the directory in which the data files are
        located. All non-file entries are ignored.
    :param expected_shape: The shape of the histogram array. Optional,
        defaults to None which means no verification.
    :param fail_fast: Whether to raise a ``StopIteration`` when
        encountering an invalid histogram shape or to simply yield None
        instead. False means the function will yield None and continue
        to iterate through the files, True means an invalid data shape
        will cause a ``StopIteration`` exception to be raised.
    :raises StopIteration: Raised when a histogram shape does not match
        the expected shape while ``fail_fast`` is ``True``.
    :yield: A dictionary of the halo data including the radial profile
        2D histogram data. The yielded result is a dictionary with the
        following keys:

        - ``histogram``: The 2D histogram array, containing the histogram
          values.
        - ``xedges``: Tuple of the lower and upper edges of the x-axis.
        - ``yedges``: Tuple of the lower and upper edges of the y-axis.
        - ``halo_id``: The ID of the halo as integer. Note that this
          will be an NDArray of shape (1, ).
        - ``halo_mass``: The mass of the halo in units of solar masses.
          The exact type of mass measure depends on the setup when the
          data was saved, but typically, this is the virial mass (M_200).
          Note that this will be an NDArray of shape (1, ).
    """
    if not isinstance(filepath, Path):
        filepath = Path(filepath)

    if not filepath.is_dir():
        logging.error(
            "Expected data file directory, got file or simlink instead:\n"
            f"{str(filepath)}"
        )

    # load every file individually and yield it
    files = list(filepath.iterdir())
    files = sorted(files, key=_natsort_key)
    for filename in files:
        if not filename.is_file():
            logging.warning(f"Skipping non-file entry {filename}.")
        with np.load(filename) as data_file:
            histogram = data_file["histogram"]
            original_histogram = data_file["original_histogram"]
            xedges = data_file["xedges"]
            yedges = data_file["yedges"]
            halo_id = data_file["halo_id"]
            halo_mass = data_file["halo_mass"]
            virial_temperature = data_file["virial_temperature"]

        # construct dictionary
        halo_data = {
            "histogram": histogram,
            "original_histogram": original_histogram,
            "xedges": xedges,
            "yedges": yedges,
            "halo_id": halo_id,
            "halo_mass": halo_mass,
            "virial_temperature": virial_temperature,
        }

        # if data verification is undesired, yield data right away
        if expected_shape is None:
            yield halo_data
            continue  # skip over all data verification code below

        # verify the data shape (is skipped for expected_shape = None)
        if histogram.shape != expected_shape:
            logging.error(
                f"Halo {halo_id} has histogram data not matching the expected "
                f"shape: Expected shape {expected_shape} but got "
                f"{histogram.shape} instead."
            )
            if fail_fast:
                raise StopIteration
            else:
                logging.warning("Yielding None. This may cause issues.")
                yield None
        else:
            yield halo_data


def load_individuals_1d_profile(
    filepath: str | Path,
    expected_shape: int | tuple[int] | None = None,
    fail_fast: bool = False,
) -> Iterator[dict[str, NDArray] | None]:
    """
    Yield the histogram data from file for every file in the directory.

    Function goes through all files in the given directory ``filepath``
    and yields the halo histogram data for every such file. The yielded
    value is a dictionary with keys 'histogram' (the histogram data as
    a 2D array), 'edges', (bin edges of the histogram bins), 'halo_id'
    and 'halo_mass' (given in physical units).

    .. warning:: The returned data is yielded in arbitrary order! There
        is no guarantee that the data is returned in order of ascending
        halo ID!

    :param filepath: Path of the directory in which the data files are
        located. All non-file entries are ignored.
    :param expected_shape: The shape of the histogram array. Optional,
        defaults to None which means no verification.
    :param fail_fast: Whether to raise a ``StopIteration`` when
        encountering an invalid histogram shape or to simply yield None
        instead. False means the function will yield None and continue
        to iterate through the files, True means an invalid data shape
        will cause a ``StopIteration`` exception to be raised.
    :raises StopIteration: Raised when a histogram shape does not match
        the expected shape while ``fail_fast`` is ``True``.
    :yield: A dictionary of the halo data including the radial profile
        histogram data. The yielded result is a dictionary with the
        following keys:

        - ``histogram``: The 2D histogram array, containing the histogram
          values.
        - ``xedges``: Tuple of the lower and upper edges of the x-axis.
        - ``yedges``: Tuple of the lower and upper edges of the y-axis.
        - ``halo_id``: The ID of the halo as integer. Note that this
          will be an NDArray of shape (1, ).
        - ``halo_mass``: The mass of the halo in units of solar masses.
          The exact type of mass measure depends on the setup when the
          data was saved, but typically, this is the virial mass (M_200).
          Note that this will be an NDArray of shape (1, ).
    """
    if not isinstance(filepath, Path):
        filepath = Path(filepath)

    if not filepath.is_dir():
        logging.error(
            "Expected data file directory, got file or simlink instead:\n"
            f"{str(filepath)}"
        )

    # load every file individually and yield it
    files = list(filepath.iterdir())
    files = sorted(files, key=_natsort_key)
    for filename in files:
        if not filename.is_file():
            logging.warning(f"Skipping non-file entry {filename}.")
            continue
        with np.load(filename) as data_file:
            # construct dictionary
            halo_data = {
                "total_inflow": data_file["total_inflow"],
                "total_outflow": data_file["total_outflow"],
                "cool_inflow": data_file["cool_inflow"],
                "cool_outflow": data_file["cool_outflow"],
                "warm_inflow": data_file["warm_inflow"],
                "warm_outflow": data_file["warm_outflow"],
                "hot_inflow": data_file["hot_inflow"],
                "hot_outflow": data_file["hot_outflow"],
                "edges": data_file["edges"],
                "halo_id": data_file["halo_id"],
                "halo_mass": data_file["halo_mass"],
                "halo_position": data_file["halo_position"],
            }

        # if data verification is undesired, yield data right away
        if expected_shape is None:
            yield halo_data
            continue  # skip over all data verification code below

        # verify the data shape (is skipped for expected_shape = None)
        if isinstance(expected_shape, int):
            expected_shape = (expected_shape, )
        any_failures = False
        for field, hist in halo_data.items():
            if field in ["edges", "halo_id", "halo_mass", "halo_position"]:
                continue
            if (s := hist.shape) != expected_shape:
                logging.error(
                    f"Halo {halo_data['halo_id']} has {field} data not "
                    f"matching the expected shape: Expected shape "
                    f"{expected_shape} but got {s} instead."
                )
                if fail_fast:
                    raise StopIteration  # fail immediately
                else:
                    any_failures = True  # yield None later

        # depending on verification: yield data or None
        if any_failures:
            logging.warning("Yielding None. This may cause issues.")
            yield None
        else:
            yield halo_data
