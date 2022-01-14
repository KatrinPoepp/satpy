#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2017-2019 Satpy developers
#
# This file is part of satpy.
#
# satpy is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# satpy is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# satpy.  If not, see <http://www.gnu.org/licenses/>.
"""Unittesting the SEVIRI L2 BUFR reader."""

import sys
import unittest
from datetime import datetime
from unittest import mock

import numpy as np
from pyresample import geometry

from satpy.tests.utils import make_dataid

FILETYPE_INFO = {'file_type':  'seviri_l2_bufr_asr'}

FILENAME_INFO = {'start_time': '20191112000000',
                 'spacecraft': 'MSG1'}
FILENAME_INFO2 = {'start_time': '20191112000000',
                  'spacecraft': 'MSG1',
                  'server': 'TESTSERVER'}
MPEF_PRODUCT_HEADER = {
    'NominalTime': datetime(2019, 11, 6, 18, 0),
    'SpacecraftName': '08',
    'RectificationLongitude': 'E0415'
}

DATASET_INFO = {
    'name': 'testdata',
    'key': '#1#brightnessTemperature',
    'coordinates': ('longitude', 'latitude'),
    'fill_value': 0
}

DATASET_INFO_LAT = {
    'name': 'latitude',
    'key': 'latitude',
    'fill_value': -1.e+100
}

DATASET_INFO_LON = {
    'name': 'longitude',
    'key': 'longitude',
    'fill_value': -1.e+100
}


DATASET_ATTRS = {
    'platform_name': 'MET08',
    'ssp_lon': 41.5,
    'seg_size': 16
}

AREA_DEF = geometry.AreaDefinition(
    'msg_seviri_iodc_48km',
    'MSG SEVIRI Indian Ocean Data Coverage service area definition with 48 km resolution',
    "",
    {'a': 6378169., 'b': 6356583.8, 'lon_0': DATASET_ATTRS['ssp_lon'],
     'h': 35785831., 'proj': 'geos', 'units': 'm'},
    232,
    232,
    (-5567248.0742, -5567248.0742, 5567248.0742, 5567248.0742)
)


AREA_DEF_EXT = geometry.AreaDefinition(
    'msg_seviri_iodc_48km_ext',
    'MSG SEVIRI Indian Ocean Data Coverage service area definition with 48 km resolution '
    '(extended outside original 3km grid)',
    "",
    {'a': 6378169., 'b': 6356583.8, 'lon_0': DATASET_ATTRS['ssp_lon'],
     'h': 35785831., 'proj': 'geos', 'units': 'm'},
    1238,
    1238,
    (-5576249.283671378, -5567248.074173927, 5567248.074173927, 5576249.283671378)
)


