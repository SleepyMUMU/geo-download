from osgeo import gdal
import mercantile
import shapely
import geopandas
import requests
import yaml

print("=" * 40)
print("  环境验证通过!")
print("=" * 40)
print(f"  GDAL:       {gdal.__version__}")
print(f"  Shapely:    {shapely.__version__}")
print(f"  GeoPandas:  {geopandas.__version__}")
print(f"  Mercantile: {mercantile.__version__}")
print(f"  Requests:   {requests.__version__}")
print(f"  PyYAML:     {yaml.__version__}")
print("=" * 40)
