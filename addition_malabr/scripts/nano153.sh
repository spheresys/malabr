#!/usr/bin/env bash
# Launch the extracted Chrome 153 for Prompt API baseline work.
# Chrome 142 (the system install) is on a RETIRED model component and is
# refused the download; 153 uses the current "On DeviceModels Manifest"
# component, which the update server still serves. See session_handoff_v6.md.
CHROME_DIR=${CHROME_DIR:-/media/chaitu/chaitanya/chrome-stable-deb/root/opt/google/chrome}
exec "$CHROME_DIR/chrome" \
  --user-data-dir="${NANO_PROFILE:-/media/chaitu/chaitanya/nano153-profile}" \
  --remote-debugging-port="${NANO_PORT:-9346}" \
  --enable-features=AIPromptAPI,AIPromptAPIForExtension,OptimizationGuideModelExecution,"OptimizationGuideOnDeviceModel:compatible_on_device_performance_classes/*" \
  --no-first-run --no-default-browser-check \
  --disable-backgrounding-occluded-windows \
  "$@" about:blank
