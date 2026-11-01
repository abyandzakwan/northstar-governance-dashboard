export type Environment =
  "all" | "dev" | "testing" | "production" | "unclassified";
export type Account = {
  id: string;
  name: string;
  environment: Environment;
  owner_team: string;
  ou_path: string;
  score_pct: number;
  critical_count: number;
  fail_count: number;
  healthy: boolean;
  stale: boolean;
  last_scanned_at?: string;
  accepted_risk_count: number;
};
export type Finding = {
  rule_id: string;
  resource: string;
  title: string;
  status: string;
  severity: string;
  detail?: string;
  region?: string;
  waiver_id?: string;
  frameworks: Record<string, string[]>;
};
export type Deployment = {
  name: string;
  version: string;
  status: string;
  drift_status: string;
  deployed_at: string | null;
  account_id?: string;
  account_name?: string;
  environment?: string;
  region: string;
  pipeline_run_url?: string;
};
export type Summary = {
  environment: Environment;
  accounts_total: number;
  healthy: number;
  avg_score: number | null;
  critical_count: number;
  stale_count: number;
};
export type Rule = {
  id: string;
  title: string;
  area: string;
  source: string;
  severity: Record<string, string>;
  frameworks: Record<string, string[]>;
  failing_accounts: number;
  passing: number;
  total: number;
};
export type Waiver = {
  id: string;
  account_id: string;
  rule_id: string;
  owner: string;
  reason: string;
  resource: string;
  expires_at: string;
  revoked_at?: string;
};
export type Control = {
  control: string;
  pass_pct: number | null;
  passed: number;
  total: number;
  accepted_risk: number;
  failing_accounts: string[];
};
export type Framework = {
  id: string;
  name: string;
  edition: string;
  scope: string;
  accounts_in_scope: number;
  controls: Control[];
};
export type Report = {
  id: string;
  status: string;
  download_url?: string;
  format: string;
};
export type Config = {
  apiUrl: string;
  cognitoDomain?: string;
  clientId?: string;
  redirectUri?: string;
  identityProvider?: string | null;
};
export let config: Config = { apiUrl: "/api" };
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function loadConfig() {
  const response = await fetch("/config.json");
  if (response.ok && response.headers.get("content-type")?.includes("json"))
    config = await response.json();
}
export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(`${config.apiUrl}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(sessionStorage.getItem("access_token")
        ? { Authorization: `Bearer ${sessionStorage.getItem("access_token")}` }
        : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (!response.ok)
    throw new ApiError(
      data.error || `Request failed (${response.status})`,
      response.status,
    );
  return data;
}
function base64url(bytes: Uint8Array) {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}
export async function login() {
  if (!config.cognitoDomain || !config.clientId)
    throw new Error("Portal sign-in configuration is unavailable.");
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const state = base64url(crypto.getRandomValues(new Uint8Array(24)));
  const challenge = base64url(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)),
    ),
  );
  sessionStorage.setItem("pkce", verifier);
  sessionStorage.setItem("oauth_state", state);
  const params = new URLSearchParams({
    client_id: config.clientId,
    response_type: "code",
    scope: "openid email profile",
    redirect_uri: config.redirectUri || location.origin,
    code_challenge: challenge,
    code_challenge_method: "S256",
    state,
  });
  if (config.identityProvider)
    params.set("identity_provider", config.identityProvider);
  location.assign(`${config.cognitoDomain}/oauth2/authorize?${params}`);
}
export async function finishLogin() {
  const params = new URLSearchParams(location.search);
  if (params.has("error"))
    throw new Error(params.get("error_description") || "Sign-in failed");
  if (!params.has("code")) return;
  if (
    !params.get("state") ||
    params.get("state") !== sessionStorage.getItem("oauth_state")
  )
    throw new Error("Sign-in state does not match. Please sign in again.");
  const result = await fetch(`${config.cognitoDomain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: config.clientId!,
      redirect_uri: config.redirectUri || location.origin,
      code: params.get("code")!,
      code_verifier: sessionStorage.getItem("pkce") || "",
    }),
  });
  const token = await result.json();
  if (!result.ok) throw new Error("Sign-in token exchange failed");
  sessionStorage.setItem("access_token", token.access_token);
  sessionStorage.removeItem("pkce");
  sessionStorage.removeItem("oauth_state");
  history.replaceState(null, "", location.pathname);
}
export function logout() {
  sessionStorage.clear();
  if (config.cognitoDomain)
    location.assign(
      `${config.cognitoDomain}/logout?${new URLSearchParams({ client_id: config.clientId!, logout_uri: config.redirectUri || location.origin })}`,
    );
  else location.reload();
}
export function envLabel(env: string) {
  return (
    (
      {
        all: "All environments",
        dev: "Development",
        testing: "Staging",
        production: "Production",
        unclassified: "Unclassified",
      } as Record<string, string>
    )[env] || env
  );
}
export function dateLabel(value?: string | null) {
  return value
    ? new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(value))
    : "Not available";
}
