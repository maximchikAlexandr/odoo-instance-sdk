import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Badge,
  Button,
  Card,
  Group,
  Progress,
  Select,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import {
  type ClusterSnapshot,
  type EnvironmentSnapshot,
  type ProjectSummary,
  type RuntimeMetrics,
  type Snapshot,
  fetchSnapshot,
} from "./api";
import { formatBytes, formatPercent } from "./format";

const POLL_MS = 2000;

export default function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const snap = await fetchSnapshot(selectedProjectId);
      setSnapshot(snap);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [selectedProjectId]);

  useEffect(() => {
    setLoading(true);
    void load();
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const projects = snapshot?.projects ?? [];
  const environments = snapshot?.environments ?? [];

  const selectData = useMemo(
    () => [
      { value: "", label: "All projects" },
      ...projects.map((p) => ({ value: p.id, label: `${p.name} (${p.display_hint})` })),
    ],
    [projects],
  );

  const filteredEnvs = useMemo(
    () =>
      selectedProjectId
        ? environments.filter((e) => e.project_id === selectedProjectId)
        : environments,
    [environments, selectedProjectId],
  );

  const projectsById = useMemo(() => {
    const m = new Map<string, ProjectSummary>();
    for (const p of projects) m.set(p.id, p);
    return m;
  }, [projects]);

  const displayedProjects = selectedProjectId
    ? projects.filter((p) => p.id === selectedProjectId)
    : projects;

  return (
    <Stack p="md" maw={1400} mx="auto">
      <Group justify="space-between" align="baseline">
        <Title order={2}>odcli monitor</Title>
        <Select
          w={320}
          data={selectData}
          value={selectedProjectId ?? ""}
          onChange={(v) => setSelectedProjectId(v ?? null)}
          placeholder="All projects"
        />
      </Group>

      <Stack gap="sm">
        {loading && !snapshot ? (
          <Text>Loading…</Text>
        ) : error ? (
          <Stack gap="xs">
            <Text c="red">API error: {error}</Text>
            <Button onClick={() => void load()} w={120}>
              Retry
            </Button>
          </Stack>
        ) : projects.length === 0 ? (
          <Text>
            No environments found. Use `odcli env checkout` to create one.
          </Text>
        ) : (
          <>
            {displayedProjects.map((p) =>
              p.cluster ? (
                <ClusterCard key={`cluster-${p.id}`} project={p} cluster={p.cluster} />
              ) : null,
            )}
            <SimpleGrid
              cols={{ base: 1, sm: 2, lg: 3 }}
              spacing="md"
            >
              {filteredEnvs.map((e) => (
                <EnvironmentCard
                  key={e.id}
                  env={e}
                  projectName={projectsById.get(e.project_id)?.name ?? e.project_id}
                />
              ))}
            </SimpleGrid>
          </>
        )}
      </Stack>
    </Stack>
  );
}

function ClusterCard({
  project,
  cluster,
}: {
  project: ProjectSummary;
  cluster: ClusterSnapshot;
}) {
  const c = cluster.container;
  const m = cluster.metrics;
  return (
    <Card withBorder padding="md" radius="sm">
      <Group justify="space-between" align="baseline" mb="xs">
        <Group gap="xs" align="baseline">
          <Text fw={600}>Cluster — {project.name}</Text>
          <Badge variant="light">{cluster.mode}</Badge>
          <Badge variant="light" color={cluster.owned ? "green" : "gray"}>
            {cluster.owned ? "owned" : "external"}
          </Badge>
          <Badge variant="light" color={cluster.state === "healthy" ? "teal" : "orange"}>
            state: {cluster.state}
          </Badge>
          {cluster.unavailability_reason ? (
            <Badge variant="light" color="red">
              {cluster.unavailability_reason}
            </Badge>
          ) : null}
        </Group>
      </Group>
      {c ? (
        <Text size="sm" c="dimmed">
          container {c.id.slice(0, 12)} · {c.name} · {c.image}
          {c.pid !== null ? ` · PID ${c.pid} (${c.pid_scope})` : ""}
        </Text>
      ) : (
        <Text size="sm" c="dimmed">container: —</Text>
      )}
      <Group gap="xl" mt="xs">
        <Text size="sm">CPU: {formatPercent(m?.cpu_percent ?? null)}</Text>
        <Text size="sm">
          RAM: {formatBytes(m?.memory_usage_bytes ?? null)}
          {m?.memory_limit_bytes ? ` / ${formatBytes(m.memory_limit_bytes)}` : ""}
        </Text>
        <Text size="sm">Volume: {formatBytes(m?.volume_usage_bytes ?? null)}</Text>
      </Group>
      {m?.memory_usage_bytes && m?.memory_limit_bytes ? (
        <Progress
          mt="xs"
          value={(m.memory_usage_bytes / m.memory_limit_bytes) * 100}
          size="xs"
          color="violet"
        />
      ) : null}
    </Card>
  );
}

function EnvironmentCard({
  env,
  projectName,
}: {
  env: EnvironmentSnapshot;
  projectName: string;
}) {
  const rt: RuntimeMetrics | null = env.runtime;
  const git = env.git;
  const st = env.storage;

  const port =
    rt && (rt.state === "ready" || rt.state === "not_ready") && rt.http_port !== null
      ? rt.http_port
      : env.allocated_http_port;

  const workerCount = rt && rt.child_pids.length > 0 ? rt.child_pids.length : 0;
  const isLive = rt?.state === "ready" || rt?.state === "not_ready";
  const isOpenEnabled = rt?.state === "ready" && rt.http_url !== null;

  return (
    <Card withBorder padding="md" radius="sm">
      <Stack gap="xs">
        <Group justify="space-between" align="baseline">
          <Text fw={600}>{env.name}</Text>
          <Text size="xs" c="dimmed">{projectName}</Text>
        </Group>

        <Group gap="xs">
          <Badge variant="light" color={lifecycleColor(env.lifecycle_state)}>
            {env.lifecycle_state}
          </Badge>
          <Badge variant="light" color={runtimeStateColor(rt?.state)}>
            runtime: {rt?.state ?? "—"}
          </Badge>
        </Group>

        <Text size="sm">
          {env.branch ?? "—"} · {env.short_sha ?? "—"}
        </Text>
        <Text size="sm" c="dimmed">
          database: {env.database ?? "—"} · port: {port ?? "—"}
        </Text>

        {git ? (
          <Text size="sm" c="dimmed">
            git: {git.state} · ↑{git.ahead} ↓{git.behind}
            {git.diff ? ` · +${git.diff.added} -${git.diff.deleted}` : ""}
          </Text>
        ) : null}

        {st ? (
          <Stack gap={2}>
            <Text size="sm">
              disk: {formatBytes(st.total_bytes)} {st.complete ? "" : "(partial)"}
            </Text>
            <Text size="xs" c="dimmed">
              worktree {formatBytes(st.worktree_bytes)} · venv{" "}
              {formatBytes(st.python_environment?.bytes ?? null)} · db{" "}
              {formatBytes(st.database?.total_bytes ?? null)} · other{" "}
              {formatBytes(st.other_files_bytes)}
            </Text>
          </Stack>
        ) : null}

        <Group justify="space-between" align="baseline">
          <Text size="sm">
            {isLive && rt ? (
              <>
                Odoo PID {rt.root_pid ?? "—"}
                {workerCount > 0 ? ` (+${workerCount} workers)` : ""}
                {" · CPU "}
                {formatPercent(rt.cpu_percent)}
                {" · RAM "}
                {formatBytes(rt.rss_bytes)}
              </>
            ) : (
              "Odoo: —"
            )}
          </Text>
          <Button
            size="xs"
            disabled={!isOpenEnabled}
            onClick={() => {
              if (rt?.http_url) window.open(rt.http_url, "_blank", "noopener,noreferrer");
            }}
          >
            Open Odoo
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}

function lifecycleColor(state: string): string {
  switch (state) {
    case "ready":
      return "green";
    case "creating":
      return "blue";
    case "failed":
    case "cleanup_failed":
      return "red";
    case "removing":
    case "removed":
      return "gray";
    default:
      return "orange";
  }
}

function runtimeStateColor(state: string | undefined): string {
  switch (state) {
    case "ready":
      return "green";
    case "not_ready":
      return "orange";
    case "stopped":
      return "gray";
    default:
      return "gray";
  }
}