"""GMS-5 VISSR Navigation.


Reference: `GMS User Guide`_, Appendix E, S-VISSR Mapping.

.. _GMS User Guide:
    https://www.data.jma.go.jp/mscweb/en/operation/fig/GMS_Users_Guide_3rd_Edition_Rev1.pdf
"""

from collections import namedtuple

import numba
import numpy as np

EARTH_FLATTENING = 1/298.257
EARTH_EQUATORIAL_RADIUS = 6378136.0
"""Constants taken from JMA's Msial library."""


def get_jitclass_type(cls):
    try:
        return cls.class_type.instance_type
    except AttributeError:
        # With NUMBA_DISABLE_JIT=1
        return cls


@numba.njit
def get_lons_lats(lines, pixels, scan_params, predicted_nav_params):
    num_lines = len(lines)
    num_pixels = len(pixels)
    output_shape = (num_lines, num_pixels)
    lons = np.zeros(output_shape)
    lats = np.zeros(output_shape)
    for i in range(num_lines):
        for j in range(num_pixels):
            point = (lines[i], pixels[j])
            obs_time = get_observation_time(point, scan_params)
            nav_params = predicted_nav_params.interpolate(obs_time)
            lon, lat = get_lon_lat(point, nav_params)
            lons[i, j] = lon
            lats[i, j] = lat
    return lons, lats


ScanningParameters = namedtuple(
    'ScanningParameters', ['start_time_of_scan', 'spinning_rate', 'num_sensors', 'sampling_angle']
)


@numba.njit
def get_observation_time(point, scan_params):
    """Calculate observation time of a VISSR pixel."""
    relative_time = _get_relative_observation_time(point, scan_params)
    return scan_params.start_time_of_scan + relative_time


@numba.njit
def _get_relative_observation_time(point, scan_params):
    line, pixel = point
    pixel = pixel + 1
    line = line + 1
    spinning_freq = 1440 * scan_params.spinning_rate
    line_step = np.floor((line - 1) / scan_params.num_sensors)
    pixel_step = (scan_params.sampling_angle * pixel) / (2 * np.pi)
    return (line_step + pixel_step) / spinning_freq


@numba.njit
def get_lon_lat(point, attitude, orbit, proj_params):
    """Get longitude and latitude coordinates for a given image pixel.

    Args:
        point: Point (line, pixel) in image coordinates.
        attitude: Attitude parameters.
        orbit: Orbital parameters.
        proj_params: Projection parameters.
    Returns:
        Longitude and latitude in degrees.
    """
    scan_angles = transform_image_coords_to_scanning_angles(
        point,
        _get_image_offset(proj_params),
        _get_sampling(proj_params)
    )
    view_vector_sat = transform_scanning_angles_to_satellite_coords(
        scan_angles,
        proj_params.misalignment
    )
    view_vector_earth_fixed = transform_satellite_to_earth_fixed_coords(
        view_vector_sat,
        orbit.greenwich_sidereal_time,
        _get_sat_sun_angles(orbit),
        attitude.angle_between_earth_and_sun,
        _get_spin_angles(attitude),
        orbit.nutation_precession
    )
    point_on_earth = intersect_with_earth(
        view_vector_earth_fixed,
        _get_sat_pos(orbit),
        _get_ellipsoid(proj_params)
    )
    lon, lat = transform_earth_fixed_to_geodetic_coords(
        point_on_earth,
        proj_params.earth_flattening
    )
    return lon, lat


@numba.njit
def _get_image_offset(proj_params):
    return proj_params.line_offset, proj_params.pixel_offset


@numba.njit
def _get_sampling(proj_params):
    return proj_params.stepping_angle, proj_params.sampling_angle


@numba.njit
def _get_sat_sun_angles(orbit):
    return np.array((
        orbit.declination_from_sat_to_sun,
        orbit.right_ascension_from_sat_to_sun
    ))


@numba.njit
def _get_spin_angles(attitude):
    return np.array((
        attitude.angle_between_sat_spin_and_z_axis,
        attitude.angle_between_sat_spin_and_yz_plane
    ))


@numba.njit
def _get_sat_pos(orbit):
    return np.array((orbit.sat_position_earth_fixed_x,
                     orbit.sat_position_earth_fixed_y,
                     orbit.sat_position_earth_fixed_z))


@numba.njit
def _get_ellipsoid(proj_params):
    return np.array((proj_params.earth_equatorial_radius,
                     proj_params.earth_flattening))


