(() => {
    const layerSelect = document.getElementById("il-map-layer");
    const mapEl = document.getElementById("il-district-map");
    const alertEl = document.getElementById("il-map-alert");
    const statusEl = document.getElementById("il-map-status");
    const summaryEl = document.getElementById("il-map-summary");
    const hoverEl = document.getElementById("il-map-hover");
    const selectedEl = document.getElementById("il-map-selected");

    if (!layerSelect || !mapEl || !alertEl || !statusEl || !summaryEl || !hoverEl || !selectedEl) {
        return;
    }
    if (!window.topojson || !window.d3) {
        alertEl.textContent = "Map libraries did not load. Refresh and retry.";
        alertEl.style.display = "block";
        return;
    }

    const fallbackBuildCommand = "python3 scripts/build_geometry.py --maps-root /Users/devin/Illinois_campaign_finance/Maps";
    const state = {
        statusPayload: null,
        activeLayerKey: layerSelect.value,
        hoveredFeatureId: "",
        selectedFeatureId: "",
    };

    const escapeHtml = (value) => String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll("\"", "&quot;")
        .replaceAll("'", "&#39;");

    const fetchJson = async (url) => {
        const response = await fetch(url);
        let payload = null;
        try {
            payload = await response.json();
        } catch (_err) {
            payload = null;
        }
        if (!response.ok) {
            const err = new Error(`Failed to load ${url}`);
            err.payload = payload;
            throw err;
        }
        return payload;
    };

    const showAlert = (message) => {
        alertEl.textContent = message;
        alertEl.style.display = "block";
    };

    const clearAlert = () => {
        alertEl.textContent = "";
        alertEl.style.display = "none";
    };

    const districtKeyForFeature = (feature, index) => {
        const props = feature?.properties || {};
        return String(
            props.geoid
            || props.GEOID
            || props.district_key
            || props.district
            || props.CD119FP
            || props.SLDLST
            || props.SLDUST
            || index
        );
    };

    const districtLabelForFeature = (feature, layerLabel) => {
        const props = feature?.properties || {};
        const fallbackDistrict = props.district_key || props.district || props.CD119FP || props.SLDLST || props.SLDUST || "?";
        return props.NAMELSAD || `${layerLabel} District ${fallbackDistrict}`;
    };

    const renderFeatureDetails = (feature, layerLabel) => {
        if (!feature) {
            return "No district selected.";
        }

        const props = feature.properties || {};
        const baseRows = [
            ["District", props.district_key || props.district || props.CD119FP || props.SLDLST || props.SLDUST || "-"],
            ["Name", props.NAMELSAD || "-"],
            ["GEOID", props.geoid || props.GEOID || "-"],
        ];
        const seen = new Set(baseRows.map(([key]) => key));
        const extraRows = Object.entries(props)
            .filter(([key, value]) => value !== null && value !== "" && value !== undefined && !seen.has(key))
            .slice(0, 8)
            .map(([key, value]) => [key, value]);

        const rows = baseRows.concat(extraRows);
        const list = rows
            .map(([key, value]) => `<strong>${escapeHtml(key)}:</strong> ${escapeHtml(value)}`)
            .join("<br>");
        return `<strong>${escapeHtml(districtLabelForFeature(feature, layerLabel))}</strong><br>${list}`;
    };

    const resetDetailPanels = () => {
        hoverEl.textContent = "Hover a district to inspect feature properties.";
        selectedEl.textContent = "Click a district to pin details.";
    };

    const currentLayerMeta = () => state.statusPayload?.layers?.[state.activeLayerKey] || null;

    const refreshPathStyling = (paths) => {
        paths
            .attr("fill", (feature, index) => {
                const featureId = districtKeyForFeature(feature, index);
                if (featureId === state.selectedFeatureId) return "#0f766e";
                if (featureId === state.hoveredFeatureId) return "#2563eb";
                return "#bfdbfe";
            })
            .attr("stroke", "#1e3a8a")
            .attr("stroke-width", (feature, index) => {
                const featureId = districtKeyForFeature(feature, index);
                return featureId === state.selectedFeatureId ? 1.4 : 0.8;
            });
    };

    const renderTopology = (topology, layerMeta) => {
        d3.select(mapEl).selectAll("*").remove();
        state.hoveredFeatureId = "";
        state.selectedFeatureId = "";
        resetDetailPanels();

        const objectKey = topology?.objects ? Object.keys(topology.objects)[0] : "";
        if (!objectKey) {
            summaryEl.textContent = "Geometry file was found, but no map features were available.";
            return;
        }

        const collection = window.topojson.feature(topology, topology.objects[objectKey]);
        const features = collection?.features || [];
        if (!features.length) {
            summaryEl.textContent = "Geometry file was found, but no district features were available.";
            return;
        }

        const width = Math.max(360, mapEl.clientWidth || 760);
        const height = 560;
        mapEl.setAttribute("width", String(width));
        mapEl.setAttribute("height", String(height));

        const projection = d3.geoMercator().fitExtent([[16, 16], [width - 16, height - 16]], collection);
        const path = d3.geoPath(projection);
        const svg = d3.select(mapEl);

        const paths = svg.append("g")
            .selectAll("path")
            .data(features)
            .join("path")
            .attr("d", path)
            .attr("cursor", "pointer");

        refreshPathStyling(paths);

        paths.on("mouseover", (_event, feature) => {
            state.hoveredFeatureId = districtKeyForFeature(feature, -1);
            hoverEl.innerHTML = renderFeatureDetails(feature, layerMeta.label);
            refreshPathStyling(paths);
        });

        paths.on("mouseout", () => {
            state.hoveredFeatureId = "";
            hoverEl.textContent = "Hover a district to inspect feature properties.";
            refreshPathStyling(paths);
        });

        paths.on("click", (_event, feature) => {
            state.selectedFeatureId = districtKeyForFeature(feature, -1);
            selectedEl.innerHTML = renderFeatureDetails(feature, layerMeta.label);
            summaryEl.textContent = `${layerMeta.label}: ${features.length} districts loaded.`;
            refreshPathStyling(paths);
        });

        summaryEl.textContent = `${layerMeta.label}: ${features.length} districts loaded.`;
    };

    const missingAssetMessage = (buildCommand) => {
        return `Geometry assets are missing. Build them with: ${buildCommand}`;
    };

    const loadLayer = async (layerKey) => {
        state.activeLayerKey = layerKey;
        const layerMeta = currentLayerMeta();
        if (!layerMeta) {
            showAlert("Unknown geometry layer selection.");
            statusEl.textContent = "Layer unavailable.";
            return;
        }

        if (!layerMeta.exists) {
            d3.select(mapEl).selectAll("*").remove();
            resetDetailPanels();
            summaryEl.textContent = `${layerMeta.label}: geometry asset missing.`;
            const buildCommand = state.statusPayload?.build_command || fallbackBuildCommand;
            showAlert(missingAssetMessage(buildCommand));
            statusEl.textContent = `${layerMeta.label}: missing (${layerMeta.filename}).`;
            return;
        }

        if (state.statusPayload?.all_present) {
            clearAlert();
        } else {
            const buildCommand = state.statusPayload?.build_command || fallbackBuildCommand;
            showAlert(missingAssetMessage(buildCommand));
        }
        statusEl.textContent = `Loading ${layerMeta.label}...`;
        try {
            const topology = await fetchJson(layerMeta.url);
            renderTopology(topology, layerMeta);
            statusEl.textContent = `${layerMeta.label}: ready (${layerMeta.filename}).`;
        } catch (err) {
            const buildCommand = err?.payload?.build_command || state.statusPayload?.build_command || fallbackBuildCommand;
            const message = err?.payload?.error === "missing_geometry_asset"
                ? missingAssetMessage(buildCommand)
                : `Unable to load ${layerMeta.label} geometry.`;
            showAlert(message);
            statusEl.textContent = `Unable to load ${layerMeta.label}.`;
        }
    };

    const init = async () => {
        try {
            const statusPayload = await fetchJson("/experimental/viz-lab/data/geometry/il/status");
            state.statusPayload = statusPayload;
            const allPresent = Boolean(statusPayload?.all_present);
            const layerKeys = Object.keys(statusPayload?.layers || {});
            if (!layerKeys.length) {
                showAlert("No geometry layers are configured in Visualization Lab.");
                statusEl.textContent = "No geometry layers configured.";
                return;
            }

            if (!(layerSelect.value in statusPayload.layers)) {
                layerSelect.value = layerKeys[0];
            }

            if (!allPresent) {
                const buildCommand = statusPayload?.build_command || fallbackBuildCommand;
                showAlert(missingAssetMessage(buildCommand));
            } else {
                clearAlert();
            }

            await loadLayer(layerSelect.value);
        } catch (_err) {
            showAlert(missingAssetMessage(fallbackBuildCommand));
            statusEl.textContent = "Unable to read geometry status.";
        }
    };

    layerSelect.addEventListener("change", () => {
        loadLayer(layerSelect.value);
    });

    init();
})();
