# Open the deployed portal on your computer

These steps open the **GKE-deployed** ECG research portal at
`http://127.0.0.1:8081/login`. They do not start a local copy of the API or
frontend. Run the tunnel on the **same computer** as the browser.

## Prerequisites

- Install the Google Cloud CLI and `kubectl`.
- Sign in to Google Cloud with an account that can access project
  `banded-torus-510415-g2` and its GKE cluster.
- Connect to a network or VPN that can reach the GKE control plane. The
  frontend load balancer is private; do not expose it publicly for this preview.

## Connect and open

1. In a terminal on your computer, select the cluster:

   ```sh
   gcloud container clusters get-credentials ecg-research-gke \
     --region asia-south1 \
     --project banded-torus-510415-g2
   kubectl -n ecg-clinical-research rollout status deployment/ecg-platform-frontend --timeout=60s
   ```

2. In that terminal, start the local tunnel:

   ```sh
   kubectl -n ecg-clinical-research port-forward --address 127.0.0.1 \
     service/ecg-platform-frontend 8081:80
   ```

   Wait for `Forwarding from 127.0.0.1:8081 -> 8080`. **Keep this terminal
   running** while using the portal. `Ctrl+C`, closing the terminal, sleeping
   the computer, or losing the cluster connection stops the tunnel.

3. Open [http://127.0.0.1:8081/login](http://127.0.0.1:8081/login) in a normal
   browser on the same computer. A browser running elsewhere cannot use this
   computer's `127.0.0.1` address. If an in-app browser blocks local URLs,
   use your regular browser.

## Troubleshooting

In another terminal, check the local page and API proxy:

```sh
curl -I http://127.0.0.1:8081/login
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8081/api/v1/auth/me
```

The login page should return `200`. The API check should return `401` before
sign-in; that confirms the API proxy is reachable, not that login has failed.

- **Connection refused / site cannot be reached:** No process is listening on
  port `8081`. Restart the port-forward command and leave it running.
- **Cannot connect to the Kubernetes server / timeout:** Check your internet
  or VPN connection, Google Cloud login, and cluster credentials. Retry step 1
  before starting the tunnel again.
- **Port 8081 already in use:** Find its listener with
  `lsof -nP -iTCP:8081 -sTCP:LISTEN`, or forward `8082:80` and open
  `http://127.0.0.1:8082/login` instead.
- **Page loads but API requests fail:** Check
  `kubectl -n ecg-clinical-research get deployment ecg-platform-api` and avoid
  sharing passwords, access tokens, or patient data in logs.

If the port-forward runs in **Cloud Shell** rather than on your computer, use
Cloud Shell's **Web Preview** for port `8081`; your computer's
`127.0.0.1:8081` will not reach the Cloud Shell tunnel. See the
[GKE deployment guide](docs/gke-deployment.md) for that workflow.
