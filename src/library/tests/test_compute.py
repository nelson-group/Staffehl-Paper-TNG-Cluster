"""
Test the compute module.
"""
import numpy as np
import pytest

from library import compute


def test_compute_get_temperature_float():
    """
    Test the get temperature function for numpy with float input
    """
    temp = compute.get_temperature(10., 0., 0.)
    assert temp == pytest.approx(984.94, 0.2)
    temp = compute.get_temperature(10, 0.5, 0.)
    assert temp == pytest.approx(673.04, 0.2)


def test_compute_get_temperature_array():
    """
    Test the get temperature function for numpy with array input
    """
    internal_energy = np.array([10, 20, 100, 3000], dtype=float)
    electron_abundance = np.array([0, 0, 0.2, 1], dtype=float)
    star_formation_rates = np.zeros(4)
    temps = compute.get_temperature(
        internal_energy, electron_abundance, star_formation_rates
    )
    expected = np.array([984.94, 1969.88, 8309.16, 153351.28], dtype=float)
    np.testing.assert_array_almost_equal(temps, expected, decimal=2)


def test_compute_get_temperature_non_zero_sfr():
    """
    Test that when the SFR of a gas cell is non-zero, temperature is set to
    an artificial value.
    """
    temp = compute.get_temperature(10., 0., 0.5)
    assert temp == 1e3
    # test the same for arrays
    internal_energy = np.array([10, 20, 100, 3000], dtype=float)
    electron_abundance = np.array([0, 0, 0.2, 1], dtype=float)
    sfrs = np.array([1, 0, 1, 1])
    expected = np.array([1000., 1969.88, 1000., 1000.], dtype=float)
    # verification
    temps = compute.get_temperature(internal_energy, electron_abundance, sfrs)
    np.testing.assert_array_almost_equal(temps, expected, decimal=2)


def test_compute_get_temperature_vectorized_float():
    """
    Test the get temperature function with float input
    """
    temp = compute.get_temperature_vectorized(10., 0., 0.)
    assert temp == pytest.approx(984.94, 0.2)
    temp = compute.get_temperature_vectorized(10, 0.5, 0.)
    assert temp == pytest.approx(673.04, 0.2)


def test_compute_get_temperature_vectorized_array():
    """
    Test the get temperature function with array input
    """
    internal_energy = np.array([10, 20, 100, 3000], dtype=float)
    electron_abundance = np.array([0, 0, 0.2, 1], dtype=float)
    star_formation_rates = np.zeros(4)
    temps = compute.get_temperature_vectorized(
        internal_energy, electron_abundance, star_formation_rates
    )
    expected = np.array([984.94, 1969.88, 8309.16, 153351.28], dtype=float)
    np.testing.assert_array_almost_equal(temps, expected, decimal=2)


def test_compute_get_temperature_vectorized_non_zero_sfr():
    """
    Test that when the SFR of a gas cell is non-zero, temperature is set to
    an artificial value.
    """
    temp = compute.get_temperature_vectorized(10., 0., 0.5)
    assert temp == 1e3


# TODO: tests for virial temperature function


def test_compute_get_radial_velocities():
    """
    Test the function that returns radial velocities
    """
    test_center = np.array([1, 2])
    pos = np.array([[2, 2], [2, 3], [2, 1], [1, 1], [1, 3]])
    vel = np.array([[0, 1], [1, 1], [-1, 1], [1, -1], [1, -1]])

    # velocities are w.r.t. halo center
    test_group_vel = np.array([0, 0])
    expected = np.array([0, np.sqrt(2), -np.sqrt(2), 1, -1])
    output = compute.get_radial_velocities(
        test_center, test_group_vel, pos, vel
    )
    np.testing.assert_allclose(output, expected, rtol=1e-4)

    # halo has its own peculiar velocity
    test_group_vel = np.array([1, -1])
    expected = np.array([-1, np.sqrt(2), -2 * np.sqrt(2), 0, 0])
    output = compute.get_radial_velocities(
        test_center, test_group_vel, pos, vel
    )
    np.testing.assert_allclose(output, expected, rtol=1e-4)


def test_get_distance_periodic_box():
    """Test function with two simple vectors"""
    a = np.array([1, 0, -1], dtype=float)
    b = np.array([0, 1, -1], dtype=float)
    output = compute.get_distance_periodic_box(a, b, 2)
    assert isinstance(output, float)
    assert output == np.sqrt(2)


def test_get_distance_periodic_box_surpassed_distance():
    """Test function with two vectors, surpassing box length"""
    a = np.array([1, 0, -2.5], dtype=float)
    b = np.array([0, 1, 0], dtype=float)
    output = compute.get_distance_periodic_box(a, b, 2)
    assert isinstance(output, float)
    assert output == 1.5


