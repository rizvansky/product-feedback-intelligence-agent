import time
from pathlib import Path
import httpx

base = "http://127.0.0.1:8000"
demo = Path("data/demo/mobile_app_reviews.csv")

with demo.open("rb") as handle:
    response = httpx.post(
        f"{base}/api/sessions/upload",
        files={"file": (demo.name, handle, "text/csv")},
        timeout=60,
    )
print("UPLOAD_STATUS:", response.status_code)
print("UPLOAD_BODY:", response.text)
response.raise_for_status()
payload = response.json()
session_id = payload["session_id"]

detail = None
for attempt in range(90):
    detail_response = httpx.get(f"{base}/api/sessions/{session_id}", timeout=60)
    detail_response.raise_for_status()
    detail = detail_response.json()
    print(
        "POLL:",
        attempt,
        detail["session"]["status"],
        detail["job"]["status"],
        detail["job"]["stage"],
    )
    if detail["session"]["status"] in {"COMPLETED", "DEGRADED_COMPLETED", "FAILED"}:
        break
    time.sleep(1)

chat = httpx.post(
    f"{base}/api/sessions/{session_id}/chat",
    json={
        "question": "What is the highest-priority issue and what evidence supports it?"
    },
    timeout=60,
)
print("CHAT_STATUS:", chat.status_code)
print("CHAT_BODY:", chat.text)
chat.raise_for_status()
