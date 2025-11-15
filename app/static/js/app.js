// PlaceTrace Alpine.js App
function placeTraceApp() {
    return {
        // State
        map: null,
        markerLayer: null,
        spatialFilterMarker: null,
        spatialFilterCircle: null,
        loading: false,
        visits: [],
        trips: [],
        activeTripTab: 'day',
        selectedTripId: null,
        selectedVisit: null,  // Currently selected visit
        activeFilters: [],
        spatialFilter: {
            lat: null,
            lon: null,
            radius_km: 10
        },
        dateRangeDraft: {
            start: '',
            end: ''
        },
        dateRange: {
            start: null,
            end: null
        },
        dateText: '',
        endDateText: '',
        showRadiusSection: false,
        showDateFilter: false,
        expandedYears: {},
        showMovementSection: false,
        showTripsSection: false,  // Default collapsed
        selectedDay: null,
        showMovement: false,
        movements: [],
        movementLayer: null,
        focusModeEnabled: true,  // Default: focus on selected day
        savedVisitState: null,   // Store visits to restore when focus disabled
        
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
            // Create map centered on Palo Alto (disable scroll wheel zoom during init)
            this.map = L.map('map', {
                scrollWheelZoom: false
            }).setView([37.4419, -122.1430], 10);
            
            // Add Esri World Street Map tiles
            L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}', {
                attribution: 'Tiles &copy; Esri',
                maxZoom: 19
            }).addTo(this.map);
            
            // Create marker cluster group
            this.markerLayer = L.markerClusterGroup({
                maxClusterRadius: 50,
                spiderfyOnMaxZoom: true,
                showCoverageOnHover: false,
                zoomToBoundsOnClick: true
            }).addTo(this.map);
            
            // Create movement layer (below markers)
            this.movementLayer = L.layerGroup().addTo(this.map);
            
            // Add click handler for spatial filter
            this.map.on('click', (e) => {
                this.setSpatialFilter(e.latlng.lat, e.latlng.lng);
                this.loadRecentVisits();
            });
            
            // Add moveend listener to reload visits based on visible bounds
            this.map.on('moveend', () => {
                // Only reload if no filters are active AND movement tracks are not active
                if (!this.selectedTripId && !this.spatialFilter.lat && !this.dateRange.start && !this.showMovement) {
                    this.loadRecentVisits();
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
                
                // Add date range filter if active
                if (this.dateRange.start) {
                    params.append('start_date', this.dateRange.start);
                }
                if (this.dateRange.end) {
                    params.append('end_date', this.dateRange.end);
                }
                
                const response = await fetch(`/api/trips?${params}`);
                const data = await response.json();
                this.trips = data.trips;
            } catch (error) {
                console.error('Error loading trips:', error);
            }
        },
        
        // Load all visits (no date/limit filters)
        async loadRecentVisits() {
            this.loading = true;
            
            try {
                const params = new URLSearchParams();
                
                // Add spatial filter if active
                if (this.spatialFilter.lat !== null) {
                    params.append('lat', this.spatialFilter.lat);
                    params.append('lon', this.spatialFilter.lon);
                    params.append('radius_km', this.spatialFilter.radius_km);
                }
                // Add date range filter if active
                else if (this.dateRange.start) {
                    params.append('start_date', this.dateRange.start);
                    if (this.dateRange.end) {
                        params.append('end_date', this.dateRange.end);
                    }
                }
                // Movement tracks with focus mode - don't reload, visits are already filtered
                else if (this.showMovement && this.focusModeEnabled) {
                    this.loading = false;
                    return;
                }
                // No filters active - use map bounds
                else {
                    const bounds = this.map.getBounds();
                    const bbox = `${bounds.getSouth()},${bounds.getWest()},${bounds.getNorth()},${bounds.getEast()}`;
                    params.append('bbox', bbox);
                }
                
                const response = await fetch(`/api/visits?${params}`);
                const data = await response.json();
                
                this.visits = data.visits;
                this.renderMarkers();
                this.fitMapToVisits();
                
            } catch (error) {
                console.error('Error loading visits:', error);
            } finally {
                this.loading = false;
            }
        },
        
        // Load visits for selected trip
        async loadTripVisits(tripId) {
            this.loading = true;
            
            try {
                const params = new URLSearchParams({
                    trip_id: tripId,
                    limit: 5000
                });
                
                // Add spatial filter if active
                if (this.spatialFilter.lat !== null) {
                    params.append('lat', this.spatialFilter.lat);
                    params.append('lon', this.spatialFilter.lon);
                    params.append('radius_km', this.spatialFilter.radius_km);
                }
                
                // Add date range filter if active
                if (this.dateRange.start) {
                    params.append('start_date', new Date(this.dateRange.start).toISOString());
                }
                if (this.dateRange.end) {
                    const endDate = new Date(this.dateRange.end);
                    endDate.setHours(23, 59, 59, 999);
                    params.append('end_date', endDate.toISOString());
                }
                
                const response = await fetch(`/api/visits?${params}`);
                const data = await response.json();
                
                this.visits = data.visits;
                this.renderMarkers();
                this.fitMapToVisits();
                
            } catch (error) {
                console.error('Error loading trip visits:', error);
            } finally {
                this.loading = false;
            }
        },
        
        // Render visit markers on map
        renderMarkers() {
            // Clear existing markers
            this.markerLayer.clearLayers();
            
            // Add marker for each visit
            this.visits.forEach(visit => {
                // Check if this is the selected visit
                const isSelected = this.selectedVisit && this.selectedVisit.id === visit.id;
                
                const marker = L.circleMarker(
                    [visit.latitude, visit.longitude],
                    {
                        radius: isSelected ? 8 : 6,
                        fillColor: isSelected ? '#EF4444' : '#3B82F6',  // red-500 if selected, blue-500 otherwise
                        color: isSelected ? '#991B1B' : '#1E40AF',      // red-900 if selected, blue-800 otherwise
                        weight: isSelected ? 2 : 1,
                        opacity: 1,
                        fillOpacity: isSelected ? 0.9 : 0.7
                    }
                );
                
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
                        ${visit.photo_count > 0 ? `<div class="text-blue-600 mt-1">${visit.photo_count} photos</div>` : ''}
                    </div>
                `;
                
                marker.bindPopup(popupContent);
                
                // Click handler - select this visit and re-render to update colors
                marker.on('click', () => {
                    this.selectedVisit = visit;
                    this.renderMarkers();  // Re-render to update marker colors
                });
                
                marker.addTo(this.markerLayer);
            });
        },
        
        // Render visits (wrapper for renderMarkers for clarity)
        renderVisits() {
            this.renderMarkers();
        },
        
        // Fit map bounds to show all visits
        fitMapToVisits() {
            if (this.visits.length === 0) return;
            
            // Only fit bounds when we have filters active
            // (Don't fit when using bbox - that would cause infinite loop)
            const hasFilters = this.selectedTripId || this.spatialFilter.lat !== null || this.dateRange.start;
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
        
        // Group trips by year
        get tripsByYear() {
            const grouped = {};
            const currentYear = new Date().getFullYear();
            
            this.filteredTrips.forEach(trip => {
                const year = new Date(trip.local_start_date).getFullYear();
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
                new Date(trip.local_start_date).getFullYear()
            ))];
            return years.length > 1;
        },
        
        // Toggle year expansion
        toggleYear(year) {
            this.expandedYears[year] = !this.expandedYears[year];
        },
        
        // Select a trip
        selectTrip(trip) {
            this.selectedTripId = trip.id;
            
            // Update movement date to trip start date (already in YYYY-MM-DD format)
            this.selectedDay = trip.local_start_date;
            
            // Add trip filter chip
            this.addFilter({
                id: `trip-${trip.id}`,
                type: 'trip',
                emoji: this.getTripEmoji(trip.category),
                label: trip.display_name,
                data: trip
            });
            
            // Load visits for this trip
            this.loadTripVisits(trip.id);
        },
        
        // Add filter chip
        addFilter(filter) {
            // Remove existing filter of same type
            this.activeFilters = this.activeFilters.filter(f => f.type !== filter.type);
            
            // Add new filter
            this.activeFilters.push(filter);
        },
        
        // Remove filter chip
        removeFilter(filterId) {
            this.activeFilters = this.activeFilters.filter(f => f.id !== filterId);
            
            // If trip filter removed, clear selection and reload
            if (filterId.startsWith('trip-')) {
                this.selectedTripId = null;
                if (this.spatialFilter.lat === null) {
                    this.loadRecentVisits();
                } else {
                    this.loadRecentVisits();  // Will include spatial filter
                }
            }
            
            // If spatial filter removed, clear it and reload
            if (filterId === 'spatial') {
                this.clearSpatialFilter();
            }
            
            // If date filter removed, clear it and reload
            if (filterId === 'date-range') {
                this.clearDateFilter();
            }
        },
        
        // Set spatial filter (map click)
        setSpatialFilter(lat, lon) {
            this.spatialFilter.lat = lat;
            this.spatialFilter.lon = lon;
            
            // Remove old visual indicators
            if (this.spatialFilterMarker) {
                this.map.removeLayer(this.spatialFilterMarker);
                this.spatialFilterMarker = null;
            }
            if (this.spatialFilterCircle) {
                this.map.removeLayer(this.spatialFilterCircle);
            }
            
            // Add circle showing radius (no center marker - it covers the visit)
            this.spatialFilterCircle = L.circle([lat, lon], {
                radius: this.spatialFilter.radius_km * 1000,  // Convert km to meters
                color: '#10B981',
                fillColor: '#10B981',
                fillOpacity: 0.1,
                weight: 2
            }).addTo(this.map);
            
            // Add filter chip
            this.addFilter({
                id: 'spatial',
                type: 'spatial',
                emoji: '📍',
                label: `Within ${this.spatialFilter.radius_km}km`,
                data: { lat, lon, radius_km: this.spatialFilter.radius_km }
            });
            
            // Note: Caller is responsible for calling loadRecentVisits()
        },
        
        // Clear spatial filter
        clearSpatialFilter() {
            this.spatialFilter.lat = null;
            this.spatialFilter.lon = null;
            
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
            if (this.selectedTripId) {
                this.loadTripVisits(this.selectedTripId);
            } else {
                this.loadRecentVisits();
            }
        },
        
        // Update radius circle when slider changes
        updateRadiusCircle() {
            // If spatial filter is active, update the circle and filter chip
            if (this.spatialFilter.lat !== null) {
                // Update circle radius
                if (this.spatialFilterCircle) {
                    this.map.removeLayer(this.spatialFilterCircle);
                }
                
                this.spatialFilterCircle = L.circle(
                    [this.spatialFilter.lat, this.spatialFilter.lon],
                    {
                        radius: this.spatialFilter.radius_km * 1000,  // Convert km to meters
                        color: '#10B981',
                        fillColor: '#10B981',
                        fillOpacity: 0.1,
                        weight: 2
                    }
                ).addTo(this.map);
                
                // Update filter chip label
                this.addFilter({
                    id: 'spatial',
                    type: 'spatial',
                    emoji: '📍',
                    label: `Within ${this.spatialFilter.radius_km}km`,
                    data: { 
                        lat: this.spatialFilter.lat, 
                        lon: this.spatialFilter.lon, 
                        radius_km: this.spatialFilter.radius_km 
                    }
                });
                
                // Note: Caller responsible for reloading visits
            }
        },
        
        // Parse date text input
        parseDate() {
            if (!this.dateText.trim()) return;
            
            const parsed = this.parseDateInput(this.dateText);
            if (!parsed) {
                alert('Format: YYYY, YYYY-MM, or YYYY-MM-DD');
                this.dateText = '';
                return;
            }
            
            this.dateRange.start = parsed.start;
            
            // Always set end - either from endDateText or auto-expanded from dateText
            if (!this.endDateText.trim()) {
                this.dateRange.end = parsed.end;
            }
        },
        
        // Parse end date text input
        parseEndDate() {
            if (!this.endDateText.trim()) {
                // Empty = today
                this.dateRange.end = new Date().toISOString().split('T')[0];
                return;
            }
            
            const parsed = this.parseDateInput(this.endDateText, true);
            if (!parsed) {
                alert('Format: YYYY, YYYY-MM, or YYYY-MM-DD');
                this.endDateText = '';
                return;
            }
            
            this.dateRange.end = parsed.end;
        },
        
        // Parse date input (returns {start, end})
        parseDateInput(text, isEndDate = false) {
            const yearRegex = /^\d{4}$/;
            const yearMonthRegex = /^\d{4}-\d{2}$/;
            const fullDateRegex = /^\d{4}-\d{2}-\d{2}$/;
            
            if (yearRegex.test(text)) {
                // Year only: YYYY
                const year = text;
                return {
                    start: `${year}-01-01`,
                    end: `${year}-12-31`
                };
            } else if (yearMonthRegex.test(text)) {
                // Year-Month: YYYY-MM
                const [year, month] = text.split('-');
                const lastDay = this.getLastDayOfMonth(text);
                return {
                    start: `${text}-01`,
                    end: `${text}-${lastDay}`
                };
            } else if (fullDateRegex.test(text)) {
                // Full date: YYYY-MM-DD
                return {
                    start: text,
                    end: text
                };
            }
            
            return null; // Invalid format
        },
        
        // Get last day of month
        getLastDayOfMonth(yearMonth) {
            const [year, month] = yearMonth.split('-').map(Number);
            const lastDay = new Date(year, month, 0).getDate();
            return String(lastDay).padStart(2, '0');
        },
        
        
        // Quick preset: Past week
        setPastWeek() {
            const now = new Date();
            const past = new Date(now - 7*24*60*60*1000);
            this.dateText = past.toISOString().split('T')[0];
            this.endDateText = now.toISOString().split('T')[0];
            this.parseDate();
            this.parseEndDate();
            this.applyDateFilter();
        },
        
        // Quick preset: Past month
        setPastMonth() {
            const now = new Date();
            const past = new Date(now);
            past.setMonth(past.getMonth() - 1);
            this.dateText = past.toISOString().split('T')[0];
            this.endDateText = now.toISOString().split('T')[0];
            this.parseDate();
            this.parseEndDate();
            this.applyDateFilter();
        },
        
        // Quick preset: Past year
        setPastYear() {
            const now = new Date();
            const past = new Date(now);
            past.setFullYear(past.getFullYear() - 1);
            this.dateText = past.toISOString().split('T')[0];
            this.endDateText = now.toISOString().split('T')[0];
            this.parseDate();
            this.parseEndDate();
            this.applyDateFilter();
        },
        
        // Apply date range filter
        applyDateFilter() {
            if (!this.dateText) return;
            
            // Parse dates if not already parsed
            this.parseDate();
            if (this.enableRange && this.endDateText) {
                this.parseEndDate();
            }
            
            if (!this.dateRange.start || !this.dateRange.end) return;
            
            // Build filter chip label
            const label = this.formatDateRangeChip();
            
            // Add filter chip
            this.addFilter({
                id: 'date',
                type: 'date',
                emoji: '📅',
                label: label.replace('📅 ', ''),
                data: { start: this.dateRange.start, end: this.dateRange.end }
            });
            
            // Reload trips and visits with date filter
            this.loadTrips();
            if (this.selectedTripId) {
                this.loadTripVisits(this.selectedTripId);
            } else {
                this.loadRecentVisits();
            }
        },
        
        // Format date range for chip display
        formatDateRangeChip() {
            if (!this.dateRange.start) return '';
            
            const start = new Date(this.dateRange.start).toLocaleDateString('en-US', { 
                year: 'numeric', 
                month: 'short', 
                day: 'numeric' 
            });
            
            const end = new Date(this.dateRange.end).toLocaleDateString('en-US', { 
                year: 'numeric', 
                month: 'short', 
                day: 'numeric' 
            });
            
            // If same date, show once
            if (this.dateRange.start === this.dateRange.end) {
                return `📅 ${start}`;
            }
            
            return `📅 ${start} → ${end}`;
        },
        
        // Clear date range filter
        clearDateFilter() {
            this.dateRange.start = null;
            this.dateRange.end = null;
            this.dateText = '';
            this.endDateText = '';
            
            // Reload trips without date filter
            this.loadTrips();
            
            // Reload visits without date filter
            if (this.selectedTripId) {
                this.loadTripVisits(this.selectedTripId);
            } else {
                this.loadRecentVisits();
            }
        },
        
        // Format date for filter chip (short format)
        formatDateShort(dateStr) {
            const date = new Date(dateStr);
            return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
        },
        
        // Get emoji for trip category
        getTripEmoji(category) {
            const emojis = {
                'Day Trip': '🚗',
                'Short Trip': '✈️',
                'Long Trip': '🌍'
            };
            return emojis[category] || '🧳';
        },
        
        // Format date range for trip display
        formatDateRange(startTime, endTime) {
            const start = new Date(startTime);
            const end = new Date(endTime);
            
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
            return dateObj.toLocaleDateString('en-US', {
                month: 'short',
                day: 'numeric',
                year: 'numeric',
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
                // Save current visits before first load (if focus mode enabled)
                if (this.focusModeEnabled && !this.savedVisitState) {
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
                
                // If focus mode enabled, update main markers to show only this day
                if (this.focusModeEnabled) {
                    this.visits = dayVisits;
                    this.renderVisits();
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
            if (this.showMovement && this.selectedDay) {
                // Turning ON tracks - loadMovements handles everything
                await this.loadMovements();
            } else {
                // Turning OFF tracks
                this.clearMovementLayer();
                
                // Clear saved state and reload visits for current viewport
                this.savedVisitState = null;
                await this.loadRecentVisits();
            }
        },
        
        // Toggle focus mode
        async toggleFocusMode() {
            if (this.focusModeEnabled) {
                // Enabling focus - show only day visits
                if (this.showMovement && this.selectedDay) {
                    // Re-load movements to get day visits
                    await this.loadMovements();
                }
            } else {
                // Disabling focus - load visits in current viewport
                this.savedVisitState = null;
                await this.loadRecentVisits();
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
            this.selectedVisit = null;
            this.renderMarkers();  // Re-render to update marker colors
        },
        
        // Time filter: view single day
        async viewDay(dateStr) {
            // Close any open popup first
            this.map.closePopup();
            
            // Clear spatial filter state (time and space are mutually exclusive)
            this.spatialFilter.lat = null;
            this.spatialFilter.lon = null;
            if (this.spatialFilterMarker) {
                this.map.removeLayer(this.spatialFilterMarker);
                this.spatialFilterMarker = null;
            }
            if (this.spatialFilterCircle) {
                this.map.removeLayer(this.spatialFilterCircle);
                this.spatialFilterCircle = null;
            }
            // Remove spatial chip from UI
            this.activeFilters = this.activeFilters.filter(f => f.id !== 'spatial');
            
            // Set date range to just this day
            this.dateRange.start = dateStr;
            this.dateRange.end = dateStr;
            
            // Add filter chip
            this.addFilter({
                id: 'date-range',
                type: 'date',
                emoji: '📅',
                label: this.formatDateShort(dateStr)
            });
            
            // Reload visits and trips (if trips panel is expanded)
            if (this.showTripsSection) {
                await this.loadTrips();
            }
            await this.loadRecentVisits();
        },
        
        // Time filter: view 3 days (±1 day)
        async view3Day(dateStr) {
            // Close any open popup first
            this.map.closePopup();
            
            // Clear spatial filter state (time and space are mutually exclusive)
            this.spatialFilter.lat = null;
            this.spatialFilter.lon = null;
            if (this.spatialFilterMarker) {
                this.map.removeLayer(this.spatialFilterMarker);
                this.spatialFilterMarker = null;
            }
            if (this.spatialFilterCircle) {
                this.map.removeLayer(this.spatialFilterCircle);
                this.spatialFilterCircle = null;
            }
            // Remove spatial chip from UI
            this.activeFilters = this.activeFilters.filter(f => f.id !== 'spatial');
            
            // Calculate 3 days: ±1 day
            const date = new Date(dateStr);
            const startDate = new Date(date);
            startDate.setDate(startDate.getDate() - 1);
            const endDate = new Date(date);
            endDate.setDate(endDate.getDate() + 1);
            
            const startStr = startDate.toISOString().split('T')[0];
            const endStr = endDate.toISOString().split('T')[0];
            
            // Set date range
            this.dateRange.start = startStr;
            this.dateRange.end = endStr;
            
            // Add filter chip
            this.addFilter({
                id: 'date-range',
                type: 'date',
                emoji: '📅',
                label: `${this.formatDateShort(startStr)} - ${this.formatDateShort(endStr)}`
            });
            
            // Reload visits and trips (if trips panel is expanded)
            if (this.showTripsSection) {
                await this.loadTrips();
            }
            await this.loadRecentVisits();
        },
        
        // Time filter: view week (±3 days)
        async viewWeek(dateStr) {
            // Close any open popup first
            this.map.closePopup();
            
            // Clear spatial filter (time and space are mutually exclusive)
            this.spatialFilter.lat = null;
            this.spatialFilter.lon = null;
            if (this.spatialFilterMarker) {
                this.map.removeLayer(this.spatialFilterMarker);
                this.spatialFilterMarker = null;
            }
            if (this.spatialFilterCircle) {
                this.map.removeLayer(this.spatialFilterCircle);
                this.spatialFilterCircle = null;
            }
            // Remove spatial chip from UI
            this.activeFilters = this.activeFilters.filter(f => f.id !== 'spatial');
            
            // Calculate week: ±3 days
            const date = new Date(dateStr);
            const startDate = new Date(date);
            startDate.setDate(startDate.getDate() - 3);
            const endDate = new Date(date);
            endDate.setDate(endDate.getDate() + 3);
            
            const startStr = startDate.toISOString().split('T')[0];
            const endStr = endDate.toISOString().split('T')[0];
            
            // Set date range
            this.dateRange.start = startStr;
            this.dateRange.end = endStr;
            
            // Add filter chip
            this.addFilter({
                id: 'date-range',
                type: 'date',
                emoji: '📅',
                label: `${this.formatDateShort(startStr)} - ${this.formatDateShort(endStr)}`
            });
            
            // Reload visits and trips (if trips panel is expanded)
            if (this.showTripsSection) {
                await this.loadTrips();
            }
            await this.loadRecentVisits();
        },
        
        // Time filter: view month (±15 days)
        async viewMonth(dateStr) {
            // Close any open popup first
            this.map.closePopup();
            
            // Clear spatial filter (time and space are mutually exclusive)
            this.spatialFilter.lat = null;
            this.spatialFilter.lon = null;
            if (this.spatialFilterMarker) {
                this.map.removeLayer(this.spatialFilterMarker);
                this.spatialFilterMarker = null;
            }
            if (this.spatialFilterCircle) {
                this.map.removeLayer(this.spatialFilterCircle);
                this.spatialFilterCircle = null;
            }
            // Remove spatial chip from UI
            this.activeFilters = this.activeFilters.filter(f => f.id !== 'spatial');
            
            // Calculate month: ±15 days
            const date = new Date(dateStr);
            const startDate = new Date(date);
            startDate.setDate(startDate.getDate() - 15);
            const endDate = new Date(date);
            endDate.setDate(endDate.getDate() + 15);
            
            const startStr = startDate.toISOString().split('T')[0];
            const endStr = endDate.toISOString().split('T')[0];
            
            // Set date range
            this.dateRange.start = startStr;
            this.dateRange.end = endStr;
            
            // Add filter chip
            this.addFilter({
                id: 'date-range',
                type: 'date',
                emoji: '📅',
                label: `${this.formatDateShort(startStr)} - ${this.formatDateShort(endStr)}`
            });
            
            // Reload visits and trips (if trips panel is expanded)
            if (this.showTripsSection) {
                await this.loadTrips();
            }
            await this.loadRecentVisits();
        },
        
        // Time filter: view full year
        async viewYear(dateStr) {
            // Close any open popup first
            this.map.closePopup();
            
            // Clear spatial filter (time and space are mutually exclusive)
            this.spatialFilter.lat = null;
            this.spatialFilter.lon = null;
            if (this.spatialFilterMarker) {
                this.map.removeLayer(this.spatialFilterMarker);
                this.spatialFilterMarker = null;
            }
            if (this.spatialFilterCircle) {
                this.map.removeLayer(this.spatialFilterCircle);
                this.spatialFilterCircle = null;
            }
            // Remove spatial chip from UI
            this.activeFilters = this.activeFilters.filter(f => f.id !== 'spatial');
            
            // Get full year
            const date = new Date(dateStr);
            const year = date.getFullYear();
            const startStr = `${year}-01-01`;
            const endStr = `${year}-12-31`;
            
            // Set date range
            this.dateRange.start = startStr;
            this.dateRange.end = endStr;
            
            // Add filter chip
            this.addFilter({
                id: 'date-range',
                type: 'date',
                emoji: '📅',
                label: `${year}`
            });
            
            // Reload visits and trips (if trips panel is expanded)
            if (this.showTripsSection) {
                await this.loadTrips();
            }
            await this.loadRecentVisits();
        },
        
        // Space filter: view visits within radius
        async viewSpace(lat, lon, radius_km) {
            // Close any open popup first
            this.map.closePopup();
            
            // Clear date filter state (time and space are mutually exclusive)
            this.dateRange.start = null;
            this.dateRange.end = null;
            // Remove date chip from UI
            this.activeFilters = this.activeFilters.filter(f => f.id !== 'date-range');
            
            // Set radius first, then call setSpatialFilter
            this.spatialFilter.radius_km = radius_km;
            this.setSpatialFilter(lat, lon);
            
            // Wait for visits to load before fitting bounds
            await this.loadRecentVisits();
        },
        
        // Clear all filters
        clearAllFilters() {
            // Clear trip selection
            this.selectedTripId = null;
            
            // Clear spatial filter
            this.spatialFilter.lat = null;
            this.spatialFilter.lon = null;
            if (this.spatialFilterMarker) {
                this.map.removeLayer(this.spatialFilterMarker);
                this.spatialFilterMarker = null;
            }
            if (this.spatialFilterCircle) {
                this.map.removeLayer(this.spatialFilterCircle);
                this.spatialFilterCircle = null;
            }
            
            // Clear date filter
            this.dateRange.start = null;
            this.dateRange.end = null;
            
            // Clear all filter chips
            this.activeFilters = [];
        }
    };
}