def test_get_distance_periodic_box_barely_surpassed_distance():
    """Test function, surpassing box length just slightly"""
    a = np.array([1, 0, -1.5], dtype=float)
    b = np.array([0, 1, 0], dtype=float)
    output = compute.get_distance_periodic_box(a, b, 2)
    assert isinstance(output, float)
    assert output == 1.5


@pytest.mark.skip(reason="Multiples of box size not supported.")
def test_get_distance_periodic_box_surpassed_multiple():
    """Test function for multiple box lengths too many"""
    a = np.array([1, 0, -1], dtype=float)
    b = np.array([4, 5, -3], dtype=float)
    output = compute.get_distance_periodic_box(a, b, 2)
    assert isinstance(output, float)
    assert output == np.sqrt(2)


def test_get_distance_periodic_box_array():
    """Test the function with an array of vectors"""
    a = np.array(
        [[1, 1, 0], [-1, 0, 1], [0, 0, 0], [2, -2, 2]],
        dtype=float,
    )
    b = np.array(
        [[-1, 1, -1], [0, 2, 2], [-1, 1, -1], [0, 0, 0]],
        dtype=float,
    )
    expected = np.array([1, np.sqrt(2), np.sqrt(3), 0], dtype=float)
    output = compute.get_distance_periodic_box(a, b, 2)
    assert isinstance(output, np.ndarray)
    assert output.shape == (4, )
    np.testing.assert_array_equal(expected, output)


def test_get_distance_periodic_box_array_and_vector():
    """Test function when mixing vector and list of vectors"""
    a = np.array([0, 0, 0], dtype=float)
    b = np.array([[1, 0, -1], [2, 2, 2], [1, 1, 2]], dtype=float)
    expected = np.array([np.sqrt(2), 0, np.sqrt(2)], dtype=float)

    # test both permutations of parameters
    output = compute.get_distance_periodic_box(a, b, 2)
    assert isinstance(output, np.ndarray)
    assert output.shape == (3, )
    np.testing.assert_array_equal(expected, output)

    output = compute.get_distance_periodic_box(b, a, 2)
    assert isinstance(output, np.ndarray)
    assert output.shape == (3, )
    np.testing.assert_array_equal(expected, output)


def test_get_distance_periodic_box_integer_dtype():
    """Test that the function can handle arrays of dtype integer"""
    a = np.array([0, 0, 0], dtype=np.int32)
    b = np.array([[1, 0, -1], [2, 2, 2], [1, 1, 2]], dtype=np.int32)
    expected = np.array([np.sqrt(2), 0, np.sqrt(2)], dtype=float)
    output = compute.get_distance_periodic_box(a, b, 2)

    assert isinstance(output, np.ndarray)
    assert np.issubdtype(output.dtype, np.floating)
    np.testing.assert_array_equal(expected, output)


def test_lookback_time_from_redshift_valid_values():
    """Test the function with valid redhsifts"""
    redshifts = np.array([0, 2, 8], dtype=np.float64)
    output = compute.lookback_time_from_redshift(redshifts)
    expected = np.array([0., 10.51366212, 13.15884972])
    np.testing.assert_array_almost_equal(expected, output)


def test_lookback_time_from_redshift_with_negative_values():
    """Test that negative redshifts remain untouched"""
    redshifts = np.array([-1, 0, 2, 8, -100], dtype=np.float64)
    output = compute.lookback_time_from_redshift(redshifts)
    expected = np.array([-1, 0, 10.51366212, 13.15884972, -100])
    np.testing.assert_array_almost_equal(expected, output)


def test_lookback_time_from_redshift_only_negative_values():
    """Test the function raises no exception with only negative values"""
    redshifts = np.array([-1, -2, -8], dtype=np.float64)
    output = compute.lookback_time_from_redshift(redshifts)
    np.testing.assert_array_almost_equal(redshifts, output)


def test_redshift_from_lookback_time_valid_values():
    """Test the function with valid values"""
    lookback_times = np.array([0, 5.5, 10, 13.6], dtype=np.float64)
    output = compute.redshift_from_lookback_time(lookback_times)
    expected = np.array([0, 0.54306493, 1.71502016, 18.63050448])
    np.testing.assert_array_almost_equal(expected, output)


def test_redshift_from_lookback_time_invalid_values():
    """Test the function with invalid values"""
    # negative and older than universe
    lookback_times = np.array([-1, 0, 5.5, 10, 13.6, 14], dtype=np.float64)
    output = compute.redshift_from_lookback_time(lookback_times)
    expected = np.array([-1, 0, 0.54306493, 1.71502016, 18.63050448, np.inf])
    np.testing.assert_array_almost_equal(expected, output)
