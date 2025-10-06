"""
FastAPI Application for Illegal Mining Detection
Complete end-to-end system following approach.txt specifications
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Dict, List, Optional, Any
import logging
import asyncio
import os
import json
import uuid
from datetime import datetime
import tempfile
import shutil
import math

from gee_utils import GEEUtils, download_sentinel2_aoi, download_dem, download_sentinel1_sar
from preprocess import Preprocessor, normalize_bands, fill_dem_voids
from detect_indices import MiningDetector, detect_mining_areas
from compare_with_lease import IllegalMiningDetector, compare_with_lease, read_lease_shapefile

# from ai_mining_detector import AIMiningDetector
# from advanced_volume_calculator import AdvancedVolumeCalculator
# from blockchain_compliance import MiningComplianceBlockchain
# from advanced_report_generator import AdvancedReportGenerator, ReportConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Illegal Mining Detection API",
    description="End-to-end system for detecting illegal mining activities using satellite imagery",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
analysis_results = {}

class AOIRequest(BaseModel):
    aoi_geojson: Dict[str, Any]
    start_date: str
    end_date: str
    use_sar: bool = False
    max_cloud_cover: int = 20

class DetectionRequest(BaseModel):
    aoi_geojson: Dict[str, Any]
    start_date: str
    end_date: str
    lease_file_path: Optional[str] = None
    use_sar: bool = False
    buffer_meters: float = 10.0
    fetch_gov_leases: bool = True

class AnalysisStatus(BaseModel):
    job_id: str
    status: str
    message: str
    timestamp: str
    progress: Optional[int] = None

gee_utils = GEEUtils()
preprocessor = Preprocessor()
mining_detector = MiningDetector()
illegal_detector = IllegalMiningDetector()

# ai_detector = AIMiningDetector()
# volume_calculator = AdvancedVolumeCalculator()
# blockchain_system = MiningComplianceBlockchain()
# report_generator = AdvancedReportGenerator()

demo_jobs: Dict[str, Any] = {}

# ------------------------------
# Demo data generators (for hackathon demo mode)
# ------------------------------

def _box_from_center(center_lon: float, center_lat: float, dx_deg: float, dy_deg: float):
    minx = center_lon - dx_deg / 2.0
    maxx = center_lon + dx_deg / 2.0
    miny = center_lat - dy_deg / 2.0
    maxy = center_lat + dy_deg / 2.0
    return [
        [minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]
    ]

def _demo_legal_leases_geojson():
    # 12 sample legal leases across India (approximate centers)
    samples = [
        (73.8, 15.3, 'Goa'),
        (75.7, 15.6, 'Karnataka'),
        (85.8, 20.5, 'Odisha'),
        (86.4, 23.6, 'Jharkhand'),
        (81.9, 20.6, 'Chhattisgarh'),
        (73.2, 22.5, 'Gujarat'),
        (74.0, 18.9, 'Maharashtra'),
        (78.5, 17.5, 'Telangana'),
        (79.7, 15.9, 'Andhra Pradesh'),
        (75.8, 26.9, 'Rajasthan'),
        (77.4, 23.3, 'Madhya Pradesh'),
        (78.7, 11.1, 'Tamil Nadu')
    ]

    features = []
    minerals = ['Iron Ore', 'Limestone', 'Bauxite', 'Manganese', 'Dolomite', 'Granite']
    for idx, (lon, lat, state) in enumerate(samples, start=1):
        # size ~ 0.18 x 0.18 degrees (varies by latitude, but OK for demo)
        coords = _box_from_center(lon, lat, 0.18, 0.18)
        area_ha = 0.18 * 0.18 * 111000 * 111000 / 10000.0
        mineral = minerals[idx % len(minerals)]
        features.append({
            "type": "Feature",
            "properties": {
                "lease_id": f"DEMO_LEASE_{idx:02d}",
                "lease_name": f"Demo Mining Lease {idx}",
                "state": state,
                "district": "Demo District",
                "mineral": mineral,
                "area_hectares": round(area_ha, 2),
                "lease_type": "Prospecting License",
                "valid_from": "2019-04-01",
                "valid_to": "2039-03-31",
                "production_2024": f"{(idx*10)%150 + 20} kt",
                "value_2024": f"₹{(idx*75)%500 + 100} cr"
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [coords]
            }
        })

    summary = {
        "total_leases": len(features),
        "total_area_hectares": round(sum(f["properties"]["area_hectares"] for f in features), 1),
        "states": sorted(list({f["properties"]["state"] for f in features})),
        "minerals": sorted(list({f["properties"]["mineral"] for f in features})),
        "value_2024_crores": {"total_value": 1250}
    }

    return {
        "type": "FeatureCollection",
        "features": features
    }, summary

def _demo_satellite_detections_geojson():
    # A few detections (some inside, some near leases)
    detections = [
        (73.82, 15.35, 0.06, 0.05, 0.82, 'High'),
        (86.45, 23.62, 0.07, 0.05, 0.77, 'Medium'),
        (81.88, 20.58, 0.05, 0.04, 0.88, 'High'),
        (75.78, 26.92, 0.04, 0.04, 0.71, 'Low'),
        (78.55, 17.52, 0.05, 0.05, 0.80, 'High'),
        (79.72, 15.92, 0.05, 0.04, 0.69, 'Medium')
    ]

    features = []
    for i, (lon, lat, dx, dy, conf, sev) in enumerate(detections, start=1):
        coords = _box_from_center(lon, lat, dx, dy)
        area_ha = dx * dy * 111000 * 111000 / 10000.0
        features.append({
            "type": "Feature",
            "properties": {
                "id": f"DEMO_DET_{i:02d}",
                "source": "Spectral analysis (demo)",
                "area_hectares": round(area_ha, 2),
                "confidence": conf,
                "ndvi": round(0.2 + (i % 5) * 0.05, 2),
                "bsi": round(0.5 + (i % 3) * 0.1, 2),
                "resolution": "10 m",
                "detection_date": datetime.now().date().isoformat(),
                "severity": sev
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [coords]
            }
        })

    return {
        "type": "FeatureCollection",
        "features": features
    }

def _demo_violation_zones_geojson():
    # Create more violation zones for demo (4-5 illegal areas)
    red_centers = [ (86.47, 23.64), (79.75, 15.95), (73.85, 15.32), (85.82, 20.52) ]
    orange_centers = [ (81.90, 20.60), (78.58, 17.54), (75.80, 26.95), (74.05, 18.92) ]

    def zone_feature(lon, lat, dx, dy, color_name):
        coords = _box_from_center(lon, lat, dx, dy)
        area_ha = dx * dy * 111000 * 111000 / 10000.0
        return {
            "type": "Feature",
            "properties": {
                "area_hectares": round(area_ha, 2),
                "confidence": 0.85 if color_name == 'red' else 0.65,
                "description": "Illegal mining violation (demo)",
                "zone": color_name
            },
            "geometry": {"type": "Polygon", "coordinates": [coords]}
        }

    red_features = [ zone_feature(lon, lat, 0.08, 0.06, 'red') for lon, lat in red_centers ]
    orange_features = [ zone_feature(lon, lat, 0.06, 0.05, 'orange') for lon, lat in orange_centers ]

    return (
        {"type": "FeatureCollection", "features": red_features},
        {"type": "FeatureCollection", "features": orange_features}
    )

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Illegal Mining Detection API",
        "version": "1.0.0",
        "status": "operational",
        "endpoints": [
            "/api/upload-lease",
            "/api/detect",
            "/api/results/{job_id}",
            "/api/report/{job_id}",
            "/api/health",
            "/api/mining-boundaries",
            "/api/satellite-data",
            "/api/analyze/quick",
            "/api/analyze/illegal-mining-detection",
            "/api/illegal-mining-results/{analysis_id}"
        ]
    }

@app.get("/api/mining-boundaries")
async def get_mining_boundaries():
    try:
        geojson, summary = _demo_legal_leases_geojson()
        return {
            "status": "success",
            "message": "Demo legal mining boundaries (12 leases across India)",
            "boundaries": geojson,
            "summary": summary,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"❌ Error fetching mining boundaries: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/satellite-data")
async def get_satellite_data():
    """
    Get demo satellite-detected mining areas
    
    Returns:
        Dict: GeoJSON FeatureCollection of satellite-detected mining areas
    """
    try:
        geojson = _demo_satellite_detections_geojson()
        return {
            "status": "success",
            "message": "Demo satellite-detected mining areas",
            "geojson": geojson,
            "total_areas": len(geojson["features"]),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"❌ Error fetching satellite data: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/analyze/quick")
async def analyze_quick(request: Dict[str, Any]):
    """
    Quick analysis endpoint (demo mode)
    
    Args:
        request: Analysis request parameters
        
    Returns:
        Dict: Analysis results
    """
    try:
        logger.info(f"🚀 Quick analysis started: {request.get('analysis_name', 'unnamed')}")
        
        # Return immediate success for demo
        return {
            "status": "success",
            "message": "Quick analysis completed",
            "analysis_name": request.get("analysis_name", "unnamed"),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"❌ Error in quick analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/analyze/illegal-mining-detection")
async def analyze_illegal_mining():
    """
    Complete illegal mining detection analysis (demo mode)
    
    Returns:
        Dict: Analysis job ID and status
    """
    try:
        analysis_id = f"demo_analysis_{uuid.uuid4().hex[:8]}"
        logger.info(f"🚨 Illegal mining detection started: {analysis_id}")
        
        # Generate demo violation zones
        red_geojson, orange_geojson = _demo_violation_zones_geojson()
        
        # Store demo results immediately
        demo_jobs[analysis_id] = {
            "analysis_id": analysis_id,
            "status": "completed",
            "message": "Demo illegal mining detection completed",
            "timestamp": datetime.now().isoformat(),
            "analysis_summary": {
                "total_legal_leases": 12,
                "total_satellite_detections": 6,
                "critical_violations": len(red_geojson["features"]),
                "warning_violations": len(orange_geojson["features"]),
                "total_violations": len(red_geojson["features"]) + len(orange_geojson["features"])
            },
            "violation_zones": {
                "red_zones_geojson": red_geojson,
                "orange_zones_geojson": orange_geojson
            }
        }
        
        return {
            "status": "success",
            "message": "Illegal mining detection initiated",
            "analysis_id": analysis_id,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"❌ Error starting illegal mining detection: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/illegal-mining-results/{analysis_id}")
async def get_illegal_mining_results(analysis_id: str):
    """
    Get illegal mining detection results
    
    Args:
        analysis_id: Analysis job ID
        
    Returns:
        Dict: Analysis results with violation zones
    """
    if analysis_id not in demo_jobs:
        raise HTTPException(status_code=404, detail="Analysis not found")
    
    return demo_jobs[analysis_id]

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "modules": {
            "gee_utils": "ready",
            "preprocessor": "ready", 
            "mining_detector": "ready",
            "illegal_detector": "ready"
        }
    }

@app.post("/api/upload-lease")
async def upload_lease(file: UploadFile = File(...)):
    """
    Upload lease boundaries (shapefile, KML, or GeoJSON)
    
    Args:
        file: Lease boundary file
        
    Returns:
        Dict: Upload status and file info
    """
    try:
        logger.info(f"📁 Uploading lease file: {file.filename}")
        
        # Create temporary directory
        temp_dir = tempfile.mkdtemp()
        file_path = os.path.join(temp_dir, file.filename)
        
        # Save uploaded file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Read and validate lease file
        lease_gdf = illegal_detector.read_lease_shapefile(file_path)
        
        if lease_gdf.empty:
            raise HTTPException(status_code=400, detail="Invalid or empty lease file")
        
        # Generate file info
        file_info = {
            "filename": file.filename,
            "file_size": os.path.getsize(file_path),
            "num_leases": len(lease_gdf),
            "total_area_ha": round(lease_gdf.geometry.to_crs('EPSG:3857').area.sum() / 10000, 2),
            "bounds": lease_gdf.total_bounds.tolist(),
            "crs": str(lease_gdf.crs),
            "columns": list(lease_gdf.columns),
            "file_path": file_path
        }
        
        logger.info(f"✅ Lease file uploaded: {len(lease_gdf)} leases")
        
        return {
            "status": "success",
            "message": "Lease file uploaded successfully",
            "file_info": file_info,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Error uploading lease file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/detect")
async def detect_illegal_mining(request: DetectionRequest, background_tasks: BackgroundTasks):
    """
    Run complete illegal mining detection analysis
    
    Args:
        request: Detection request with AOI and parameters
        
    Returns:
        Dict: Job ID and status
    """
    try:
        # Generate unique job ID
        job_id = str(uuid.uuid4())
        
        logger.info(f"🚀 Starting illegal mining detection: {job_id}")
        
        # Store initial status
        analysis_results[job_id] = {
            "job_id": job_id,
            "status": "processing",
            "message": "Illegal mining detection analysis initiated...",
            "timestamp": datetime.now().isoformat(),
            "progress": 0,
            "request": request.dict()
        }
        
        # Run analysis in background
        background_tasks.add_task(
            _run_illegal_mining_analysis, job_id, request
        )
        
        return {
            "job_id": job_id,
            "status": "processing",
            "message": "Illegal mining detection analysis initiated. This may take several minutes.",
            "timestamp": datetime.now().isoformat(),
            "estimated_completion_time": "5-15 minutes"
        }
        
    except Exception as e:
        logger.error(f"❌ Error starting illegal mining detection: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def _run_illegal_mining_analysis(job_id: str, request: DetectionRequest):
    """Background task for illegal mining analysis"""
    try:
        logger.info(f"🛰️ Running illegal mining analysis for {job_id}")
        
        # Update progress
        analysis_results[job_id]["progress"] = 10
        analysis_results[job_id]["message"] = "Downloading satellite data..."
        
        # Step 1: Download satellite data
        temp_dir = tempfile.mkdtemp()
        sentinel2_path = os.path.join(temp_dir, "sentinel2.tif")
        dem_path = os.path.join(temp_dir, "dem.tif")
        
        # Download Sentinel-2 data
        success = gee_utils.download_sentinel2_aoi(
            request.aoi_geojson,
            request.start_date,
            request.end_date,
            sentinel2_path,
            max_cloud_cover=20
        )
        
        if not success:
            raise Exception("Failed to download Sentinel-2 data")
        
        # Download DEM data
        success = gee_utils.download_dem(
            request.aoi_geojson,
            dem_path,
            "SRTM"
        )
        
        if not success:
            raise Exception("Failed to download DEM data")
        
        analysis_results[job_id]["progress"] = 30
        analysis_results[job_id]["message"] = "Preprocessing satellite data..."
        
        # Step 2: Preprocess data
        normalized_sentinel2 = os.path.join(temp_dir, "sentinel2_normalized.tif")
        filled_dem = os.path.join(temp_dir, "dem_filled.tif")
        
        # Normalize Sentinel-2 bands
        preprocessor.normalize_bands(sentinel2_path, normalized_sentinel2)
        
        # Fill DEM voids
        preprocessor.fill_dem_voids(dem_path, filled_dem)
        
        analysis_results[job_id]["progress"] = 50
        analysis_results[job_id]["message"] = "Detecting mining activities..."
        
        # Step 3: Detect mining areas
        detection_results = mining_detector.detect_mining_areas(
            normalized_sentinel2, 
            temp_dir
        )
        
        if not detection_results or detection_results['polygons'].empty:
            raise Exception("No mining areas detected")
        
        analysis_results[job_id]["progress"] = 70
        analysis_results[job_id]["message"] = "Comparing with legal boundaries..."
        
        # Step 4: Compare with lease boundaries
        if request.lease_file_path and os.path.exists(request.lease_file_path):
            lease_gdf = illegal_detector.read_lease_shapefile(request.lease_file_path)
        elif request.fetch_gov_leases:
            # Try fetching from configured government WFS
            # Compute AOI bbox
            coords = request.aoi_geojson['coordinates'][0]
            minx = min(c[0] for c in coords)
            miny = min(c[1] for c in coords)
            maxx = max(c[0] for c in coords)
            maxy = max(c[1] for c in coords)
            lease_gdf = illegal_detector.fetch_government_leases((minx, miny, maxx, maxy))
            if lease_gdf.empty:
                raise Exception("Government leases fetch returned no data. Provide a lease file or configure GOV_WFS_URL.")
        else:
            raise Exception("No lease data provided. Upload a lease file or enable fetch_gov_leases.")
        
        # Compare detected areas with lease boundaries
        comparison_results = illegal_detector.compare_with_lease(
            detection_results['polygons'],
            lease_gdf,
            request.buffer_meters
        )
        
        analysis_results[job_id]["progress"] = 90
        analysis_results[job_id]["message"] = "Generating results..."
        
        # Step 5: Generate summary statistics
        summary_stats = illegal_detector.generate_summary_statistics(comparison_results)
        
        # Export results
        export_files = illegal_detector.export_results(
            comparison_results,
            temp_dir,
            'all'
        )
        
        # Update final results
        analysis_results[job_id].update({
            "status": "completed",
            "message": "Illegal mining detection analysis completed successfully.",
            "timestamp": datetime.now().isoformat(),
            "progress": 100,
            "results": {
                "detection_results": detection_results,
                "comparison_results": comparison_results.to_dict('records') if not comparison_results.empty else [],
                "summary_statistics": summary_stats,
                "export_files": export_files,
                "temp_directory": temp_dir
            }
        })
        
        logger.info(f"✅ Illegal mining analysis completed: {job_id}")
        
    except Exception as e:
        logger.error(f"❌ Error in illegal mining analysis: {e}")
        analysis_results[job_id].update({
            "status": "failed",
            "message": f"Analysis failed: {str(e)}",
            "timestamp": datetime.now().isoformat(),
            "error": str(e)
        })

def _create_sample_lease_boundaries(aoi_geojson: Dict) -> Any:
    """Create sample lease boundaries for demo purposes"""
    import geopandas as gpd
    from shapely.geometry import Polygon
    
    # Extract AOI bounds
    coords = aoi_geojson['coordinates'][0]
    min_lon = min(coord[0] for coord in coords)
    max_lon = max(coord[0] for coord in coords)
    min_lat = min(coord[1] for coord in coords)
    max_lat = max(coord[1] for coord in coords)
    
    # Create sample lease boundaries
    width = max_lon - min_lon
    height = max_lat - min_lat
    
    sample_leases = []
    for i in range(3):  # Create 3 sample leases
        # Random position within AOI
        center_lon = min_lon + (i + 1) * width / 4
        center_lat = min_lat + (i + 1) * height / 4
        
        # Create lease polygon
        lease_size = min(width, height) * 0.1  # 10% of AOI size
        lease_poly = Polygon([
            [center_lon - lease_size/2, center_lat - lease_size/2],
            [center_lon + lease_size/2, center_lat - lease_size/2],
            [center_lon + lease_size/2, center_lat + lease_size/2],
            [center_lon - lease_size/2, center_lat + lease_size/2],
            [center_lon - lease_size/2, center_lat - lease_size/2]
        ])
        
        sample_leases.append({
            'lease_id': f'sample_lease_{i+1}',
            'lease_name': f'Sample Mining Lease {i+1}',
            'state': 'Test State',
            'district': 'Test District',
            'mineral': 'Iron Ore',
            'area_hectares': lease_poly.area * 111000 * 111000 / 10000,  # Rough conversion
            'geometry': lease_poly
        })
    
    return gpd.GeoDataFrame(sample_leases, crs='EPSG:4326')

@app.get("/api/results/{job_id}")
async def get_results(job_id: str):
    """
    Get analysis results for a job
    
    Args:
        job_id: Job ID
        
    Returns:
        Dict: Analysis results
    """
    if job_id not in analysis_results:
        raise HTTPException(status_code=404, detail="Job not found")
    
    result = analysis_results[job_id]
    
    # Return appropriate response based on status
    if result["status"] == "completed":
        return {
            "job_id": job_id,
            "status": "completed",
            "message": "Analysis completed successfully",
            "timestamp": result["timestamp"],
            "results": result["results"]
        }
    elif result["status"] == "failed":
        return {
            "job_id": job_id,
            "status": "failed",
            "message": result["message"],
            "timestamp": result["timestamp"],
            "error": result.get("error", "Unknown error")
        }
    else:
        return {
            "job_id": job_id,
            "status": result["status"],
            "message": result["message"],
            "timestamp": result["timestamp"],
            "progress": result.get("progress", 0)
        }

@app.get("/api/report/{job_id}")
async def get_report(job_id: str):
    """
    Generate and download PDF report for a job
    
    Args:
        job_id: Job ID
        
    Returns:
        FileResponse: PDF report
    """
    if job_id not in analysis_results:
        raise HTTPException(status_code=404, detail="Job not found")
    
    result = analysis_results[job_id]
    
    if result["status"] != "completed":
        raise HTTPException(status_code=400, detail="Analysis not completed yet")
    
    try:
        # Generate PDF report (placeholder for now)
        report_path = os.path.join(result["results"]["temp_directory"], "illegal_mining_report.pdf")
        
        # Create a simple text report for now
        with open(report_path.replace('.pdf', '.txt'), 'w') as f:
            f.write("ILLEGAL MINING DETECTION REPORT\n")
            f.write("=" * 40 + "\n\n")
            f.write(f"Job ID: {job_id}\n")
            f.write(f"Generated: {result['timestamp']}\n\n")
            
            summary = result["results"]["summary_statistics"]
            f.write("SUMMARY STATISTICS\n")
            f.write("-" * 20 + "\n")
            f.write(f"Total detected areas: {summary['total_detected_areas']}\n")
            f.write(f"Legal areas: {summary['legal_areas']}\n")
            f.write(f"Illegal areas: {summary['illegal_areas']}\n")
            f.write(f"Mixed areas: {summary['mixed_areas']}\n")
            f.write(f"Total detected area: {summary['total_detected_area_ha']} hectares\n")
            f.write(f"Legal area: {summary['legal_area_ha']} hectares\n")
            f.write(f"Illegal area: {summary['illegal_area_ha']} hectares\n")
            f.write(f"Compliance rate: {summary['compliance_rate_percent']}%\n")
            f.write(f"Violation rate: {summary['violation_rate_percent']}%\n")
        
        return FileResponse(
            report_path.replace('.pdf', '.txt'),
            media_type='text/plain',
            filename=f"illegal_mining_report_{job_id}.txt"
        )
        
    except Exception as e:
        logger.error(f"❌ Error generating report: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/download/{job_id}/{file_type}")
async def download_file(job_id: str, file_type: str):
    """
    Download analysis files
    
    Args:
        job_id: Job ID
        file_type: Type of file to download (geojson, shapefile, csv)
        
    Returns:
        FileResponse: Requested file
    """
    if job_id not in analysis_results:
        raise HTTPException(status_code=404, detail="Job not found")
    
    result = analysis_results[job_id]
    
    if result["status"] != "completed":
        raise HTTPException(status_code=400, detail="Analysis not completed yet")
    
    try:
        export_files = result["results"]["export_files"]
        
        if file_type not in export_files:
            raise HTTPException(status_code=404, detail=f"File type {file_type} not found")
        
        file_path = export_files[file_type]
        
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="File not found")
        
        media_type = {
            'geojson': 'application/geo+json',
            'shapefile': 'application/zip',
            'csv': 'text/csv',
            'summary': 'application/json'
        }.get(file_type, 'application/octet-stream')
        
        return FileResponse(
            file_path,
            media_type=media_type,
            filename=os.path.basename(file_path)
        )
        
    except Exception as e:
        logger.error(f"❌ Error downloading file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/jobs")
async def list_jobs():
    return {
        "jobs": [
            {
                "job_id": job_id,
                "status": result["status"],
                "timestamp": result["timestamp"],
                "progress": result.get("progress", 0)
            }
            for job_id, result in analysis_results.items()
        ]
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )