import { useCallback, useEffect, useRef, useState } from "react";
import { Badge, Button, Card, Group, Select, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import {
  getMonitorSnapshot,
  startRuntime,
  stopRuntime,
  type RuntimeActionRequest,
  type BackupFreshnessSnapshot,
  type PublicationSnapshot,
  type RuntimeMetrics,
  type Snapshot,
} from "./generated";
import client from "./client-config";
import { formatBytes, formatLocalTimestamp, formatPercent } from "./format";

const POLL_MS = 2000;

export default function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [instanceFilter, setInstanceFilter] = useState<string | null>("all");
  const pendingLoad = useRef<Promise<void> | null>(null);

  const load = useCallback(() => {
    if (pendingLoad.current) return pendingLoad.current;
    const request = (async () => {
      try {
        // Keep an authoritative project list while the selector is narrowed;
        // filtering is a view concern, not an API polling concern.
        const response = await getMonitorSnapshot({
          client,
          throwOnError: true,
        });
        const snap = response.data;
        setSnapshot(snap);
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Unable to load snapshot.");
      } finally {
        setLoading(false);
      }
    })();
    pendingLoad.current = request;
    void request.then(() => {
      if (pendingLoad.current === request) pendingLoad.current = null;
    });
    return request;
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    let nextAt = Date.now();
    const poll = async () => {
      await load();
      // Keep the wall-clock cadence while serial execution prevents overlap.
      // Slow requests skip missed slots and resume at the first future slot.
      const now = Date.now();
      do nextAt += POLL_MS; while (nextAt <= now);
      if (!cancelled) timeout = setTimeout(() => void poll(), nextAt - now);
    };
    setLoading(true);
    void poll();
    return () => {
      cancelled = true;
      if (timeout) clearTimeout(timeout);
    };
  }, [load]);

  const projects = snapshot?.projects ?? [];
  const environments = snapshot?.environments ?? [];

  const selectData = [
    { value: "", label: "All projects" },
    ...projects.map((p) => ({ value: p.id, label: `${p.name} (${p.display_hint})` })),
  ];
  const scopedEnvs = selectedProjectId
    ? environments.filter((e) => e.project_id === selectedProjectId)
    : environments;
  const scopedProjects = selectedProjectId
    ? projects.filter((p) => p.id === selectedProjectId)
    : projects;
  const isRunning = (runtime: RuntimeMetrics | null) =>
    runtime?.state === "ready" || runtime?.state === "not_ready";
  const displayedProjects = instanceFilter === "running"
    ? scopedProjects.filter((project) => isRunning(project.runtime))
    : scopedProjects;
  const filteredEnvs = instanceFilter === "running"
    ? scopedEnvs.filter((environment) => isRunning(environment.runtime))
    : scopedEnvs;
  const hasInstances = displayedProjects.length + filteredEnvs.length > 0;

  return (
    <div className="monitor-shell">
      <header className="monitor-navbar">
        <div className="monitor-navbar-inner">
          <span className="monitor-app-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor">
              <rect x="3" y="3" width="7" height="7" rx="1" />
              <rect x="14" y="3" width="7" height="7" rx="1" />
              <rect x="3" y="14" width="7" height="7" rx="1" />
              <rect x="14" y="14" width="7" height="7" rx="1" />
            </svg>
          </span>
          <span className="monitor-brand">odcli</span>
          <span className="monitor-app-name">Monitor</span>
          <a className="monitor-admin-link" href="/admin/">Admin</a>
        </div>
      </header>

      <main className="monitor-content">
        <div className="monitor-control-panel">
          <div>
            <Title order={1} className="monitor-title">Instances</Title>
            <Text className="monitor-subtitle">
              {selectedProjectId ? projects.find((p) => p.id === selectedProjectId)?.name : "All projects"}
              {snapshot ? ` · ${displayedProjects.length + filteredEnvs.length} instances` : ""}
            </Text>
          </div>
          <Select
            className="monitor-project-select"
            aria-label="Project"
            data={selectData}
            searchable
            value={selectedProjectId ?? ""}
            onChange={(v) => setSelectedProjectId(v ?? null)}
            placeholder="All projects"
          />
          <Select
            className="monitor-instance-select"
            aria-label="Instances"
            data={[{ value: "all", label: "All instances" }, { value: "running", label: "Running" }]}
            value={instanceFilter ?? "all"}
            onChange={setInstanceFilter}
          />
        </div>

        {loading && !snapshot ? (
          <div className="monitor-message" role="status">Loading instances…</div>
        ) : error ? (
          <Stack gap="xs" className="monitor-message" role="alert">
            <Text c="red">API error: {error}</Text>
            <Button onClick={() => void load()} w={120}>Retry</Button>
          </Stack>
        ) : projects.length === 0 ? (
          <div className="monitor-message">No projects or environments found.</div>
        ) : !hasInstances ? (
          <div className="monitor-message">No running instances in this selection.</div>
        ) : (
          <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="md" className="instance-grid">
            {displayedProjects.map((project) => (
              <RuntimeCard
                key={`project-${project.id}`}
                name={project.name}
                kind="Main checkout"
                target={{ owner_kind: "project", owner_id: project.id }}
                refresh={load}
                branch={project.runtime?.branch}
                runtime={project.runtime}
                publication={project.publication}
                backupFreshness={project.backup_freshness}
                storage={project.storage}
                database={project.runtime?.database_name}
                testId="project-card"
                actionTestId="open-project-odoo"
              />
            ))}
            {filteredEnvs.map((env) => (
              <RuntimeCard
                key={env.id}
                name={env.name}
                kind="Worktree"
                target={{ owner_kind: "environment", owner_id: env.id }}
                refresh={load}
                branch={env.branch}
                runtime={env.runtime}
                publication={env.publication}
                backupFreshness={env.backup_freshness}
                storage={env.storage}
                database={env.database}
                lifecycleState={env.lifecycle_state}
                testId="environment-card"
                actionTestId="open-odoo"
              />
            ))}
          </SimpleGrid>
        )}
      </main>
    </div>
  );
}

