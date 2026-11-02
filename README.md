# northstar-governance-dashboard

A central dashboard for AWS compliance evidence, exceptions and reviewed remediation.

For GitHub Actions and a deployment to one testing AWS account, follow [the standalone deployment guide](docs/standalone.md). Start with the checked-in [portal configuration example](infra/envs/central.tfvars.json.example) and [foundation example](infra/envs/foundation.tfvars.example). For multiple real AWS accounts, see [organization enrollment](docs/deployment.md).

The local demo contains six virtual accounts, two per environment:

| Environment | Flight | Booking |
|---|---|---|
| dev | ns-fligth-dev | ns-book-dev |
| testing | ns-fligth-testing | ns-book-testing |
| production | ns-fligth-production | ns-book-production |

These are synthetic records. Terraform deploys the portal into your existing testing AWS account; it does not create those six AWS accounts. The deployed portal collects real evidence from the configured account.

Run the local demo in two terminals:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
PYTHONPATH=app/functions LOCAL_STATE=:memory: .venv/bin/python -m portal.local
```

```sh
cd app/frontend
npm ci
npm run dev
```

Open the Vite URL. The in-memory demo starts fresh each time the API restarts. For persistent local demo state, omit `LOCAL_STATE=:memory:`; an existing `.local/state.json` retains its saved records until you move it aside.
