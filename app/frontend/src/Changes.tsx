import { useEffect, useState } from "react";
import {
  ArrowRight,
  CheckCircle2,
  ExternalLink,
  GitPullRequest,
  Loader2,
  Play,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";
import {
  api,
  dateLabel,
  envLabel,
  type Account,
  type Environment,
  type Finding,
} from "./api";

type Target = {
  id: string;
  account_id: string;
  account_name: string;
  environment: string;
  rule_id: string;
  resource: string;
  region: string;
  runbook_id: string;
  impact: string;
  before: unknown;
  desired: unknown;
  workspace?: {
    directory: string;
    variable_file: string;
    resource_address: string;
  };
};
type Change = {
  id: string;
  batch_id: string;
  status: string;
  kind: string;
  driver: string;
  environment: string;
  requires_approval: boolean;
  plan_hash?: string;
  created_at: string;
  error?: string;
  targets: Target[];
  pull_request?: { url: string };
  events: { at: string; message: string; status?: string }[];
  outcomes: Record<
    string,
    { status: string; message: string; attempts: number }
  >;
};
type Pick = {
  account_id: string;
  rule_id: string;
  resource: string;
  region?: string;
};
function key(value: Pick) {
  return [
    value.account_id,
    value.rule_id,
    value.resource,
    value.region || "",
  ].join("|");
}
const terminal = [
  "SUCCEEDED",
  "FAILED",
  "PARTIAL_FAILURE",
  "MANUAL_ACTION_REQUIRED",
  "STALE_PLAN",
  "EXPIRED",
];

export function Changes({
  accounts,
  environment,
  initial,
  onError,
  onUpdated,
}: {
  accounts: Account[];
  environment: Environment;
  initial?: Pick | null;
  onError: (error: string) => void;
  onUpdated: () => Promise<void>;
}) {
  const [jobs, setJobs] = useState<Change[]>([]),
    [findings, setFindings] = useState<Finding[]>([]),
    [accountId, setAccountId] = useState(
      initial?.account_id || accounts[0]?.id || "",
    );
  const [selection, setSelection] = useState<Record<string, Pick>>(
      initial ? { [key(initial)]: initial } : {},
    ),
    [busy, setBusy] = useState(false),
    [mode, setMode] = useState(""),
    [enabled, setEnabled] = useState(false),
    [expanded, setExpanded] = useState(""),
    [failure, setFailure] = useState(false),
    [notice, setNotice] = useState("");
  const [findingVersion, setFindingVersion] = useState(0);
  async function refresh() {
    try {
      const response = await api<{
        items: Change[];
        mode: string;
        live_enabled: boolean;
      }>("/changes");
      setJobs(response.items);
      setMode(response.mode);
      setEnabled(response.live_enabled);
    } catch (e) {
      onError((e as Error).message);
    }
  }
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (initial) {
      setAccountId(initial.account_id);
      setSelection((prev) => ({ ...prev, [key(initial)]: initial }));
    }
  }, [initial]);
  useEffect(() => {
    if (!accountId && accounts.length) setAccountId(accounts[0].id);
  }, [accounts, accountId]);
  useEffect(() => {
    if (!accountId) return;
    let active = true;
    setFindings([]);
    api<{ items: Finding[] }>(`/accounts/${accountId}/findings`)
      .then((result) => {
        if (active)
          setFindings(
            result.items.filter((f) => ["FAIL", "WARN"].includes(f.status)),
          );
      })
      .catch((e) => onError(e.message));
    return () => {
      active = false;
    };
  }, [accountId, findingVersion]);
  async function create() {
    setBusy(true);
    try {
      const response = await api<{ items: Change[] }>("/changes", "POST", {
        targets: Object.values(selection),
        simulate_failure: failure,
      });
      setExpanded(response.items[0]?.id || "");
      setSelection({});
      setNotice(
        "Plans created. Mixed environments are split so dev/staging do not wait for production approval.",
      );
      await refresh();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function action(job: Change, operation: string) {
    setBusy(true);
    try {
      await api(`/changes/${job.id}/${operation}`, "POST");
      await refresh();
      setFindingVersion((v) => v + 1);
      await onUpdated();
      setNotice(
        operation === "demo-approve"
          ? "Simulated PR approval recorded; demo execution finished."
          : "Change request submitted. Its status and account outcomes are recorded below.",
      );
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="change-policy">
        <ShieldAlert size={20} />
        <div>
          <strong>
            One central controller · Environment-based change policy
          </strong>
          <p>
            Dev and staging: dashboard action, no human approval. Production OU:
            approved GitHub PR before apply. Active exceptions are excluded.
          </p>
        </div>
        <span className="badge purple">ns-infrastructure-terraform</span>
      </div>
      {mode === "demo" ? (
        <div className="demo-banner">
          <Play size={14} />
          <span>
            Workflow demonstration · virtual accounts, simulated PR approval and
            deployment outcomes. No AWS or GitHub writes.
          </span>
        </div>
      ) : (
        !enabled && (
          <div className="error-banner">
            Live remediation is disabled. Plans can be inspected; central
            configuration must explicitly enable execution.
          </div>
        )
      )}
      {notice && (
        <div className="change-notice" role="status">
          <CheckCircle2 size={16} />
          {notice}
        </div>
      )}
      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>Plan a remediation</h2>
            <p>
              Choose findings across accounts. Terraform-owned resources use
              their mapped monorepo workspace.
            </p>
          </div>
          <button
            className="button primary"
            disabled={busy || !Object.keys(selection).length}
            onClick={() => void create()}
          >
            <Play size={14} />
            {busy
              ? "Working…"
              : `Prepare ${Object.keys(selection).length || ""} selected`}
          </button>
        </div>
        <div className="table-controls">
          <select
            aria-label="Remediation account"
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
          >
            <option value="">Choose an account</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} · {envLabel(a.environment)}
              </option>
            ))}
          </select>
          <span className="muted">
            {Object.keys(selection).length} selected across accounts · maximum
            50
          </span>
          {mode === "demo" && (
            <label className="failure-toggle">
              <input
                type="checkbox"
                checked={failure}
                onChange={(e) => setFailure(e.target.checked)}
              />
              Simulate first deployment failure
            </label>
          )}
          <button className="text-button" onClick={() => setSelection({})}>
            Clear selection
          </button>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>SELECT</th>
                <th>NON-COMPLIANT CONTROL</th>
                <th>RESOURCE</th>
                <th>STATUS</th>
              </tr>
            </thead>
            <tbody>
              {findings.map((f, i) => {
                const pick = {
                  account_id: accountId,
                  rule_id: f.rule_id,
                  resource: f.resource,
                  region: f.region,
                };
                const id = key(pick);
                return (
                  <tr key={i}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Select ${f.rule_id} ${i}`}
                        checked={!!selection[id]}
                        onChange={(e) =>
                          setSelection((previous) => {
                            const next = { ...previous };
                            if (e.target.checked) next[id] = pick;
                            else delete next[id];
                            return next;
                          })
                        }
                      />
                    </td>
                    <td>
                      <strong>{f.title}</strong>
                      <small className="table-sub">{f.rule_id}</small>
                    </td>
                    <td>
                      <code className="resource-wrap">{f.resource}</code>
                    </td>
                    <td>
                      <span className="badge red">{f.status}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!findings.length && (
            <div className="empty">
              <CheckCircle2 size={25} />
              <h3>No actionable non-compliance in this account</h3>
              <p>
                Unknown evidence and approved exceptions cannot be
                auto-remediated.
              </p>
            </div>
          )}
        </div>
      </section>
      <div className="section-title change-heading">
        <h2>Change requests & execution history</h2>
        <button className="text-button" onClick={() => void refresh()}>
          <RefreshCw size={13} />
          Refresh
        </button>
      </div>
      {jobs
        .filter((j) => environment === "all" || j.environment === environment)
        .map((job) => (
          <section key={job.id} className="panel change-card">
            <div className="panel-head">
              <button
                className="change-title"
                onClick={() => setExpanded(expanded === job.id ? "" : job.id)}
              >
                <GitPullRequest size={20} />
                <span>
                  <strong>
                    {job.kind === "exception"
                      ? "Governance exception"
                      : "Remediation"}{" "}
                    · {job.id.slice(0, 8)}
                  </strong>
                  <small>
                    {envLabel(job.environment)} · {job.targets.length} target(s)
                    · {dateLabel(job.created_at)}
                  </small>
                </span>
              </button>
              <span
                className={`badge ${job.status === "SUCCEEDED" ? "green" : job.status.includes("FAIL") || job.status === "STALE_PLAN" ? "red" : job.status.includes("AWAITING") ? "amber" : "blue"}`}
              >
                {job.status.replaceAll("_", " ").toLowerCase()}
              </span>
            </div>
            <div className="change-actions">
              <span className="muted">
                {job.requires_approval
                  ? "Production PR approval required"
                  : "No human approval required"}{" "}
                ·{" "}
                {mode === "demo"
                  ? "simulated workflow"
                  : job.driver === "terraform"
                    ? "Terraform monorepo workflow"
                    : job.driver === "policy"
                      ? "Governance policy workflow"
                      : "Allowlisted AWS runbook"}
              </span>
              {job.pull_request && (
                <a
                  className="button small"
                  href={job.pull_request.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  Review PR <ExternalLink size={12} />
                </a>
              )}
              {job.status === "PLANNED" && (
                <button
                  className="button primary small"
                  disabled={busy || !enabled}
                  onClick={() => void action(job, "execute")}
                >
                  {job.requires_approval
                    ? "Request production PR"
                    : "Execute change"}
                  <ArrowRight size={12} />
                </button>
              )}
              {job.status === "AWAITING_APPROVAL" && mode === "demo" && (
                <button
                  className="button primary small"
                  disabled={busy}
                  onClick={() => void action(job, "demo-approve")}
                >
                  Simulate approved PR
                </button>
              )}
              {["FAILED", "PARTIAL_FAILURE"].includes(job.status) &&
                job.driver !== "terraform" && (
                  <button
                    className="button small"
                    disabled={busy}
                    onClick={() => void action(job, "retry")}
                  >
                    Retry failed targets
                  </button>
                )}
              {!terminal.includes(job.status) &&
                !["PLANNED", "AWAITING_APPROVAL", "AWAITING_PLAN"].includes(
                  job.status,
                ) && <Loader2 size={15} className="spin" />}
              <button
                className="text-button"
                onClick={() => setExpanded(expanded === job.id ? "" : job.id)}
              >
                {expanded === job.id
                  ? "Hide plan & evidence"
                  : "Inspect plan & evidence"}
              </button>
            </div>
            {expanded === job.id && (
              <div className="change-details">
                {job.error && <div className="error-banner">{job.error}</div>}
                <p className="form-help">
                  Plan SHA-256: <code>{job.plan_hash || "Preparing…"}</code>.
                  Production approval applies only to this immutable plan;
                  changed scope or drift requires a new plan.
                </p>
                {job.targets.map((t) => (
                  <div className="target-plan" key={t.id}>
                    <div>
                      <strong>{t.account_name}</strong>
                      <span className="badge">{t.rule_id}</span>
                      <code className="resource-wrap">{t.resource}</code>
                    </div>
                    <p>{t.impact}</p>
                    {t.workspace && (
                      <p>
                        Workspace: <code>{t.workspace.directory}</code> ·{" "}
                        <code>{t.workspace.resource_address}</code>
                      </p>
                    )}
                    <div className="plan-diff">
                      <div>
                        <small>BEFORE</small>
                        <pre>{JSON.stringify(t.before, null, 2)}</pre>
                      </div>
                      <div>
                        <small>DESIRED</small>
                        <pre>{JSON.stringify(t.desired, null, 2)}</pre>
                      </div>
                    </div>
                    {job.outcomes[t.id] && (
                      <div className="target-result">
                        <span
                          className={`badge ${job.outcomes[t.id].status === "SUCCEEDED" ? "green" : "red"}`}
                        >
                          {job.outcomes[t.id].status}
                        </span>
                        {job.outcomes[t.id].message}
                      </div>
                    )}
                  </div>
                ))}
                <div className="event-timeline">
                  {job.events.map((e, i) => (
                    <div key={i}>
                      <time>{dateLabel(e.at)}</time>
                      <span>{e.message}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>
        ))}
      {!jobs.length && (
        <div className="empty">
          <GitPullRequest size={28} />
          <h3>No change requests yet</h3>
          <p>
            Prepare a finding remediation to demonstrate planning, approval,
            execution and verification.
          </p>
        </div>
      )}
    </>
  );
}
