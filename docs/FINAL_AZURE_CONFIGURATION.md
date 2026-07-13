# Final Azure production configuration

Run these steps from Azure Cloud Shell PowerShell after the code deployment is green.

## 1. Configure custom HTTP probes

```powershell
./scripts/configure_container_apps_probes.ps1
```

Verify:

```powershell
foreach ($app in @("inventory-agent", "supplier-agent", "supervisor-agent")) {
    az containerapp show `
        --name $app `
        --resource-group grupoderecursos_cassiofrd `
        --query "properties.template.containers[0].probes" `
        --output yaml
}
```

The applications use:

- Startup: `GET /live`
- Liveness: `GET /live`
- Readiness: `GET /ready`

## 2. Create and connect Application Insights

```powershell
./scripts/configure_application_insights.ps1
```

This creates a workspace-based Application Insights resource linked to the existing Log Analytics workspace, stores the connection string as a Container Apps secret, and enables `OTEL_ENABLED=true`.

Generate traffic after configuration, then check Application Insights. Telemetry ingestion can take several minutes.

## 3. Protect specialist agents with internal ingress

Before this step, choose a strong shared API token and set it only in the current shell:

```powershell
$env:API_TOKEN = "<strong-random-token>"
./scripts/secure_specialist_ingress.ps1 -Apply
```

The script:

- stores the same token as a secret in all three Container Apps;
- configures the Supervisor to authenticate calls to specialists;
- changes Inventory and Supplier ingress from external to internal;
- keeps the Supervisor external.

After this change, direct internet smoke tests against Inventory and Supplier are expected to stop working. Test through the Supervisor instead.

## 4. Final verification

```powershell
$supervisorFqdn = az containerapp show `
    --name supervisor-agent `
    --resource-group grupoderecursos_cassiofrd `
    --query properties.configuration.ingress.fqdn `
    --output tsv

$supervisorUrl = "https://$supervisorFqdn"

Invoke-RestMethod "$supervisorUrl/live"
Invoke-WebRequest "$supervisorUrl/ready" -UseBasicParsing

$headers = @{
    Authorization = "Bearer $env:API_TOKEN"
}

$body = @{
    question = "Quem fornece o PARAFUSO-M20?"
    session_id = "production-release"
} | ConvertTo-Json

Invoke-RestMethod `
    -Uri "$supervisorUrl/copilot" `
    -Method POST `
    -Headers $headers `
    -ContentType "application/json; charset=utf-8" `
    -Body $body
```
