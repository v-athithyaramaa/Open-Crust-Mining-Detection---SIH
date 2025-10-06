"""
Preprocessing Module for Satellite Data
Handles reprojection, clipping, alignment, and normalization
"""

import rasterio
import numpy as np
import logging
from typing import Dict, List, Optional, Tuple
import geopandas as gpd
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.mask import mask
from rasterio.enums import Resampling as RasterioResampling
import os
from scipy import ndimage
from scipy.interpolate import griddata

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Preprocessor:
    """Preprocessing utilities for satellite data"""
    
    def __init__(self):
        """Initialize preprocessor"""
        self.target_crs = "EPSG:4326"  # WGS84
        self.target_resolution = 10  # meters for Sentinel-2
        
        logger.info("Preprocessing module initialized")
    
    def reproject_raster(self, src_path: str, dst_path: str, 
                        dst_crs: str = "EPSG:4326", 
                        resampling: str = "bilinear") -> bool:
        """
        Reproject raster to target CRS
        
        Args:
            src_path: Source raster path
            dst_path: Destination raster path
            dst_crs: Target CRS
            resampling: Resampling method
            
        Returns:
            bool: Success status
        """
        try:
            logger.info(f"🔄 Reprojecting {src_path} to {dst_crs}")
            
            with rasterio.open(src_path) as src:
                # Calculate transform and dimensions for target CRS
                transform, width, height = calculate_default_transform(
                    src.crs, dst_crs, src.width, src.height, *src.bounds
                )
                
                # Get resampling method
                resample_method = getattr(Resampling, resampling)
                
                # Create destination dataset
                dst_kwargs = src.profile.copy()
                dst_kwargs.update({
                    'crs': dst_crs,
                    'transform': transform,
                    'width': width,
                    'height': height
                })
                
                with rasterio.open(dst_path, 'w', **dst_kwargs) as dst:
                    for i in range(1, src.count + 1):
                        reproject(
                            source=rasterio.band(src, i),
                            destination=rasterio.band(dst, i),
                            src_transform=src.transform,
                            src_crs=src.crs,
                            dst_transform=transform,
                            dst_crs=dst_crs,
                            resampling=resample_method
                        )
            
            logger.info(f"✅ Reprojection complete: {dst_path}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error reprojecting raster: {e}")
            return False
    
    def clip_raster_by_shape(self, raster_path: str, shapefile_path: str, 
                           dst_path: str, crop: bool = True) -> bool:
        """
        Clip raster by shapefile/GeoJSON
        
        Args:
            raster_path: Input raster path
            shapefile_path: Shapefile/GeoJSON path
            dst_path: Output raster path
            crop: Whether to crop to shape bounds
            
        Returns:
            bool: Success status
        """
        try:
            logger.info(f"✂️ Clipping {raster_path} by {shapefile_path}")
            
            # Read shapefile
            if shapefile_path.endswith('.geojson'):
                shapes = gpd.read_file(shapefile_path)
            else:
                shapes = gpd.read_file(shapefile_path)
            
            # Ensure CRS match
            with rasterio.open(raster_path) as src:
                if shapes.crs != src.crs:
                    shapes = shapes.to_crs(src.crs)
                
                # Get shapes as list of geometries
                shapes_list = [geom for geom in shapes.geometry]
                
                # Clip raster
                clipped_data, clipped_transform = mask(src, shapes_list, crop=crop)
                
                # Update metadata
                clipped_meta = src.profile.copy()
                clipped_meta.update({
                    'height': clipped_data.shape[1],
                    'width': clipped_data.shape[2],
                    'transform': clipped_transform
                })
                
                # Write clipped raster
                with rasterio.open(dst_path, 'w', **clipped_meta) as dst:
                    dst.write(clipped_data)
            
            logger.info(f"✅ Clipping complete: {dst_path}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error clipping raster: {e}")
            return False
    
    def match_rasters(self, raster_base: str, raster_to_match: str, 
                     out_matched: str, resampling: str = "bilinear") -> bool:
        """
        Match one raster to another's grid (resolution, extent, CRS)
        
        Args:
            raster_base: Reference raster
            raster_to_match: Raster to be matched
            out_matched: Output matched raster
            resampling: Resampling method
            
        Returns:
            bool: Success status
        """
        try:
            logger.info(f"🎯 Matching {raster_to_match} to {raster_base}")
            
            with rasterio.open(raster_base) as base:
                with rasterio.open(raster_to_match) as to_match:
                    # Get resampling method
                    resample_method = getattr(Resampling, resampling)
                    
                    # Create destination dataset matching base raster
                    dst_kwargs = to_match.meta.copy()
                    dst_kwargs.update({
                        'crs': base.crs,
                        'transform': base.transform,
                        'width': base.width,
                        'height': base.height
                    })
                    
                    with rasterio.open(out_matched, 'w', **dst_kwargs) as dst:
                        for i in range(1, to_match.count + 1):
                            reproject(
                                source=rasterio.band(to_match, i),
                                destination=rasterio.band(dst, i),
                                src_transform=to_match.transform,
                                src_crs=to_match.crs,
                                dst_transform=base.transform,
                                dst_crs=base.crs,
                                resampling=resample_method
                            )
            
            logger.info(f"✅ Raster matching complete: {out_matched}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error matching rasters: {e}")
            return False
    
    def normalize_bands(self, raster_path: str, dst_path: str, 
                       scale_factor: float = 10000) -> bool:
        """
        Normalize bands to 0-1 range
        
        Args:
            raster_path: Input raster path
            dst_path: Output raster path
            scale_factor: Scale factor for normalization
            
        Returns:
            bool: Success status
        """
        try:
            logger.info(f"📊 Normalizing bands in {raster_path}")
            
            with rasterio.open(raster_path) as src:
                # Read all bands
                data = src.read().astype(np.float32)
                
                # Normalize each band
                normalized_data = np.zeros_like(data)
                for i in range(data.shape[0]):
                    band = data[i]
                    # Handle nodata values
                    valid_mask = band != src.nodata if src.nodata else np.ones_like(band, dtype=bool)
                    
                    if np.any(valid_mask):
                        # Normalize to 0-1 range
                        band_min = np.percentile(band[valid_mask], 2)  # 2nd percentile
                        band_max = np.percentile(band[valid_mask], 98)  # 98th percentile
                        
                        if band_max > band_min:
                            normalized_band = (band - band_min) / (band_max - band_min)
                            normalized_band = np.clip(normalized_band, 0, 1)
                        else:
                            normalized_band = band / scale_factor
                        
                        normalized_data[i] = normalized_band
                    else:
                        normalized_data[i] = band / scale_factor
                
                # Write normalized raster
                dst_kwargs = src.profile.copy()
                dst_kwargs.update({'dtype': rasterio.float32, 'nodata': None})
                
                with rasterio.open(dst_path, 'w', **dst_kwargs) as dst:
                    dst.write(normalized_data)
            
            logger.info(f"✅ Band normalization complete: {dst_path}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error normalizing bands: {e}")
            return False
    
    def fill_dem_voids(self, dem_path: str, dst_path: str, 
                      method: str = "gdal") -> bool:
        """
        Fill voids in DEM using interpolation
        
        Args:
            dem_path: Input DEM path
            dst_path: Output DEM path
            method: Interpolation method ("gdal" or "scipy")
            
        Returns:
            bool: Success status
        """
        try:
            logger.info(f"🕳️ Filling DEM voids in {dem_path}")
            
            with rasterio.open(dem_path) as src:
                dem_data = src.read(1).astype(np.float32)
                
                if method == "gdal":
                    # Use GDAL's fill nodata algorithm
                    filled_data = self._gdal_fill_nodata(dem_data, src.nodata)
                else:
                    # Use scipy interpolation
                    filled_data = self._scipy_fill_nodata(dem_data, src.nodata)
                
                # Write filled DEM
                dst_kwargs = src.profile.copy()
                dst_kwargs.update({'dtype': rasterio.float32})
                
                with rasterio.open(dst_path, 'w', **dst_kwargs) as dst:
                    dst.write(filled_data, 1)
            
            logger.info(f"✅ DEM void filling complete: {dst_path}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error filling DEM voids: {e}")
            return False
    
    def _gdal_fill_nodata(self, data: np.ndarray, nodata: float) -> np.ndarray:
        """Fill nodata using GDAL-like algorithm"""
        try:
            # Create mask for valid data
            if nodata is not None:
                valid_mask = data != nodata
            else:
                valid_mask = ~np.isnan(data)
            
            if np.all(valid_mask):
                return data
            
            # Use scipy's binary dilation to expand valid areas
            from scipy.ndimage import binary_dilation
            
            # Dilate valid areas
            dilated_mask = binary_dilation(valid_mask, iterations=3)
            
            # Interpolate using griddata
            y, x = np.mgrid[0:data.shape[0], 0:data.shape[1]]
            points = np.column_stack((y[valid_mask], x[valid_mask]))
            values = data[valid_mask]
            
            # Create grid for interpolation
            grid_y, grid_x = np.mgrid[0:data.shape[0], 0:data.shape[1]]
            grid_points = np.column_stack((grid_y.ravel(), grid_x.ravel()))
            
            # Interpolate
            filled_values = griddata(points, values, grid_points, method='linear')
            filled_data = filled_values.reshape(data.shape)
            
            # Fill remaining holes with nearest neighbor
            filled_data = griddata(points, values, grid_points, method='nearest')
            filled_data = filled_data.reshape(data.shape)
            
            return filled_data
            
        except Exception as e:
            logger.warning(f"⚠️ GDAL fill failed, using simple interpolation: {e}")
            return self._scipy_fill_nodata(data, nodata)
    
    def _scipy_fill_nodata(self, data: np.ndarray, nodata: float) -> np.ndarray:
        """Fill nodata using scipy interpolation"""
        try:
            # Create mask for valid data
            if nodata is not None:
                valid_mask = data != nodata
            else:
                valid_mask = ~np.isnan(data)
            
            if np.all(valid_mask):
                return data
            
            # Get coordinates of valid and invalid points
            y, x = np.mgrid[0:data.shape[0], 0:data.shape[1]]
            valid_points = np.column_stack((y[valid_mask], x[valid_mask]))
            valid_values = data[valid_mask]
            invalid_points = np.column_stack((y[~valid_mask], x[~valid_mask]))
            
            if len(invalid_points) == 0:
                return data
            
            # Interpolate invalid points
            filled_values = griddata(valid_points, valid_values, invalid_points, 
                                   method='linear', fill_value=np.nan)
            
            # Handle remaining NaN values with nearest neighbor
            nan_mask = np.isnan(filled_values)
            if np.any(nan_mask):
                nearest_values = griddata(valid_points, valid_values, 
                                        invalid_points[nan_mask], method='nearest')
                filled_values[nan_mask] = nearest_values
            
            # Create filled data array
            filled_data = data.copy()
            filled_data[~valid_mask] = filled_values
            
            return filled_data
            
        except Exception as e:
            logger.error(f"❌ Error in scipy interpolation: {e}")
            return data
    
    def smooth_dem(self, dem_path: str, dst_path: str, 
                   sigma: float = 1.0) -> bool:
        """
        Smooth DEM to reduce noise
        
        Args:
            dem_path: Input DEM path
            dst_path: Output DEM path
            sigma: Gaussian smoothing sigma
            
        Returns:
            bool: Success status
        """
        try:
            logger.info(f"🌊 Smoothing DEM: {dem_path}")
            
            with rasterio.open(dem_path) as src:
                dem_data = src.read(1).astype(np.float32)
                
                # Apply Gaussian smoothing
                smoothed_data = ndimage.gaussian_filter(dem_data, sigma=sigma)
                
                # Write smoothed DEM
                dst_kwargs = src.profile.copy()
                dst_kwargs.update({'dtype': rasterio.float32})
                
                with rasterio.open(dst_path, 'w', **dst_kwargs) as dst:
                    dst.write(smoothed_data, 1)
            
            logger.info(f"✅ DEM smoothing complete: {dst_path}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error smoothing DEM: {e}")
            return False
    
    def align_rasters(self, raster_paths: List[str], output_dir: str, 
                     reference_raster: str = None) -> List[str]:
        """
        Align multiple rasters to same grid
        
        Args:
            raster_paths: List of raster paths to align
            output_dir: Output directory
            reference_raster: Reference raster (if None, use first raster)
            
        Returns:
            List[str]: List of aligned raster paths
        """
        try:
            logger.info(f"🎯 Aligning {len(raster_paths)} rasters")
            
            if reference_raster is None:
                reference_raster = raster_paths[0]
            
            aligned_paths = []
            
            for i, raster_path in enumerate(raster_paths):
                if raster_path == reference_raster:
                    # Copy reference raster
                    aligned_path = os.path.join(output_dir, f"aligned_{i}.tif")
                    with rasterio.open(raster_path) as src:
                        with rasterio.open(aligned_path, 'w', **src.meta) as dst:
                            dst.write(src.read())
                    aligned_paths.append(aligned_path)
                else:
                    # Align to reference
                    aligned_path = os.path.join(output_dir, f"aligned_{i}.tif")
                    success = self.match_rasters(reference_raster, raster_path, aligned_path)
                    if success:
                        aligned_paths.append(aligned_path)
                    else:
                        logger.error(f"❌ Failed to align {raster_path}")
            
            logger.info(f"✅ Raster alignment complete: {len(aligned_paths)} rasters")
            return aligned_paths
            
        except Exception as e:
            logger.error(f"❌ Error aligning rasters: {e}")
            return []

