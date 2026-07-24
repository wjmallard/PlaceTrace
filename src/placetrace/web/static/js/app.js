// PlaceTrace Alpine.js App

// Visit id -> Leaflet marker. Kept outside the Alpine component so the
// markers are not wrapped in reactive proxies.
const visitMarkers = new Map();

function placeTraceApp() {
    return {
        // State
        map: null,
        markerLayer: null,
        spatialFilterMarker: null,
        spatialFilterCircle: null,
        loading: false,
        visits: [],
        
        // Geohash encoding function
        encodeGeohash(lat, lon, precision = 10) {
            const base32 = '0123456789bcdefghjkmnpqrstuvwxyz';
            let idx = 0;
            let bit = 0;
            let evenBit = true;
            let geohash = '';
            
            let latMin = -90, latMax = 90;
            let lonMin = -180, lonMax = 180;
            
            while (geohash.length < precision) {
                if (evenBit) {
                    const lonMid = (lonMin + lonMax) / 2;
                    if (lon > lonMid) {
                        idx = (idx << 1) + 1;
                        lonMin = lonMid;
                    } else {
                        idx = idx << 1;
                        lonMax = lonMid;
                    }
                } else {
                    const latMid = (latMin + latMax) / 2;
                    if (lat > latMid) {
                        idx = (idx << 1) + 1;
                        latMin = latMid;
                    } else {
                        idx = idx << 1;
                        latMax = latMid;
                    }
                }
                evenBit = !evenBit;
                
                if (++bit === 5) {
                    geohash += base32[idx];
                    bit = 0;
                    idx = 0;
                }
            }
            
            return geohash;
        },
        
        trips: [],
        activeTripTab: 'day',
        selectedVisit: null,  // Currently selected visit
        spaceFilterEnabled: false,
        timeFilterEnabled: false,
        
        // Centralized filter manager
        filterManager: {
            spatial: { lat: null, lon: null, radius_km: 10 },
            temporal: { start: null, end: null },
            tripId: null,

            // Build URLSearchParams from current filter state
            buildParams(options = {}) {
                const params = new URLSearchParams();
                
                // Spatial filter (if active)
                if (this.spatial.lat !== null) {
                    params.append('lat', this.spatial.lat);
                    params.append('lon', this.spatial.lon);
                    params.append('radius_km', this.spatial.radius_km);
                }
                // Bbox fallback (only if no spatial filter AND includeBbox requested)
                else if (options.includeBbox && options.map) {
                    const bounds = options.map.getBounds();
                    const bbox = `${bounds.getSouth()},${bounds.getWest()},${bounds.getNorth()},${bounds.getEast()}`;
                    params.append('bbox', bbox);
                }
                
                // Temporal filter
                if (this.temporal.start) {
                    params.append('start_date', this.temporal.start);
                }
                if (this.temporal.end) {
                    params.append('end_date', this.temporal.end);
                }
                
                // Trip filter
                if (this.tripId) {
                    params.append('trip_id', this.tripId);
                    params.append('limit', 5000);
                }

                return params;
            },
            
            // Clear specific filters
            clearSpatial() {
                this.spatial = { lat: null, lon: null, radius_km: 10 };
            },
            
            clearTemporal() {
                this.temporal = { start: null, end: null };
            },
            
            clearTrip() {
                this.tripId = null;
            },
            
            clearAll() {
                this.spatial = { lat: null, lon: null, radius_km: 10 };
                this.temporal = { start: null, end: null };
                this.tripId = null;
            }
        },
        
        // UI state for date inputs
        startDate: '',
        endDate: '',
        expandedYears: {},
        showTripsSection: false,  // Default collapsed
        selectedDay: null,
        showMovement: false,
        movements: [],
        movementLayer: null,
        showAllNearbyVisits: false,  // Default: show only selected day visits
        savedVisitState: null,   // Store visits to restore when showing all nearby
        
        // Viewport-driven reload state
        moveDebounce: null,     // Debounce timer for moveend refreshes
        visitRequestSeq: 0,     // Discards out-of-order visit responses

        // Visit table panel state
        showVisitTable: false,
        visitTableData: [],
        visitTableSortedData: [],  // Cached sorted data
        visitTableSort: {
            column: 'local_start_date',
            ascending: false  // Default: most recent first
        },
        mapBoundsVersion: 0,  // Increment to trigger table direction updates
        
        // Initialize
        init() {
            this.initMap();
            // Wait for map to be ready before loading data
            this.map.whenReady(() => {
                this.loadTrips();
                this.loadRecentVisits();
            });
        },
        
        // Initialize Leaflet map
        initMap() {
            // Map settings injected by the template from config.yaml
            const mapConfig = window.PLACETRACE_CONFIG;

            // Create map at the configured center (disable scroll wheel zoom during init)
            this.map = L.map('map', {
                scrollWheelZoom: false
            }).setView(
                [mapConfig.default_center.lat, mapConfig.default_center.lon],
                mapConfig.default_zoom
            );

            // Add base map tiles (keepBuffer retains extra off-screen tiles while panning)
            L.tileLayer(mapConfig.tiles.url, {
                attribution: mapConfig.tiles.attribution,
                maxZoom: 19,
                keepBuffer: 4
            }).addTo(this.map);
            
            // Create marker cluster group
            this.markerLayer = L.markerClusterGroup({
                maxClusterRadius: 50,
                spiderfyOnMaxZoom: true,
                showCoverageOnHover: false,
                zoomToBoundsOnClick: true,
                animate: false  // Disable all animations to prevent conflicts
            }).addTo(this.map);
            
            // Create movement layer (below markers)
            this.movementLayer = L.layerGroup().addTo(this.map);
            
            // Add click handler for spatial filter
            this.map.on('click', (e) => {
                // Only set spatial filter if space filter is enabled
                if (this.spaceFilterEnabled) {
                    this.setSpatialFilter(e.latlng.lat, e.latlng.lng);
                    this.selectedVisit = null; // Clear selected visit since we're clicking arbitrary point
                    this.loadRecentVisits();
                }
            });
            
            // Add moveend listener to reload visits based on visible bounds
            this.map.on('moveend', () => {
                // Increment bounds version to trigger table direction updates
                this.mapBoundsVersion++;

                // Only reload if no filters are active AND movement tracks are not active
                if (!this.filterManager.tripId && !this.filterManager.spatial.lat && !this.filterManager.temporal.start && !this.showMovement) {
                    // Debounced background refresh: no loading overlay while panning/zooming
                    clearTimeout(this.moveDebounce);
                    this.moveDebounce = setTimeout(() => this.loadRecentVisits({ background: true }), 250);
                }
            });
            
            // Enable scroll wheel zoom after map is fully initialized
            this.map.whenReady(() => {
                this.map.scrollWheelZoom.enable();
            });
        },
        
        // Load all trips and populate tabs
        async loadTrips() {
            try {
                const params = new URLSearchParams();
                params.append('limit', 1000);  // API default is 100, which would truncate the sidebar

                // Add date range filter if active
                if (this.filterManager.temporal.start) {
                    params.append('start_date', this.filterManager.temporal.start);
                }
                if (this.filterManager.temporal.end) {
                    params.append('end_date', this.filterManager.temporal.end);
                }
                
                const response = await fetch(`/api/trips?${params}`);
                const data = await response.json();
                this.trips = data.trips;
            } catch (error) {
                console.error('Error loading trips:', error);
            }
        },
        
        // Load all visits (no date/limit filters)
        // background: true skips the loading overlay (viewport refreshes on pan/zoom)
        async loadRecentVisits({ background = false } = {}) {
            if (!background) {
                this.loading = true;
            }
            const seq = ++this.visitRequestSeq;

            try {
                // Build params using filterManager - include bbox only if no other spatial filter
                const params = this.filterManager.buildParams({
                    includeBbox: !this.filterManager.spatial.lat && !this.filterManager.tripId && !this.filterManager.temporal.start && !this.showMovement,
                    map: this.map
                });

                const response = await fetch(`/api/visits?${params}`);
                const data = await response.json();

                // A newer request superseded this one - discard the stale response
                if (seq !== this.visitRequestSeq) {
                    return;
                }

                this.visits = data.visits;
                this.renderMarkers();
                this.fitMapToVisits();

                // Reload table if visible
                if (this.showVisitTable) {
                    await this.loadVisitTableData();
                }

            } catch (error) {
                console.error('Error loading visits:', error);
            } finally {
                if (!background) {
                    this.loading = false;
                }
            }
        },
        
        // Load visits for selected trip
        async loadTripVisits(tripId) {
            this.filterManager.tripId = tripId;
            await this.loadRecentVisits();
        },
        
        // Build the circle icon for a visit marker
        visitIcon(isSelected) {
            const size = 12;
            const fillColor = isSelected ? '#DC2626' : '#3B82F6';  // darker red or blue
            const borderColor = isSelected ? '#991B1B' : '#1E40AF';  // dark red or dark blue
            const borderWidth = isSelected ? 3 : 1;

            const markerHtml = `
                <div style="
                    width: ${size}px;
                    height: ${size}px;
                    background-color: ${fillColor};
                    border: ${borderWidth}px solid ${borderColor};
                    border-radius: 50%;
                    opacity: 0.9;
                ">
                </div>
            `;

            return L.divIcon({
                html: markerHtml,
                className: '',
                iconSize: [size, size],
                iconAnchor: [size/2, size/2]
            });
        },

        // Create a marker (with popup and click handler) for a visit
        createVisitMarker(visit) {
            const isSelected = this.selectedVisit && this.selectedVisit.id === visit.id;
            const marker = L.marker(
                [visit.latitude, visit.longitude],
                { icon: this.visitIcon(isSelected) }
            );
            marker.visitId = visit.id;

            // Popup with visit info
            const popupContent = `
                <div class="text-sm">
                    <div class="font-semibold">${visit.location_name}</div>
                    <div class="text-gray-600 mt-1">
                        ${this.formatLocalDateTime(visit.local_start_date, visit.local_start_time)}
                    </div>
                    <div class="text-gray-600">
                        ${visit.duration_minutes} minutes
                    </div>
                </div>
            `;
            marker.bindPopup(popupContent);

            // Click handler - select this visit and optionally set spatial filter
            marker.on('click', (e) => {
                // If space filter is enabled, set spatial filter centered on this visit
                if (this.spaceFilterEnabled) {
                    // Stop event propagation so map click handler doesn't fire
                    L.DomEvent.stopPropagation(e);
                    this.setSpatialFilter(visit.latitude, visit.longitude, false); // Don't show X, visit marker shows selection
                    this.loadRecentVisits();
                }

                this.setSelectedVisit(visit);

                // Scroll table to show this visit
                this.scrollTableToVisit(visit.id);
            });

            return marker;
        },

        // Select (or clear, with null) a visit, restyling only the affected markers
        setSelectedVisit(visit) {
            const prevId = this.selectedVisit ? this.selectedVisit.id : null;
            this.selectedVisit = visit;

            const prevMarker = prevId !== null ? visitMarkers.get(prevId) : null;
            if (prevMarker) {
                prevMarker.setIcon(this.visitIcon(false));
            }

            const newMarker = visit ? visitMarkers.get(visit.id) : null;
            if (newMarker) {
                newMarker.setIcon(this.visitIcon(true));
            }
        },

        // Sync visit markers with this.visits, adding/removing only what changed
        // (a refresh that returns the same visits does no DOM work at all).
        // Membership is derived from the cluster group itself each time, not from
        // our own bookkeeping, so any transient divergence (an operation deferred
        // or dropped mid zoom animation) heals on the next sync instead of
        // leaving permanent holes.
        renderMarkers() {
            const wanted = new Map(this.visits.map(visit => [visit.id, visit]));

            const toRemove = [];
            visitMarkers.clear();
            for (const marker of this.markerLayer.getLayers()) {
                if (wanted.has(marker.visitId) && !visitMarkers.has(marker.visitId)) {
                    visitMarkers.set(marker.visitId, marker);
                } else {
                    // Unwanted, or a duplicate of one already kept
                    toRemove.push(marker);
                }
            }

            const toAdd = [];
            for (const [id, visit] of wanted) {
                if (!visitMarkers.has(id)) {
                    const marker = this.createVisitMarker(visit);
                    visitMarkers.set(id, marker);
                    toAdd.push(marker);
                }
            }

            // Bulk operations recluster once instead of once per marker
            if (toRemove.length > 0) {
                this.markerLayer.removeLayers(toRemove);
            }
            if (toAdd.length > 0) {
                this.markerLayer.addLayers(toAdd);
            }
        },
        
        // Fit map bounds to show all visits
        fitMapToVisits() {
            if (this.visits.length === 0) return;
            
            // Only fit bounds when we have filters active
            // (Don't fit when using bbox - that would cause infinite loop)
            const hasFilters = this.filterManager.tripId || this.filterManager.spatial.lat !== null || this.filterManager.temporal.start;
            if (!hasFilters) return;
            
            const bounds = L.latLngBounds(
                this.visits.map(v => [v.latitude, v.longitude])
            );
            
            // Disable animation to avoid popup animation conflicts
            this.map.fitBounds(bounds, { 
                padding: [50, 50],
                animate: false
            });
        },
        
        // Get trips for active tab
        get filteredTrips() {
            // Map tab name to API category format: 'day' -> 'Day Trip'
            const categoryMap = {
                'day': 'Day Trip',
                'short': 'Short Trip',
                'long': 'Long Trip'
            };
            const targetCategory = categoryMap[this.activeTripTab];
            return this.trips.filter(trip => trip.category === targetCategory);
        },
        
        // Get trip counts for each category
        get tripCounts() {
            return {
                day: this.trips.filter(t => t.category === 'Day Trip').length,
                short: this.trips.filter(t => t.category === 'Short Trip').length,
                long: this.trips.filter(t => t.category === 'Long Trip').length
            };
        },
        
        // Parse a YYYY-MM-DD string as local time (bare date strings parse as UTC)
        parseLocalDate(dateStr) {
            return new Date(dateStr + 'T00:00:00');
        },

        // Group trips by year
        get tripsByYear() {
            const grouped = {};
            const currentYear = new Date().getFullYear();

            this.filteredTrips.forEach(trip => {
                const year = this.parseLocalDate(trip.local_start_date).getFullYear();
                if (!grouped[year]) {
                    grouped[year] = [];
                    // Expand current year by default
                    if (!this.expandedYears.hasOwnProperty(year)) {
                        this.expandedYears[year] = (year === currentYear);
                    }
                }
                grouped[year].push(trip);
            });
            
            // Sort years descending
            return Object.keys(grouped)
                .sort((a, b) => b - a)
                .map(year => ({
                    year: parseInt(year),
                    trips: grouped[year],
                    count: grouped[year].length
                }));
        },
        
        // Check if year grouping is needed
        get shouldShowYearGrouping() {
            const years = [...new Set(this.filteredTrips.map(trip =>
                this.parseLocalDate(trip.local_start_date).getFullYear()
            ))];
            return years.length > 1;
        },
        
        // Toggle year expansion
        toggleYear(year) {
            this.expandedYears[year] = !this.expandedYears[year];
        },
        
        // Select a trip
        selectTrip(trip) {
            // Update movement date to trip start date (already in YYYY-MM-DD format)
            this.selectedDay = trip.local_start_date;

            // Load visits for this trip
            this.loadTripVisits(trip.id);
        },
        
        // Set spatial filter (map click)
        setSpatialFilter(lat, lon, showMarker = true) {
            this.filterManager.spatial.lat = lat;
            this.filterManager.spatial.lon = lon;
            
            // Remove old visual indicators
            if (this.spatialFilterMarker) {
                this.map.removeLayer(this.spatialFilterMarker);
                this.spatialFilterMarker = null;
            }
            if (this.spatialFilterCircle) {
                this.map.removeLayer(this.spatialFilterCircle);
            }
            
            // Add red X marker at center point (only for arbitrary point clicks, not visit clicks)
            if (showMarker) {
                const xIcon = L.divIcon({
                    className: 'spatial-filter-x',
                    html: '<div style="position: relative; width: 24px; height: 24px;"><div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); color: #EF4444; font-size: 28px; font-weight: bold; line-height: 1;">✕</div></div>',
                    iconSize: [24, 24],
                    iconAnchor: [12, 12]
                });
                
                this.spatialFilterMarker = L.marker([lat, lon], {
                    icon: xIcon,
                    interactive: false  // Don't interfere with map clicks
                }).addTo(this.map);
            }
            
            // Add circle showing radius
            this.spatialFilterCircle = L.circle([lat, lon], {
                radius: this.filterManager.spatial.radius_km * 1000,  // Convert km to meters
                color: '#10B981',
                fillColor: '#10B981',
                fillOpacity: 0.1,
                weight: 2
            }).addTo(this.map);

            // Note: Caller is responsible for calling loadRecentVisits()
        },
        
        // Clear spatial filter
        clearSpatialFilter() {
            this.filterManager.clearSpatial();
            this.spaceFilterEnabled = false; // Disable the toggle
            
            // Remove visual indicators
            if (this.spatialFilterMarker) {
                this.map.removeLayer(this.spatialFilterMarker);
                this.spatialFilterMarker = null;
            }
            if (this.spatialFilterCircle) {
                this.map.removeLayer(this.spatialFilterCircle);
                this.spatialFilterCircle = null;
            }
            
            // Reload visits without spatial filter
            this.loadRecentVisits();
        },
        
        // Update radius circle when slider changes
        updateRadiusCircle() {
            // If spatial filter is active, update the circle
            if (this.filterManager.spatial.lat !== null) {
                // Update circle radius
                if (this.spatialFilterCircle) {
                    this.map.removeLayer(this.spatialFilterCircle);
                }
                
                this.spatialFilterCircle = L.circle(
                    [this.filterManager.spatial.lat, this.filterManager.spatial.lon],
                    {
                        radius: this.filterManager.spatial.radius_km * 1000,  // Convert km to meters
                        color: '#10B981',
                        fillColor: '#10B981',
                        fillOpacity: 0.1,
                        weight: 2
                    }
                ).addTo(this.map);

                // Note: Caller responsible for reloading visits
            }
        },
        
        // Handle time filter enable/disable
        toggleTimeFilter() {
            if (!this.timeFilterEnabled) {
                // Disabling - clear filter state but keep the date inputs
                const wasActive = this.filterManager.temporal.start !== null;
                
                if (wasActive) {
                    // Clear the filter in filterManager
                    this.filterManager.clearTemporal();
                    
                    // Reload without the filter
                    this.loadTrips();
                    this.loadRecentVisits();
                }
            } else {
                // Enabling - apply filter immediately if valid dates exist
                if (this.startDate && this.endDate) {
                    this.applyDateFilter();
                }
            }
        },
        
        // Handle space filter enable/disable
        toggleSpaceFilter() {
            if (!this.spaceFilterEnabled) {
                // Disabling - clear spatial filter only if it was active
                const wasActive = this.filterManager.spatial.lat !== null;
                
                if (wasActive) {
                    this.clearSpatialFilter();
                }
            }
            // Enabling - just enable the controls, don't apply filter yet
        },
        
        // Get radius slider position (0-6) from radius_km
        getRadiusSliderPosition() {
            const radiusOptions = [1, 2, 5, 10, 20, 50, 100];
            const index = radiusOptions.indexOf(this.filterManager.spatial.radius_km);
            return index >= 0 ? index : 3; // Default to 10km (index 3)
        },
        
        // Set radius_km from slider position (0-6)
        setRadiusFromSlider(position) {
            const radiusOptions = [1, 2, 5, 10, 20, 50, 100];
            this.filterManager.spatial.radius_km = radiusOptions[position];
            this.updateRadiusCircle();
            
            // Only reload if spatial filter is already active
            if (this.filterManager.spatial.lat !== null) {
                this.loadRecentVisits();
            }
        },
        
        // Handle start date change
        onStartDateChange() {
            if (!this.startDate) {
                return;
            }

            // If end is empty or less than start, set end = start
            if (!this.endDate || this.endDate < this.startDate) {
                this.endDate = this.startDate;
            }
            
            // Apply the filter
            this.applyDateFilter();
        },
        
        // Handle end date change
        onEndDateChange() {
            if (!this.endDate) {
                return;
            }
            
            // If end < start, set end = start
            if (this.endDate < this.startDate) {
                this.endDate = this.startDate;
            }

            // Apply the filter
            this.applyDateFilter();
        },
        
        // Apply simple date filter (no parsing, just use the date inputs directly)
        applyDateFilter() {
            if (!this.startDate || !this.endDate) {
                return;
            }
            
            this.filterManager.temporal.start = this.startDate;
            this.filterManager.temporal.end = this.endDate;

            // Reload trips and visits with date filter
            this.loadTrips();
            this.loadRecentVisits();
            
            // Reload table if visible
            if (this.showVisitTable) {
                this.loadVisitTableData();
            }
        },
        
        // Quick preset: set the date filter to a past window ending today
        setPastRange({ days = 0, months = 0, years = 0 }) {
            const now = new Date();
            const past = new Date(now);
            past.setDate(past.getDate() - days);
            past.setMonth(past.getMonth() - months);
            past.setFullYear(past.getFullYear() - years);

            this.timeFilterEnabled = true;
            this.startDate = past.toISOString().split('T')[0];
            this.endDate = now.toISOString().split('T')[0];

            this.applyDateFilter();
        },

        setPastWeek() { this.setPastRange({ days: 7 }); },
        setPastMonth() { this.setPastRange({ months: 1 }); },
        setPastYear() { this.setPastRange({ years: 1 }); },

        // Clear date range filter
        clearDateFilter() {
            this.filterManager.clearTemporal();
            this.timeFilterEnabled = false; // Disable the toggle
            this.startDate = '';
            this.endDate = '';
            
            // Reload trips without date filter
            this.loadTrips();
            
            // Reload visits without date filter
            this.loadRecentVisits();
        },
        
        // Format date range for trip display
        formatDateRange(startTime, endTime) {
            const start = this.parseLocalDate(startTime);
            const end = this.parseLocalDate(endTime);
            
            const options = { month: 'short', day: 'numeric', year: 'numeric' };
            
            if (start.getFullYear() === end.getFullYear() &&
                start.getMonth() === end.getMonth() &&
                start.getDate() === end.getDate()) {
                return start.toLocaleDateString('en-US', options);
            }
            
            return `${start.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} - ${end.toLocaleDateString('en-US', options)}`;
        },
        
        // Format datetime for visit display
        formatDateTime(datetime) {
            // Local time strings come as "HH:MM:SS" - combine with date for display
            // If it's an ISO datetime, parse it; otherwise treat as local time string
            if (!datetime) return '';
            
            // If datetime contains 'T', it's an ISO datetime string
            if (datetime.includes('T')) {
                const date = new Date(datetime);
                return date.toLocaleDateString('en-US', {
                    month: 'short',
                    day: 'numeric',
                    year: 'numeric',
                    hour: 'numeric',
                    minute: '2-digit'
                });
            }
            
            // Otherwise it's just a time string like "14:30:00"
            // Return time portion only
            const [hours, minutes] = datetime.split(':');
            const hour = parseInt(hours);
            const ampm = hour >= 12 ? 'PM' : 'AM';
            const displayHour = hour % 12 || 12;
            return `${displayHour}:${minutes} ${ampm}`;
        },
        
        formatLocalDateTime(date, time) {
            // Combine local_date and local_time for display
            if (!date || !time) return '';
            
            const dateObj = new Date(date + 'T' + time);
            
            // Format date part
            const datePart = dateObj.toLocaleDateString('en-US', {
                month: 'short',
                day: 'numeric',
                year: 'numeric'
            });
            
            // Format time part
            const timePart = dateObj.toLocaleTimeString('en-US', {
                hour: 'numeric',
                minute: '2-digit'
            });
            
            return `${datePart} • ${timePart}`;
        },
        
        formatLocalDate(date, time) {
            // Format just the date part
            if (!date || !time) return '';
            const dateObj = new Date(date + 'T' + time);
            return dateObj.toLocaleDateString('en-US', {
                month: 'short',
                day: 'numeric',
                year: 'numeric'
            });
        },
        
        formatLocalTime(date, time) {
            // Format just the time part
            if (!date || !time) return '';
            const dateObj = new Date(date + 'T' + time);
            return dateObj.toLocaleTimeString('en-US', {
                hour: 'numeric',
                minute: '2-digit'
            });
        },
        
        // Load movements for selected day
        async loadMovements() {
            if (!this.selectedDay || !this.showMovement) {
                this.clearMovementLayer();
                return;
            }
            
            this.loading = true;
            
            try {
                // Save current visits before first load (if showing only this day)
                if (!this.showAllNearbyVisits && !this.savedVisitState) {
                    this.savedVisitState = [...this.visits];
                }
                
                // Fetch movements and visits for the selected day
                const [movementsResponse, visitsResponse] = await Promise.all([
                    fetch(`/api/movements?date=${this.selectedDay}&include_routes=true`),
                    fetch(`/api/visits?date=${this.selectedDay}`)
                ]);
                
                const movementsData = await movementsResponse.json();
                const visitsData = await visitsResponse.json();
                
                this.movements = movementsData.movements || [];
                const dayVisits = visitsData.visits || [];
                
                // Render movement tracks with day visits on timeline
                this.renderMovementsWithVisits(dayVisits);
                
                // If NOT showing all nearby, update main markers to show only this day
                if (!this.showAllNearbyVisits) {
                    this.visits = dayVisits;
                    this.renderMarkers();
                }
                
            } catch (error) {
                console.error('Error loading movements:', error);
            } finally {
                this.loading = false;
            }
        },
        
        // Render movement polylines with visits as chronological chain
        renderMovementsWithVisits(dayVisits) {
            this.clearMovementLayer();
            
            const activityColors = {
                'WALKING': '#4ade80',
                'RUNNING': '#22c55e',
                'CYCLING': '#f59e0b',
                'DRIVING': '#3b82f6',
                'IN_VEHICLE': '#3b82f6',
                'IN_PASSENGER_VEHICLE': '#3b82f6',
                'IN_BUS': '#8b5cf6',
                'IN_TRAIN': '#8b5cf6',
                'FLYING': '#ef4444',
                'MOTORCYCLING': '#f97316',
                'UNKNOWN': '#9ca3af'
            };
            
            const bounds = [];
            
            // Build chronological timeline of visits and movements
            const timeline = [];
            
            // Add visits
            dayVisits.forEach(visit => {
                timeline.push({
                    type: 'visit',
                    time: new Date(visit.local_start_date + 'T' + visit.local_start_time),
                    data: visit,
                    lat: visit.latitude,
                    lon: visit.longitude
                });
            });
            
            // Add movements
            this.movements.forEach(movement => {
                timeline.push({
                    type: 'movement',
                    time: new Date(movement.local_start_date + 'T' + movement.local_start_time),
                    data: movement
                });
            });
            
            // Sort chronologically
            timeline.sort((a, b) => a.time - b.time);
                        
            // Render timeline with connectors
            for (let i = 0; i < timeline.length; i++) {
                const item = timeline[i];
                const nextItem = timeline[i + 1];
                
                if (item.type === 'visit') {
                    // Draw visit marker (smaller when in movement mode)
                    const marker = L.circleMarker([item.lat, item.lon], {
                        radius: 4,
                        fillColor: '#3B82F6',
                        color: '#1E40AF',
                        weight: 1,
                        opacity: 1,
                        fillOpacity: 0.8
                    });
                    
                    const popupContent = `
                        <div class="text-sm">
                            <div class="font-semibold">${item.data.location_name}</div>
                            <div class="text-gray-600 mt-1">
                                ${this.formatLocalDateTime(item.data.local_start_date, item.data.local_start_time)}
                            </div>
                            <div class="text-gray-600">
                                ${item.data.duration_minutes} minutes
                            </div>
                        </div>
                    `;
                    marker.bindPopup(popupContent);
                    marker.addTo(this.movementLayer);
                    bounds.push([item.lat, item.lon]);
                    
                    // Draw connector to next item
                    if (nextItem) {
                        let nextLat, nextLon;
                        if (nextItem.type === 'movement') {
                            nextLat = nextItem.data.start_latitude;
                            nextLon = nextItem.data.start_longitude;
                        } else {
                            nextLat = nextItem.lat;
                            nextLon = nextItem.lon;
                        }
                        
                        // Gray dashed connector line
                        const connector = L.polyline(
                            [[item.lat, item.lon], [nextLat, nextLon]],
                            {
                                color: '#9ca3af',
                                weight: 2,
                                opacity: 0.5,
                                dashArray: '5, 5'
                            }
                        );
                        connector.addTo(this.movementLayer);
                    }
                    
                } else if (item.type === 'movement') {
                    // Draw movement track
                    let path;
                    if (item.data.route_geojson && item.data.route_geojson.coordinates) {
                        // GeoJSON uses [lon, lat], Leaflet uses [lat, lon] - flip them
                        path = item.data.route_geojson.coordinates.map(coord => [coord[1], coord[0]]);
                    } else {
                        // Draw straight line from start to end
                        path = [
                            [item.data.start_latitude, item.data.start_longitude],
                            [item.data.end_latitude, item.data.end_longitude]
                        ];
                    }
                    
                    if (path.length >= 2) {
                        path.forEach(point => bounds.push(point));
                        
                        const polyline = L.polyline(path, {
                            color: activityColors[item.data.activity_type] || activityColors['UNKNOWN'],
                            weight: 4,
                            opacity: 0.7,
                            smoothFactor: 1
                        });
                        
                        // Add popup with segment info
                        const startTime = this.formatDateTime(item.data.local_start_time);
                        const endTime = this.formatDateTime(item.data.local_end_time);
                        
                        const activityLabel = item.data.activity_type || 'Movement';
                        const distanceKm = (item.data.distance_meters / 1000).toFixed(1);
                        
                        polyline.bindPopup(`
                            <div class="text-sm">
                                <div class="font-semibold">${activityLabel}</div>
                                <div class="text-gray-600 mt-1">${startTime} - ${endTime}</div>
                                <div class="text-gray-600">Distance: ${distanceKm} km</div>
                            </div>
                        `);
                        
                        polyline.addTo(this.movementLayer);
                        
                        // Draw connector to next item (any type)
                        if (nextItem) {
                            let nextLat, nextLon;
                            if (nextItem.type === 'visit') {
                                nextLat = nextItem.lat;
                                nextLon = nextItem.lon;
                            } else {
                                // Next item is a movement
                                nextLat = nextItem.data.start_latitude;
                                nextLon = nextItem.data.start_longitude;
                            }
                            
                            const connector = L.polyline(
                                [path[path.length - 1], [nextLat, nextLon]],
                                {
                                    color: '#9ca3af',
                                    weight: 2,
                                    opacity: 0.5,
                                    dashArray: '5, 5'
                                }
                            );
                            connector.addTo(this.movementLayer);
                        }
                    }
                }
            }
            
            // Fit map to all bounds
            if (bounds.length > 0) {
                this.map.fitBounds(L.latLngBounds(bounds), { 
                    padding: [50, 50],
                    animate: false
                });
            }
        },
        
        // Clear movement layer
        clearMovementLayer() {
            if (this.movementLayer) {
                this.movementLayer.clearLayers();
            }
        },
        
        // Toggle movement display
        async toggleMovement() {
            if (this.showMovement) {
                // Turning ON tracks
                // If a visit is selected but no day is set, use the visit's date
                if (this.selectedVisit && !this.selectedDay) {
                    this.selectedDay = this.selectedVisit.local_start_date;
                }
                
                // Load movements if we have a day
                if (this.selectedDay) {
                    await this.loadMovements();
                }
            } else {
                // Turning OFF tracks
                this.clearMovementLayer();
                
                // Only reload visits if we had actually loaded movement data
                // (savedVisitState being set indicates we changed the visit state)
                if (this.savedVisitState) {
                    this.savedVisitState = null;
                    await this.loadRecentVisits();
                }
            }
        },
        
        // Toggle show all nearby visits
        async toggleShowAllNearbyVisits() {
            if (this.showAllNearbyVisits) {
                // Enabling show all - only reload if we have movement data loaded
                if (this.showMovement && this.selectedDay && this.savedVisitState) {
                    this.savedVisitState = null;
                    await this.loadRecentVisits();
                }
            } else {
                // Disabling show all - show only day visits
                if (this.showMovement && this.selectedDay) {
                    // Re-load movements to get day visits
                    await this.loadMovements();
                }
            }
        },
        
        // Navigate to previous day
        async prevDay() {
            if (!this.selectedDay) return;
            const date = new Date(this.selectedDay);
            date.setDate(date.getDate() - 1);
            this.selectedDay = date.toISOString().split('T')[0];
            await this.loadMovements();
        },
        
        // Navigate to next day
        async nextDay() {
            if (!this.selectedDay) return;
            const date = new Date(this.selectedDay);
            date.setDate(date.getDate() + 1);
            this.selectedDay = date.toISOString().split('T')[0];
            await this.loadMovements();
        },
        
        // Clear selected visit
        clearSelectedVisit() {
            this.setSelectedVisit(null);
        },
        
        // Apply a date-range filter, clearing any spatial filter
        // (time and space filters are mutually exclusive)
        async applyTimeWindow(startStr, endStr) {
            // Close any open popup first
            this.map.closePopup();

            // Clear spatial filter state
            this.filterManager.clearSpatial();
            if (this.spatialFilterMarker) {
                this.map.removeLayer(this.spatialFilterMarker);
                this.spatialFilterMarker = null;
            }
            if (this.spatialFilterCircle) {
                this.map.removeLayer(this.spatialFilterCircle);
                this.spatialFilterCircle = null;
            }

            // Set date range
            this.filterManager.temporal.start = startStr;
            this.filterManager.temporal.end = endStr;

            // Reload visits and trips (if trips panel is expanded)
            if (this.showTripsSection) {
                await this.loadTrips();
            }
            await this.loadRecentVisits();
        },

        // Compute [start, end] date strings for a ±days window around a date
        dateWindow(dateStr, days) {
            const date = new Date(dateStr);
            const startDate = new Date(date);
            startDate.setDate(startDate.getDate() - days);
            const endDate = new Date(date);
            endDate.setDate(endDate.getDate() + days);

            return [
                startDate.toISOString().split('T')[0],
                endDate.toISOString().split('T')[0]
            ];
        },

        // Time filter: view single day
        async viewDay(dateStr) {
            await this.applyTimeWindow(dateStr, dateStr);
        },

        // Time filter: view 3 days (±1 day)
        async view3Day(dateStr) {
            await this.applyTimeWindow(...this.dateWindow(dateStr, 1));
        },

        // Time filter: view week (±3 days)
        async viewWeek(dateStr) {
            await this.applyTimeWindow(...this.dateWindow(dateStr, 3));
        },

        // Time filter: view month (±15 days)
        async viewMonth(dateStr) {
            await this.applyTimeWindow(...this.dateWindow(dateStr, 15));
        },

        // Time filter: view full year
        async viewYear(dateStr) {
            const year = this.parseLocalDate(dateStr).getFullYear();
            await this.applyTimeWindow(`${year}-01-01`, `${year}-12-31`);
        },
        
        // Space filter: view visits within radius
        async viewSpace(lat, lon, radius_km) {
            // Close any open popup first
            this.map.closePopup();
            
            // Clear date filter state (time and space are mutually exclusive)
            this.filterManager.clearTemporal();

            // Set radius first, then call setSpatialFilter
            this.filterManager.spatial.radius_km = radius_km;
            this.setSpatialFilter(lat, lon);
            
            // Wait for visits to load before fitting bounds
            await this.loadRecentVisits();
        },
        
        // Visit Table Functions
        
        // Check if we're showing viewport-limited results
        isViewportLimited() {
            return !this.filterManager.tripId && 
                   !this.filterManager.spatial.lat && 
                   !this.filterManager.temporal.start;
        },
        
        // Load visit table data
        async loadVisitTableData() {
            try {
                // Build params - never include bbox for table (we want ALL filtered visits)
                const params = this.filterManager.buildParams({ includeBbox: false });

                // If no filters active, we need to use bbox to avoid loading everything
                if (this.isViewportLimited()) {
                    const bounds = this.map.getBounds();
                    const bbox = `${bounds.getSouth()},${bounds.getWest()},${bounds.getNorth()},${bounds.getEast()}`;
                    params.append('bbox', bbox);
                }

                const response = await fetch(`/api/visits?${params}`);
                const data = await response.json();

                this.visitTableData = data.visits;
                this.updateSortedVisitTableData();
            } catch (error) {
                console.error('Error loading visit table data:', error);
            }
        },
        
        // Update sorted data array
        updateSortedVisitTableData() {
            const data = [...this.visitTableData];
            const column = this.visitTableSort.column;
            const ascending = this.visitTableSort.ascending;

            data.sort((a, b) => {
                let aVal = a[column];
                let bVal = b[column];
                
                // Handle nulls
                if (aVal === null || aVal === undefined) return 1;
                if (bVal === null || bVal === undefined) return -1;
                
                // Compare
                if (aVal < bVal) return ascending ? -1 : 1;
                if (aVal > bVal) return ascending ? 1 : -1;
                return 0;
            });
            
            this.visitTableSortedData = data;
        },
        
        // Sort visit table
        sortVisitTable(column) {
            if (this.visitTableSort.column === column) {
                // Toggle direction if same column
                this.visitTableSort.ascending = !this.visitTableSort.ascending;
            } else {
                // New column - default to descending for dates/numbers, ascending for text
                this.visitTableSort.column = column;
                this.visitTableSort.ascending = column === 'location_name';
            }
            this.updateSortedVisitTableData();
        },
        
        // Select visit from table
        selectVisitFromTable(visit) {
            this.setSelectedVisit(visit);

            const bounds = this.map.getBounds();
            const visitLatLng = L.latLng(visit.latitude, visit.longitude);
            
            // Check if visit is outside viewport
            if (!bounds.contains(visitLatLng)) {
                // Outside viewport - pan to center it
                this.map.panTo(visitLatLng, {
                    animate: true,
                    duration: 0.5
                });
            } else {
                // Inside viewport - check if it's near the edge (within 20% margin)
                const mapSize = this.map.getSize();
                const point = this.map.latLngToContainerPoint(visitLatLng);
                
                const marginX = mapSize.x * 0.2;
                const marginY = mapSize.y * 0.2;
                
                const nearEdge = 
                    point.x < marginX || 
                    point.x > mapSize.x - marginX ||
                    point.y < marginY || 
                    point.y > mapSize.y - marginY;
                
                if (nearEdge) {
                    // Near edge - pan to center it
                    this.map.panTo(visitLatLng, {
                        animate: true,
                        duration: 0.5
                    });
                }
                // Otherwise, don't move (visit is comfortably visible)
            }
        },
        
        // Scroll table to show selected visit
        scrollTableToVisit(visitId) {
            if (!this.showVisitTable) return;
            
            // Use setTimeout to ensure DOM is updated
            setTimeout(() => {
                const row = document.getElementById(`table-visit-${visitId}`);
                if (row) {
                    row.scrollIntoView({ 
                        behavior: 'smooth', 
                        block: 'center' 
                    });
                }
            }, 100);
        },
        
        // Format time as HH:MM:SS (rounded seconds)
        formatTime(localTime) {
            if (!localTime) return '-';
            
            // Split into parts
            const parts = localTime.split(':');
            if (parts.length !== 3) return localTime; // Return as-is if unexpected format
            
            const hours = parts[0];
            const minutes = parts[1];
            const seconds = Math.round(parseFloat(parts[2])); // Round to nearest second
            
            return `${hours}:${minutes}:${String(seconds).padStart(2, '0')}`;
        },
        
        // Format duration as 3d 4h 25m
        formatDuration(minutes) {
            if (!minutes || minutes === 0) return '-';
            
            const totalMinutes = Math.floor(minutes);
            const days = Math.floor(totalMinutes / (24 * 60));
            const remainingMinutes = totalMinutes % (24 * 60);
            const hours = Math.floor(remainingMinutes / 60);
            const mins = remainingMinutes % 60;
            
            const parts = [];
            if (days > 0) parts.push(`${days}d`);
            if (hours > 0) parts.push(`${hours}h`);
            if (mins > 0 || parts.length === 0) parts.push(`${mins}m`);
            
            return parts.join(' ');
        },
        
        // Get direction indicator for visit relative to viewport
        getVisitDirection(visit) {
            // Reference mapBoundsVersion to make this reactive
            const _ = this.mapBoundsVersion;
            
            if (!this.map) return '';
            
            const bounds = this.map.getBounds();
            const lat = visit.latitude;
            const lon = visit.longitude;
            
            // Check if in viewport
            if (bounds.contains([lat, lon])) {
                return '•';
            }
            
            // Determine direction
            const north = lat > bounds.getNorth();
            const south = lat < bounds.getSouth();
            const east = lon > bounds.getEast();
            const west = lon < bounds.getWest();
            
            // Diagonal directions
            if (north && east) return '↗';
            if (north && west) return '↖';
            if (south && east) return '↘';
            if (south && west) return '↙';
            
            // Cardinal directions
            if (north) return '↑';
            if (south) return '↓';
            if (east) return '→';
            if (west) return '←';
            
            return '•';
        },
        
        // Clear all filters
        clearAllFilters() {
            // Clear all filter state
            this.filterManager.clearAll();
            
            // Clear visual indicators
            if (this.spatialFilterMarker) {
                this.map.removeLayer(this.spatialFilterMarker);
                this.spatialFilterMarker = null;
            }
            if (this.spatialFilterCircle) {
                this.map.removeLayer(this.spatialFilterCircle);
                this.spatialFilterCircle = null;
            }
        }
    };
}
