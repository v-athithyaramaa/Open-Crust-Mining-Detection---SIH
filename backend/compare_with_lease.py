"""
Illegal Mining Detection by Comparing with Legal Lease Boundaries
Proper spatial overlay analysis to identify illegal mining activities
"""

import geopandas as gpd
import numpy as np
import logging
from typing import Dict, List, Optional, Tuple, Union
from shapely.geometry import Polygon, MultiPolygon, Point
from shapely.ops import unary_union
import json
import os
from pyproj import CRS
import fiona
import requests

GOV_WFS_URL = os.getenv('GOV_WFS_URL', '').strip() or ''

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class IllegalMiningDetector:
    """Detect illegal mining by comparing with legal lease boundaries"""
    
    def __init__(self, buffer_meters: float = 10.0):
        """
        Initialize illegal mining detector
        
        Args:
            buffer_meters: Buffer tolerance for lease boundaries (meters)
        """
        self.buffer_meters = buffer_meters
        self.tolerance_ha = 0.01  # 0.01 hectares tolerance for small spillovers
        
        logger.info(f"Illegal mining detector initialized (buffer: {buffer_meters}m)")
    
    def read_lease_shapefile(self, path: str) -> gpd.GeoDataFrame:
        """
        Read lease boundaries from various formats
        
        Args:
            path: Path to shapefile, KML, or GeoJSON
            
        Returns:
            gpd.GeoDataFrame: Lease boundaries
        """
        try:
            logger.info(f"📁 Reading lease boundaries from {path}")
            
            # Determine file format
            if path.endswith('.geojson'):
                gdf = gpd.read_file(path)
            elif path.endswith('.kml'):
                # Handle KML files
                gdf = gpd.read_file(path, driver='KML')
            elif path.endswith('.shp'):
                gdf = gpd.read_file(path)
            elif path.endswith('.zip'):
                # Handle zipped shapefiles
                gdf = gpd.read_file(f"zip://{path}")
            else:
                # Try to read with fiona
                gdf = gpd.read_file(path)
            
            # Ensure valid geometries
            gdf = gdf[gdf.geometry.is_valid]
            
            # Standardize column names
            gdf = self._standardize_lease_columns(gdf)
            
            logger.info(f"✅ Loaded {len(gdf)} lease boundaries")
            return gdf
            
        except Exception as e:
            logger.error(f"❌ Error reading lease file: {e}")
            return gpd.GeoDataFrame()

    def fetch_government_leases(self, aoi_bbox: Tuple[float, float, float, float] = None) -> gpd.GeoDataFrame:
        """Fetch legal mining leases from a live government WFS if configured.
        Expects GOV_WFS_URL env var pointing to a WFS GetFeature endpoint returning GeoJSON.
        Optionally filter by bbox if service supports it.
        """
        try:
            if not GOV_WFS_URL:
                logger.warning("⚠️ GOV_WFS_URL not set; cannot fetch government leases")
                return gpd.GeoDataFrame()
            params = {}
            if aoi_bbox is not None:
                # Many WFS servers support bbox param as minx,miny,maxx,maxy
                params['bbox'] = ','.join(map(str, aoi_bbox))
            resp = requests.get(GOV_WFS_URL, params=params, timeout=60)
            resp.raise_for_status()
            gdf = gpd.read_file(resp.text)
            if gdf.empty:
                logger.warning("⚠️ Government WFS returned no leases")
                return gpd.GeoDataFrame()
            gdf = gdf[gdf.geometry.notnull()]
            gdf = gdf[gdf.geometry.is_valid]
            gdf = self._standardize_lease_columns(gdf)
            logger.info(f"✅ Fetched {len(gdf)} government leases from WFS")
            return gdf
        except Exception as e:
            logger.error(f"❌ Error fetching government leases: {e}")
            return gpd.GeoDataFrame()
    
    def _standardize_lease_columns(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Standardize column names in lease GeoDataFrame"""
        
        # Common column name mappings
        column_mappings = {
            'lease_id': ['lease_id', 'id', 'lease_no', 'lease_number', 'ML_NO'],
            'lease_name': ['lease_name', 'name', 'mine_name', 'lease_title', 'ML_NAME'],
            'state': ['state', 'state_name', 'STATE', 'STATE_NAME'],
            'district': ['district', 'district_name', 'DISTRICT', 'DISTRICT_NAME'],
            'mineral': ['mineral', 'mineral_type', 'MINERAL', 'MINERAL_TYPE'],
            'area_hectares': ['area_hectares', 'area_ha', 'area', 'AREA_HA', 'AREA'],
            'valid_from': ['valid_from', 'from_date', 'start_date', 'VALID_FROM'],
            'valid_to': ['valid_to', 'to_date', 'end_date', 'VALID_TO']
        }
        
        # Rename columns
        for standard_name, possible_names in column_mappings.items():
            for possible_name in possible_names:
                if possible_name in gdf.columns and standard_name not in gdf.columns:
                    gdf = gdf.rename(columns={possible_name: standard_name})
                    break
        
        # Add missing columns with default values
        if 'lease_id' not in gdf.columns:
            gdf['lease_id'] = [f"lease_{i+1}" for i in range(len(gdf))]
        if 'lease_name' not in gdf.columns:
            gdf['lease_name'] = gdf['lease_id']
        if 'state' not in gdf.columns:
            gdf['state'] = 'Unknown'
        if 'district' not in gdf.columns:
            gdf['district'] = 'Unknown'
        if 'mineral' not in gdf.columns:
            gdf['mineral'] = 'Unknown'
        if 'area_hectares' not in gdf.columns:
            # Calculate area if not provided
            gdf['area_hectares'] = gdf.geometry.to_crs('EPSG:3857').area / 10000
        if 'valid_from' not in gdf.columns:
            gdf['valid_from'] = '2020-01-01'
        if 'valid_to' not in gdf.columns:
            gdf['valid_to'] = '2030-12-31'
        
        return gdf
    
    def compare_with_lease(self, detected_polygons: gpd.GeoDataFrame, 
                          lease_polygons: gpd.GeoDataFrame,
                          equal_area_crs: str = "EPSG:3857") -> gpd.GeoDataFrame:
        """
        Compare detected mining polygons with legal lease boundaries
        
        Args:
            detected_polygons: Detected mining areas
            lease_polygons: Legal lease boundaries
            equal_area_crs: Equal area CRS for accurate area calculations
            
        Returns:
            gpd.GeoDataFrame: Analysis results with legal/illegal classification
        """
        try:
            logger.info(f"⚖️ Comparing {len(detected_polygons)} detected areas with {len(lease_polygons)} legal leases")
            
            if detected_polygons.empty or lease_polygons.empty:
                logger.warning("⚠️ Empty input data")
                return gpd.GeoDataFrame()
            
            # Ensure both GeoDataFrames are in the same CRS
            if detected_polygons.crs != lease_polygons.crs:
                lease_polygons = lease_polygons.to_crs(detected_polygons.crs)
            
            # Project to equal area CRS for accurate area calculations
            detected_ea = detected_polygons.to_crs(equal_area_crs)
            lease_ea = lease_polygons.to_crs(equal_area_crs)
            
            # Create union of all lease boundaries
            lease_union = lease_ea.unary_union
            
            # Add buffer to lease boundaries for tolerance
            if self.buffer_meters > 0:
                lease_union_buffered = lease_union.buffer(self.buffer_meters)
            else:
                lease_union_buffered = lease_union
            
            # Analyze each detected polygon
            results = []
            
            for idx, detected_poly in detected_ea.iterrows():
                result = self._analyze_single_polygon(
                    detected_poly, lease_union_buffered, lease_ea, equal_area_crs
                )
                results.append(result)
            
            # Create results GeoDataFrame
            results_gdf = gpd.GeoDataFrame(results, crs=equal_area_crs)
            
            # Convert back to original CRS
            results_gdf = results_gdf.to_crs(detected_polygons.crs)
            
            # Add original detected polygon data
            for col in detected_polygons.columns:
                if col not in results_gdf.columns:
                    results_gdf[col] = detected_polygons[col].values
            
            logger.info(f"✅ Analysis complete: {len(results_gdf)} areas analyzed")
            return results_gdf
            
        except Exception as e:
            logger.error(f"❌ Error in lease comparison: {e}")
            return gpd.GeoDataFrame()
    
    def _analyze_single_polygon(self, detected_poly: gpd.GeoSeries, 
                               lease_union: Union[Polygon, MultiPolygon],
                               lease_gdf: gpd.GeoDataFrame,
                               crs: str) -> Dict:
        """Analyze a single detected polygon against lease boundaries"""
        
        try:
            # Calculate total area
            total_area_m2 = detected_poly.geometry.area
            total_area_ha = total_area_m2 / 10000
            
            # Find intersection with lease boundaries
            inside_geom = detected_poly.geometry.intersection(lease_union)
            inside_area_m2 = inside_geom.area if inside_geom.area > 0 else 0
            inside_area_ha = inside_area_m2 / 10000
            
            # Calculate area outside lease boundaries
            outside_geom = detected_poly.geometry.difference(lease_union)
            outside_area_m2 = outside_geom.area if outside_geom.area > 0 else 0
            outside_area_ha = outside_area_m2 / 10000
            
            # Calculate overlap percentage
            if total_area_ha > 0:
                overlap_percentage = (inside_area_ha / total_area_ha) * 100
            else:
                overlap_percentage = 0
            
            # Find overlapping leases
            overlapping_leases = []
            for _, lease in lease_gdf.iterrows():
                if detected_poly.geometry.intersects(lease.geometry):
                    overlap_area = detected_poly.geometry.intersection(lease.geometry).area / 10000
                    overlapping_leases.append({
                        'lease_id': lease.get('lease_id', 'unknown'),
                        'lease_name': lease.get('lease_name', 'unknown'),
                        'overlap_area_ha': round(overlap_area, 2)
                    })
            
            # Classify as legal/illegal/mixed
            status = self._classify_mining_status(outside_area_ha, overlap_percentage)
            
            # Calculate confidence score
            confidence = self._calculate_confidence_score(
                total_area_ha, overlap_percentage, len(overlapping_leases)
            )
            
            return {
                'geometry': detected_poly.geometry,
                'total_area_ha': round(total_area_ha, 2),
                'inside_area_ha': round(inside_area_ha, 2),
                'outside_area_ha': round(outside_area_ha, 2),
                'overlap_percentage': round(overlap_percentage, 1),
                'status': status,
                'confidence': round(confidence, 2),
                'overlapping_leases': overlapping_leases,
                'num_overlapping_leases': len(overlapping_leases),
                'illegal_area_ha': round(outside_area_ha, 2) if status in ['illegal', 'mixed'] else 0
            }
            
        except Exception as e:
            logger.error(f"❌ Error analyzing polygon: {e}")
            return {
                'geometry': detected_poly.geometry,
                'total_area_ha': 0,
                'inside_area_ha': 0,
                'outside_area_ha': 0,
                'overlap_percentage': 0,
                'status': 'error',
                'confidence': 0,
                'overlapping_leases': [],
                'num_overlapping_leases': 0,
                'illegal_area_ha': 0
            }
    
    def _classify_mining_status(self, outside_area_ha: float, 
                               overlap_percentage: float) -> str:
        """Classify mining status based on area outside lease boundaries"""
        
        # Legal: minimal area outside lease boundaries
        if outside_area_ha <= self.tolerance_ha:
            return 'legal'
        
        # Mixed: some area outside but mostly within lease
        elif overlap_percentage >= 80:
            return 'mixed'
        
        # Illegal: significant area outside lease boundaries
        else:
            return 'illegal'
    
    def _calculate_confidence_score(self, total_area_ha: float, 
                                   overlap_percentage: float,
                                   num_overlapping_leases: int) -> float:
        """Calculate confidence score for the classification"""
        
        # Base confidence on overlap percentage
        if overlap_percentage >= 95:
            base_confidence = 0.95
        elif overlap_percentage >= 80:
            base_confidence = 0.85
        elif overlap_percentage >= 50:
            base_confidence = 0.70
        else:
            base_confidence = 0.60
        
        # Adjust based on area size (larger areas are more reliable)
        if total_area_ha >= 10:
            area_factor = 1.0
        elif total_area_ha >= 1:
            area_factor = 0.9
        else:
            area_factor = 0.8
        
        # Adjust based on number of overlapping leases
        if num_overlapping_leases == 1:
            lease_factor = 1.0
        elif num_overlapping_leases > 1:
            lease_factor = 0.9  # Multiple leases can be confusing
        else:
            lease_factor = 0.8  # No overlapping leases
        
        confidence = base_confidence * area_factor * lease_factor
        return min(1.0, max(0.0, confidence))
    
    def generate_summary_statistics(self, results_gdf: gpd.GeoDataFrame) -> Dict:
        """Generate summary statistics from analysis results"""
        
        if results_gdf.empty:
            return {
                'total_detected_areas': 0,
                'legal_areas': 0,
                'illegal_areas': 0,
                'mixed_areas': 0,
                'total_detected_area_ha': 0,
                'legal_area_ha': 0,
                'illegal_area_ha': 0,
                'compliance_rate_percent': 0,
                'violation_rate_percent': 0
            }
        
        # Count areas by status
        status_counts = results_gdf['status'].value_counts()
        
        # Calculate areas by status
        legal_mask = results_gdf['status'] == 'legal'
        illegal_mask = results_gdf['status'] == 'illegal'
        mixed_mask = results_gdf['status'] == 'mixed'
        
        total_area = results_gdf['total_area_ha'].sum()
        legal_area = results_gdf[legal_mask]['total_area_ha'].sum()
        illegal_area = results_gdf[illegal_mask]['illegal_area_ha'].sum()
        mixed_area = results_gdf[mixed_mask]['illegal_area_ha'].sum()
        
        # Calculate rates
        compliance_rate = (legal_area / total_area * 100) if total_area > 0 else 0
        violation_rate = ((illegal_area + mixed_area) / total_area * 100) if total_area > 0 else 0
        
        return {
            'total_detected_areas': len(results_gdf),
            'legal_areas': status_counts.get('legal', 0),
            'illegal_areas': status_counts.get('illegal', 0),
            'mixed_areas': status_counts.get('mixed', 0),
            'total_detected_area_ha': round(total_area, 2),
            'legal_area_ha': round(legal_area, 2),
            'illegal_area_ha': round(illegal_area + mixed_area, 2),
            'compliance_rate_percent': round(compliance_rate, 1),
            'violation_rate_percent': round(violation_rate, 1),
            'average_confidence': round(results_gdf['confidence'].mean(), 2)
        }
    
    def export_results(self, results_gdf: gpd.GeoDataFrame, 
                      output_dir: str, format: str = 'geojson') -> Dict:
        """Export analysis results to various formats"""
        
        try:
            os.makedirs(output_dir, exist_ok=True)
            
            exported_files = {}
            
            if format == 'geojson' or format == 'all':
                geojson_path = os.path.join(output_dir, 'illegal_mining_analysis.geojson')
                results_gdf.to_file(geojson_path, driver='GeoJSON')
                exported_files['geojson'] = geojson_path
            
            if format == 'shapefile' or format == 'all':
                shp_path = os.path.join(output_dir, 'illegal_mining_analysis.shp')
                results_gdf.to_file(shp_path, driver='ESRI Shapefile')
                exported_files['shapefile'] = shp_path
            
            if format == 'csv' or format == 'all':
                csv_path = os.path.join(output_dir, 'illegal_mining_analysis.csv')
                # Drop geometry column for CSV
                csv_data = results_gdf.drop(columns=['geometry'])
                csv_data.to_csv(csv_path, index=False)
                exported_files['csv'] = csv_path
            
            # Export summary statistics
            summary = self.generate_summary_statistics(results_gdf)
            summary_path = os.path.join(output_dir, 'summary_statistics.json')
            with open(summary_path, 'w') as f:
                json.dump(summary, f, indent=2)
            exported_files['summary'] = summary_path
            
            logger.info(f"✅ Results exported to {output_dir}")
            return exported_files
            
        except Exception as e:
            logger.error(f"❌ Error exporting results: {e}")
            return {}

# Standalone functions for easy integration
def compare_with_lease(detected_polygons: gpd.GeoDataFrame, 
                      lease_polygons: gpd.GeoDataFrame,
                      buffer_meters: float = 10.0) -> gpd.GeoDataFrame:
    """Compare detected mining areas with legal lease boundaries"""
    detector = IllegalMiningDetector(buffer_meters)
    return detector.compare_with_lease(detected_polygons, lease_polygons)

def read_lease_shapefile(path: str) -> gpd.GeoDataFrame:
    """Read lease boundaries from file"""
    detector = IllegalMiningDetector()
    return detector.read_lease_shapefile(path)

if __name__ == "__main__":
    # Test illegal mining detection
    print("🧪 Testing illegal mining detection...")
    
    # Create test data
    from detect_indices import MiningDetector
    from gee_utils import GEEUtils
    
    # Create test mining detection
    test_aoi = {
        "type": "Polygon",
        "coordinates": [[
            [76.0, 15.0], [77.0, 15.0], [77.0, 16.0], [76.0, 16.0], [76.0, 15.0]
        ]]
    }
    
    # Create test data
    gee_utils = GEEUtils()
    sentinel2_path = "test_sentinel2.tif"
    gee_utils.download_sentinel2_aoi(test_aoi, "2024-01-01", "2024-01-31", sentinel2_path)
    
    # Detect mining areas
    detector = MiningDetector()
    detection_results = detector.detect_mining_areas(sentinel2_path, "test_output")
    
    if detection_results and not detection_results['polygons'].empty:
        # Create test lease boundaries
        test_lease_data = {
            'lease_id': ['lease_1', 'lease_2'],
            'lease_name': ['Test Lease 1', 'Test Lease 2'],
            'geometry': [
                Polygon([(76.1, 15.1), (76.5, 15.1), (76.5, 15.5), (76.1, 15.5), (76.1, 15.1)]),
                Polygon([(76.6, 15.6), (76.9, 15.6), (76.9, 15.9), (76.6, 15.9), (76.6, 15.6)])
            ]
        }
        test_leases = gpd.GeoDataFrame(test_lease_data, crs='EPSG:4326')
        
        # Compare with leases
        illegal_detector = IllegalMiningDetector()
        results = illegal_detector.compare_with_lease(
            detection_results['polygons'], 
            test_leases
        )
        
        if not results.empty:
            summary = illegal_detector.generate_summary_statistics(results)
            print(f"✅ Illegal mining detection successful:")
            print(f"   - {summary['total_detected_areas']} areas analyzed")
            print(f"   - {summary['legal_areas']} legal areas")
            print(f"   - {summary['illegal_areas']} illegal areas")
            print(f"   - {summary['mixed_areas']} mixed areas")
            print(f"   - Compliance rate: {summary['compliance_rate_percent']}%")
        else:
            print("❌ No results from illegal mining detection")
    else:
        print("❌ No mining areas detected for testing")
