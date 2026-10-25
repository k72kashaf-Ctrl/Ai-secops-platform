Zenith SecOps - local camera clips
==================================

Drop video/image clip files in this folder, named exactly as the events
reference them in their payload "clip" field (for example clip-0201.mp4 for
event EVT-I2002 from CAM-PN-01).

When a matching file exists, GET /cameras returns "media_url": "/media/<clip>"
and the API serves it read-only from this folder only. Missing files are fine:
the camera wall just shows the latest event without a clip.

Use a different folder with the ZENITH_MEDIA_DIR environment variable.

Live streams are configured separately: copy data/cameras.example.json to
data/cameras.json (or point ZENITH_CAMERAS_FILE at your own file) and map each
device_id to {"url": "...", "kind": "mp4" | "hls" | "mjpeg" | "image", "label": "..."}.
An optional "label" (e.g. "Demo footage") is shown on the tile with "NOT EVIDENCE" so
presentation clips are never mistaken for footage recorded by that camera.
Entries with an unknown kind or a URL that is not http(s):// or /path are ignored.
