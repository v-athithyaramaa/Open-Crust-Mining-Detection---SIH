"""
Mining Detection using Spectral Indices
Real spectral analysis for detecting mining activities
"""

import rasterio
import numpy as np
import logging
from typing import Dict, List, Optional, Tuple
import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon, shape
from shapely.ops import unary_union
from rasterio.features import shapes as rio_shapes
from scipy import ndimage
from skimage.morphology import remove_small_objects, binary_opening, binary_closing
from skimage.measure import label, regionprops
import json
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MiningDetector:
    """Mining detection using spectral indices"""
    
    def __init__(self):
        """Initialize mining detector with default parameters"""
        # Spectral index thresholds for mining detection
        self.thresholds = {
            'ndvi': 0.2,      # Below this = non-vegetated (potential mining)
            'bsi': 0.3,       # Above this = bare soil (potential mining)
            'ndbi': 0.1,      # Above this = built-up/bare areas
            'ndwi': 0.2,      # Below this = no water (dry areas)
            'min_area_ha': 0.001,  # Minimum area in hectares (0.001 ha = 10 m²)
            'max_area_ha': 1000, # Maximum area in hectares
        }
        
        # Band indices for Sentinel-2 (0-based)
        self.sentinel2_bands = {
            'B2': 0,  # Blue
            'B3': 1,  # Green
            'B4': 2,  # Red
            'B8': 3,  # NIR
            'B11': 4, # SWIR1
            'B12': 5  # SWIR2
        }
        
        logger.info("Mining detector initialized with spectral thresholds")
    
    def generate_mask(self, raster_path: str, output_path: str = None) -> Dict:
        """
        Generate mining detection mask using spectral indices
        
        Args:
            raster_path: Path to multiband Sentinel-2 raster
            output_path: Optional output path for mask raster
            
        Returns:
            Dict: Detection results with mask and statistics
        """
        try:
            logger.info(f"🔍 Analyzing {raster_path} for mining activities")
            
            with rasterio.open(raster_path) as src:
                # Read bands
                blue = src.read(self.sentinel2_bands['B2'] + 1).astype(np.float32)
                green = src.read(self.sentinel2_bands['B3'] + 1).astype(np.float32)
                red = src.read(self.sentinel2_bands['B4'] + 1).astype(np.float32)
                nir = src.read(self.sentinel2_bands['B8'] + 1).astype(np.float32)
                swir1 = src.read(self.sentinel2_bands['B11'] + 1).astype(np.float32)
                swir2 = src.read(self.sentinel2_bands['B12'] + 1).astype(np.float32)
                
                # Calculate spectral indices
                indices = self._calculate_spectral_indices(blue, green, red, nir, swir1, swir2)
                
                # Generate mining mask
                mining_mask = self._create_mining_mask(indices)
                
                # Apply morphological operations
                cleaned_mask = self._clean_mask(mining_mask)
                
                # Apply additional morphological operations to connect nearby pixels
                from scipy.ndimage import binary_dilation, binary_erosion
                # Dilate to connect nearby pixels
                cleaned_mask = binary_dilation(cleaned_mask, structure=np.ones((3, 3)))
                # Erode back to original size
                cleaned_mask = binary_erosion(cleaned_mask, structure=np.ones((2, 2)))
                
                # Save mask if output path provided
                if output_path:
                    self._save_mask(cleaned_mask, src, output_path)
                
                # Calculate statistics
                stats = self._calculate_mask_statistics(cleaned_mask, src)
                
                logger.info(f"✅ Mining detection complete: {stats['total_pixels']} pixels detected")
                
                return {
                    'mask': cleaned_mask,
                    'indices': indices,
                    'statistics': stats,
                    'transform': src.transform,
                    'crs': src.crs,
                    'bounds': src.bounds
                }
                
        except Exception as e:
            logger.error(f"❌ Error in mining detection: {e}")
            return {}
    
    def _calculate_spectral_indices(self, blue: np.ndarray, green: np.ndarray, 
                                  red: np.ndarray, nir: np.ndarray, 
                                  swir1: np.ndarray, swir2: np.ndarray) -> Dict:
        """Calculate spectral indices for mining detection"""
        
        # Avoid division by zero
        epsilon = 1e-8
        
        # NDVI (Normalized Difference Vegetation Index)
        ndvi = (nir - red) / (nir + red + epsilon)
        
        # BSI (Bare Soil Index)
        bsi = ((swir1 + red) - (nir + blue)) / ((swir1 + red) + (nir + blue) + epsilon)
        
        # NDBI (Normalized Difference Built-up Index)
        ndbi = (swir1 - nir) / (swir1 + nir + epsilon)
        
        # NDWI (Normalized Difference Water Index)
        ndwi = (green - nir) / (green + nir + epsilon)
        
        # Additional indices for mining detection
        
        # MNDWI (Modified NDWI) - better for water detection
        mndwi = (green - swir1) / (green + swir1 + epsilon)
        
        # SAVI (Soil Adjusted Vegetation Index) - accounts for soil background
        l = 0.5  # Soil adjustment factor
        savi = ((nir - red) / (nir + red + l)) * (1 + l)
        
        # EVI (Enhanced Vegetation Index) - improved vegetation detection
        evi = 2.5 * ((nir - red) / (nir + 6 * red - 7.5 * blue + 1 + epsilon))
        
        # NBR (Normalized Burn Ratio) - useful for disturbance detection
        nbr = (nir - swir2) / (nir + swir2 + epsilon)
        
        return {
            'ndvi': ndvi,
            'bsi': bsi,
            'ndbi': ndbi,
            'ndwi': ndwi,
            'mndwi': mndwi,
            'savi': savi,
            'evi': evi,
            'nbr': nbr
        }
    
    def _create_mining_mask(self, indices: Dict) -> np.ndarray:
        """Create mining detection mask using spectral indices"""
        
        # Primary mining detection criteria
        # Mining areas typically have:
        # - Low vegetation (low NDVI)
        # - High bare soil (high BSI)
        # - Low water content (low NDWI)
        # - High built-up/bare areas (high NDBI)
        
        # Create individual masks
        low_vegetation = indices['ndvi'] < self.thresholds['ndvi']
        high_bare_soil = indices['bsi'] > self.thresholds['bsi']
        low_water = indices['ndwi'] < self.thresholds['ndwi']
        high_builtup = indices['ndbi'] > self.thresholds['ndbi']
        
        # Additional criteria for better detection
        low_savi = indices['savi'] < 0.1  # Low soil-adjusted vegetation
        low_evi = indices['evi'] < 0.1    # Low enhanced vegetation
        disturbed_nbr = indices['nbr'] < 0.1  # Disturbed areas (low NBR)
        
        # Combine criteria (at least 3 out of 7 conditions must be met)
        conditions = np.stack([
            low_vegetation,
            high_bare_soil,
            low_water,
            high_builtup,
            low_savi,
            low_evi,
            disturbed_nbr
        ], axis=0)
        
        # Count how many conditions are met for each pixel
        condition_count = np.sum(conditions, axis=0)
        
        # Mining mask: at least 4 conditions met
        mining_mask = condition_count >= 4
        
        # Additional filtering based on index combinations
        # Mining areas should have specific spectral signatures
        mining_signature = (
            (indices['ndvi'] < 0.15) &  # Very low vegetation
            (indices['bsi'] > 0.4) &    # High bare soil
            (indices['ndbi'] > 0.2)     # High built-up index
        )
        
        # Combine masks
        final_mask = mining_mask | mining_signature
        
        return final_mask.astype(np.uint8)
    
    def _clean_mask(self, mask: np.ndarray) -> np.ndarray:
        """Clean mining mask using morphological operations"""
        
        # Remove small objects (noise)
        min_size = 50  # pixels
        cleaned = remove_small_objects(mask.astype(bool), min_size=min_size)
        
        # Apply morphological opening (remove small holes)
        cleaned = binary_opening(cleaned, footprint=np.ones((3, 3)))
        
        # Apply morphological closing (fill small gaps)
        cleaned = binary_closing(cleaned, footprint=np.ones((5, 5)))
        
        # Remove small objects again after morphological operations
        cleaned = remove_small_objects(cleaned, min_size=min_size)
        
        return cleaned.astype(np.uint8)
    
    def _save_mask(self, mask: np.ndarray, src: rasterio.DatasetReader, 
                   output_path: str):
        """Save mining mask as GeoTIFF"""
        
        profile = src.profile.copy()
        profile.update({
            'dtype': rasterio.uint8,
            'count': 1,
            'nodata': 0
        })
        
        with rasterio.open(output_path, 'w', **profile) as dst:
            dst.write(mask, 1)
            dst.set_band_description(1, 'Mining Detection Mask')
    
    def _calculate_mask_statistics(self, mask: np.ndarray, src: rasterio.DatasetReader) -> Dict:
        """Calculate statistics for mining mask"""
        
        # Calculate pixel area in square meters
        pixel_area_m2 = abs(src.transform.a * src.transform.e)
        
        # Count mining pixels
        mining_pixels = np.sum(mask)
        total_pixels = mask.size
        
        # Calculate area in hectares
        mining_area_ha = (mining_pixels * pixel_area_m2) / 10000
        
        # Calculate percentage
        mining_percentage = (mining_pixels / total_pixels) * 100
        
        return {
            'total_pixels': int(mining_pixels),
            'total_area_ha': round(mining_area_ha, 2),
            'mining_percentage': round(mining_percentage, 2),
            'pixel_area_m2': pixel_area_m2
        }
    
    def polygonize_mask(self, mask: np.ndarray, transform: rasterio.transform.Affine, 
                       crs: str, min_area_ha: float = None) -> gpd.GeoDataFrame:
        """
        Convert mining mask to polygons
        
        Args:
            mask: Binary mining mask
            transform: Raster transform
            crs: Coordinate reference system
            min_area_ha: Minimum area threshold in hectares
            
        Returns:
            gpd.GeoDataFrame: Mining polygons
        """
        try:
            logger.info("🔷 Converting mining mask to polygons")
            
            if min_area_ha is None:
                min_area_ha = self.thresholds['min_area_ha']
            
            # Polygonize directly from mask using rasterio.features.shapes
            # This is more robust than regionprops->coords hulls and preserves topology
            mask_bool = mask.astype(bool)
            polygons = []
            properties = []

            # Generate GeoJSON-like shapes
            for geom, value in rio_shapes(mask.astype(np.uint8), mask=mask_bool, transform=transform):
                if int(value) != 1:
                    continue
                poly = shape(geom)
                if not poly.is_valid or poly.is_empty:
                    continue

                # Compute area in hectares using rough WGS84 conversion (EPSG:4326)
                # For higher accuracy, users should reproject to equal-area CRS upstream.
                area_ha = (poly.area * 111000.0 * 111000.0) / 10000.0
                if min_area_ha is not None and area_ha < min_area_ha:
                    continue

                properties.append({
                    'area_ha': round(area_ha, 2),
                    'area_m2': round(area_ha * 10000.0, 0),
                    'perimeter_m': round(poly.length * 111000.0, 0),
                    'compactness': round(4 * np.pi * poly.area / (poly.length ** 2 + 1e-9), 3),
                    'mining_id': f"mining_{len(polygons)+1}"
                })
                polygons.append(poly)

            if polygons:
                gdf = gpd.GeoDataFrame(properties, geometry=polygons, crs=crs)
                logger.info(f"✅ Created {len(gdf)} mining polygons")
                return gdf
            else:
                logger.info("ℹ️ No mining polygons found")
                return gpd.GeoDataFrame(columns=['area_ha', 'area_m2', 'perimeter_m', 'compactness', 'mining_id'], crs=crs, geometry=[])
                
        except Exception as e:
            logger.error(f"❌ Error polygonizing mask: {e}")
            return gpd.GeoDataFrame(columns=['area_ha', 'area_m2', 'perimeter_m', 'compactness', 'mining_id'], crs=crs, geometry=[])
    
    def detect_mining_areas(self, raster_path: str, output_dir: str = None) -> Dict:
        """
        Complete mining detection pipeline
        
        Args:
            raster_path: Path to input raster
            output_dir: Output directory for results
            
        Returns:
            Dict: Complete detection results
        """
        try:
            logger.info(f"🚀 Starting complete mining detection pipeline for {raster_path}")
            
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            
            # Generate mining mask
            mask_path = os.path.join(output_dir, "mining_mask.tif") if output_dir else None
            detection_results = self.generate_mask(raster_path, mask_path)
            
            if not detection_results:
                return {}
            
            # Convert mask to polygons
            polygons_gdf = self.polygonize_mask(
                detection_results['mask'],
                detection_results['transform'],
                detection_results['crs']
            )
            
            # Save polygons
            polygons_path = None
            if output_dir and not polygons_gdf.empty:
                polygons_path = os.path.join(output_dir, "mining_polygons.geojson")
                polygons_gdf.to_file(polygons_path, driver="GeoJSON")
            
            # Calculate additional statistics
            if not polygons_gdf.empty:
                total_detected_area = polygons_gdf['area_ha'].sum()
                avg_area = polygons_gdf['area_ha'].mean()
                max_area = polygons_gdf['area_ha'].max()
                min_area = polygons_gdf['area_ha'].min()
            else:
                total_detected_area = avg_area = max_area = min_area = 0
            
            results = {
                'detection_results': detection_results,
                'polygons': polygons_gdf,
                'polygons_path': polygons_path,
                'mask_path': mask_path,
                'summary': {
                    'total_polygons': len(polygons_gdf),
                    'total_area_ha': round(total_detected_area, 2),
                    'average_area_ha': round(avg_area, 2),
                    'max_area_ha': round(max_area, 2),
                    'min_area_ha': round(min_area, 2),
                    'detection_thresholds': self.thresholds
                }
            }
            
            logger.info(f"✅ Mining detection complete: {len(polygons_gdf)} areas, {total_detected_area:.1f} hectares")
            return results
            
        except Exception as e:
            logger.error(f"❌ Error in mining detection pipeline: {e}")
            return {}