@numba.njit
def transform_image_coords_to_scanning_angles(point, offset, sampling):
    """Transform image coordinates to scanning angles.

    Args:
        point: Point (line, pixel) in image coordinates.
        offset: Offset (line, pixel) from image center.
        sampling: Stepping angle (along line) and sampling angle (along pixels)
            in radians.
    Returns:
        Scanning angles (x, y) at the pixel center (rad).
    """
    line, pixel = point
    line_offset, pixel_offset = offset
    stepping_angle, sampling_angle = sampling
    x = sampling_angle * (pixel + 1 - pixel_offset)
    y = stepping_angle * (line + 1 - line_offset)
    return np.array([x, y])


@numba.njit
def transform_scanning_angles_to_satellite_coords(angles, misalignment):
    """Transform scanning angles to satellite angular momentum coordinates.

    Args:
        angles: Scanning angles (x, y) in radians.
        misalignment: Misalignment matrix (3x3)

    Returns:
        View vector (x, y, z) in satellite angular momentum coordinates.
    """
    rotation, vector = _get_transforms_from_scanning_angles_to_satellite_coords(
        angles
    )
    return np.dot(rotation, np.dot(misalignment, vector))


@numba.njit
def _get_transforms_from_scanning_angles_to_satellite_coords(angles):
    x, y = angles
    cos_x = np.cos(x)
    sin_x = np.sin(x)
    rot = np.array(((cos_x, -sin_x, 0),
                    (sin_x, cos_x, 0),
                    (0, 0, 1)))
    vec = np.array([np.cos(y), 0, np.sin(y)])
    return rot, vec


@numba.njit
def transform_satellite_to_earth_fixed_coords(
        point,
        greenwich_sidereal_time,
        sat_sun_angles,
        earth_sun_angle,
        spin_angles,
        nutation_precession
):
    """Transform from earth-fixed to satellite angular momentum coordinates.

    Args:
        point: Point (x, y, z) in satellite angular momentum coordinates.
        greenwich_sidereal_time: True Greenwich sidereal time (rad).
        sat_sun_angles: Declination from satellite to sun (rad),
            right ascension from satellite to sun (rad)
        earth_sun_angle: Angle between sun and earth center on the z-axis
            vertical plane (rad)
        spin_angles: Angle between satellite spin axis and z-axis (rad),
            angle between satellite spin axis and yz-plane
        nutation_precession: Nutation and precession matrix (3x3)
    Returns:
        Point (x', y', z') in earth-fixed coordinates.
    """
    sat_unit_vectors = _get_satellite_unit_vectors(
        greenwich_sidereal_time,
        sat_sun_angles,
        earth_sun_angle,
        spin_angles,
        nutation_precession
    )
    return np.dot(sat_unit_vectors, point)


@numba.njit
def _get_satellite_unit_vectors(
        greenwich_sidereal_time,
        sat_sun_angles,
        earth_sun_angle,
        spin_angles,
        nutation_precession
):
    unit_vector_z = _get_satellite_unit_vector_z(
        spin_angles, greenwich_sidereal_time, nutation_precession
    )
    unit_vector_x = _get_satellite_unit_vector_x(
        earth_sun_angle, sat_sun_angles, unit_vector_z
    )
    unit_vector_y = _get_satellite_unit_vector_y(unit_vector_x, unit_vector_z)
    return np.stack((unit_vector_x, unit_vector_y, unit_vector_z), axis=-1)


@numba.njit
def _get_satellite_unit_vector_z(spin_angles, greenwich_sidereal_time, nutation_precession):
    sat_z_axis_1950 = _get_satellite_z_axis_1950(spin_angles)
    rotation = _get_transform_from_1950_to_earth_fixed(greenwich_sidereal_time)
    z_vec = np.dot(rotation, np.dot(nutation_precession, sat_z_axis_1950))
    return normalize_vector(z_vec)


@numba.njit
def _get_satellite_z_axis_1950(spin_angles):
    """Get satellite z-axis (spin) in mean of 1950 coordinates."""
    alpha, delta = spin_angles
    cos_delta = np.cos(delta)
    x = np.sin(delta)
    y = -cos_delta * np.sin(alpha)
    z = cos_delta * np.cos(alpha)
    return np.array([x, y, z])


