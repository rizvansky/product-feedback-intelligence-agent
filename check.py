import time
from pathlib import Path
import httpx

base = "http://127.0.0.1:8000"
demo = Path("data/demo/mobile_app_reviews.csv")

with demo.open("rb") as handle:
    response = httpx.post(
        f"{base}/api/sessions/upload",
        files={"file": (demo.name, handle, "text/csv")},
        timeout=30,
    )
print("UPLOAD_STATUS:", response.status_code)
print("UPLOAD_BODY:", response.text)
response.raise_for_status()
payload = response.json()
session_id = payload["session_id"]

detail = None
for attempt in range(60):
    detail_response = httpx.get(f"{base}/api/sessions/{session_id}", timeout=30)
    detail_response.raise_for_status()
    detail = detail_response.json()
    session_status = detail["session"]["status"]
    job_status = detail["job"]["status"]
    print(f"POLL_{attempt}: session={session_status} job={job_status}")
    if session_status in {"COMPLETED", "DEGRADED_COMPLETED", "FAILED"}:
        break
    time.sleep(1)

if detail is None:
    raise RuntimeError("No session detail received")

print("FINAL_SESSION_STATUS:", detail["session"]["status"])
print("FINAL_JOB_STATUS:", detail["job"]["status"])
print("CLUSTERS_COUNT:", len(detail.get("clusters") or []))

chat = httpx.post(
    f"{base}/api/sessions/{session_id}/chat",
    json={
        "question": "What is the highest-priority issue and what evidence supports it?"
    },
    timeout=30,
)
print("CHAT_STATUS:", chat.status_code)
print("CHAT_BODY:", chat.text)
chat.raise_for_status()
