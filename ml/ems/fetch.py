import argparse
import hashlib
import json
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import truststore


SOURCE = "https://data.cityofnewyork.us/Public-Safety/EMS-Incident-Dispatch-Data/76xm-jjuj"
API = "https://data.cityofnewyork.us/resource/76xm-jjuj.json"
METADATA = "https://data.cityofnewyork.us/api/views/76xm-jjuj.json"
DICTIONARY = "https://data.cityofnewyork.us/api/views/76xm-jjuj/files/1f3c87df-ffa3-4bda-a63c-45aeac003a26?download=true&filename=EMS_incident_dispatch_data_description.xlsx"
START = "2023-01-01T00:00:00"
END = "2023-04-01T00:00:00"
MAX_ROWS = 400_000
PAGE_SIZE = 50_000
COLUMNS = ["incident_id", "incident_datetime", "initial_call_type",
           "initial_severity_level_code", "borough", "valid_dispatch_rspns_time_indc",
           "dispatch_response_seconds_qy"]
WHERE = f"incident_datetime >= '{START}' AND incident_datetime < '{END}'"


def request_json(url: str, parameters: dict | None = None):
    address = url + ("?" + urlencode(parameters) if parameters else "")
    for attempt in range(4):
        try:
            with urlopen(address, timeout=180, context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)) as response:
                return json.load(response)
        except (OSError, ValueError):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(output: Path) -> dict:
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=True)
    sample = output / "sample.jsonl"
    if sample.exists() or (output / "manifest.json").exists():
        raise ValueError("Use a new output directory; existing snapshots are never overwritten")
    metadata = request_json(METADATA)
    count = int(request_json(API, {"$select": "count(*)", "$where": WHERE})[0]["count"])
    if not 0 < count <= MAX_ROWS:
        raise ValueError(f"Refusing unbounded or unexpected sample: {count} rows")
    queries = []
    received = 0
    temporary = output / "sample.jsonl.partial"
    with temporary.open("w") as destination:
        for offset in range(0, count, PAGE_SIZE):
            query = {"$select": ",".join(COLUMNS), "$where": WHERE,
                     "$order": "incident_datetime ASC,incident_id ASC",
                     "$limit": min(PAGE_SIZE, count - offset), "$offset": offset}
            rows = request_json(API, query)
            if len(rows) != query["$limit"]:
                raise ValueError("Source changed or page was truncated")
            for row in rows:
                destination.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            received += len(rows)
            queries.append(query)
            print(f"Fetched {received}/{count}", flush=True)
    latest = request_json(METADATA)
    final_count = int(request_json(API, {"$select": "count(*)", "$where": WHERE})[0]["count"])
    if metadata.get("rowsUpdatedAt") != latest.get("rowsUpdatedAt") or final_count != count:
        raise ValueError("Source revision changed during pagination; discard partial snapshot")
    temporary.rename(sample)
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    with urlopen(DICTIONARY, timeout=120, context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)) as response:
        (output / "dictionary.xlsx").write_bytes(response.read())
    manifest = {"source_url": SOURCE, "api_url": API, "dictionary_url": DICTIONARY,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_rows_updated_at": metadata.get("rowsUpdatedAt"),
                "start_inclusive": START, "end_exclusive": END,
                "row_count": received, "max_rows": MAX_ROWS, "queries": queries,
                "sample_sha256": sha256(sample),
                "metadata_sha256": sha256(output / "metadata.json"),
                "dictionary_sha256": sha256(output / "dictionary.xlsx"),
                "fetch_seconds": time.perf_counter() - started}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/ems/q1-2023"))
    print(json.dumps(fetch(parser.parse_args().output), indent=2))
