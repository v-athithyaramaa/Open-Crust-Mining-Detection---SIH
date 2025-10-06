import React, { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

interface IllegalMiningArea {
  id: string;
  name: string;
  area_ha: number;
  depth_m: number;
  width_m: number;
  length_m: number;
  volume_m3: number;
  coordinates: [number, number][];
  severity: 'low' | 'medium' | 'high' | 'critical';
  description: string;
}

interface MapComponentProps {
  center: [number, number];
  zoom: number;
  style?: React.CSSProperties;
  miningBoundaries?: any;
  showBoundaries?: boolean;
  illegalAreas?: IllegalMiningArea[];
  selectedIllegalArea?: IllegalMiningArea | null;
  violationZones?: {
    redZones: any[];
    orangeZones: any[];
  };
  satelliteData?: any;
}

const MapComponent: React.FC<MapComponentProps> = ({ 
  center, 
  zoom, 
  style, 
  miningBoundaries, 
  showBoundaries = false,
  illegalAreas = [],
  selectedIllegalArea = null,
  violationZones = { redZones: [], orangeZones: [] },
  satelliteData = null
}) => {
  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<L.Map | null>(null);
  const boundariesLayerRef = useRef<L.LayerGroup | null>(null);
  const illegalAreasLayerRef = useRef<L.LayerGroup | null>(null);
  const violationZonesLayerRef = useRef<L.LayerGroup | null>(null);
  const satelliteLayerRef = useRef<L.LayerGroup | null>(null);
  const [currentZoom, setCurrentZoom] = useState(zoom);

  useEffect(() => {
    if (!mapRef.current) return;

    // Clean up existing map
    if (mapInstanceRef.current) {
      mapInstanceRef.current.remove();
      mapInstanceRef.current = null;
    }

    // Create new map
    const map = L.map(mapRef.current).setView(center, zoom);
    mapInstanceRef.current = map;
    setCurrentZoom(zoom);

    // Track zoom changes
    map.on('zoomend', () => {
      setCurrentZoom(map.getZoom());
    });

    // Add tile layer
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    // Add satellite layer
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      attribution: 'Esri',
      maxZoom: 19
    }).addTo(map);

    // Add layer control
    const baseMaps = {
      "OpenStreetMap": L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      }),
      "Satellite": L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
        attribution: 'Esri'
      })
    };

    L.control.layers(baseMaps).addTo(map);

    // Cleanup function
    return () => {
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove();
        mapInstanceRef.current = null;
      }
    };
  }, [center, zoom]);

  // Add/remove mining boundaries when they change
  useEffect(() => {
    if (!mapInstanceRef.current || !miningBoundaries) return;

    // Remove existing boundaries layer
    if (boundariesLayerRef.current) {
      mapInstanceRef.current.removeLayer(boundariesLayerRef.current);
    }

    if (showBoundaries && miningBoundaries.boundaries) {
      // Create new layer group for boundaries
      const boundariesLayer = L.layerGroup();
      boundariesLayerRef.current = boundariesLayer;

      // Add each mining lease as a polygon or circle marker depending on zoom
      miningBoundaries.boundaries.features.forEach((feature: any) => {
        const coords = feature.geometry.coordinates[0].map((coord: [number, number]) => [coord[1], coord[0]]);
        
        // Calculate centroid for circle marker
        const centroid = coords.reduce((acc: [number, number], coord: [number, number]) => [acc[0] + coord[0] / coords.length, acc[1] + coord[1] / coords.length], [0, 0]);
        
        // Add polygon (always, for detailed view)
        const polygon = L.polygon(coords, {
          color: '#2ecc71',
          weight: currentZoom < 7 ? 2 : 5,
          fillColor: '#2ecc71',
          fillOpacity: currentZoom < 7 ? 0.3 : 0.5,
          className: 'legal-mining-polygon'
        });

        // Add popup with detailed lease information
        const popupContent = `
          <div style="width: 300px;">
            <h4 style="color: #2c3e50; margin-bottom: 10px;">🏛️ Official Mining Lease</h4>
            <div style="background: #f8f9fa; padding: 10px; border-radius: 5px; margin-bottom: 10px;">
              <p><b>Lease ID:</b> ${feature.properties.lease_id}</p>
              <p><b>Name:</b> ${feature.properties.lease_name}</p>
              <p><b>State:</b> ${feature.properties.state}</p>
              <p><b>District:</b> ${feature.properties.district}</p>
            </div>
            <div style="background: #e8f5e8; padding: 10px; border-radius: 5px; margin-bottom: 10px;">
              <p><b>Mineral:</b> ${feature.properties.mineral}</p>
              <p><b>Area:</b> ${feature.properties.area_hectares} hectares</p>
              <p><b>Lease Type:</b> ${feature.properties.lease_type}</p>
            </div>
            <div style="background: #fff3cd; padding: 10px; border-radius: 5px; margin-bottom: 10px;">
              <p><b>Valid From:</b> ${feature.properties.valid_from}</p>
              <p><b>Valid To:</b> ${feature.properties.valid_to}</p>
            </div>
            ${feature.properties.production_2024 ? `
            <div style="background: #d1ecf1; padding: 10px; border-radius: 5px;">
              <p><b>Production (2024):</b> ${feature.properties.production_2024}</p>
              <p><b>Value (2024):</b> ${feature.properties.value_2024}</p>
            </div>
            ` : ''}
          </div>
        `;
        polygon.bindPopup(popupContent);
        boundariesLayer.addLayer(polygon);
        
        // Add radar ring marker when zoomed out (< 7)
        if (currentZoom < 7) {
          // Create custom HTML marker with radar rings
          const radarIcon = L.divIcon({
            className: 'radar-marker',
            html: `
              <div class="radar-container">
                <div class="radar-ring legal-ring"></div>
                <div class="radar-ring legal-ring" style="animation-delay: 0.6s"></div>
                <div class="radar-ring legal-ring" style="animation-delay: 1.2s"></div>
                <div class="radar-dot legal-dot"></div>
              </div>
            `,
            iconSize: [60, 60],
            iconAnchor: [30, 30]
          });
          const marker = L.marker([centroid[0], centroid[1]], { icon: radarIcon });
          marker.bindPopup(popupContent);
          boundariesLayer.addLayer(marker);
        }
      });

            // Add boundaries layer to map
            boundariesLayer.addTo(mapInstanceRef.current);
          }
        }, [miningBoundaries, showBoundaries, currentZoom]);

        // Add/remove illegal areas when they change
        useEffect(() => {
          if (!mapInstanceRef.current) return;

          // Remove existing illegal areas layer
          if (illegalAreasLayerRef.current) {
            mapInstanceRef.current.removeLayer(illegalAreasLayerRef.current);
          }

          if (illegalAreas.length > 0) {
            // Create new layer group for illegal areas
            const illegalAreasLayer = L.layerGroup();
            illegalAreasLayerRef.current = illegalAreasLayer;

            // Add each illegal mining area as a polygon
            illegalAreas.forEach((area) => {
              const coords = area.coordinates.map((coord: [number, number]) => [coord[1], coord[0]] as [number, number]);
              
              // Get color based on severity
              const getSeverityColor = (severity: string) => {
                switch (severity) {
                  case 'low': return '#10b981';
                  case 'medium': return '#f59e0b';
                  case 'high': return '#ef4444';
                  case 'critical': return '#dc2626';
                  default: return '#6b7280';
                }
              };

              const polygon = L.polygon(coords, {
                color: getSeverityColor(area.severity),
                weight: 6,
                fillColor: getSeverityColor(area.severity),
                fillOpacity: 0.7,
                dashArray: '8, 8'
              });

              // Add popup with area information
              const popupContent = `
                <div style="width: 300px;">
                  <h4 style="color: ${getSeverityColor(area.severity)}; margin: 0 0 10px 0;">🚨 ${area.name}</h4>
                  <p><b>Severity:</b> <span style="color: ${getSeverityColor(area.severity)}; font-weight: bold;">${area.severity.toUpperCase()}</span></p>
                  <p><b>Area:</b> ${area.area_ha} hectares</p>
                  <p><b>Depth:</b> ${area.depth_m} m</p>
                  <p><b>Volume:</b> ${area.volume_m3.toLocaleString()} m³</p>
                  <p><b>Dimensions:</b> ${area.length_m}m × ${area.width_m}m</p>
                  <p style="margin-top: 10px; font-style: italic; color: #666;">${area.description}</p>
                </div>
              `;
              polygon.bindPopup(popupContent);

              // Highlight selected area
              if (selectedIllegalArea && selectedIllegalArea.id === area.id) {
                polygon.setStyle({
                  weight: 8,
                  fillOpacity: 0.8,
                  dashArray: '12, 6'
                });
              }

              illegalAreasLayer.addLayer(polygon);
            });

            // Add illegal areas layer to map
            illegalAreasLayer.addTo(mapInstanceRef.current);
          }
          }, [illegalAreas, selectedIllegalArea]);

  // Add/remove violation zones when they change
  useEffect(() => {
    if (!mapInstanceRef.current) return;

    // Remove existing violation zones layer
    if (violationZonesLayerRef.current) {
      mapInstanceRef.current.removeLayer(violationZonesLayerRef.current);
    }

    if (violationZones.redZones.length > 0 || violationZones.orangeZones.length > 0) {
      // Create new layer group for violation zones
      const violationZonesLayer = L.layerGroup();
      violationZonesLayerRef.current = violationZonesLayer;

      // Add red zones (critical violations)
      violationZones.redZones.forEach((zone) => {
        const coords = zone.geometry.coordinates[0].map((coord: [number, number]) => [coord[1], coord[0]] as [number, number]);
        const centroid = coords.reduce((acc: [number, number], coord: [number, number]) => [acc[0] + coord[0] / coords.length, acc[1] + coord[1] / coords.length], [0, 0]);
        
        const polygon = L.polygon(coords, {
          color: '#dc2626',
          weight: currentZoom < 7 ? 2 : 4,
          fillColor: '#dc2626',
          fillOpacity: currentZoom < 7 ? 0.4 : 0.7,
          dashArray: '10, 5',
          className: 'illegal-mining-polygon'
        });

        // Add popup with zone information
        const popupContent = `
          <div style="width: 300px;">
            <h4 style="color: #dc2626; margin: 0 0 10px 0;">🚨 CRITICAL VIOLATION ZONE</h4>
            <p><b>Severity:</b> <span style="color: #dc2626; font-weight: bold;">CRITICAL</span></p>
            <p><b>Area:</b> ${zone.properties.area_hectares || 0} hectares</p>
            <p><b>Confidence:</b> ${(zone.properties.confidence * 100 || 0).toFixed(1)}%</p>
            <p style="margin-top: 10px; font-style: italic; color: #666;">${zone.properties.description || 'Critical illegal mining violation detected'}</p>
            <p style="margin-top: 10px; font-weight: bold; color: #dc2626;">IMMEDIATE ACTION REQUIRED</p>
          </div>
        `;
        polygon.bindPopup(popupContent);
        violationZonesLayer.addLayer(polygon);
        
        // Add radar ring marker when zoomed out
        if (currentZoom < 7) {
          const radarIcon = L.divIcon({
            className: 'radar-marker',
            html: `
              <div class="radar-container">
                <div class="radar-ring illegal-ring"></div>
                <div class="radar-ring illegal-ring" style="animation-delay: 0.5s"></div>
                <div class="radar-ring illegal-ring" style="animation-delay: 1s"></div>
                <div class="radar-dot illegal-dot"></div>
              </div>
            `,
            iconSize: [70, 70],
            iconAnchor: [35, 35]
          });
          const marker = L.marker([centroid[0], centroid[1]], { icon: radarIcon });
          marker.bindPopup(popupContent);
          violationZonesLayer.addLayer(marker);
        }
      });

      // Add orange zones (warning violations)
      violationZones.orangeZones.forEach((zone) => {
        const coords = zone.geometry.coordinates[0].map((coord: [number, number]) => [coord[1], coord[0]] as [number, number]);
        const centroid = coords.reduce((acc: [number, number], coord: [number, number]) => [acc[0] + coord[0] / coords.length, acc[1] + coord[1] / coords.length], [0, 0]);
        
        const polygon = L.polygon(coords, {
          color: '#f59e0b',
          weight: currentZoom < 7 ? 2 : 3,
          fillColor: '#f59e0b',
          fillOpacity: currentZoom < 7 ? 0.3 : 0.6,
          dashArray: '8, 8',
          className: 'illegal-mining-polygon'
        });

        // Add popup with zone information
        const popupContent = `
          <div style="width: 300px;">
            <h4 style="color: #f59e0b; margin: 0 0 10px 0;">⚠️ WARNING ZONE</h4>
            <p><b>Severity:</b> <span style="color: #f59e0b; font-weight: bold;">WARNING</span></p>
            <p><b>Area:</b> ${zone.properties.area_hectares || 0} hectares</p>
            <p><b>Confidence:</b> ${(zone.properties.confidence * 100 || 0).toFixed(1)}%</p>
            <p style="margin-top: 10px; font-style: italic; color: #666;">${zone.properties.description || 'Potential illegal mining activity detected'}</p>
            <p style="margin-top: 10px; font-weight: bold; color: #f59e0b;">MONITORING REQUIRED</p>
          </div>
        `;
        polygon.bindPopup(popupContent);
        violationZonesLayer.addLayer(polygon);
        
        // Add radar ring marker when zoomed out
        if (currentZoom < 7) {
          const radarIcon = L.divIcon({
            className: 'radar-marker',
            html: `
              <div class="radar-container">
                <div class="radar-ring warning-ring"></div>
                <div class="radar-ring warning-ring" style="animation-delay: 0.6s"></div>
                <div class="radar-ring warning-ring" style="animation-delay: 1.2s"></div>
                <div class="radar-dot warning-dot"></div>
              </div>
            `,
            iconSize: [65, 65],
            iconAnchor: [32, 32]
          });
          const marker = L.marker([centroid[0], centroid[1]], { icon: radarIcon });
          marker.bindPopup(popupContent);
          violationZonesLayer.addLayer(marker);
        }
      });

      // Add violation zones layer to map
      violationZonesLayer.addTo(mapInstanceRef.current);
    }
  }, [violationZones, currentZoom]);

  // Add/remove satellite data when it changes
  useEffect(() => {
    if (!mapInstanceRef.current) return;

    // Remove existing satellite layer
    if (satelliteLayerRef.current) {
      mapInstanceRef.current.removeLayer(satelliteLayerRef.current);
    }

    if (satelliteData && satelliteData.geojson && satelliteData.geojson.features.length > 0) {
      // Create new layer group for satellite data
      const satelliteLayer = L.layerGroup();
      satelliteLayerRef.current = satelliteLayer;

      // Add satellite-detected mining areas
      satelliteData.geojson.features.forEach((feature: any) => {
        const coords = feature.geometry.coordinates[0].map((coord: [number, number]) => [coord[1], coord[0]] as [number, number]);
        const centroid = coords.reduce((acc: [number, number], coord: [number, number]) => [acc[0] + coord[0] / coords.length, acc[1] + coord[1] / coords.length], [0, 0]);
        
        const polygon = L.polygon(coords, {
          color: '#3498db',
          weight: currentZoom < 7 ? 1 : 2,
          fillColor: '#3498db',
          fillOpacity: currentZoom < 7 ? 0.3 : 0.6,
          dashArray: '5, 5',
          className: 'satellite-mining-polygon'
        });

        // Add popup with satellite data information
        const popupContent = `
          <div style="width: 300px;">
            <h4 style="color: #3498db; margin: 0 0 10px 0;">🛰️ SATELLITE DETECTED MINING AREA</h4>
            <p><b>Source:</b> ${feature.properties.source || 'Satellite'}</p>
            <p><b>Area:</b> ${feature.properties.area_hectares || 0} hectares</p>
            <p><b>Confidence:</b> ${((feature.properties.confidence || 0) * 100).toFixed(1)}%</p>
            <p><b>NDVI:</b> ${feature.properties.ndvi || 0}</p>
            <p><b>BSI:</b> ${feature.properties.bsi || 0}</p>
            <p><b>Resolution:</b> ${feature.properties.resolution || 'Unknown'}</p>
            <p><b>Detection Date:</b> ${feature.properties.detection_date || 'Unknown'}</p>
            <p style="margin-top: 10px; font-style: italic; color: #666;">Detected via spectral analysis</p>
          </div>
        `;
        polygon.bindPopup(popupContent);
        satelliteLayer.addLayer(polygon);
        
        // Add radar ring marker when zoomed out
        if (currentZoom < 7) {
          const radarIcon = L.divIcon({
            className: 'radar-marker',
            html: `
              <div class="radar-container">
                <div class="radar-ring satellite-ring"></div>
                <div class="radar-ring satellite-ring" style="animation-delay: 0.7s"></div>
                <div class="radar-ring satellite-ring" style="animation-delay: 1.4s"></div>
                <div class="radar-dot satellite-dot"></div>
              </div>
            `,
            iconSize: [55, 55],
            iconAnchor: [27, 27]
          });
          const marker = L.marker([centroid[0], centroid[1]], { icon: radarIcon });
          marker.bindPopup(popupContent);
          satelliteLayer.addLayer(marker);
        }
      });

      // Add satellite layer to map
      satelliteLayer.addTo(mapInstanceRef.current);
    }
  }, [satelliteData, currentZoom]);

  return <div ref={mapRef} style={style} />;
};

export default MapComponent;