# Standalone functions for easy integration
def reproject_raster(src_path: str, dst_path: str, 
                    dst_crs: str = "EPSG:4326", 
                    resampling: str = "bilinear") -> bool:
    """Reproject raster to target CRS"""
    preprocessor = Preprocessor()
    return preprocessor.reproject_raster(src_path, dst_path, dst_crs, resampling)

def clip_raster_by_shape(raster_path: str, shapefile_path: str, 
                        dst_path: str, crop: bool = True) -> bool:
    """Clip raster by shapefile/GeoJSON"""
    preprocessor = Preprocessor()
    return preprocessor.clip_raster_by_shape(raster_path, shapefile_path, dst_path, crop)

def match_rasters(raster_base: str, raster_to_match: str, 
                 out_matched: str, resampling: str = "bilinear") -> bool:
    """Match one raster to another's grid"""
    preprocessor = Preprocessor()
    return preprocessor.match_rasters(raster_base, raster_to_match, out_matched, resampling)

def normalize_bands(raster_path: str, dst_path: str, 
                   scale_factor: float = 10000) -> bool:
    """Normalize bands to 0-1 range"""
    preprocessor = Preprocessor()
    return preprocessor.normalize_bands(raster_path, dst_path, scale_factor)

def fill_dem_voids(dem_path: str, dst_path: str, 
                  method: str = "gdal") -> bool:
    """Fill voids in DEM"""
    preprocessor = Preprocessor()
    return preprocessor.fill_dem_voids(dem_path, dst_path, method)