function RuntimeCard({
  name,
  kind,
  branch,
  target,
  refresh,
  runtime,
  publication,
  backupFreshness,
  storage,
  database,
  lifecycleState,
  testId,
  actionTestId,
}: {
  name: string;
  kind: "Main checkout" | "Worktree";
  branch?: string | null;
  target: RuntimeActionRequest;
  refresh: () => Promise<void>;
  runtime: RuntimeMetrics | null;
  publication?: PublicationSnapshot;
  backupFreshness?: BackupFreshnessSnapshot;
  storage: { total_bytes: number | null; complete: boolean };
  database: string | null | undefined;
  lifecycleState?: string;
  testId: string;
  actionTestId: string;
}) {
  const [pending, setPending] = useState<"start" | "stop" | null>(null);
  const pendingAction = useRef(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [completedState, setCompletedState] = useState<"started" | "stopped" | null>(null);
  const runtimeLive = runtime?.state === "ready" || runtime?.state === "not_ready";
  useEffect(() => {
    if (completedState && runtimeLive === (completedState === "started")) setCompletedState(null);
  }, [completedState, runtimeLive]);
  const live = completedState ? completedState === "started" : runtimeLive;
  const state = lifecycleState && lifecycleState !== "ready"
    ? lifecycleState
    : completedState === "stopped" ? "stopped" : completedState === "started" ? "not_ready" : runtime?.state;
  const status = pending
    ? { label: pending === "start" ? "Starting…" : "Stopping…", color: "orange" }
    : runtimeStatus(state);
  const controllable = !lifecycleState || lifecycleState === "ready";

  const controlRuntime = async () => {
    if (pendingAction.current || !controllable) return;
    const action = live ? "stop" : "start";
    pendingAction.current = true;
    setPending(action);
    setActionError(null);
    try {
      const operation = action === "start" ? startRuntime : stopRuntime;
      const result = await operation({ client, body: target, throwOnError: true });
      setCompletedState(result.data.state);
      await refresh();
    } catch (error) {
      setActionError(
        typeof error === "object" && error !== null && "message" in error && typeof error.message === "string"
          ? error.message : `Unable to ${action} this instance.`,
      );
    } finally {
      pendingAction.current = false;
      setPending(null);
    }
  };
  const externalUrl = publication?.state === "available" ? publication.external_url : null;

  return (
    <Card data-testid={testId} className="instance-card" withBorder padding="lg" radius="sm">
      <div className="instance-heading">
        <div className="instance-identity">
          <Text className="instance-name" title={name}>{name}</Text>
          <Text className="instance-kind">{kind}</Text>
          <Text className="instance-branch">
            <span>Branch</span> <code title={branch ?? undefined}>{branch || "Not recorded"}</code>
          </Text>
        </div>
        <Badge className="instance-status" color={status.color} variant="light">
          {status.label}
        </Badge>
      </div>

      <dl className="instance-fields">
        <div>
          <dt>Database</dt>
          <dd title={database ?? undefined}>{database ?? "Not configured"}</dd>
        </div>
        <div>
          <dt>Local endpoint</dt>
          <dd title={runtime?.http_url ?? undefined}>{runtime?.http_url ?? "Not running"}</dd>
        </div>
        <div className="instance-freshness">
          <dt>Backup download</dt>
          {backupFreshness?.downloaded_at && backupFreshness.age_seconds != null ? (
            <dd>
              <time dateTime={backupFreshness.downloaded_at}>{formatLocalTimestamp(backupFreshness.downloaded_at)}</time>
              <span className="instance-freshness-age"> ({formatAge(backupFreshness.age_seconds)} old)</span>
            </dd>
          ) : <dd>Backup download date unknown</dd>}
        </div>
        <div>
          <dt>Resources</dt>
          <dd>{formatBytes(storage.total_bytes)}{storage.complete ? "" : " (incomplete)"}</dd>
        </div>
      </dl>

      <div className="instance-footer">
        <Group gap="lg" className="instance-metrics">
          {live && runtime?.cpu_percent != null ? (
            <Text><span>CPU</span> {formatPercent(runtime.cpu_percent)}</Text>
          ) : null}
          {live && runtime?.memory_bytes != null ? (
            <Text><span>RAM</span> {formatBytes(runtime.memory_bytes)}</Text>
          ) : null}
        </Group>
        <Group gap="sm" className="instance-actions">
          <Button
            data-testid={actionTestId}
            className="instance-open"
            size="sm"
            disabled={!externalUrl}
            title={externalUrl ? "Open published Odoo" : publication?.reason ?? "Publish this instance to open Odoo"}
            onClick={() => {
              if (externalUrl) window.open(externalUrl, "_blank", "noopener,noreferrer");
            }}
          >
            Open Odoo <span className="instance-open-arrow" aria-hidden="true">↗</span>
          </Button>
          <Button
            data-testid="runtime-control"
            variant="outline"
            color={live ? "red" : "odoo"}
            size="sm"
            disabled={pending !== null || !controllable}
            loading={pending !== null}
            onClick={() => void controlRuntime()}
          >
            {pending === "start" ? "Starting…" : pending === "stop" ? "Stopping…" : live ? "Stop" : "Start"}
          </Button>
        </Group>
        {actionError ? <Text size="xs" c="red" role="alert">{actionError}</Text> : null}
        {/* TODO(#147): Restore Open pgAdmin and its status after automatic credential provisioning and authorization are ready. */}
      </div>
    </Card>
  );
}

function formatAge(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)} seconds`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} minutes`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hours`;
  const days = Math.floor(hours / 24);
  return `${days} days`;
}

function runtimeStatus(state: string | undefined): { label: string; color: string } {
  switch (state) {
    case "ready": return { label: "Running", color: "green" };
    case "not_ready": return { label: "Not ready", color: "orange" };
    case "stopped": return { label: "Stopped", color: "gray" };
    case "creating": return { label: "Creating", color: "blue" };
    case "failed":
    case "cleanup_failed": return { label: "Failed", color: "red" };
    case "removing": return { label: "Removing", color: "orange" };
    case "removed": return { label: "Removed", color: "gray" };
    default: return { label: "Not started", color: "gray" };
  }
}
