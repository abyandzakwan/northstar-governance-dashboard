import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  Bell,
  Blocks,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  Cloud,
  Code2,
  Download,
  ExternalLink,
  FileBarChart2,
  GitBranch,
  LayoutDashboard,
  ListFilter,
  Loader2,
  LockKeyhole,
  LogOut,
  Menu,
  MoreHorizontal,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  ShieldAlert,
  SlidersHorizontal,
  Users,
  X,
} from "lucide-react";
import {
  ApiError,
  api,
  loadConfig,
  finishLogin,
  login,
  logout,
  envLabel,
  dateLabel,
  type Account,
  type Control,
  type Deployment,
  type Environment,
  type Finding,
  type Framework,
  type Report,
  type Rule,
  type Summary,
  type Waiver,
} from "./api";
import "./style.css";
import { Changes } from "./Changes";

type Page =
  | "overview"
  | "accounts"
  | "rules"
  | "frameworks"
  | "deployments"
  | "waivers"
  | "reports"
  | "changes";
type Session = {
  subject: string;
  admin: boolean;
  teams: string[];
  mode: string;
  deployment_mode?: string;
  sample_securityhub?: boolean;
};
type DeploymentPage = {
  items: Deployment[];
  next_cursor: string | null;
  total: number;
  drifted: number;
};
const pageNames: Record<Page, string> = {
  changes: "Remediation & changes",
  overview: "Organization overview",
  accounts: "AWS accounts",
  rules: "Governance rules",
  frameworks: "Compliance frameworks",
  deployments: "Deployments",
  waivers: "Risk exceptions",
  reports: "Reports & exports",
};
const navigation = [
  { id: "changes", label: "Remediation & changes", icon: Play },
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "accounts", label: "AWS accounts", icon: Cloud },
  { id: "rules", label: "Governance rules", icon: ShieldCheck },
  { id: "frameworks", label: "Frameworks", icon: Blocks },
  { id: "deployments", label: "Deployments", icon: GitBranch },
  { id: "waivers", label: "Risk exceptions", icon: ShieldAlert },
  { id: "reports", label: "Reports & exports", icon: FileBarChart2 },
] as const;

