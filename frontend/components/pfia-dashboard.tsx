"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  BrainCircuit,
  FileUp,
  MessageSquareQuote,
  Radar,
  RefreshCcw
} from "lucide-react";

import {
  PUBLIC_API_BASE_URL,
  askQuestion,
  fetchDemoFile,
  fetchSession,
  uploadReviews
} from "@/lib/api";
import type {
  AlertRecord,
  ChatResponse,
  ClusterRecord,
  EventRecord,
  RuntimeMetadata,
  SessionDetail
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardEyebrow, CardTitle } from "@/components/ui/card";

const POLL_INTERVAL_MS = 2000;

function statusTone(status: string): "neutral" | "success" | "danger" {
  if (status.includes("FAILED")) {
    return "danger";
  }
  if (status.includes("COMPLETED")) {
    return "success";
  }
  return "neutral";
}

function renderRuntime(metadata: RuntimeMetadata | null | undefined) {
  if (!metadata) {
    return <p className="text-sm text-ink/60">Run a session to inspect runtime metadata.</p>;
  }

  const usedAgents = Object.entries(metadata.agent_usage || {})
    .filter(([, meta]) => meta?.used)
    .map(([name, meta]) => `${name} (${meta.mode || "unknown"})`);

  return (
    <div className="space-y-2 text-sm text-ink/80">
      <div><strong>Runtime profile:</strong> {metadata.runtime_profile}</div>
      <div><strong>Presentation mode:</strong> {metadata.presentation_mode}</div>
      <div><strong>Low data mode:</strong> {metadata.low_data_mode ? "yes" : "no"}</div>
      <div><strong>Trace id:</strong> <code>{metadata.trace_correlation_id}</code></div>
      <div><strong>Trace exporters:</strong> {(metadata.trace_exporters_effective || []).join(", ") || "n/a"}</div>
      <div><strong>PII backend:</strong> {metadata.pii_backend_effective}</div>
      <div><strong>Sentiment backend:</strong> {metadata.sentiment_backend_effective}</div>
      <div><strong>Embedding backend:</strong> {metadata.embedding_backend_effective || metadata.embedding_backend}</div>
      <div><strong>Generation backend:</strong> {metadata.generation_backend_effective}</div>
      <div><strong>Retrieval backend:</strong> {metadata.retrieval_backend_effective}</div>
      <div><strong>Chroma mode:</strong> {metadata.chroma_mode_effective || "n/a"}</div>
      <div><strong>Chroma endpoint:</strong> {metadata.chroma_endpoint_effective || metadata.chroma_persist_dir || "n/a"}</div>
      <div><strong>LLM / embedding calls:</strong> {metadata.llm_call_count} / {metadata.embedding_call_count}</div>
      <div><strong>Weak signals:</strong> {metadata.weak_signal_count}</div>
      <div><strong>Mixed sentiment clusters:</strong> {metadata.mixed_sentiment_cluster_count}</div>
      <div><strong>Mixed-language reviews:</strong> {metadata.mixed_language_review_count}</div>
      <div><strong>Estimated cost USD:</strong> {(metadata.estimated_cost_usd || 0).toFixed(6)}</div>
      <div><strong>Agents used:</strong> {usedAgents.length ? usedAgents.join(", ") : "deterministic-only path"}</div>
    </div>
  );
}

function ClusterCard({
  cluster,
  alerts
}: {
  cluster: ClusterRecord;
  alerts: AlertRecord[];
}) {
  const alert = alerts.find(
    (item) => item.cluster_id === cluster.cluster_id && !item.insufficient_history
  );

  return (
    <article className="rounded-[1.6rem] border border-ink/8 bg-white/80 p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="font-mono text-sm text-ink/55">{cluster.cluster_id}</div>
          <h3 className="mt-2 text-3xl font-display text-ink">{cluster.label}</h3>
        </div>
        <Badge>{cluster.priority_score.toFixed(2)}</Badge>
      </div>
      <p className="mt-4 text-lg leading-8 text-ink/90">{cluster.summary}</p>
      <p className="mt-4 text-sm text-ink/65">
        Reviews: {cluster.size} · Sentiment: {cluster.sentiment_score.toFixed(2)} · Trend:{" "}
        {cluster.trend_delta.toFixed(2)}
      </p>
      <div className="mt-4 flex flex-wrap gap-2">
        {cluster.keywords.map((keyword) => (
          <span
            key={keyword}
            className="rounded-full bg-mist px-3 py-1 text-sm text-ink/80"
          >
            {keyword}
          </span>
        ))}
      </div>
      {alert ? (
        <div className="mt-4 rounded-2xl bg-lagoon/8 px-4 py-3 text-sm text-lagoon">
          Alert: {alert.reason}
        </div>
      ) : null}
    </article>
  );
}

