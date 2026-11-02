"""
Road-damage detection module.

*** DEMO MODE — READ THIS BEFORE DEMOING THE PROJECT ***
There is no trained computer-vision model wired in here. `detect_road_damage()`
below is a deterministic MOCK: it derives a pseudo-detection from the image's
own bytes (hash + dimensions), so the same photo always yields the same
result and different photos plausibly yield different results -- good enough
to drive the whole workflow (severity, GPS, complaints, dashboards) honestly,
but it is NOT reading pixels for real damage. Every response from the API
that includes `"is_demo": true` is telling you exactly this.

--- HOW TO SWAP IN A REAL MODEL (e.g. YOLOv8 via ultralytics) ---
1. `pip install ultralytics opencv-python`
2. Train or download road-damage weights (e.g. on the RDD2022 dataset).
3. Replace the body of `detect_road_damage()` with:

    from ultralytics import YOLO
    _model = YOLO(os.environ["RDM_MODEL_PATH"])

    def detect_road_damage(image_path):
        results = _model.predict(image_path, conf=CONFIDENCE_THRESHOLD)[0]
        detections = []
        for box in results.boxes:
            detections.append({
                "damage_type": _model.names[int(box.cls)],
                "confidence": float(box.conf),
                "bbox": [float(v) for v in box.xyxy[0]],
            })
        return detections

4. Keep `estimate_severity()` as-is (or refine it) -- it's independent of
   which model produced the boxes.
5. Delete `is_demo` / the mock-hash logic once real inference is in place.
"""
import hashlib
import os
import random
from PIL import Image, ImageDraw, ImageFont

CONFIDENCE_THRESHOLD = float(os.environ.get("RDM_CONF_THRESHOLD", 0.55))

DAMAGE_CLASSES = [
    "pothole",
    "longitudinal_crack",
    "transverse_crack",
    "alligator_crack",
    "damaged_surface",
    "waterlogged_section",
]

SEVERITY_COLORS = {
    "low": "#22c55e",
    "medium": "#eab308",
    "high": "#f97316",
    "critical": "#ef4444",
}


def detect_road_damage(image_path):
    """DEMO detector. Returns a list of raw detections (no severity yet)."""
    with open(image_path, "rb") as f:
        data = f.read()
    digest = hashlib.sha256(data).hexdigest()
    rng = random.Random(digest)  # deterministic per-image

    with Image.open(image_path) as im:
        w, h = im.size

    num_detections = rng.choice([1, 1, 1, 2])  # usually one, sometimes two
    detections = []
    for _ in range(num_detections):
        damage_type = rng.choice(DAMAGE_CLASSES)
        confidence = round(rng.uniform(0.40, 0.98), 2)
        bw, bh = rng.uniform(0.15, 0.5) * w, rng.uniform(0.1, 0.4) * h
        x1 = rng.uniform(0, max(1, w - bw))
        y1 = rng.uniform(0, max(1, h - bh))
        detections.append({
            "damage_type": damage_type,
            "confidence": confidence,
            "bbox": [round(x1, 1), round(y1, 1), round(x1 + bw, 1), round(y1 + bh, 1)],
            "image_width": w,
            "image_height": h,
        })
    return detections


def estimate_severity(detection):
    """
    Severity from damage type, size (bbox area fraction of image) and
    confidence. Real system would also factor in user-reported context
    and count of nearby damaged areas (see duplicate-clustering in app.py).
    """
    x1, y1, x2, y2 = detection["bbox"]
    area_fraction = ((x2 - x1) * (y2 - y1)) / max(1, detection["image_width"] * detection["image_height"])
    confidence = detection["confidence"]

    type_weight = {
        "pothole": 1.0,
        "alligator_crack": 0.9,
        "waterlogged_section": 0.8,
        "damaged_surface": 0.6,
        "longitudinal_crack": 0.5,
        "transverse_crack": 0.4,
    }.get(detection["damage_type"], 0.5)

    score = (area_fraction * 2.5) + (confidence * 0.4) + (type_weight * 0.3)

    if score >= 0.9:
        return "critical"
    elif score >= 0.55:
        return "high"
    elif score >= 0.3:
        return "medium"
    return "low"


def annotate_image(image_path, detections, output_path):
    """Draw bounding boxes + labels so authorities can review evidence."""
    with Image.open(image_path).convert("RGB") as im:
        draw = ImageDraw.Draw(im)
        for det in detections:
            color = SEVERITY_COLORS.get(det["severity"], "#ffffff")
            x1, y1, x2, y2 = det["bbox"]
            draw.rectangle([x1, y1, x2, y2], outline=color, width=4)
            label = f'{det["damage_type"]} {det["confidence"]:.0%} ({det["severity"]})'
            text_bg = [x1, max(0, y1 - 18), x1 + 8 * len(label), y1]
            draw.rectangle(text_bg, fill=color)
            draw.text((x1 + 2, max(0, y1 - 18)), label, fill="#111111")
        im.save(output_path)


# --- Mock reverse geocoding -------------------------------------------------
# DEMO MODE: no external geocoding API call is made (no network egress in
# this environment). This snaps a lat/lng to whichever demo city center is
# nearest, purely so the "authority mapping by city" feature has something
# to key off of. Swap for a real Nominatim/Google Geocoding API call later.
DEMO_CITIES = [
    {"city": "Delhi", "state": "Delhi", "country": "India", "lat": 28.6139, "lng": 77.2090},
    {"city": "Mumbai", "state": "Maharashtra", "country": "India", "lat": 19.0760, "lng": 72.8777},
    {"city": "Bengaluru", "state": "Karnataka", "country": "India", "lat": 12.9716, "lng": 77.5946},
    {"city": "Pune", "state": "Maharashtra", "country": "India", "lat": 18.5204, "lng": 73.8567},
]


def reverse_geocode(lat, lng):
    if lat is None or lng is None:
        return {"address": None, "city": None, "state": None, "country": None}

    def dist(c):
        return (c["lat"] - lat) ** 2 + (c["lng"] - lng) ** 2

    nearest = min(DEMO_CITIES, key=dist)
    return {
        "address": f"Near {nearest['city']} (demo address — {lat:.4f}, {lng:.4f})",
        "city": nearest["city"],
        "state": nearest["state"],
        "country": nearest["country"],
    }