function Badge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: string;
}) {
  return (
    <span className={`badge ${tone}`}>
      <i />
      {children}
    </span>
  );
}
function Status({ status }: { status: string }) {
  const tone = ["PASS", "CURRENT", "IN_SYNC", "healthy", "ready"].includes(
    status,
  )
    ? "green"
    : ["FAIL", "critical", "DRIFTED", "failed"].includes(status)
      ? "red"
      : ["WARN", "OUTDATED", "ACCEPTED_RISK"].includes(status)
        ? "amber"
        : "neutral";
  return <Badge tone={tone}>{status.toLowerCase().replaceAll("_", " ")}</Badge>;
}
function Spark({
  values,
  color = "#20a88b",
  large = false,
}: {
  values: number[];
  color?: string;
  large?: boolean;
}) {
  const min = Math.min(...values) - 3,
    max = Math.max(...values) + 3;
  const points = values
    .map(
      (v, i) =>
        `${(i * 600) / Math.max(values.length - 1, 1)},${110 - ((v - min) / (max - min)) * 90}`,
    )
    .join(" ");
  return (
    <svg
      className={large ? "spark large" : "spark"}
      viewBox="0 0 600 125"
      preserveAspectRatio="none"
      role="img"
      aria-label="Compliance score trend"
    >
      <defs>
        <linearGradient
          id={large ? "fill-large" : "fill-small"}
          x1="0"
          y1="0"
          x2="0"
          y2="1"
        >
          <stop offset="0" stopColor={color} stopOpacity=".16" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {large &&
        [20, 55, 90].map((y) => (
          <line
            key={y}
            x1="0"
            x2="600"
            y1={y}
            y2={y}
            stroke="#edf0f3"
            strokeDasharray="4 5"
          />
        ))}
      <polygon
        points={`0,125 ${points} 600,125`}
        fill={`url(#${large ? "fill-large" : "fill-small"})`}
      />
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth={large ? 2.5 : 3}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
function Empty({ text }: { text: string }) {
  return (
    <div className="empty">
      <CheckCircle2 size={30} />
      <h3>{text}</h3>
      <p>Try another environment or adjust your filters.</p>
    </div>
  );
}

function App() {
  const [session, setSession] = useState<Session | null>(null),
    [bootError, setBootError] = useState("");
  const [page, setPage] = useState<Page>("overview"),
    [env, setEnv] = useState<Environment>("all"),
    [search, setSearch] = useState("");
  const [summaries, setSummaries] = useState<Summary[]>([]),
    [accounts, setAccounts] = useState<Account[]>([]),
    [trend, setTrend] = useState<{ date: string; score_pct: number }[]>([]),
    [updated, setUpdated] = useState("");
  const [envTrends, setEnvTrends] = useState<
    Record<string, { date: string; score_pct: number }[]>
  >({});
  const [rules, setRules] = useState<Rule[]>([]),
    [deployments, setDeployments] = useState<Deployment[]>([]),
    [waivers, setWaivers] = useState<Waiver[]>([]),
    [framework, setFramework] = useState<Framework | null>(null),
    [frameworkId, setFrameworkId] = useState("cis-aws");
  const [selected, setSelected] = useState<Account | null>(null),
    [findings, setFindings] = useState<Finding[]>([]),
    [accountDeploys, setAccountDeploys] = useState<Deployment[]>([]),
    [detailLoading, setDetailLoading] = useState(false);
  const [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [toast, setToast] = useState(""),
    [busy, setBusy] = useState(false),
    [mobile, setMobile] = useState(false);
  const [statusFilter, setStatusFilter] = useState(""),
    [team, setTeam] = useState(""),
    [ou, setOu] = useState(""),
    [ruleFilter, setRuleFilter] = useState(""),
    [frameworkAccounts, setFrameworkAccounts] = useState<string[] | null>(null);
  const [waiverForm, setWaiverForm] = useState<{
      account_id: string;
      rule_id: string;
      resource: string;
    } | null>(null),
    [weekly, setWeekly] = useState({ enabled: false, channel: "email" });
  const [detailStatus, setDetailStatus] = useState("issues"),
    [detailSeverity, setDetailSeverity] = useState("");
  const [changePick, setChangePick] = useState<{
    account_id: string;
    rule_id: string;
    resource: string;
    region?: string;
  } | null>(null);
  const [frameworks, setFrameworks] = useState<
    { id: string; name: string; source?: string }[]
  >([]);
  const [reportFormat, setReportFormat] = useState("csv");
  const [waiverError, setWaiverError] = useState("");
  const [policyVersion, setPolicyVersion] = useState("");
  const [deploymentInfo, setDeploymentInfo] = useState({
    next_cursor: null as string | null,
    total: 0,
    drifted: 0,
  });
  const requestVersion = useRef(0);
  const notify = (message: string) => {
    setToast(message);
  };
  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(""), 7000);
      return () => clearTimeout(t);
    }
  }, [toast]);
  useEffect(() => {
    setWaiverError("");
  }, [waiverForm]);
  useEffect(() => {
    if (!selected && !waiverForm) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialogs = document.querySelectorAll<HTMLElement>('[role="dialog"]');
    const dialog = dialogs[dialogs.length - 1];
    if (!dialog) return;
    const focusable = () =>
      Array.from(
        dialog.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea, a[href]",
        ),
      );
    focusable()[0]?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const nodes = focusable();
      const first = nodes[0],
        last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", trap);
    return () => {
      document.removeEventListener("keydown", trap);
      previous?.focus();
    };
  }, [Boolean(selected), Boolean(waiverForm)]);
  useEffect(() => {
    loadConfig()
      .then(finishLogin)
      .then(() => api<Session>("/session"))
      .then(setSession)
      .catch((e) => {
        setBootError(
          e instanceof ApiError && e.status === 401 ? "" : e.message,
        );
        setLoading(false);
      });
  }, []);
  const refresh = useCallback(async () => {
    const version = ++requestVersion.current;
    setLoading(true);
    setError("");
    try {
      const [s, a, r, d, w] = await Promise.all([
        api<{ items: Summary[]; updated_at: string }>("/environments"),
        api<{ items: Account[] }>(`/accounts?env=${env}`),
        api<{ items: Rule[]; version: string }>(`/rules?env=${env}`),
        api<DeploymentPage>(`/deployments?env=${env}`),
        api<{ items: Waiver[] }>("/waivers"),
      ]);
      if (version !== requestVersion.current) return;
      setSummaries(s.items);
      setUpdated(s.updated_at);
      setAccounts(a.items);
      setSelected((current) =>
        current
          ? a.items.find((item) => item.id === current.id) || current
          : null,
      );
      setRules(r.items);
      setPolicyVersion(r.version);
      setDeployments(d.items);
      setDeploymentInfo(d);
      setWaivers(w.items);
    } catch (e) {
      if (version === requestVersion.current) setError((e as Error).message);
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  }, [env]);
  useEffect(() => {
    if (session) void refresh();
  }, [session, refresh]);
  useEffect(() => {
    if (!session) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const run = async () => {
      try {
        const response = await api<{ items: typeof trend; status?: string }>(
          `/trends?env=${env}`,
        );
        if (active) {
          setTrend(response.items);
          if (response.status === "pending") timer = setTimeout(run, 2000);
        }
      } catch (e) {
        if (active) setError((e as Error).message);
      }
    };
    void run();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [env, session]);
  useEffect(() => {
    if (!session) return;
    let active = true;
    const timers: ReturnType<typeof setTimeout>[] = [];
    for (const environment of ["dev", "testing", "production"]) {
      const run = async () => {
        try {
          const response = await api<{
            items: { date: string; score_pct: number }[];
            status?: string;
          }>(`/trends?env=${environment}`);
          if (active) {
            setEnvTrends((previous) => ({
              ...previous,
              [environment]: response.items,
            }));
            if (response.status === "pending")
              timers.push(setTimeout(run, 2000));
          }
        } catch {
          /* The main trend request reports errors; cards leave missing history empty. */
        }
      };
      void run();
    }
    return () => {
      active = false;
      timers.forEach(clearTimeout);
    };
  }, [session]);
  useEffect(() => {
    if (!session) return;
    api<{ items: { id: string; name: string; source?: string }[] }>(
      "/frameworks",
    )
      .then((r) => setFrameworks(r.items))
      .catch((e) => setError(e.message));
  }, [session]);
  useEffect(() => {
    if (page !== "frameworks" || !session) return;
    setFramework(null);
    let active = true;
    api<Framework>(`/frameworks/${frameworkId}?env=${env}`)
      .then((r) => {
        if (active) setFramework(r);
      })
      .catch((e) => setError(e.message));
    return () => {
      active = false;
    };
  }, [page, env, frameworkId, session]);
  useEffect(() => {
    if (page === "reports" && session?.admin)
      api<typeof weekly>("/settings/weekly")
        .then(setWeekly)
        .catch((e) => setError(e.message));
  }, [page, session]);
  useEffect(() => {
    if (!selected) return;
    setDetailLoading(true);
    setFindings([]);
    setAccountDeploys([]);
    let active = true;
    Promise.all([
      api<{ items: Finding[] }>(`/accounts/${selected.id}/findings`),
      api<{ items: Deployment[] }>(`/accounts/${selected.id}/deployments`),
    ])
      .then(([f, d]) => {
        if (active) {
          setFindings(f.items);
          setAccountDeploys(d.items);
        }
      })
      .catch((e) => setError(e.message))
      .finally(() => {
        if (active) setDetailLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selected]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSelected(null);
        setWaiverForm(null);
        setMobile(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const navigate = (next: Page) => {
    setPage(next);
    setSearch("");
    setRuleFilter("");
    setFrameworkAccounts(null);
    setMobile(false);
  };
  async function exportReport() {
    setBusy(true);
    try {
      let report = await api<Report>("/reports", "POST", {
        format: reportFormat,
        environment: env,
      });
      for (let i = 0; i < 60; i++) {
        report = await api<Report>(`/reports/${report.id}`);
        if (report.status === "failed")
          throw new Error("Export failed. Contact the platform team.");
        if (report.status === "ready" && report.download_url) {
          const a = document.createElement("a");
          a.href = report.download_url;
          a.download = `governance.${reportFormat}`;
          a.rel = "noopener";
          document.body.append(a);
          a.click();
          a.remove();
          notify(
            "Your confidential export is ready. Download links expire after 15 minutes.",
          );
          return;
        }
        await new Promise((r) => setTimeout(r, 2000));
      }
      throw new Error("Export is still processing. Refresh Reports to retry.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function scan(accountId?: string) {
    setBusy(true);
    try {
      const result = await api<{ status: string }>(
        "/scans",
        "POST",
        accountId
          ? { account_id: accountId }
          : { environment: env === "all" ? "production" : env },
      );
      notify(
        result.status === "complete"
          ? "Demo scan complete. Evidence and scores refreshed."
          : "Scan queued. Results will update after collection completes.",
      );
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const visible = accounts.filter(
    (a) =>
      (!search ||
        (a.name + a.id + a.owner_team)
          .toLowerCase()
          .includes(search.toLowerCase())) &&
      (!team || a.owner_team === team) &&
      (!ou || a.ou_path === ou) &&
      (!statusFilter ||
        (statusFilter === "healthy" ? a.healthy : !a.healthy)) &&
      (!frameworkAccounts || frameworkAccounts.includes(a.id)),
  );
  const total = summaries.reduce((n, s) => n + s.accounts_total, 0),
    healthy = summaries.reduce((n, s) => n + s.healthy, 0),
    critical = summaries.reduce((n, s) => n + s.critical_count, 0),
    stale = summaries.reduce((n, s) => n + s.stale_count, 0);
  const avg = total
    ? summaries.reduce((n, s) => n + (s.avg_score || 0) * s.accounts_total, 0) /
      total
    : 0;
  const scoped =
    env === "all" ? summaries : summaries.filter((s) => s.environment === env);
  function AccountTable({
    limit,
    review = false,
  }: {
    limit?: number;
    review?: boolean;
  }) {
    const rows = review
      ? visible
          .filter((a) => !a.healthy || a.stale)
          .sort(
            (a, b) =>
              b.critical_count - a.critical_count || a.score_pct - b.score_pct,
          )
      : visible;
    return (
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>AWS ACCOUNT</th>
              <th>ENVIRONMENT</th>
              <th>TEAM</th>
              <th>
                COMPLIANCE <ChevronDown size={12} />
              </th>
              <th>CRITICAL</th>
              <th>STATUS</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, limit).map((a) => (
              <tr key={a.id}>
                <td>
                  <button
                    className="account-name"
                    onClick={() => {
                      setSelected(a);
                      setDetailStatus("issues");
                    }}
                  >
                    <span className="cloud-box">
                      <Cloud size={17} />
                    </span>
                    <span>
                      <strong>{a.name}</strong>
                      <small>{a.id}</small>
                    </span>
                  </button>
                </td>
                <td>
                  <Badge
                    tone={
                      a.environment === "production"
                        ? "purple"
                        : a.environment === "testing"
                          ? "blue"
                          : "neutral"
                    }
                  >
                    {envLabel(a.environment)}
                  </Badge>
                </td>
                <td>
                  <span className="team-name">{a.owner_team}</span>
                </td>
                <td>
                  <div className="score-cell">
                    <strong>{a.score_pct?.toFixed(1) ?? "—"}%</strong>
                    <div className="progress">
                      <i
                        style={{
                          width: `${a.score_pct || 0}%`,
                          background: a.healthy ? "#20a88b" : "#e4a14b",
                        }}
                      />
                    </div>
                  </div>
                </td>
                <td>
                  {a.critical_count ? (
                    <span className="critical-number">{a.critical_count}</span>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td>
                  <Badge tone={a.stale ? "amber" : a.healthy ? "green" : "red"}>
                    {a.stale
                      ? "Stale data"
                      : a.healthy
                        ? "Healthy"
                        : "Needs attention"}
                  </Badge>
                </td>
                <td>
                  <button
                    className="icon-button"
                    aria-label={`Open ${a.name}`}
                    onClick={() => setSelected(a)}
                  >
                    <ChevronRight size={17} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && (
          <Empty
            text={
              review
                ? "No accounts need attention"
                : "No accounts match your filters"
            }
          />
        )}
      </div>
    );
  }

  if (!session)
    return (
      <div className="signin">
        <div className="brand-icon">
          <ShieldCheck size={30} />
        </div>
        <h1>AWS Governance</h1>
        <p>One view of your organization's security posture.</p>
        {bootError && <p className="error-text">{bootError}</p>}
        <button
          className="button primary"
          onClick={() => login().catch((e) => setBootError(e.message))}
        >
          <LockKeyhole size={16} />
          Sign in to portal
        </button>
        <small>Confidential · Authorized employees only</small>
      </div>
    );
  return (
    <div className="shell">
      <aside className={mobile ? "sidebar open" : "sidebar"}>
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            navigate("overview");
          }}
        >
          <span className="brand-icon">
            <ShieldCheck size={24} />
          </span>
          <span>
            governance<span className="brand-sub">AWS CONTROL CENTER</span>
          </span>
        </a>
        <div className="workspace">
          <span className="workspace-icon">
            <Cloud size={17} />
          </span>
          <div>
            {session.deployment_mode === "standalone"
              ? "Account portal"
              : "Organization portal"}
            <small>
              {session.deployment_mode === "standalone"
                ? "Standalone test account"
                : "Central Security account"}
            </small>
          </div>
          <ChevronDown size={13} />
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav>
          {navigation.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => navigate(id)}
              className={page === id ? "nav-item active" : "nav-item"}
            >
              <Icon size={18} />
              <span>{label}</span>
              {id === "waivers" &&
                waivers.filter(
                  (w) => !w.revoked_at && new Date(w.expires_at) > new Date(),
                ).length > 0 && (
                  <b>{waivers.filter((w) => !w.revoked_at).length}</b>
                )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="security-note">
            <span className="live-dot" />
            <span>
              Governed remediation<small>Changes are scoped and audited</small>
            </span>
            <LockKeyhole size={13} />
          </div>
          <button className="profile" onClick={logout} title="Sign out">
            <span className="avatar">PA</span>
            <span>
              {session.admin ? "Platform admin" : "Portal user"}
              <small>
                {session.mode === "demo"
                  ? "Local demo workspace"
                  : "Authenticated session"}
              </small>
            </span>
            <LogOut size={15} />
          </button>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            <button
              className="icon-button mobile-toggle"
              onClick={() => setMobile(!mobile)}
              aria-label="Toggle navigation"
            >
              <Menu size={20} />
            </button>
            <span>Workspace</span>
            <ChevronRight size={13} />
            <strong>{pageNames[page]}</strong>
          </div>
          <div className="topbar-right">
            <span className="classification">
              <LockKeyhole size={11} />
              Confidential
            </span>
            <span className="top-divider" />
            <span className="connection">
              <span className="live-dot" />
              {session.mode === "demo"
                ? "Demo data"
                : session.sample_securityhub
                  ? "AWS + sample findings"
                  : "AWS connected"}
            </span>
          </div>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <div className="eyebrow">AWS GOVERNANCE & COMPLIANCE</div>
              <h1>{pageNames[page]}</h1>
              <p>
                {page === "changes"
                  ? "Plan, approve and verify changes across your AWS accounts."
                  : page === "overview"
                    ? "A clear view of your organization’s security posture. Every account, one place."
                    : page === "accounts"
                      ? "Explore account health, ownership and resource-level evidence."
                      : page === "rules"
                        ? "One shared baseline. The right level of governance for every environment."
                        : page === "frameworks"
                          ? "Turn configuration evidence into a clear view of technical controls."
                          : page === "deployments"
                            ? "Track your baseline rollouts, stack updates and configuration drift."
                            : page === "waivers"
                              ? "Document accepted risk with clear ownership and an expiry date."
                              : "Audit-ready evidence and scheduled governance summaries."}
              </p>
            </div>
            <div className="heading-actions">
              <button
                className="button"
                disabled={loading}
                onClick={() => void refresh()}
              >
                <RefreshCw size={15} className={loading ? "spin" : ""} />
                Refresh
              </button>
              <button
                className="button primary"
                onClick={() =>
                  page === "reports" ? void exportReport() : navigate("reports")
                }
                disabled={busy}
              >
                <Download size={15} />
                {busy ? "Preparing…" : "Export report"}
              </button>
            </div>
          </div>
          {session.mode === "demo" && (
            <div className="demo-banner">
              <Activity size={14} />
              <span>
                <strong>Interactive demo</strong> · Synthetic accounts and
                evidence. Changes are saved locally; no AWS resources are
                modified.
              </span>
              <Badge tone="blue">Preview</Badge>
            </div>
          )}
          {session.sample_securityhub && (
            <div className="demo-banner" role="status">
              <ShieldAlert size={14} />
              <span>
                <strong>Sample Security Hub data</strong> · Labelled sample
                findings appear when live evidence is unavailable. Scores may
                include sample data. Live remediation is disabled.
              </span>
              <Badge tone="amber">Test deployment</Badge>
            </div>
          )}
          {error && (
            <div className="error-banner" role="alert">
              <ShieldAlert size={17} />
              {error}
              <button
                className="icon-button"
                onClick={() => setError("")}
                aria-label="Dismiss error"
              >
                <X size={16} />
              </button>
            </div>
          )}
          <div className="filterbar">
            <div className="environment-select">
              <Cloud size={15} />
              <select
                aria-label="Environment"
                value={env}
                onChange={(e) => setEnv(e.target.value as Environment)}
              >
                {(
                  [
                    "all",
                    "production",
                    "testing",
                    "dev",
                    "unclassified",
                  ] as const
                ).map((v) => (
                  <option key={v} value={v}>
                    {envLabel(v)}
                  </option>
                ))}
              </select>
            </div>
            <span className="filter-separator" />
            <span className="filter-info">
              <span className="live-dot" />
              Last updated {dateLabel(updated)}
            </span>
            <span className="filter-end">
              <Clock3 size={13} />
              15-minute freshness target
            </span>
          </div>

          {page === "changes" && (
            <Changes
              accounts={accounts}
              environment={env}
              initial={changePick}
              onError={setError}
              onUpdated={refresh}
            />
          )}
          {page === "overview" && (
            <>
              <div className="metrics">
                <div className="metric">
                  <span>
                    Organization compliance <ShieldCheck size={16} />
                  </span>
                  <div>
                    <strong>
                      {avg.toFixed(1)}
                      <em>%</em>
                    </strong>
                    <Badge
                      tone={healthy === total && total > 0 ? "green" : "amber"}
                    >
                      {healthy === total && total > 0
                        ? "On track"
                        : "Needs attention"}
                    </Badge>
                  </div>
                  <small>Across {total} governed accounts</small>
                  <div className="metric-line">
                    <i style={{ width: `${avg}%` }} />
                  </div>
                </div>
                <div className="metric">
                  <span>
                    AWS accounts <Cloud size={16} />
                  </span>
                  <div>
                    <strong>{total}</strong>
                    <span className="metric-unit">accounts</span>
                  </div>
                  <small>
                    <span className="green-text">{healthy} healthy</span>
                    <span className="dot-separator">·</span>
                    {total - healthy} need attention
                  </small>
                  <div className="mini-segments">
                    {summaries
                      .filter((s) => s.environment !== "unclassified")
                      .map((s) => (
                        <i
                          key={s.environment}
                          className={s.environment}
                          style={{ flex: s.accounts_total }}
                        />
                      ))}
                  </div>
                </div>
                <div className="metric">
                  <span>
                    Critical findings <ShieldAlert size={16} />
                  </span>
                  <div>
                    <strong className="red-text">{critical}</strong>
                    <Badge tone="red">Action required</Badge>
                  </div>
                  <small>Prioritize risks in Production</small>
                  <button
                    className="text-button"
                    onClick={() => {
                      setEnv("production");
                      navigate("accounts");
                    }}
                  >
                    Review accounts <ArrowRight size={13} />
                  </button>
                </div>
                <div className="metric">
                  <span>
                    Data freshness <Activity size={16} />
                  </span>
                  <div>
                    <strong>
                      {total ? Math.round(((total - stale) / total) * 100) : 0}
                      <em>%</em>
                    </strong>
                    <span className="status-icon">
                      <Check size={16} />
                    </span>
                  </div>
                  <small>
                    {stale
                      ? `${stale} accounts have stale evidence`
                      : "All accounts scanned within 26 hours"}
                  </small>
                  <span className="subtle-label">
                    <span className="live-dot" />
                    Event-driven collection
                  </span>
                </div>
              </div>
              <div className="section-title">
                <h2>
                  Environments <span>03</span>
                </h2>
                <button
                  className="text-button"
                  onClick={() => navigate("accounts")}
                >
                  View all accounts <ArrowRight size={14} />
                </button>
              </div>
              <div className="environment-cards">
                {scoped
                  .filter((s) => s.environment !== "unclassified")
                  .map((s) => (
                    <button
                      key={s.environment}
                      className={`environment-card ${s.environment}`}
                      onClick={() => {
                        setEnv(s.environment);
                        navigate("accounts");
                      }}
                    >
                      <div className="env-top">
                        <span className={`env-symbol ${s.environment}`}>
                          <LayersIcon env={s.environment} />
                        </span>
                        <div>
                          <h3>{envLabel(s.environment)}</h3>
                          <small>{s.accounts_total} AWS accounts</small>
                        </div>
                        <ChevronRight size={16} />
                      </div>
                      <div className="env-score">
                        <strong>
                          {s.avg_score?.toFixed(1) ?? "—"}
                          <em>%</em>
                        </strong>
                        <span>Compliance score</span>
                      </div>
                      {envTrends[s.environment]?.length ? (
                        <Spark
                          values={envTrends[s.environment].map(
                            (t) => t.score_pct,
                          )}
                          color={
                            s.environment === "production"
                              ? "#8870cb"
                              : s.environment === "testing"
                                ? "#588bcd"
                                : "#20a88b"
                          }
                        />
                      ) : (
                        <div className="missing-trend">
                          Daily history is building
                        </div>
                      )}
                      <div className="env-footer">
                        <span>
                          <i className="green-dot" />
                          {s.healthy} healthy
                        </span>
                        <span>
                          <i className="amber-dot" />
                          {s.accounts_total - s.healthy} need attention
                        </span>
                        <span
                          className={s.critical_count ? "red-text" : "muted"}
                        >
                          {s.critical_count} critical
                        </span>
                      </div>
                    </button>
                  ))}
              </div>
              {summaries.some(
                (s) => s.environment === "unclassified" && s.accounts_total > 0,
              ) && (
                <button
                  className="unclassified-notice"
                  onClick={() => {
                    setEnv("unclassified");
                    navigate("accounts");
                  }}
                >
                  <ShieldAlert size={16} />
                  <strong>
                    {
                      summaries.find((s) => s.environment === "unclassified")
                        ?.accounts_total
                    }{" "}
                    unclassified account
                  </strong>
                  <span>
                    Assign an Environment tag or move it to a governed OU.
                  </span>
                  <ArrowRight size={15} />
                </button>
              )}
              <div className="overview-lower">
                <section className="panel trend-panel">
                  <div className="panel-head">
                    <div>
                      <h2>Compliance trend</h2>
                      <p>Daily compliance score · {envLabel(env)}</p>
                    </div>
                    <Badge>Last 30 days</Badge>
                  </div>
                  <div className="trend-score">
                    {trend.at(-1)?.score_pct.toFixed(1) ?? "—"}
                    <span>%</span>
                    <small>latest daily snapshot</small>
                  </div>
                  {trend.length ? (
                    <>
                      <Spark large values={trend.map((t) => t.score_pct)} />
                      <div className="chart-axis">
                        {[
                          trend[0],
                          trend[Math.floor(trend.length / 2)],
                          trend.at(-1)!,
                        ].map((t) => (
                          <span key={t.date}>
                            {new Date(t.date + "T12:00:00").toLocaleDateString(
                              undefined,
                              { month: "short", day: "numeric" },
                            )}
                          </span>
                        ))}
                      </div>
                    </>
                  ) : (
                    <Empty text="Historical snapshots are not available yet" />
                  )}
                </section>
                <section className="panel priorities">
                  <div className="panel-head">
                    <div>
                      <h2>Needs your attention</h2>
                      <p>Start with the highest-impact controls</p>
                    </div>
                    <ShieldAlert size={18} />
                  </div>
                  {[...rules]
                    .sort((a, b) => b.failing_accounts - a.failing_accounts)
                    .filter((r) => r.failing_accounts > 0)
                    .slice(0, 3)
                    .map((r, i) => (
                      <button
                        className="priority-item"
                        key={r.id}
                        onClick={async () => {
                          navigate("rules");
                          setRuleFilter(r.id);
                        }}
                      >
                        <span className={`priority-number p${i}`}>{i + 1}</span>
                        <span>
                          <strong>{r.title}</strong>
                          <small>
                            {r.failing_accounts} accounts affected · {r.area}
                          </small>
                        </span>
                        <ChevronRight size={16} />
                      </button>
                    ))}
                  <div className="priorities-footer">
                    <span className="live-dot" />
                    Policy version {policyVersion || "—"}
                  </div>
                </section>
              </div>
              <section className="panel account-panel">
                <div className="panel-head">
                  <div>
                    <h2>Accounts to review</h2>
                    <p>Critical findings first, then lowest compliance score</p>
                  </div>
                  <button
                    className="button small"
                    onClick={() => navigate("accounts")}
                  >
                    View all accounts <ArrowRight size={13} />
                  </button>
                </div>
                <AccountTable limit={5} review />
              </section>
            </>
          )}

          {page === "accounts" && (
            <section className="panel">
              <div className="panel-head">
                <div>
                  <h2>
                    Account inventory{" "}
                    <span className="count">{visible.length}</span>
                  </h2>
                  <p>
                    Every discovered account, automatically classified and
                    evaluated.
                  </p>
                </div>
                {session.admin && (
                  <button
                    className="button small"
                    disabled={busy}
                    onClick={() => void scan()}
                  >
                    <Play size={13} />
                    Scan {env === "all" ? "Production" : envLabel(env)}
                  </button>
                )}
              </div>
              <div className="table-controls">
                <label className="search">
                  <Search size={15} />
                  <input
                    aria-label="Search accounts"
                    placeholder="Search name, account ID or team…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </label>
                <select
                  aria-label="Account status"
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                >
                  <option value="">All statuses</option>
                  <option value="healthy">Healthy</option>
                  <option value="attention">Needs attention</option>
                </select>
                <select
                  aria-label="Team"
                  value={team}
                  onChange={(e) => setTeam(e.target.value)}
                >
                  <option value="">All teams</option>
                  {[...new Set(accounts.map((a) => a.owner_team))].map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
                <select
                  aria-label="Organizational unit"
                  value={ou}
                  onChange={(e) => setOu(e.target.value)}
                >
                  <option value="">All OUs</option>
                  {[...new Set(accounts.map((a) => a.ou_path))].map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
                {(team || ou || statusFilter || frameworkAccounts) && (
                  <button
                    className="text-button"
                    onClick={() => {
                      setTeam("");
                      setOu("");
                      setStatusFilter("");
                      setFrameworkAccounts(null);
                    }}
                  >
                    Clear filters
                  </button>
                )}
              </div>
              <AccountTable />
              <div className="table-footer">
                Showing {visible.length} accounts · Scores use each
                environment's governance profile
              </div>
            </section>
          )}

          {page === "rules" && (
            <>
              <div className="two-columns">
                <section className="panel">
                  <div className="panel-head">
                    <div>
                      <h2>Versioned rule catalog</h2>
                      <p>
                        Policy changes are reviewed through GitHub pull
                        requests.
                      </p>
                    </div>
                    <Badge tone="blue">{rules.length} controls</Badge>
                  </div>
                  <div className="table-controls">
                    <label className="search">
                      <Search size={15} />
                      <input
                        aria-label="Search rules"
                        placeholder="Find a rule…"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                      />
                    </label>
                  </div>
                  <div className="rule-list">
                    {rules
                      .filter(
                        (r) =>
                          (!ruleFilter || r.id === ruleFilter) &&
                          `${r.title} ${r.id}`
                            .toLowerCase()
                            .includes(search.toLowerCase()),
                      )
                      .map((r) => (
                        <button
                          className="rule-item"
                          key={r.id}
                          onClick={async () => {
                            try {
                              const response = await api<{ items: Account[] }>(
                                `/rules/${r.id}/accounts?env=${env}`,
                              );
                              navigate("accounts");
                              setFrameworkAccounts(
                                response.items.map((a) => a.id),
                              );
                            } catch (e) {
                              setError((e as Error).message);
                            }
                          }}
                        >
                          <span className="rule-icon">
                            <ShieldCheck size={19} />
                          </span>
                          <span>
                            <strong>{r.title}</strong>
                            <small>
                              {r.id} · {r.source}
                            </small>
                            <span className="severity-row">
                              {(["dev", "testing", "production"] as const).map(
                                (e) => (
                                  <span key={e}>
                                    {envLabel(e)}{" "}
                                    <b
                                      className={
                                        r.severity[e] === "critical"
                                          ? "red-text"
                                          : ""
                                      }
                                    >
                                      {r.severity[e]}
                                    </b>
                                  </span>
                                ),
                              )}
                            </span>
                          </span>
                          <span className="rule-affected">
                            {r.failing_accounts}
                            <small>affected accounts</small>
                          </span>
                          <ChevronRight size={16} />
                        </button>
                      ))}
                  </div>
                  {ruleFilter && (
                    <button
                      className="button catalog-reset"
                      onClick={() => setRuleFilter("")}
                    >
                      Show all controls
                    </button>
                  )}
                </section>
                <section className="panel heatmap-panel">
                  <div className="panel-head">
                    <div>
                      <h2>Rule × organizational unit</h2>
                      <p>Affected accounts per control and OU</p>
                    </div>
                  </div>
                  <Heatmap accounts={accounts} rules={rules} />
                </section>
              </div>
            </>
          )}

          {page === "frameworks" && (
            <>
              <div className="tabs">
                {frameworks.map((f) => (
                  <button
                    key={f.id}
                    className={frameworkId === f.id ? "active" : ""}
                    onClick={() => setFrameworkId(f.id)}
                  >
                    {f.name}
                    {f.source === "securityhub" ? " · AWS" : ""}
                  </button>
                ))}
              </div>
              {framework ? (
                <section className="panel">
                  <div className="panel-head">
                    <div>
                      <h2>
                        {framework.name}{" "}
                        <Badge>Edition {framework.edition}</Badge>
                      </h2>
                      <p>
                        {framework.accounts_in_scope} accounts in scope ·{" "}
                        {envLabel(env)}
                      </p>
                    </div>
                    <Blocks size={24} />
                  </div>
                  <div className="framework-note">
                    <CircleHelp size={17} />
                    {framework.scope}
                  </div>
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>CONTROL</th>
                          <th>PASS RATE</th>
                          <th>EVIDENCE PASSED</th>
                          <th>ACCEPTED RISK</th>
                          <th>FAILING ACCOUNTS</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {framework.controls.map((c) => (
                          <tr key={c.control}>
                            <td>
                              <strong>{c.control}</strong>
                            </td>
                            <td>
                              <div className="score-cell">
                                <strong>
                                  {c.pass_pct === null
                                    ? "N/A"
                                    : `${c.pass_pct}%`}
                                </strong>
                                <div className="progress">
                                  <i style={{ width: `${c.pass_pct || 0}%` }} />
                                </div>
                              </div>
                            </td>
                            <td>
                              {c.passed} / {c.total}
                            </td>
                            <td>{c.accepted_risk}</td>
                            <td>{c.failing_accounts.length}</td>
                            <td>
                              <button
                                className="text-button"
                                onClick={() => {
                                  navigate("accounts");
                                  setFrameworkAccounts(c.failing_accounts);
                                }}
                              >
                                View accounts <ArrowRight size={13} />
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              ) : (
                <div className="loading">
                  <Loader2 className="spin" />
                  Loading framework evidence…
                </div>
              )}
            </>
          )}

          {page === "deployments" && (
            <section className="panel">
              <div className="panel-head">
                <div>
                  <h2>Baseline & stack deployments</h2>
                  <p>
                    CloudFormation and StackSets · Terraform pipeline events can
                    be integrated separately.
                  </p>
                </div>
                <Badge tone="amber">{deploymentInfo.drifted} drifted</Badge>
              </div>
              <div className="table-controls">
                <label className="search">
                  <Search size={15} />
                  <input
                    aria-label="Search deployments"
                    placeholder="Search displayed stacks or accounts…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </label>
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>STACK / BASELINE</th>
                      <th>AWS ACCOUNT</th>
                      <th>VERSION</th>
                      <th>DEPLOYMENT</th>
                      <th>DRIFT</th>
                      <th>LAST DEPLOYED</th>
                    </tr>
                  </thead>
                  <tbody>
                    {deployments
                      .filter((d) =>
                        (d.name + (d.account_name || ""))
                          .toLowerCase()
                          .includes(search.toLowerCase()),
                      )
                      .map((d, i) => (
                        <tr key={i}>
                          <td>
                            <span className="inline-icon">
                              <GitBranch size={16} />
                              <strong>{d.name}</strong>
                            </span>
                            <small className="table-sub">{d.region}</small>
                          </td>
                          <td>
                            <button
                              className="text-button"
                              onClick={() =>
                                setSelected(
                                  accounts.find((a) => a.id === d.account_id) ||
                                    null,
                                )
                              }
                            >
                              {d.account_name}
                            </button>
                            <small className="table-sub">
                              {envLabel(d.environment || "")}
                            </small>
                          </td>
                          <td>
                            <code>{d.version}</code>
                          </td>
                          <td>
                            <Status status={d.status} />
                          </td>
                          <td>
                            <Status status={d.drift_status} />
                          </td>
                          <td>{dateLabel(d.deployed_at)}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
                {!deployments.length && (
                  <Empty text="No deployment evidence yet" />
                )}
              </div>
              <div className="table-footer deployment-footer">
                <span>
                  Showing {deployments.length} of {deploymentInfo.total} recent
                  and baseline stacks. Open an account for full history.
                </span>
                {deploymentInfo.next_cursor && (
                  <button
                    className="button small"
                    disabled={busy}
                    onClick={async () => {
                      const version = requestVersion.current;
                      setBusy(true);
                      try {
                        const response = await api<DeploymentPage>(
                          `/deployments?env=${env}&cursor=${deploymentInfo.next_cursor}`,
                        );
                        if (version === requestVersion.current) {
                          setDeployments((previous) => [
                            ...previous,
                            ...response.items,
                          ]);
                          setDeploymentInfo(response);
                        }
                      } catch (e) {
                        setError((e as Error).message);
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    Load more
                  </button>
                )}
              </div>
            </section>
          )}

          {page === "waivers" && (
            <section className="panel">
              <div className="panel-head">
                <div>
                  <h2>Approved risk exceptions</h2>
                  <p>
                    Exceptions remain visible in audit evidence. Production
                    waivers expire within 90 days.
                  </p>
                </div>
                {(session.admin || session.teams.length > 0) && (
                  <button
                    className="button primary small"
                    onClick={() =>
                      setWaiverForm({
                        account_id: accounts[0]?.id || "",
                        rule_id: rules[0]?.id || "",
                        resource: "",
                      })
                    }
                  >
                    <Plus size={14} />
                    Create exception
                  </button>
                )}
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>RULE / ACCOUNT</th>
                      <th>OWNER</th>
                      <th>REASON</th>
                      <th>EXPIRES</th>
                      <th>STATUS</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {waivers
                      .filter(
                        (w) =>
                          env === "all" ||
                          accounts.some((a) => a.id === w.account_id),
                      )
                      .map((w) => (
                        <tr key={w.id}>
                          <td>
                            <strong>{w.rule_id}</strong>
                            <small className="table-sub">{w.account_id}</small>
                          </td>
                          <td>{w.owner}</td>
                          <td className="reason-cell">{w.reason}</td>
                          <td>{dateLabel(w.expires_at)}</td>
                          <td>
                            <Badge
                              tone={
                                w.revoked_at
                                  ? "neutral"
                                  : new Date(w.expires_at) > new Date()
                                    ? "amber"
                                    : "red"
                              }
                            >
                              {w.revoked_at
                                ? "Revoked"
                                : new Date(w.expires_at) > new Date()
                                  ? "Accepted risk"
                                  : "Expired"}
                            </Badge>
                          </td>
                          <td>
                            {!w.revoked_at &&
                              (session.admin ||
                                session.teams.includes(
                                  accounts.find((a) => a.id === w.account_id)
                                    ?.owner_team || "",
                                )) && (
                                <button
                                  className="text-button red-text"
                                  onClick={async () => {
                                    try {
                                      const result = await api<{
                                        pending_approval?: boolean;
                                      }>(`/waivers/${w.id}`, "DELETE");
                                      notify(
                                        result.pending_approval
                                          ? "Production revocation requested. Review the PR in Remediation & changes."
                                          : "Exception revoked. Original control status restored.",
                                      );
                                      await refresh();
                                    } catch (e) {
                                      setError((e as Error).message);
                                    }
                                  }}
                                >
                                  Revoke
                                </button>
                              )}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
                {!waivers.length && (
                  <Empty text="No risk exceptions recorded" />
                )}
              </div>
            </section>
          )}

          {page === "reports" && (
            <div className="report-grid">
              <section className="panel report-builder">
                <div className="report-art">
                  <FileBarChart2 size={32} />
                  <span>CONFIDENTIAL</span>
                </div>
                <h2>Build an audit export</h2>
                <p>
                  Current resource-level evidence, environment profiles and
                  approved risk exceptions in one file.
                </p>
                <label className="field">
                  Environment
                  <select
                    aria-label="Report environment"
                    value={env}
                    onChange={(e) => setEnv(e.target.value as Environment)}
                  >
                    {(
                      [
                        "all",
                        "production",
                        "testing",
                        "dev",
                        "unclassified",
                      ] as const
                    ).map((e) => (
                      <option key={e} value={e}>
                        {envLabel(e)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  File format
                  <select
                    aria-label="Report format"
                    value={reportFormat}
                    onChange={(e) => setReportFormat(e.target.value)}
                  >
                    <option value="csv">CSV · Spreadsheet ready</option>
                    <option value="json">JSON · Structured evidence</option>
                  </select>
                </label>
                <div className="report-includes">
                  <Check size={15} />
                  Resource-level findings
                  <Check size={15} />
                  Active and expired exceptions
                  <Check size={15} />
                  Framework control mappings
                </div>
                <button
                  className="button primary wide"
                  disabled={busy}
                  onClick={() => void exportReport()}
                >
                  {busy ? (
                    <Loader2 size={16} className="spin" />
                  ) : (
                    <ArrowDownToLine size={16} />
                  )}
                  {busy ? "Preparing your export…" : "Generate & download"}
                </button>
                <small className="report-foot">
                  <LockKeyhole size={12} />
                  Downloads require your access. Links expire in 15 minutes.
                </small>
              </section>
              <section className="panel weekly-panel">
                <div className="panel-head">
                  <div>
                    <h2>Weekly summary</h2>
                    <p>A regular pulse on organization-wide governance.</p>
                  </div>
                  <Bell size={20} />
                </div>
                <div className="weekly-content">
                  <div className="schedule-line">
                    <span className="schedule-icon">
                      <Clock3 size={24} />
                    </span>
                    <div>
                      <strong>Every Monday at 08:00 UTC</strong>
                      <small>
                        Delivered to the configured governance channel
                      </small>
                    </div>
                  </div>
                  <label className="field">
                    Delivery channel
                    <select
                      aria-label="Weekly summary channel"
                      disabled={!session.admin}
                      value={weekly.channel}
                      onChange={(e) =>
                        setWeekly({ ...weekly, channel: e.target.value })
                      }
                    >
                      <option value="email">Email · SNS subscribers</option>
                      <option value="slack">
                        Slack · Secrets Manager webhook
                      </option>
                    </select>
                  </label>
                  <label className="toggle-label">
                    <span>
                      <strong>Enable weekly summaries</strong>
                      <small>
                        {session.mode === "demo"
                          ? "Demo settings are stored locally. Delivery requires AWS deployment."
                          : "Recipients are managed through Terraform and Secrets Manager."}
                      </small>
                    </span>
                    <input
                      aria-label="Enable weekly summaries"
                      type="checkbox"
                      checked={weekly.enabled}
                      disabled={!session.admin}
                      onChange={(e) =>
                        setWeekly({ ...weekly, enabled: e.target.checked })
                      }
                    />
                  </label>
                  <button
                    className="button"
                    disabled={!session.admin || busy}
                    onClick={async () => {
                      try {
                        await api("/settings/weekly", "PUT", weekly);
                        notify("Weekly summary settings saved.");
                      } catch (e) {
                        setError((e as Error).message);
                      }
                    }}
                  >
                    <Check size={15} />
                    Save settings
                  </button>
                  <div className="framework-note">
                    <CircleHelp size={16} />
                    Framework reports cover technical controls. Procedural
                    evidence stays with the ISMS team.
                  </div>
                </div>
              </section>
            </div>
          )}
          <footer className="page-footer">
            <span>
              <ShieldCheck size={13} />
              AWS Governance & Compliance Portal
            </span>
            <span>
              Scoped member actions<span className="dot-separator">·</span>
              Encrypted evidence<span className="dot-separator">·</span>Login
              protected
            </span>
          </footer>
        </main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <CheckCircle2 size={20} />
          <span>{toast}</span>
          <button
            onClick={() => setToast("")}
            aria-label="Dismiss notification"
          >
            <X size={16} />
          </button>
        </div>
      )}
      {selected && (
        <div
          className="overlay"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setSelected(null);
          }}
        >
          <section
            className="drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="account-title"
          >
            <div className="drawer-head">
              <span className="eyebrow">ACCOUNT DETAIL</span>
              <button
                className="icon-button"
                aria-label="Close account detail"
                onClick={() => setSelected(null)}
              >
                <X size={20} />
              </button>
            </div>
            <h2 id="account-title">{selected.name}</h2>
            <div className="detail-meta">
              <code>{selected.id}</code>
              <Badge>{envLabel(selected.environment)}</Badge>
              <span>{selected.owner_team}</span>
            </div>
            <div className="detail-score">
              <strong>{selected.score_pct}%</strong>
              <span>
                Compliance score<small>{selected.ou_path}</small>
              </span>
              {session.admin && (
                <button
                  className="button small"
                  disabled={busy}
                  onClick={() => void scan(selected.id)}
                >
                  <RefreshCw size={13} />
                  Rescan
                </button>
              )}
            </div>
            <p className="muted">
              Last scanned {dateLabel(selected.last_scanned_at)}
            </p>
            <div className="detail-section-title">
              <h3>Resource findings</h3>
              <select
                aria-label="Finding status"
                value={detailStatus}
                onChange={(e) => setDetailStatus(e.target.value)}
              >
                <option value="issues">Issues & accepted risk</option>
                <option value="all">All results</option>
                <option value="PASS">Passing</option>
                <option value="ACCEPTED_RISK">Accepted risk</option>
              </select>
              <select
                aria-label="Finding severity"
                value={detailSeverity}
                onChange={(e) => setDetailSeverity(e.target.value)}
              >
                <option value="">All severities</option>
                <option value="critical">Critical</option>
                <option value="fail">Fail</option>
                <option value="warn">Warn</option>
              </select>
            </div>
            {detailLoading ? (
              <div className="loading">
                <Loader2 className="spin" />
                Loading evidence…
              </div>
            ) : (
              findings
                .filter(
                  (f) =>
                    (detailStatus === "all" || detailStatus === "issues"
                      ? !["PASS", "NOT_APPLICABLE"].includes(f.status) ||
                        detailStatus === "all"
                      : f.status === detailStatus) &&
                    (!detailSeverity || f.severity === detailSeverity),
                )
                .map((f, i) => (
                  <div className="finding" key={i}>
                    <div>
                      <Status status={f.status} />
                      {f.sample_data && <Badge tone="amber">Sample</Badge>}
                      <span
                        className={
                          f.severity === "critical" ? "red-text" : "muted"
                        }
                      >
                        {f.severity}
                      </span>
                    </div>
                    <strong>{f.title}</strong>
                    <code>{f.resource}</code>
                    {f.detail && <p>{f.detail}</p>}
                    <div className="finding-actions">
                      {!f.sample_data && ["FAIL", "WARN"].includes(f.status) &&
                        (session.admin ||
                          session.teams.includes(selected.owner_team)) && (
                          <button
                            className="button primary small"
                            onClick={() => {
                              setChangePick({
                                account_id: selected.id,
                                rule_id: f.rule_id,
                                resource: f.resource,
                                region: f.region,
                              });
                              setSelected(null);
                              navigate("changes");
                            }}
                          >
                            Plan remediation <Play size={12} />
                          </button>
                        )}

                      <a
                        href={`https://${f.region || "us-east-1"}.console.aws.amazon.com/resource-explorer/home?region=${f.region || "us-east-1"}#/search?query=${encodeURIComponent(f.resource)}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        AWS console <ExternalLink size={12} />
                      </a>
                      {["FAIL", "WARN"].includes(f.status) &&
                        (session.admin ||
                          (selected.environment !== "production" &&
                            session.teams.includes(selected.owner_team))) && (
                          <button
                            className="text-button"
                            onClick={() =>
                              setWaiverForm({
                                account_id: selected.id,
                                rule_id: f.rule_id,
                                resource: f.resource,
                              })
                            }
                          >
                            Record exception <Plus size={12} />
                          </button>
                        )}
                    </div>
                  </div>
                ))
            )}
            <h3 className="detail-subhead">Deployment history</h3>
            {accountDeploys.map((d, i) => (
              <div className="detail-deployment" key={i}>
                <GitBranch size={18} />
                <span>
                  <strong>{d.name}</strong>
                  <small>
                    {d.version} · {dateLabel(d.deployed_at)}
                  </small>
                </span>
                <Status status={d.drift_status} />
              </div>
            ))}
            {!detailLoading && !accountDeploys.length && (
              <p className="muted">No deployment evidence available.</p>
            )}
            <h3 className="detail-subhead">Active risk exceptions</h3>
            {waivers
              .filter(
                (w) =>
                  w.account_id === selected.id &&
                  !w.revoked_at &&
                  new Date(w.expires_at) > new Date(),
              )
              .map((w) => (
                <div className="finding" key={w.id}>
                  <Badge tone="amber">Accepted risk</Badge>
                  <strong>{w.rule_id}</strong>
                  <p>{w.reason}</p>
                  <p>
                    Owner: {w.owner} · Expires {dateLabel(w.expires_at)}
                  </p>
                </div>
              ))}
          </section>
        </div>
      )}
      {waiverForm && (
        <div className="overlay modal-layer">
          <form
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="waiver-title"
            onSubmit={async (e) => {
              e.preventDefault();
              const form = new FormData(e.currentTarget);
              setBusy(true);
              try {
                const waiverResult = await api<{ pending_approval?: boolean }>(
                  "/waivers",
                  "POST",
                  {
                    ...waiverForm,
                    account_id: form.get("account_id"),
                    rule_id: form.get("rule_id"),
                    owner: form.get("owner"),
                    reason: form.get("reason"),
                    expires_at: new Date(
                      form.get("expiry") as string,
                    ).toISOString(),
                  },
                );
                setWaiverForm(null);
                setSelected(null);
                notify(
                  waiverResult.pending_approval
                    ? "Production exception requested. Review its PR in Remediation & changes."
                    : "Risk exception approved. Evidence and account score updated.",
                );
                await refresh();
              } catch (err) {
                setWaiverError((err as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <div className="drawer-head">
              <h2 id="waiver-title">Record a risk exception</h2>
              <button
                type="button"
                className="icon-button"
                aria-label="Close exception form"
                onClick={() => setWaiverForm(null)}
              >
                <X size={20} />
              </button>
            </div>
            <p>
              Accepted risk stays visible in audit exports and automatically
              expires.
            </p>
            {waiverError && (
              <div className="error-banner" role="alert">
                {waiverError}
              </div>
            )}
            <label className="field">
              AWS account
              <select
                name="account_id"
                defaultValue={waiverForm.account_id}
                required
              >
                {accounts
                  .filter(
                    (a) =>
                      session.admin ||
                      (a.environment !== "production" &&
                        session.teams.includes(a.owner_team)),
                  )
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name} ({a.id})
                    </option>
                  ))}
              </select>
            </label>
            <label className="field">
              Rule
              <select name="rule_id" defaultValue={waiverForm.rule_id}>
                {rules.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.title}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Risk owner
              <input
                name="owner"
                type="email"
                placeholder="owner@company.com"
                required
              />
            </label>
            <label className="field">
              Reason
              <textarea
                name="reason"
                minLength={10}
                required
                placeholder="Explain why this risk is accepted and the planned resolution…"
              />
            </label>
            <label className="field">
              Expiry date
              <input
                name="expiry"
                type="datetime-local"
                required
                min={new Date(
                  Date.now() - new Date().getTimezoneOffset() * 60000,
                )
                  .toISOString()
                  .slice(0, 16)}
              />
            </label>
            <p className="form-help">
              Production exceptions require an administrator and expire within
              90 days.
            </p>
            <button
              className="button primary wide"
              disabled={busy}
              type="submit"
            >
              {busy ? "Saving…" : "Submit exception"}
            </button>
          </form>
        </div>
      )}
    </div>
  );
}

function LayersIcon({ env }: { env: string }) {
  return env === "production" ? (
    <ShieldCheck size={20} />
  ) : env === "testing" ? (
    <Code2 size={20} />
  ) : (
    <Cloud size={20} />
  );
}
function Heatmap({ accounts, rules }: { accounts: Account[]; rules: Rule[] }) {
  // Aggregated account rule counters come from the evaluator; no inferred status from scores.
  const ous = [...new Set(accounts.map((a) => a.ou_path))].slice(0, 6);
  return (
    <div className="heatmap-scroll">
      <table className="heatmap">
        <thead>
          <tr>
            <th>CONTROL</th>
            {ous.map((ou) => (
              <th key={ou} title={ou}>
                {ou.split("/").at(-1)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rules.slice(0, 10).map((r) => (
            <tr key={r.id}>
              <td title={r.title}>
                {r.area} · {r.id.split("_")[0]}
              </td>
              {ous.map((ou) => {
                const members = accounts.filter((a) => a.ou_path === ou);
                const failing = members.filter(
                  (a) =>
                    ((
                      a as Account & {
                        rule_summary?: Record<string, { issues: number }>;
                      }
                    ).rule_summary?.[r.id]?.issues || 0) > 0,
                ).length;
                return (
                  <td key={ou}>
                    <span
                      title={`${r.title}: ${failing} of ${members.length} accounts with issues`}
                      className={failing ? "heat-cell warm" : "heat-cell cool"}
                    >
                      {failing}
                    </span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="heatmap-legend">
        <span>
          <i className="heat-cell cool" />
          No issues
        </span>
        <span>
          <i className="heat-cell warm" />
          Needs attention
        </span>
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