# Standalone functions for easy integration
def generate_mining_mask(raster_path: str, output_path: str = None) -> Dict:
    """Generate mining detection mask"""
    detector = MiningDetector()
    return detector.generate_mask(raster_path, output_path)

def detect_mining_areas(raster_path: str, output_dir: str = None) -> Dict:
    """Complete mining detection pipeline"""
    detector = MiningDetector()
    return detector.detect_mining_areas(raster_path, output_dir)

if __name__ == "__main__":
    # Test mining detection
    print("🧪 Testing mining detection...")
    
    # Create test data
    test_aoi = {
        "type": "Polygon",
        "coordinates": [[
            [76.0, 15.0], [77.0, 15.0], [77.0, 16.0], [76.0, 16.0], [76.0, 15.0]
        ]]
    }
    
    # Create test Sentinel-2 data
    from gee_utils import GEEUtils
    gee_utils = GEEUtils()
    
    sentinel2_path = "test_sentinel2.tif"
    success = gee_utils.download_sentinel2_aoi(test_aoi, "2024-01-01", "2024-01-31", sentinel2_path)
    
    if success:
        # Test mining detection
        detector = MiningDetector()
        results = detector.detect_mining_areas(sentinel2_path, "test_output")
        
        if results:
            print(f"✅ Mining detection successful:")
            print(f"   - {results['summary']['total_polygons']} mining areas detected")
            print(f"   - {results['summary']['total_area_ha']} hectares total")
            print(f"   - Average area: {results['summary']['average_area_ha']} hectares")
        else:
            print("❌ Mining detection failed")
    else:
        print("❌ Failed to create test data")