@numba.njit
def _get_transform_from_1950_to_earth_fixed(greenwich_sidereal_time):
    cos = np.cos(greenwich_sidereal_time)
    sin = np.sin(greenwich_sidereal_time)
    return np.array(
        ((cos, sin, 0),
         (-sin, cos, 0),
         (0, 0, 1))
    )


@numba.njit
def _get_satellite_unit_vector_x(earth_sun_angle, sat_sun_angles,
                                 sat_unit_vector_z):
    beta = earth_sun_angle
    sat_sun_vector = _get_vector_from_satellite_to_sun(sat_sun_angles)
    z_cross_satsun = np.cross(sat_unit_vector_z, sat_sun_vector)
    z_cross_satsun = normalize_vector(z_cross_satsun)
    x_vec = z_cross_satsun * np.sin(beta) + \
        np.cross(z_cross_satsun, sat_unit_vector_z) * np.cos(beta)
    return normalize_vector(x_vec)


@numba.njit
def _get_vector_from_satellite_to_sun(sat_sun_angles):
    declination, right_ascension = sat_sun_angles
    cos_declination = np.cos(declination)
    x = cos_declination * np.cos(right_ascension)
    y = cos_declination * np.sin(right_ascension)
    z = np.sin(declination)
    return np.array([x, y, z])


@numba.njit
def _get_satellite_unit_vector_y(sat_unit_vector_x, sat_unit_vector_z):
    y_vec = np.cross(sat_unit_vector_z, sat_unit_vector_x)
    return normalize_vector(y_vec)


@numba.njit
def intersect_with_earth(view_vector, sat_pos, ellipsoid):
    """Intersect instrument viewing vector with the earth's surface.

    Args:
        view_vector: Instrument viewing vector (x, y, z) in earth-fixed
            coordinates.
        sat_pos: Satellite position (x, y, z) in earth-fixed coordinates.
        ellipsoid: Flattening and equatorial radius of the earth.
    Returns:
        Intersection (x', y', z') with the earth's surface.
    """
    distance = _get_distance_to_intersection(
        view_vector,
        sat_pos,
        ellipsoid
    )
    return sat_pos + distance * view_vector


@numba.njit
def _get_distance_to_intersection(view_vector, sat_pos, ellipsoid):
    """Get distance to intersection with the earth.

    If the instrument is pointing towards the earth, there will be two
    intersections with the surface. Choose the one on the instrument-facing
    side of the earth.
    """
    d1, d2 = _get_distances_to_intersections(view_vector, sat_pos, ellipsoid)
    return min(d1, d2)


@numba.njit
def _get_distances_to_intersections(view_vector, sat_pos, ellipsoid):
    equatorial_radius, flattening = ellipsoid
    flat2 = (1 - flattening) ** 2
    ux, uy, uz = view_vector
    x, y, z = sat_pos

    a = flat2 * (ux**2 + uy**2) + uz**2
    b = flat2 * (x*ux + y*uy) + z*uz
    c = flat2 * (x**2 + y**2 - equatorial_radius**2) + z**2

    tmp = np.sqrt((b**2 - a*c))
    dist_1 = (-b + tmp) / a
    dist_2 = (-b - tmp) / a
    return dist_1, dist_2


@numba.njit
def transform_earth_fixed_to_geodetic_coords(point, earth_flattening):
    """Transform from earth-fixed to geodetic coordinates.

    Args:
        point: Point (x, y, z) in earth-fixed coordinates.
        earth_flattening: Flattening of the earth.

    Returns:
        Geodetic longitude and latitude (degrees).
    """
    x, y, z = point
    f = earth_flattening
    lon = np.arctan2(y, x)
    lat = np.arctan2(z, ((1 - f)**2 * np.sqrt(x**2 + y**2)))
    return np.rad2deg(lon), np.rad2deg(lat)


@numba.njit
def normalize_vector(v):
    """Normalize the given vector."""
    return v / np.sqrt(np.dot(v, v))


Attitude = namedtuple(
    'Attitude',
    [
        'angle_between_earth_and_sun',
        'angle_between_sat_spin_and_z_axis',
        'angle_between_sat_spin_and_yz_plane'
    ]
)


Orbit = namedtuple(
    'Orbit',
    [
        'greenwich_sidereal_time',
        'declination_from_sat_to_sun',
        'right_ascension_from_sat_to_sun',
        'sat_position_earth_fixed_x',
        'sat_position_earth_fixed_y',
        'sat_position_earth_fixed_z',
        'nutation_precession',
    ]
)