function EventCard({ event }: { event: EventRecord }) {
  return (
    <div className="rounded-[1.4rem] border border-ink/8 bg-white/80 p-4">
      <div className="flex items-center justify-between gap-4">
        <strong className="text-xl text-ink">{event.stage}</strong>
        <span className="text-sm font-semibold uppercase tracking-[0.18em] text-lagoon">
          {event.level}
        </span>
      </div>
      <p className="mt-2 text-ink/85">{event.message}</p>
      <div className="mt-2 text-xs text-ink/55">
        {event.created_at} · {event.correlation_id}
      </div>
    </div>
  );
}

function ReviewCard({
  review
}: {
  review: SessionDetail["simple_list_reviews"][number];
}) {
  return (
    <article className="rounded-[1.6rem] border border-ink/8 bg-white/80 p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="font-mono text-sm text-ink/55">{review.review_id}</div>
          <h3 className="mt-2 text-2xl font-display text-ink">{review.source}</h3>
        </div>
        <Badge>{review.language}</Badge>
      </div>
      <p className="mt-4 text-lg leading-8 text-ink/90">{review.text}</p>
      <p className="mt-4 text-sm text-ink/65">
        {review.created_at} · flags: {review.flags.length ? review.flags.join(", ") : "none"}
      </p>
    </article>
  );
}

