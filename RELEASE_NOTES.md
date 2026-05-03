## What's Changed

**Voltx: HA Sensor Overrides for AC-Coupled Solar and Grid**
Voltx inverters communicate via Modbus registers that have no visibility into third-party AC-coupled solar (such as Enphase/Envoy systems) or site grid CT metering. Three new optional entity pickers are now available in the Voltx configuration flow, letting you map your own Home Assistant sensors for solar power (W), daily solar energy (kWh), and grid power (W). When set, these HA sensor values are used in place of the inverter's built-in estimates — which are known to be inaccurate for AC-coupled setups. All three fields are fully optional and backwards-compatible, so existing Voltx configurations continue to work without any changes.

Update available via HACS