ProjectionParameters = namedtuple(
    'ProjectionParameters',
    [
        'line_offset',
        'pixel_offset',
        'stepping_angle',
        'sampling_angle',
        'misalignment',
        'earth_flattening',
        'earth_equatorial_radius',
    ]
)


# FIXME
# @numba.experimental.jitclass(
#     [
#         ('attitude', get_jitclass_type(Attitude)),
#         ('orbit', get_jitclass_type(Orbit)),
#         ('proj_params', get_jitclass_type(ProjectionParameters)),
#     ]
# )
# class NavigationParameters:
#     def __init__(self, attitude, orbit, proj_params):
#         self.attitude = attitude
#         self.orbit = orbit
#         self.proj_params = proj_params
#
#         # TODO: Remember that all angles are expected in rad
#         # TODO: Watch out shape of 3x3 matrices! See msVissrNav.c
#
#     def get_image_offset(self):
#         return self.proj_params.line_offset, self.proj_params.pixel_offset
#
#     def get_sampling(self):
#         return self.proj_params.stepping_angle, self.proj_params.sampling_angle
#
#     def get_sat_sun_angles(self):
#         return np.array([
#             self.orbit.declination_from_sat_to_sun,
#             self.orbit.right_ascension_from_sat_to_sun
#         ])
#
#     def get_spin_angles(self):
#         return np.array([
#             self.attitude.angle_between_sat_spin_and_z_axis,
#             self.attitude.angle_between_sat_spin_and_yz_plane
#         ])
#
#     def get_ellipsoid(self):
#         return np.array([
#             self.proj_params.earth_equatorial_radius,
#             self.proj_params.earth_flattening
#         ])
#
#     def get_sat_position(self):
#         return np.array((self.orbit.sat_position_earth_fixed_x,
#                          self.orbit.sat_position_earth_fixed_y,
#                          self.orbit.sat_position_earth_fixed_z))


OrbitPrediction = namedtuple(
    'OrbitPrediction',
    [
        'prediction_times',
        'greenwich_sidereal_time',
        'declination_from_sat_to_sun',
        'right_ascension_from_sat_to_sun',
        'sat_position_earth_fixed_x',
        'sat_position_earth_fixed_y',
        'sat_position_earth_fixed_z',
        'nutation_precession',
    ]
)


@numba.njit
def interpolate_orbit_prediction(orbit_prediction, observation_time):
    greenwich_sidereal_time = interpolate_angles(
        observation_time,
        orbit_prediction.prediction_times,
        orbit_prediction.greenwich_sidereal_time
    )
    declination_from_sat_to_sun = interpolate_angles(
        observation_time,
        orbit_prediction.prediction_times,
        orbit_prediction.declination_from_sat_to_sun
    )
    right_ascension_from_sat_to_sun = interpolate_angles(
        observation_time,
        orbit_prediction.prediction_times,
        orbit_prediction.right_ascension_from_sat_to_sun
    )
    sat_position_earth_fixed_x = interpolate_continuous(
        observation_time,
        orbit_prediction.prediction_times,
        orbit_prediction.sat_position_earth_fixed_x
    )
    sat_position_earth_fixed_y = interpolate_continuous(
        observation_time,
        orbit_prediction.prediction_times,
        orbit_prediction.sat_position_earth_fixed_y
    )
    sat_position_earth_fixed_z = interpolate_continuous(
        observation_time,
        orbit_prediction.prediction_times,
        orbit_prediction.sat_position_earth_fixed_z
    )
    nutation_precession = interpolate_nearest(
        observation_time,
        orbit_prediction.prediction_times,
        orbit_prediction.nutation_precession
    )
    return Orbit(
        greenwich_sidereal_time,
        declination_from_sat_to_sun,
        right_ascension_from_sat_to_sun,
        sat_position_earth_fixed_x,
        sat_position_earth_fixed_y,
        sat_position_earth_fixed_z,
        nutation_precession
    )


AttitudePrediction = namedtuple(
    'AttitudePrediction',
    [
        'prediction_times',
        'angle_between_earth_and_sun',
        'angle_between_sat_spin_and_z_axis',
        'angle_between_sat_spin_and_yz_plane',
    ]
)


