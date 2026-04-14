import type {
  ChatResponse,
  SessionDetail,
  UploadResponse
} from "@/lib/types";

export const PUBLIC_API_BASE_URL =
  process.env.NEXT_PUBLIC_PFIA_API_BASE_URL?.replace(/\/$/, "") || "/pfia";

function apiBaseUrl(): string {
  return PUBLIC_API_BASE_URL;
}

async function parseJson<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T & {
    detail?: string;
    message?: string;
    error?: string;
  };
  if (!response.ok) {
    const message =
      payload.message || payload.detail || payload.error || "Request failed.";
    throw new Error(message);
  }
  return payload;
}

export async function uploadReviews(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${apiBaseUrl()}/api/sessions/upload`, {
    method: "POST",
    body: formData
  });
  return parseJson<UploadResponse>(response);
}

export async function fetchDemoFile(): Promise<File> {
  const response = await fetch(`${apiBaseUrl()}/api/demo/sample-file`);
  if (!response.ok) {
    throw new Error("Could not load the demo dataset.");
  }
  const blob = await response.blob();
  return new File([blob], "mobile_app_reviews.csv", { type: "text/csv" });
}

export async function fetchSession(sessionId: string): Promise<SessionDetail> {
  const response = await fetch(`${apiBaseUrl()}/api/sessions/${sessionId}`, {
    cache: "no-store"
  });
  return parseJson<SessionDetail>(response);
}

export async function askQuestion(
  sessionId: string,
  question: string
): Promise<ChatResponse> {
  const response = await fetch(`${apiBaseUrl()}/api/sessions/${sessionId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question })
  });
  return parseJson<ChatResponse>(response);
}
