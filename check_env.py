import geo_common as common
from osgeo import gdal
import geopandas, mercantile, PIL, numpy, requests
print('GDAL',gdal.VersionInfo())
print('City features',len(common.cities(common.load_config())))
print('Environment OK; cloud authentication and network are checked separately.')