if __name__ == "__main__":
    # Test preprocessing functions
    print("🧪 Testing preprocessing functions...")
    
    # Create test data
    test_aoi = {
        "type": "Polygon",
        "coordinates": [[
            [76.0, 15.0], [77.0, 15.0], [77.0, 16.0], [76.0, 16.0], [76.0, 15.0]
        ]]
    }
    
    # Test with demo data
    from gee_utils import GEEUtils
    gee_utils = GEEUtils()
    
    # Create test Sentinel-2 data
    sentinel2_path = "test_sentinel2.tif"
    gee_utils.download_sentinel2_aoi(test_aoi, "2024-01-01", "2024-01-31", sentinel2_path)
    
    # Test preprocessing
    preprocessor = Preprocessor()
    
    # Test normalization
    normalized_path = "test_sentinel2_normalized.tif"
    success = preprocessor.normalize_bands(sentinel2_path, normalized_path)
    print(f"Normalization: {'✅ Success' if success else '❌ Failed'}")
    
    # Test DEM processing
    dem_path = "test_dem.tif"
    gee_utils.download_dem(test_aoi, dem_path, "SRTM")
    
    filled_dem_path = "test_dem_filled.tif"
    success = preprocessor.fill_dem_voids(dem_path, filled_dem_path)
    print(f"DEM void filling: {'✅ Success' if success else '❌ Failed'}")
    
    smoothed_dem_path = "test_dem_smoothed.tif"
    success = preprocessor.smooth_dem(filled_dem_path, smoothed_dem_path)
    print(f"DEM smoothing: {'✅ Success' if success else '❌ Failed'}")