export function PfiaDashboard() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [question, setQuestion] = useState(
    "What is the highest-priority issue and what evidence supports it?"
  );
  const [answer, setAnswer] = useState<ChatResponse | null>(null);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const pollRef = useRef<number | null>(null);

  async function loadSession(targetSessionId: string) {
    const payload = await fetchSession(targetSessionId);
    setDetail(payload);
    return payload;
  }

  useEffect(() => {
    if (!sessionId) {
      return;
    }
    void loadSession(sessionId).then((payload) => {
      const terminal = ["COMPLETED", "DEGRADED_COMPLETED", "FAILED"].includes(
        payload.session.status
      );
      if (terminal) {
        if (pollRef.current) {
          window.clearInterval(pollRef.current);
          pollRef.current = null;
        }
        return;
      }
      pollRef.current = window.setInterval(() => {
        void loadSession(sessionId).then((nextPayload) => {
          if (
            ["COMPLETED", "DEGRADED_COMPLETED", "FAILED"].includes(
              nextPayload.session.status
            ) &&
            pollRef.current
          ) {
            window.clearInterval(pollRef.current);
            pollRef.current = null;
          }
        });
      }, POLL_INTERVAL_MS);
    }).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "Could not load session.");
    });

    return () => {
      if (pollRef.current) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [sessionId]);

  async function handleFile(file: File) {
    setIsSubmitting(true);
    setError(null);
    setAnswer(null);
    try {
      const payload = await uploadReviews(file);
      setSessionId(payload.session_id);
      setJobId(payload.job_id);
      setUploadMessage(`Queued session ${payload.session_id}.`);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Upload failed.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function runDemo() {
    setIsSubmitting(true);
    setError(null);
    setAnswer(null);
    try {
      const file = await fetchDemoFile();
      await handleFile(file);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Could not load the demo dataset.");
      setIsSubmitting(false);
    }
  }

  async function submitQuestion(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!sessionId) {
      setError("Run a session first.");
      return;
    }
    if (question.trim().length < 3) {
      setError("Question must contain at least 3 characters.");
      return;
    }
    setError(null);
    try {
      const payload = await askQuestion(sessionId, question.trim());
      setAnswer(payload);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Q&A request failed.");
    }
  }

  const metrics = useMemo(() => {
    const summary = detail?.preprocessing_summary;
    if (!summary) {
      return [];
    }
    return [
      { label: "Reviews kept", value: summary.kept_records },
      { label: "Duplicates", value: summary.duplicate_records },
      { label: "Quarantined", value: summary.quarantined_records },
      { label: "Clusters", value: detail?.top_clusters.length || detail?.clusters.length || 0 }
    ];
  }, [detail]);

  return (
    <main className="mx-auto flex min-h-screen max-w-[1560px] flex-col gap-6 px-4 py-8 md:px-8">
      <Card className="overflow-hidden">
        <div className="grid gap-8 lg:grid-cols-[1.4fr_0.8fr]">
          <div>
            <CardEyebrow>Product Feedback Intelligence Agent</CardEyebrow>
            <h1 className="mt-4 max-w-4xl font-display text-5xl leading-tight text-ink md:text-7xl">
              Turn a raw review export into an explainable product brief.
            </h1>
            <p className="mt-5 max-w-3xl text-lg leading-8 text-ink/75 md:text-xl">
              Separate Next.js frontend for upload, runtime visibility, Markdown report
              review, and grounded Q&A over completed PFIA sessions.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Button onClick={runDemo} disabled={isSubmitting}>
                <Radar className="mr-2 h-4 w-4" />
                Run Demo Dataset
              </Button>
              <a
                className="inline-flex items-center justify-center rounded-full border border-ink/10 bg-white/70 px-5 py-3 text-sm font-semibold text-ink transition hover:bg-white"
                href={`${PUBLIC_API_BASE_URL}/api/demo/sample-file`}
                target="_blank"
                rel="noreferrer"
              >
                Download Demo CSV
              </a>
            </div>
            {uploadMessage ? (
              <div className="mt-5 rounded-3xl bg-lagoon/8 px-4 py-3 text-sm text-lagoon">
                {uploadMessage}
              </div>
            ) : null}
            {error ? (
              <div className="mt-5 rounded-3xl bg-ember/10 px-4 py-3 text-sm text-ember">
                {error}
              </div>
            ) : null}
          </div>

          <div className="rounded-[1.8rem] bg-sand p-5">
            <CardEyebrow>API Target</CardEyebrow>
            <div className="mt-4 space-y-3 text-sm text-ink/70">
              <div><strong>Base URL:</strong> <code>{PUBLIC_API_BASE_URL}</code></div>
              <div><strong>Session:</strong> <code>{sessionId || "n/a"}</code></div>
              <div><strong>Job:</strong> <code>{jobId || "n/a"}</code></div>
              <div className="pt-3">
                <label className="block text-sm font-semibold text-ink">Upload arbitrary CSV / JSON</label>
                <input
                  className="mt-3 block w-full rounded-2xl border border-ink/10 bg-white px-4 py-3 text-sm text-ink"
                  type="file"
                  accept=".csv,.json"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) {
                      void handleFile(file);
                    }
                  }}
                />
              </div>
            </div>
          </div>
        </div>
      </Card>

      <section className="grid gap-6 xl:grid-cols-2">
        <Card>
          <div className="flex items-start justify-between gap-4">
            <div>
              <CardEyebrow>1. Runtime</CardEyebrow>
              <CardTitle>Live execution state</CardTitle>
            </div>
            <Badge tone={statusTone(detail?.session.status || "IDLE")}>
              {detail?.session.status || "IDLE"}
            </Badge>
          </div>
          <div className="mt-6 grid gap-4 md:grid-cols-2">
            {metrics.length ? (
              metrics.map((item) => (
                <div
                  key={item.label}
                  className="rounded-[1.4rem] border border-ink/8 bg-white/80 p-4"
                >
                  <div className="text-sm text-ink/55">{item.label}</div>
                  <div className="mt-2 text-4xl font-display">{item.value}</div>
                </div>
              ))
            ) : (
              <div className="rounded-[1.4rem] border border-dashed border-ink/15 bg-white/60 p-4 text-sm text-ink/55 md:col-span-2">
                Start a session to populate runtime state and summary metrics.
              </div>
            )}
          </div>
          <div className="mt-6">{renderRuntime(detail?.runtime_metadata)}</div>
          {detail?.warnings.length ? (
            <div className="mt-6 space-y-3">
              {detail.warnings.map((warning) => (
                <div
                  key={warning}
                  className="rounded-[1.2rem] bg-lagoon/8 px-4 py-3 text-sm text-lagoon"
                >
                  {warning}
                </div>
              ))}
            </div>
          ) : null}
        </Card>

        <Card>
          <div className="flex items-start justify-between gap-4">
            <div>
              <CardEyebrow>2. Q&A</CardEyebrow>
              <CardTitle>Ask grounded questions</CardTitle>
            </div>
            <MessageSquareQuote className="h-8 w-8 text-lagoon" />
          </div>
          <form className="mt-6 space-y-4" onSubmit={submitQuestion}>
            <textarea
              className="min-h-40 w-full rounded-[1.6rem] border border-ink/10 bg-white/80 px-5 py-4 text-base text-ink outline-none ring-0 transition placeholder:text-ink/35 focus:border-lagoon/40"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
            />
            <Button type="submit">
              <BrainCircuit className="mr-2 h-4 w-4" />
              Ask PFIA
            </Button>
          </form>
          <div className="mt-6 space-y-4">
            {answer ? (
              <>
                <div className="rounded-[1.4rem] border border-ink/8 bg-white/80 p-5">
                  <div className="text-sm font-bold uppercase tracking-[0.18em] text-lagoon">
                    Answer
                  </div>
                  <pre className="mt-4 font-body text-base leading-8 text-ink/90">
                    {answer.answer}
                  </pre>
                </div>
                <div className="rounded-[1.4rem] border border-ink/8 bg-white/80 p-5">
                  <div className="text-sm font-bold uppercase tracking-[0.18em] text-lagoon">
                    Tool trace
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    {answer.tool_trace.map((item, index) => (
                      <span
                        key={`${item.tool}-${index}`}
                        className="rounded-full bg-mist px-3 py-1 text-sm text-ink/80"
                      >
                        {item.tool}: {item.output_summary}
                      </span>
                    ))}
                  </div>
                </div>
              </>
            ) : (
              <div className="rounded-[1.4rem] border border-dashed border-ink/15 bg-white/60 p-5 text-sm text-ink/55">
                Ask a question after the batch reaches a completed state.
              </div>
            )}
          </div>
        </Card>
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.05fr_0.95fr]">
        <Card>
          <div className="flex items-start justify-between gap-4">
            <div>
              <CardEyebrow>3. Findings</CardEyebrow>
              <CardTitle>Top clusters and alerts</CardTitle>
            </div>
            <Activity className="h-8 w-8 text-lagoon" />
          </div>
          <div className="mt-6 space-y-4">
            {detail?.presentation_mode === "simple_list" ? (
              <>
                <div className="rounded-[1.4rem] bg-lagoon/8 px-4 py-3 text-sm text-lagoon">
                  Low-data mode is active, so PFIA shows the sanitized review list first.
                </div>
                {detail.simple_list_reviews.length ? (
                  detail.simple_list_reviews.map((review) => (
                    <ReviewCard key={review.review_id} review={review} />
                  ))
                ) : (
                  <div className="rounded-[1.4rem] border border-dashed border-ink/15 bg-white/60 p-5 text-sm text-ink/55">
                    No sanitized review previews are available for this session.
                  </div>
                )}
                {detail.top_clusters.length ? (
                  <>
                    <div className="pt-3 text-sm font-semibold uppercase tracking-[0.18em] text-lagoon">
                      Provisional themes
                    </div>
                    {detail.top_clusters.map((cluster) => (
                      <ClusterCard key={cluster.cluster_id} cluster={cluster} alerts={detail.alerts} />
                    ))}
                  </>
                ) : null}
              </>
            ) : detail?.top_clusters.length ? (
              detail.top_clusters.map((cluster) => (
                <ClusterCard key={cluster.cluster_id} cluster={cluster} alerts={detail.alerts} />
              ))
            ) : (
              <div className="rounded-[1.4rem] border border-dashed border-ink/15 bg-white/60 p-5 text-sm text-ink/55">
                Clusters appear here after analysis completes.
              </div>
            )}
            {detail?.weak_signals.length ? (
              <>
                <div className="pt-3 text-sm font-semibold uppercase tracking-[0.18em] text-lagoon">
                  Weak signals
                </div>
                {detail.weak_signals.map((cluster) => (
                  <ClusterCard key={cluster.cluster_id} cluster={cluster} alerts={detail.alerts} />
                ))}
              </>
            ) : null}
          </div>
        </Card>

        <Card>
          <div className="flex items-start justify-between gap-4">
            <div>
              <CardEyebrow>4. Report</CardEyebrow>
              <CardTitle>Markdown artifact</CardTitle>
            </div>
            <FileUp className="h-8 w-8 text-lagoon" />
          </div>
          <pre className="mt-6 max-h-[36rem] overflow-auto rounded-[1.6rem] bg-[#f8f4eb] p-5 font-mono text-sm leading-7 text-ink/90">
            {detail?.report?.markdown || "The report will appear here after the job completes."}
          </pre>
        </Card>
      </section>

      <Card>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardEyebrow>5. Timeline</CardEyebrow>
            <CardTitle>Structured stage events</CardTitle>
          </div>
          <Button
            variant="secondary"
            onClick={() => {
              if (sessionId) {
                void loadSession(sessionId);
              }
            }}
            disabled={!sessionId}
          >
            <RefreshCcw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        </div>
        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          {detail?.events.length ? (
            detail.events.map((event, index) => (
              <EventCard key={`${event.event}-${index}`} event={event} />
            ))
          ) : (
            <div className="rounded-[1.4rem] border border-dashed border-ink/15 bg-white/60 p-5 text-sm text-ink/55">
              Stage events appear here after the session starts moving.
            </div>
          )}
        </div>
      </Card>
    </main>
  );
}