class TestSeviriL2Bufr(unittest.TestCase):
    """Test NativeMSGBufrHandler."""

    @unittest.skipIf(sys.platform.startswith('win'), "'eccodes' not supported on Windows")
    def seviri_l2_bufr_test(self, filename, as_area_def=False):
        """Test the SEVIRI BUFR handler."""
        import eccodes as ec

        from satpy.readers.seviri_l2_bufr import SeviriL2BufrFileHandler
        buf1 = ec.codes_bufr_new_from_samples('BUFR4_local_satellite')
        ec.codes_set(buf1, 'unpack', 1)
        samp1 = np.random.uniform(low=250, high=350, size=(128,))
        lat = np.random.uniform(low=-20, high=20, size=(128,))
        lon = np.random.uniform(low=-20, high=20, size=(128,))
        # write the bufr test data twice as we want to read in and the concatenate the data in the reader
        # 55 id corresponds to METEOSAT 8
        ec.codes_set(buf1, 'satelliteIdentifier', 55)
        ec.codes_set_array(buf1, 'latitude', lat)
        ec.codes_set_array(buf1, 'latitude', lat)
        ec.codes_set_array(buf1, 'longitude', lon)
        ec.codes_set_array(buf1, 'longitude', lon)
        ec.codes_set_array(buf1, '#1#brightnessTemperature', samp1)
        ec.codes_set_array(buf1, '#1#brightnessTemperature', samp1)

        m = mock.mock_open()
        # only our offline product contain MPEF product headers so we get the metadata from there
        if ('BUFRProd' in filename):
            with mock.patch('satpy.readers.seviri_l2_bufr.np.fromfile') as fromfile:
                fromfile.return_value = MPEF_PRODUCT_HEADER
                with mock.patch('satpy.readers.seviri_l2_bufr.recarray2dict') as recarray2dict:
                    recarray2dict.side_effect = (lambda x: x)
                    fh = SeviriL2BufrFileHandler(filename, FILENAME_INFO2, FILETYPE_INFO, as_area_def=as_area_def)
                    fh.mpef_header = MPEF_PRODUCT_HEADER

        else:
            # No Mpef Header  so we get the metadata from the BUFR messages
            with mock.patch('satpy.readers.seviri_l2_bufr.open', m, create=True):
                with mock.patch('eccodes.codes_bufr_new_from_file',
                                side_effect=[buf1, None, buf1, None, buf1, None]) as ec1:
                    ec1.return_value = ec1.side_effect
                    with mock.patch('eccodes.codes_set') as ec2:
                        ec2.return_value = 1
                        with mock.patch('eccodes.codes_release') as ec5:
                            ec5.return_value = 1
                            fh = SeviriL2BufrFileHandler(filename, FILENAME_INFO, FILETYPE_INFO,
                                                         as_area_def=as_area_def)

        # Test reading latitude/longitude (needed to test AreaDefintiion implementation)
        zlat = self.read_data(buf1, m, fh, DATASET_INFO_LAT)
        zlon = self.read_data(buf1, m, fh, DATASET_INFO_LON)
        np.testing.assert_array_equal(zlat.values, np.concatenate((lat, lat), axis=0))
        np.testing.assert_array_equal(zlon.values, np.concatenate((lon, lon), axis=0))

        # Test reading dataset
        z = self.read_data(buf1, m, fh, DATASET_INFO)

        # Test dataset attributes
        self.assertEqual(z.attrs['platform_name'],
                         DATASET_ATTRS['platform_name'])
        self.assertEqual(z.attrs['ssp_lon'],
                         DATASET_ATTRS['ssp_lon'])
        self.assertEqual(z.attrs['seg_size'],
                         DATASET_ATTRS['seg_size'])

        # Test dataset with SwathDefintion and AreaDefinition, respectively
        if not fh.as_area_def:
            self.as_swath_definition(fh, z, samp1)
        else:
            self.as_area_definition(fh, z, samp1)

    @staticmethod
    def read_data(buf1, m, fh, dataset_info):
        """Read data from mock file."""
        with mock.patch('satpy.readers.seviri_l2_bufr.open', m, create=True):
            with mock.patch('eccodes.codes_bufr_new_from_file',
                            side_effect=[buf1, buf1, None]) as ec1:
                ec1.return_value = ec1.side_effect
                with mock.patch('eccodes.codes_set') as ec2:
                    ec2.return_value = 1
                    with mock.patch('eccodes.codes_release') as ec5:
                        ec5.return_value = 1
                        z = fh.get_dataset(make_dataid(name='dummmy', resolution=48000), dataset_info)

        return z

    def as_swath_definition(self, fh, z, samp1):
        """Perform checks if data loaded as swath definition."""
        # With swath definition there will be no AreaDefinition implemented
        self.assertRaises(NotImplementedError, fh.get_area_def, None)
        # concatenate original test arrays as get_dataset will have read and concatented the data
        x1 = np.concatenate((samp1, samp1), axis=0)
        np.testing.assert_array_equal(z.values, x1)

    def as_area_definition(self, fh, z, samp1):
        """Perform checks if data loaded as AreaDefinition."""
        ad = fh.get_area_def(None)
        self.assertEqual(ad, AREA_DEF)

        # Put BUFR data on 2D grid that the 2D array returned by get_dataset should correspond to
        icol, irow = ad.get_array_coordinates_from_lonlat(fh.longitude.compute(),
                                                          fh.latitude.compute())
        icol, irow = np.ceil(icol).astype(int), np.ceil(irow).astype(int)

        data = np.empty(ad.shape)
        data[:] = np.nan
        data[irow, icol] = np.concatenate((samp1, samp1), axis=0)
        np.testing.assert_array_equal(z.values, data)

        # Test that the correct AreaDefinition is identified for products with 3 pixel segements
        fh.seg_size = 3
        ad_ext = fh._construct_area_def(make_dataid(name='dummmy', resolution=9000))
        self.assertEqual(ad_ext, AREA_DEF_EXT)

    def test_seviri_l2_bufr(self):
        """Call the test function."""
        self.seviri_l2_bufr_test('ASRBUFRProd_20191106130000Z_00_OMPEFS01_MET08_FES_E0000')
        self.seviri_l2_bufr_test('MSG1-SEVI-MSGASRE-0101-0101-20191106130000.000000000Z-20191106131702-1362128.bfr')
        self.seviri_l2_bufr_test('MSG1-SEVI-MSGASRE-0101-0101-20191106101500.000000000Z-20191106103218-1362148')
        self.seviri_l2_bufr_test('ASRBUFRProd_20191106130000Z_00_OMPEFS01_MET08_FES_E0000',
                                 as_area_def=True)
        self.seviri_l2_bufr_test('MSG1-SEVI-MSGASRE-0101-0101-20191106130000.000000000Z-20191106131702-1362128.bfr',
                                 as_area_def=True)
        self.seviri_l2_bufr_test('MSG1-SEVI-MSGASRE-0101-0101-20191106101500.000000000Z-20191106103218-1362148',
                                 as_area_def=True)