@numba.njit
def interpolate_attitude_prediction(attitude_prediction, observation_time):
    angle_between_earth_and_sun = interpolate_angles(
        observation_time,
        attitude_prediction.prediction_times,
        attitude_prediction.angle_between_earth_and_sun
    )
    angle_between_sat_spin_and_z_axis = interpolate_angles(
        observation_time,
        attitude_prediction.prediction_times,
        attitude_prediction.angle_between_sat_spin_and_z_axis
    )
    angle_between_sat_spin_and_yz_plane = interpolate_angles(
        observation_time,
        attitude_prediction.prediction_times,
        attitude_prediction.angle_between_sat_spin_and_yz_plane
    )
    return Attitude(
        angle_between_earth_and_sun,
        angle_between_sat_spin_and_z_axis,
        angle_between_sat_spin_and_yz_plane
    )


# FIXME
# @numba.experimental.jitclass(
#     [
#         ('attitude_prediction', get_jitclass_type(AttitudePrediction)),
#         ('orbit_prediction', get_jitclass_type(OrbitPrediction)),
#         ('proj_params', get_jitclass_type(ProjectionParameters)),
#     ]
# )
# class PredictedNavigationParameters:
#     def __init__(self, attitude_prediction, orbit_prediction, proj_params):
#         self.attitude_prediction = attitude_prediction
#         self.orbit_prediction = orbit_prediction
#         self.proj_params = proj_params
#
#     def interpolate(self, observation_time):
#         attitude = self.attitude_prediction.interpolate(observation_time)
#         orbit = self.orbit_prediction.interpolate(observation_time)
#         return self._get_nav_params(attitude, orbit)
#
#     def _get_nav_params(self, attitude, orbit):
#         return NavigationParameters(attitude, orbit, self.proj_params)


@numba.njit
def interpolate_continuous(x, x_sample, y_sample):
    """Linear interpolation of continuous quantities.

    Numpy equivalent would be np.interp(..., left=np.nan, right=np.nan), but
    numba currently doesn't support those keyword arguments.
    """
    try:
        return _interpolate(x, x_sample, y_sample)
    except Exception:
        return np.nan


@numba.njit
def interpolate_angles(x, x_sample, y_sample):
    """Linear interpolation of periodic angles.

    In order to preserve the periodicity, change phase jumps greater than pi
    to their 2*pi complement, then perform interpolation and finally wrap
    the results to [-pi, pi].
    """
    try:
        return _wrap_2pi(_interpolate(x, x_sample, unwrap(y_sample)))
    except Exception:
        return np.nan


@numba.njit
def _interpolate(x, x_sample, y_sample):
    i = _find_enclosing_index(x, x_sample)
    offset = y_sample[i]
    x_diff = x_sample[i+1] - x_sample[i]
    y_diff = y_sample[i+1] - y_sample[i]
    slope = y_diff / x_diff
    dist = x - x_sample[i]
    return offset + slope * dist


@numba.njit
def _find_enclosing_index(x, x_sample):
    """Find where x_sample encloses x."""
    for i in range(len(x_sample) - 1):
        if x_sample[i] <= x < x_sample[i+1]:
            return i
    raise Exception('x not enclosed by x_sample')


@numba.njit
def _wrap_2pi(values):
    """Wrap values to interval [-pi, pi].

    Source: https://stackoverflow.com/a/15927914/5703449
    """
    return (values + np.pi) % (2 * np.pi) - np.pi


@numba.njit
def interpolate_nearest(x, x_sample, y_sample):
    """Nearest neighbour interpolation."""
    try:
        return _interpolate_nearest(x, x_sample, y_sample)
    except Exception:
        return np.nan * np.ones_like(y_sample[0])


@numba.njit
def _interpolate_nearest(x, x_sample, y_sample):
    i = _find_enclosing_index(x, x_sample)
    return y_sample[i]


@numba.njit
def unwrap(p, discont=np.pi):
    """Numba implementation of np.unwrap in one dimension."""
    p = np.ascontiguousarray(p)
    dd = np.diff(p)
    slice1 = slice(1, None)
    ddmod = np.mod(dd + np.pi, 2*np.pi) - np.pi
    ddmod = np.where((ddmod == -np.pi) & (dd > 0), np.pi, ddmod)
    ph_correct = ddmod - dd
    ph_correct = np.where(np.fabs(dd) < discont, 0, ph_correct)
    up = p.copy()
    up[slice1] = p[slice1] + ph_correct.cumsum()
    return up
